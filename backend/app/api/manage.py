"""Managed writes (Phase 2): staged changes, confirmation, audit, creatives,
and the Google-only surface (keywords, search terms, PMax asset groups).

Guardrail architecture: there is no endpoint that writes to a platform
directly. Every mutation is staged as a PendingChange (returning the exact
before/after diff the user must confirm), and a second explicit call to
/manage/changes/{id}/execute performs the write and records an immutable
audit entry — success or failure. Read-only Google reports and creative
building (which cannot affect spend by itself) are the only direct calls.
"""

import datetime as dt
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope, require_team
from ..models.ads import Ad, AdGroup, Campaign, Creative
from ..models.audit import (
    AUDIT_FAILED,
    AUDIT_SUCCESS,
    CHANGE_CANCELED,
    CHANGE_EXECUTED,
    CHANGE_FAILED,
    CHANGE_PENDING,
    CHANGE_TTL_MINUTES,
    AuditLogEntry,
    PendingChange,
)
from ..models.base import utcnow
from ..models.core import (
    PLATFORM_GOOGLE,
    PLATFORM_META,
    AdAccount,
    PlatformConnection,
    User,
)
from ..schemas import (
    AssetGroupOut,
    AuditEntryOut,
    ChangeCreateIn,
    CreativeCreateIn,
    CreativeOut,
    ImageUploadIn,
    KeywordOut,
    PendingChangeOut,
    SearchTermOut,
)
from ..services import change_executor
from ..services import connections as conn_svc
from ..services import google_ads_api, meta_api

router = APIRouter(prefix="/api", tags=["manage"])

_ENTITY_MODELS = {"campaign": Campaign, "ad_group": AdGroup, "ad": Ad}

# What can be staged, per entity type. Anything else is rejected at staging
# time so unsupported combinations never reach the executor.
_ALLOWED = {
    "campaign": {"create", "update", "pause", "resume"},
    "ad_group": {"create", "update", "pause", "resume"},
    "ad": {"create", "update", "pause", "resume"},
    "keyword": {"add", "remove"},
    "campaign_negative": {"add"},
    "asset_group": {"pause", "resume"},
}

# Payload fields whose values are eligible for the visible diff, per entity.
_DIFFABLE_FIELDS = [
    "name",
    "status",
    "objective",
    "daily_budget_micros",
    "lifetime_budget_micros",
    "bid_micros",
    "text",
    "match_type",
    "negative",
    "headlines",
    "descriptions",
    "final_url",
    "criterion_id",
]


def _connection_for(db: Session, account: AdAccount) -> PlatformConnection:
    conn = db.get(PlatformConnection, account.connection_id)
    if conn is None or conn.status != "active":
        raise HTTPException(
            409,
            f"The {account.platform} connection for this account is not active"
            + (f": {conn.error_detail}" if conn and conn.error_detail else ""),
        )
    return conn


def _load_entity(db: Session, scope: TenantScope, entity_type: str, entity_id: str):
    model = _ENTITY_MODELS.get(entity_type)
    if model is None:
        raise HTTPException(400, f"{entity_type} changes do not reference local rows")
    return scope.get_or_404(db, model, entity_id)


def _verify_entity_in_account(db: Session, entity, entity_type: str, account: AdAccount):
    """Walk the hierarchy up to the ad account so a staged change can never
    execute against a different account than the one it claims."""
    if entity_type == "campaign":
        ok = entity.ad_account_id == account.id
    elif entity_type == "ad_group":
        campaign = db.get(Campaign, entity.campaign_id)
        ok = campaign is not None and campaign.ad_account_id == account.id
    else:  # ad
        ad_group = db.get(AdGroup, entity.ad_group_id)
        campaign = db.get(Campaign, ad_group.campaign_id) if ad_group else None
        ok = campaign is not None and campaign.ad_account_id == account.id
    if not ok:
        raise HTTPException(400, "Entity does not belong to the given ad account")


def _build_diff(body: ChangeCreateIn, entity) -> List[Dict[str, Any]]:
    if body.action in ("pause", "resume"):
        after = "PAUSED" if body.action == "pause" else "ACTIVE/ENABLED"
        before = getattr(entity, "status", None) if entity is not None else None
        return [{"field": "status", "before": before, "after": after}]
    diff = []
    for field in _DIFFABLE_FIELDS:
        if field in body.payload and body.payload[field] is not None:
            before = getattr(entity, field, None) if entity is not None else None
            diff.append({"field": field, "before": before, "after": body.payload[field]})
    if not diff:
        raise HTTPException(400, "Change payload contains no supported fields")
    return diff


def _validate_payload_refs(
    db: Session, scope: TenantScope, body: ChangeCreateIn, account: AdAccount
) -> None:
    """Creates/adds carry parent ids in the payload — verify each one is in
    scope and inside the same ad account before staging."""
    refs = {
        "campaign_id": Campaign,
        "ad_group_id": AdGroup,
        "creative_id": Creative,
    }
    for key, model in refs.items():
        if key in body.payload and body.payload[key]:
            obj = scope.get_or_404(db, model, body.payload[key])
            if key != "creative_id":
                _verify_entity_in_account(
                    db, obj, "campaign" if key == "campaign_id" else "ad_group", account
                )
            elif obj.client_id != account.client_id:
                raise HTTPException(400, "Creative belongs to a different client")


@router.post("/manage/changes", response_model=PendingChangeOut)
def stage_change(
    body: ChangeCreateIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    if body.entity_type not in _ALLOWED:
        raise HTTPException(400, f"Unknown entity type {body.entity_type}")
    if body.action not in _ALLOWED[body.entity_type]:
        raise HTTPException(
            400, f"Action {body.action} not allowed for {body.entity_type}"
        )
    account = scope.get_or_404(db, AdAccount, body.ad_account_id)
    if body.entity_type in ("keyword", "campaign_negative", "asset_group"):
        if account.platform != PLATFORM_GOOGLE:
            raise HTTPException(400, f"{body.entity_type} is Google-only")

    entity = None
    entity_external_id = body.entity_external_id
    entity_name = body.entity_name
    if body.action in ("update", "pause", "resume") and body.entity_type != "asset_group":
        if not body.entity_id:
            raise HTTPException(400, "entity_id is required for this action")
        entity = _load_entity(db, scope, body.entity_type, body.entity_id)
        if entity.platform != account.platform:
            raise HTTPException(400, "Entity platform does not match account")
        _verify_entity_in_account(db, entity, body.entity_type, account)
        entity_external_id = entity.external_id
        entity_name = entity.name
    elif body.entity_type == "asset_group":
        if not entity_external_id:
            raise HTTPException(400, "entity_external_id is required for asset groups")
    _validate_payload_refs(db, scope, body, account)

    change = PendingChange(
        organization_id=account.organization_id,
        client_id=account.client_id,
        created_by_user_id=user.id,
        platform=account.platform,
        ad_account_id=account.id,
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        entity_external_id=entity_external_id,
        entity_name=entity_name or body.payload.get("name"),
        action=body.action,
        payload=body.payload,
        diff=_build_diff(body, entity),
        expires_at=utcnow() + dt.timedelta(minutes=CHANGE_TTL_MINUTES),
    )
    db.add(change)
    db.commit()
    db.refresh(change)
    return change


@router.get("/manage/changes", response_model=List[PendingChangeOut])
def list_changes(
    status: Optional[str] = None,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    stmt = select(PendingChange).order_by(PendingChange.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(PendingChange.status == status)
    return db.execute(scope.filter(stmt, PendingChange)).scalars().all()


@router.delete("/manage/changes/{change_id}", response_model=PendingChangeOut)
def cancel_change(
    change_id: str,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    change = scope.get_or_404(db, PendingChange, change_id)
    if change.status != CHANGE_PENDING:
        raise HTTPException(409, f"Change is already {change.status}")
    change.status = CHANGE_CANCELED
    db.commit()
    db.refresh(change)
    return change


def _write_audit(
    db: Session,
    change: PendingChange,
    account: AdAccount,
    user: User,
    status: str,
    error_detail: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
) -> None:
    db.add(
        AuditLogEntry(
            organization_id=change.organization_id,
            client_id=change.client_id,
            pending_change_id=change.id,
            user_id=user.id,
            user_email=user.email,
            user_name=user.full_name,
            platform=change.platform,
            ad_account_external_id=account.external_id,
            entity_type=change.entity_type,
            entity_external_id=(result or {}).get("external_id")
            or change.entity_external_id,
            entity_name=change.entity_name,
            action=change.action,
            diff=change.diff,
            status=status,
            error_detail=error_detail,
        )
    )


@router.post("/manage/changes/{change_id}/execute", response_model=PendingChangeOut)
def execute_change(
    change_id: str,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    change = scope.get_or_404(db, PendingChange, change_id)
    if change.status != CHANGE_PENDING:
        raise HTTPException(409, f"Change is already {change.status}")
    if change.expires_at < utcnow():
        change.status = CHANGE_CANCELED
        change.error_detail = "Confirmation window expired — stage the change again"
        db.commit()
        raise HTTPException(409, "Change expired before confirmation; stage it again")

    account = db.get(AdAccount, change.ad_account_id)
    conn = _connection_for(db, account)

    try:
        result = change_executor.execute(db, change, account, conn)
    except (meta_api.MetaAuthError, google_ads_api.GoogleAuthError) as e:
        change.status = CHANGE_FAILED
        change.error_detail = str(e)
        _write_audit(db, change, account, user, AUDIT_FAILED, str(e))
        conn_svc.mark_disconnected(db, conn, str(e))  # commits
        raise HTTPException(
            502,
            f"{change.platform} rejected the stored credentials — the connection "
            "has been marked disconnected and needs to be re-authorized",
        )
    except (
        meta_api.MetaApiError,
        google_ads_api.GoogleApiError,
        change_executor.UnsupportedChange,
    ) as e:
        change.status = CHANGE_FAILED
        change.error_detail = str(e)
        _write_audit(db, change, account, user, AUDIT_FAILED, str(e))
        db.commit()
        raise HTTPException(502, f"{change.platform} rejected the change: {e}")

    change.status = CHANGE_EXECUTED
    change.executed_at = utcnow()
    change.executed_by_user_id = user.id
    if result.get("external_id"):
        change.entity_external_id = result["external_id"]
    _write_audit(db, change, account, user, AUDIT_SUCCESS, result=result)
    db.commit()
    db.refresh(change)
    return change


@router.get("/audit-log", response_model=List[AuditEntryOut])
def audit_log(
    client_id: Optional[str] = None,
    platform: Optional[str] = None,
    entity_type: Optional[str] = None,
    action: Optional[str] = None,
    status: Optional[str] = None,
    since: Optional[dt.datetime] = None,
    until: Optional[dt.datetime] = None,
    limit: int = Query(200, le=1000),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Queryable trail: who changed what, on which platform, when. Client
    users see their own account's entries (that's the 'why did spend change'
    answer); team sees everything."""
    stmt = select(AuditLogEntry).order_by(AuditLogEntry.created_at.desc()).limit(limit)
    stmt = scope.filter(stmt, AuditLogEntry)
    if client_id:
        scope.check_client_id(client_id)
        stmt = stmt.where(AuditLogEntry.client_id == client_id)
    if platform:
        stmt = stmt.where(AuditLogEntry.platform == platform)
    if entity_type:
        stmt = stmt.where(AuditLogEntry.entity_type == entity_type)
    if action:
        stmt = stmt.where(AuditLogEntry.action == action)
    if status:
        stmt = stmt.where(AuditLogEntry.status == status)
    if since:
        stmt = stmt.where(AuditLogEntry.created_at >= since)
    if until:
        stmt = stmt.where(AuditLogEntry.created_at <= until)
    return db.execute(stmt).scalars().all()


# --- creatives (Meta) ---
# Building a creative can't change spend by itself — an ad has to reference
# it, and ad creation goes through the pending-change flow. So these are
# direct, but team-gated.


def _meta_account(
    db: Session, scope: TenantScope, account_id: str
) -> tuple[AdAccount, PlatformConnection]:
    account = scope.get_or_404(db, AdAccount, account_id)
    if account.platform != PLATFORM_META:
        raise HTTPException(400, "This endpoint is Meta-only")
    return account, _connection_for(db, account)


def _google_account(
    db: Session, scope: TenantScope, account: AdAccount
) -> PlatformConnection:
    if account.platform != PLATFORM_GOOGLE:
        raise HTTPException(400, "This endpoint is Google-only")
    return _connection_for(db, account)


@router.get("/ad-accounts/{account_id}/pages")
def list_pages(
    account_id: str,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    account, conn = _meta_account(db, scope, account_id)
    try:
        return meta_api.fetch_pages(conn_svc.get_access_token(conn))
    except meta_api.MetaAuthError as e:
        conn_svc.mark_disconnected(db, conn, str(e))
        raise HTTPException(502, "Meta rejected the stored credentials")


@router.post("/ad-accounts/{account_id}/images")
def upload_image(
    account_id: str,
    body: ImageUploadIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    account, conn = _meta_account(db, scope, account_id)
    try:
        resp = meta_api.upload_ad_image(
            conn_svc.get_access_token(conn), account.external_id, body.data_b64, body.name
        )
    except meta_api.MetaAuthError as e:
        conn_svc.mark_disconnected(db, conn, str(e))
        raise HTTPException(502, "Meta rejected the stored credentials")
    images = resp.get("images", {})
    first = next(iter(images.values()), {})
    return {"image_hash": first.get("hash"), "url": first.get("url")}


@router.post("/ad-accounts/{account_id}/creatives", response_model=CreativeOut)
def create_creative(
    account_id: str,
    body: CreativeCreateIn,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    account, conn = _meta_account(db, scope, account_id)
    link_data: Dict[str, Any] = {"message": body.message, "link": body.link}
    if body.title:
        link_data["name"] = body.title
    if body.description:
        link_data["description"] = body.description
    if body.image_hash:
        link_data["image_hash"] = body.image_hash
    if body.call_to_action:
        link_data["call_to_action"] = {
            "type": body.call_to_action,
            "value": {"link": body.link},
        }
    spec = {"page_id": body.page_id, "link_data": link_data}
    try:
        resp = meta_api.create_creative(
            conn_svc.get_access_token(conn),
            account.external_id,
            {"name": body.name, "object_story_spec": spec},
        )
    except meta_api.MetaAuthError as e:
        conn_svc.mark_disconnected(db, conn, str(e))
        raise HTTPException(502, "Meta rejected the stored credentials")
    creative = Creative(
        organization_id=account.organization_id,
        client_id=account.client_id,
        platform=PLATFORM_META,
        external_id=resp["id"],
        name=body.name,
        title=body.title,
        body=body.message,
        media_type="image" if body.image_hash else "link",
        raw={"object_story_spec": spec},
    )
    db.add(creative)
    db.commit()
    db.refresh(creative)
    return creative


@router.get("/ad-accounts/{account_id}/creatives", response_model=List[CreativeOut])
def list_creatives(
    account_id: str,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    account = scope.get_or_404(db, AdAccount, account_id)
    stmt = (
        select(Creative)
        .where(
            Creative.client_id == account.client_id,
            Creative.platform == account.platform,
        )
        .order_by(Creative.created_at.desc())
    )
    return db.execute(stmt).scalars().all()


@router.get("/meta/preview-formats")
def preview_formats(user: User = Depends(require_team)):
    return meta_api.META_PREVIEW_FORMATS


@router.get("/creatives/{creative_id}/previews")
def creative_previews(
    creative_id: str,
    ad_format: str = "MOBILE_FEED_STANDARD",
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Meta renders the creative in the real placement template and returns
    iframe HTML — this is what makes previews placement-accurate."""
    creative = scope.get_or_404(db, Creative, creative_id)
    if creative.platform != PLATFORM_META:
        raise HTTPException(400, "Previews via this endpoint are Meta-only")
    conn = db.execute(
        select(PlatformConnection).where(
            PlatformConnection.client_id == creative.client_id,
            PlatformConnection.platform == PLATFORM_META,
        )
    ).scalar_one_or_none()
    if conn is None or conn.status != "active":
        raise HTTPException(409, "Meta connection is not active")
    try:
        return meta_api.fetch_previews(
            conn_svc.get_access_token(conn), creative.external_id, ad_format
        )
    except meta_api.MetaAuthError as e:
        conn_svc.mark_disconnected(db, conn, str(e))
        raise HTTPException(502, "Meta rejected the stored credentials")


# --- Google-only management surface ---


@router.get("/ad-groups/{ad_group_id}/keywords", response_model=List[KeywordOut])
def list_keywords(
    ad_group_id: str,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    ad_group = scope.get_or_404(db, AdGroup, ad_group_id)
    campaign = db.get(Campaign, ad_group.campaign_id)
    account = db.get(AdAccount, campaign.ad_account_id)
    conn = _google_account(db, scope, account)
    try:
        return google_ads_api.fetch_keywords(
            conn_svc.get_refresh_token(conn), account.external_id, ad_group.external_id
        )
    except google_ads_api.GoogleAuthError as e:
        conn_svc.mark_disconnected(db, conn, str(e))
        raise HTTPException(502, "Google rejected the stored credentials")


@router.get(
    "/campaigns/{campaign_id}/negative-keywords", response_model=List[KeywordOut]
)
def list_campaign_negatives(
    campaign_id: str,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    campaign = scope.get_or_404(db, Campaign, campaign_id)
    account = db.get(AdAccount, campaign.ad_account_id)
    conn = _google_account(db, scope, account)
    try:
        rows = google_ads_api.fetch_campaign_negative_keywords(
            conn_svc.get_refresh_token(conn), account.external_id, campaign.external_id
        )
    except google_ads_api.GoogleAuthError as e:
        conn_svc.mark_disconnected(db, conn, str(e))
        raise HTTPException(502, "Google rejected the stored credentials")
    return [{**r, "status": None} for r in rows]


@router.get("/campaigns/{campaign_id}/search-terms", response_model=List[SearchTermOut])
def search_terms(
    campaign_id: str,
    days: int = 30,
    ad_group_id: Optional[str] = None,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    campaign = scope.get_or_404(db, Campaign, campaign_id)
    account = db.get(AdAccount, campaign.ad_account_id)
    conn = _google_account(db, scope, account)
    ad_group_external = None
    if ad_group_id:
        ad_group = scope.get_or_404(db, AdGroup, ad_group_id)
        ad_group_external = ad_group.external_id
    try:
        return google_ads_api.fetch_search_terms(
            conn_svc.get_refresh_token(conn),
            account.external_id,
            campaign_external_id=campaign.external_id,
            ad_group_external_id=ad_group_external,
            days=days,
        )
    except google_ads_api.GoogleAuthError as e:
        conn_svc.mark_disconnected(db, conn, str(e))
        raise HTTPException(502, "Google rejected the stored credentials")


@router.get("/campaigns/{campaign_id}/asset-groups", response_model=List[AssetGroupOut])
def asset_groups(
    campaign_id: str,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    campaign = scope.get_or_404(db, Campaign, campaign_id)
    account = db.get(AdAccount, campaign.ad_account_id)
    conn = _google_account(db, scope, account)
    try:
        return google_ads_api.fetch_asset_groups(
            conn_svc.get_refresh_token(conn), account.external_id, campaign.external_id
        )
    except google_ads_api.GoogleAuthError as e:
        conn_svc.mark_disconnected(db, conn, str(e))
        raise HTTPException(502, "Google rejected the stored credentials")
