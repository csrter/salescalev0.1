import secrets
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope, require_admin
from ..models.core import Client, Organization, PlatformConnection, User
from ..models.crm import LeadFormConfig
from ..pagination import Page, paginator
from ..services import entitlements, external_sync, lead_notify, sms_consent
from ..schemas import (
    ClientCreate,
    ClientLeadNotificationsIn,
    ClientTimezoneIn,
    ClientOutPublic,
    ClientOutTeam,
    ConnectionOut,
    ExternalSyncConfigIn,
    GuaranteeConfigIn,
    LeadFormEnabledIn,
    LeadFormConfigIn,
    LeadFormConfigOut,
)
from ..services.metrics import GUARANTEE_METRICS

router = APIRouter(prefix="/api/clients", tags=["clients"])


def _serialize_client(client: Client, scope: TenantScope):
    # Role decides the schema: internal fields only exist in the team shape.
    if scope.is_team:
        return ClientOutTeam.model_validate(client)
    return ClientOutPublic.model_validate(client)


@router.get("")
def list_clients(
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
    page: Page = paginator(default=100, maximum=500),
):
    stmt = (
        select(Client)
        .where(
            Client.organization_id == scope.organization_id,
            # The house client (agency's own prospect pipeline) is not a real
            # client — keep it out of the roster; fetch-by-id still works.
            Client.is_house.is_(False),
        )
        .order_by(Client.created_at)
        .limit(page.limit)
        .offset(page.offset)
    )
    if not scope.is_team:
        stmt = stmt.where(Client.id == scope.client_id)
    clients = db.execute(stmt).scalars().all()
    return [_serialize_client(c, scope) for c in clients]


def _get_client_or_404(db: Session, scope: TenantScope, client_id: str) -> Client:
    # Client's own tenant keys are organization_id + its own id, so it can't
    # go through scope.get_or_404 (which reads obj.client_id).
    scope.check_client_id(client_id)
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(404, "Not found")
    scope.check_organization_id(client.organization_id)
    return client


@router.get("/{client_id}")
def get_client(
    client_id: str,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    client = _get_client_or_404(db, scope, client_id)
    return _serialize_client(client, scope)


@router.post("", response_model=ClientOutTeam, status_code=201)
def create_client(
    body: ClientCreate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    org = db.get(Organization, user.organization_id)
    entitlements.enforce_can_add_client(db, org)
    client = Client(
        organization_id=user.organization_id,
        name=body.name,
        internal_notes=body.internal_notes,
    )
    db.add(client)
    db.commit()
    return ClientOutTeam.model_validate(client)


# Guarantee terms are client management (Organization-configured tenant
# data), not day-to-day campaign work — hence admin, not member.
@router.put("/{client_id}/guarantee")
def set_guarantee(
    client_id: str,
    body: GuaranteeConfigIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    if body.metric not in GUARANTEE_METRICS:
        raise HTTPException(
            400, f"metric must be one of {', '.join(sorted(GUARANTEE_METRICS))}"
        )
    config = body.model_dump()
    if config["start_date"] is not None:
        config["start_date"] = config["start_date"].isoformat()
    # Reassign (not mutate) so SQLAlchemy sees the JSON column change.
    client.metric_settings = {
        **(client.metric_settings or {}),
        "guarantee": config,
    }
    db.commit()
    return {"guarantee": config}


@router.delete("/{client_id}/guarantee", status_code=204)
def clear_guarantee(
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    settings = dict(client.metric_settings or {})
    settings.pop("guarantee", None)
    client.metric_settings = settings
    db.commit()


@router.get("/{client_id}/guarantee")
def get_guarantee(
    client_id: str,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """The configured terms (not progress — that's /api/metrics/guarantee).
    Client-role readable: a client can see the guarantee they were sold."""
    client = _get_client_or_404(db, scope, client_id)
    return {"guarantee": (client.metric_settings or {}).get("guarantee")}


# --- Phase 6: native lead-form routing (admin — client setup, like the
# conversion configs) ---


@router.get("/{client_id}/lead-forms", response_model=List[LeadFormConfigOut])
def list_lead_form_configs(
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    return (
        db.execute(select(LeadFormConfig).where(LeadFormConfig.client_id == client.id))
        .scalars()
        .all()
    )


@router.put("/{client_id}/lead-forms/{platform}", response_model=LeadFormConfigOut)
def set_lead_form_config(
    client_id: str,
    platform: str,
    body: LeadFormConfigIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if platform not in ("meta", "google"):
        raise HTTPException(400, "platform must be meta or google")
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    # The (platform, external_key) pair routes public webhooks, so a key
    # already claimed by any client (any tenant) can't be claimed again.
    clash = db.execute(
        select(LeadFormConfig).where(
            LeadFormConfig.platform == platform,
            LeadFormConfig.external_key == body.external_key,
            LeadFormConfig.client_id != client.id,
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(409, "This key is already routed to another client")
    config = db.execute(
        select(LeadFormConfig).where(
            LeadFormConfig.client_id == client.id,
            LeadFormConfig.platform == platform,
        )
    ).scalar_one_or_none()
    if config is None:
        config = LeadFormConfig(
            organization_id=client.organization_id,
            client_id=client.id,
            platform=platform,
            external_key=body.external_key,
            enabled=body.enabled,
        )
        db.add(config)
    else:
        config.external_key = body.external_key
        config.enabled = body.enabled
    db.commit()
    return config


@router.post(
    "/{client_id}/lead-forms/landing-page/rotate", response_model=LeadFormConfigOut
)
def rotate_landing_page_webhook(
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Generic landing-page-form webhook (any form tool that can POST to a
    URL — Webflow, WPForms, Zapier, a plain HTML form). Unlike meta/google,
    there is no external platform dictating the key, so the secret is
    generated server-side (never client-supplied) and folded into the URL
    path — the model most third-party form tools support out of the box.
    Calling this again rotates the key, invalidating the old URL."""
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    config = db.execute(
        select(LeadFormConfig).where(
            LeadFormConfig.client_id == client.id,
            LeadFormConfig.platform == "landing_page",
        )
    ).scalar_one_or_none()
    key = secrets.token_urlsafe(24)
    if config is None:
        config = LeadFormConfig(
            organization_id=client.organization_id,
            client_id=client.id,
            platform="landing_page",
            external_key=key,
            enabled=True,
        )
        db.add(config)
    else:
        config.external_key = key
        config.enabled = True
    db.commit()
    return config


@router.patch(
    "/{client_id}/lead-forms/landing-page", response_model=LeadFormConfigOut
)
def set_landing_page_webhook_enabled(
    client_id: str,
    body: LeadFormEnabledIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    config = db.execute(
        select(LeadFormConfig).where(
            LeadFormConfig.client_id == client.id,
            LeadFormConfig.platform == "landing_page",
        )
    ).scalar_one_or_none()
    if config is None:
        raise HTTPException(404, "No landing-page webhook configured yet")
    config.enabled = body.enabled
    db.commit()
    return config


# --- Phase 6: optional external CRM sync (admin, opt-in per client) ---


@router.get("/{client_id}/external-sync")
def get_external_sync(
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    config = (client.metric_settings or {}).get("external_sync")
    if not config:
        return {"configured": False}
    # Never echo the shared secret back out.
    return {
        "configured": True,
        "enabled": bool(config.get("enabled")),
        "url": config.get("url"),
    }


@router.put("/{client_id}/external-sync")
def set_external_sync(
    client_id: str,
    body: ExternalSyncConfigIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    # SSRF guard: only a public https target is allowed (no internal/metadata
    # hosts), rejected at save time.
    try:
        external_sync.validate_external_url(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    data = body.model_dump()
    data["secret"] = external_sync.encrypt_config_secret(data["secret"])  # at rest
    client.metric_settings = {
        **(client.metric_settings or {}),
        "external_sync": data,
    }
    db.commit()
    return {"configured": True, "enabled": body.enabled, "url": body.url}


@router.delete("/{client_id}/external-sync", status_code=204)
def clear_external_sync(
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    settings = dict(client.metric_settings or {})
    settings.pop("external_sync", None)
    client.metric_settings = settings
    db.commit()


_MAX_CLIENT_NOTIFICATION_PHONES = 10


# --- per-client lead SMS notifications (admin, opt-in) — the client's own
# contacts (e.g. the business owner), alongside the org-wide ops numbers set
# in Settings; see services/lead_notify.py for how the two combine. ---


@router.get("/{client_id}/lead-notifications")
def get_client_lead_notifications(
    client_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    org = db.get(Organization, user.organization_id)
    config = (client.metric_settings or {}).get("lead_notifications") or {}
    return {
        "enabled": bool(config.get("enabled")),
        "phones": config.get("phones") or [],
        # This client's own template (null → falls back to default_template),
        # and the fallback itself: the org-wide template, or the built-in.
        "message_template": config.get("template"),
        "default_template": org.lead_notification_template or lead_notify.DEFAULT_TEMPLATE,
    }


@router.put("/{client_id}/lead-notifications")
def set_client_lead_notifications(
    client_id: str,
    body: ClientLeadNotificationsIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    phones: List[str] = []
    for raw in body.phones:
        normalized = sms_consent.normalize_phone(raw)
        if not normalized:
            raise HTTPException(422, f"Could not parse phone number: {raw!r}")
        if normalized not in phones:
            phones.append(normalized)
    if len(phones) > _MAX_CLIENT_NOTIFICATION_PHONES:
        raise HTTPException(
            400, f"Up to {_MAX_CLIENT_NOTIFICATION_PHONES} notification numbers"
        )
    # Blank/omitted template → None (falls back to the org template, then the
    # built-in default); validated against the same {{token}} set as the org.
    template = (body.message_template or "").strip() or None
    if template:
        bad = lead_notify.unknown_tokens(template)
        if bad:
            raise HTTPException(422, f"Unknown token(s): {', '.join(bad)}")
    notifications = {"enabled": body.enabled, "phones": phones}
    if template:
        notifications["template"] = template
    client.metric_settings = {
        **(client.metric_settings or {}),
        "lead_notifications": notifications,
    }
    db.commit()
    org = db.get(Organization, user.organization_id)
    return {
        "enabled": body.enabled,
        "phones": phones,
        "message_template": template,
        "default_template": org.lead_notification_template or lead_notify.DEFAULT_TEMPLATE,
    }


@router.put("/{client_id}/timezone")
def set_client_timezone(
    client_id: str,
    body: ClientTimezoneIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """This client's default timezone (canonical IANA, validated; None clears
    it → inherits the org default). Campaigns scoped to this client inherit it
    at creation. Does not rewrite existing campaigns."""
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    client.timezone = body.timezone  # normalized by the validator; None clears
    db.commit()
    return {"timezone": client.timezone}


@router.get("/{client_id}/connections", response_model=List[ConnectionOut])
def list_connections(
    client_id: str,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    _get_client_or_404(db, scope, client_id)
    conns = (
        db.execute(
            select(PlatformConnection).where(
                PlatformConnection.client_id == client_id
            )
        )
        .scalars()
        .all()
    )
    return conns
