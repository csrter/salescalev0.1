"""Ad-account discovery, attachment, and reassignment — shared by the OAuth
connect callbacks and the account-picker endpoints (api/connect_accounts).

The problem this separates: an agency login routinely sees MANY ad accounts —
a Google manager (MCC) exposes its whole client roster, and a Meta Business
Manager login likewise. Auto-attaching everything the token can see to the one
client being connected mixes every client's spend into a single profile. So
discovery (what the token can see, live) is decoupled from attachment (which
client each account belongs to): the callback auto-attaches only when the
choice is unambiguous — exactly one new account — and otherwise the Admin
assigns accounts explicitly through the picker.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models.ads import Ad, AdGroup, Campaign, InsightDaily, QualitySnapshot
from ..models.audit import CHANGE_PENDING, AUDIT_SUCCESS, AuditLogEntry, PendingChange
from ..models.core import (
    AdAccount,
    Client,
    PLATFORM_GOOGLE,
    PLATFORM_META,
    PlatformConnection,
    User,
)
from . import connections as conn_svc
from . import google_ads_api, meta_api

# SQLite's default parameter cap is ~999; chunk IN-lists well below it.
_IN_CHUNK = 500


def _chunks(values: List[str]) -> List[List[str]]:
    return [values[i : i + _IN_CHUNK] for i in range(0, len(values), _IN_CHUNK)]


def discover(db: Session, conn: PlatformConnection) -> List[Dict[str, Any]]:
    """Live listing of every ad account this connection's token can reach,
    normalized to one shape across platforms. Raises the platform's *AuthError
    when the token is dead (callers mark the connection disconnected) and
    *ApiError for other upstream failures. Never persisted — the AdAccount
    table only gains rows through an explicit attach."""
    if conn.platform == PLATFORM_META:
        token = conn_svc.get_access_token(conn)
        return [
            {
                "external_id": acct["id"],
                "name": acct.get("name") or acct["id"],
                "currency": acct.get("currency"),
                "timezone": acct.get("timezone_name"),
                "status": str(acct.get("account_status")),
            }
            for acct in meta_api.fetch_ad_accounts(token)
        ]
    if conn.platform == PLATFORM_GOOGLE:
        refresh_token = conn_svc.get_refresh_token(conn)
        # Accounts shared directly with this login, plus — for any manager
        # (MCC) — the enabled ad accounts under it. One inaccessible account
        # (deactivated / not enabled / no permission) never aborts the rest.
        discovered: List[Dict[str, Any]] = []
        for cid in google_ads_api.list_accessible_customers(refresh_token):
            try:
                details = google_ads_api.fetch_customer_details(refresh_token, cid)
            except (google_ads_api.GoogleAuthError, google_ads_api.GoogleApiError):
                continue
            if details.get("is_manager"):
                try:
                    discovered.extend(
                        google_ads_api.list_manager_child_accounts(refresh_token, cid)
                    )
                except (google_ads_api.GoogleAuthError, google_ads_api.GoogleApiError):
                    continue
            else:
                discovered.append(details)
        seen: set[str] = set()
        out: List[Dict[str, Any]] = []
        for details in discovered:
            if details["external_id"] in seen:
                continue  # reachable both directly and under its manager
            seen.add(details["external_id"])
            out.append(details)
        return out
    raise ValueError(f"No account discovery for platform {conn.platform!r}")


def annotate_attachment(
    db: Session,
    organization_id: str,
    platform: str,
    discovered: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Mark each discovered account with where it's already attached.
    Same-org attachments are named (the picker shows "with <client>" and can
    move them); another Organization's attachment renders the account
    unavailable, without naming that tenant."""
    if not discovered:
        return []
    existing: Dict[str, AdAccount] = {}
    ext_ids = [d["external_id"] for d in discovered]
    for chunk in _chunks(ext_ids):
        for acct in (
            db.execute(
                select(AdAccount).where(
                    AdAccount.platform == platform,
                    AdAccount.external_id.in_(chunk),
                )
            )
            .scalars()
            .all()
        ):
            existing[acct.external_id] = acct
    client_names = {
        c.id: c.name
        for c in db.execute(
            select(Client).where(Client.organization_id == organization_id)
        ).scalars()
    }
    return [
        {**d, **_attachment_fields(existing.get(d["external_id"]), organization_id, client_names)}
        for d in discovered
    ]


def _attachment_fields(
    acct: Optional[AdAccount], organization_id: str, client_names: Dict[str, str]
) -> Dict[str, Any]:
    if acct is None:
        return {"available": True, "attached": None}
    if acct.organization_id != organization_id:
        # Claimed by another tenant — unavailable, and never named.
        return {"available": False, "attached": None}
    return {
        "available": True,
        "attached": {
            "account_id": acct.id,
            "client_id": acct.client_id,
            "client_name": client_names.get(acct.client_id, "another client"),
        },
    }


def _existing(db: Session, platform: str, external_id: str) -> Optional[AdAccount]:
    return db.execute(
        select(AdAccount).where(
            AdAccount.platform == platform, AdAccount.external_id == external_id
        )
    ).scalar_one_or_none()


def attach(
    db: Session,
    organization_id: str,
    client_id: str,
    conn: PlatformConnection,
    details: Dict[str, Any],
) -> Optional[AdAccount]:
    """Attach one discovered account to a client. Returns the new AdAccount,
    or None when it's already attached in this org (idempotent — never steals
    an account from another client; that's an explicit reassign). Raises
    PermissionError when another Organization holds it."""
    existing = _existing(db, conn.platform, details["external_id"])
    if existing is not None:
        if existing.organization_id != organization_id:
            raise PermissionError(
                f"{conn.platform} ad account {details['external_id']} is already "
                "connected elsewhere"
            )
        return None
    acct = AdAccount(
        organization_id=organization_id,
        client_id=client_id,
        connection_id=conn.id,
        platform=conn.platform,
        external_id=details["external_id"],
        name=details["name"],
        currency=details.get("currency"),
        timezone=details.get("timezone"),
        status=details.get("status"),
    )
    db.add(acct)
    return acct


@dataclass
class AutoAttachOutcome:
    attached: List[AdAccount] = field(default_factory=list)
    # More than one unattached account was discoverable — the callback can't
    # know which client each belongs to, so the Admin picks in the UI.
    needs_selection: bool = False


def auto_attach(
    db: Session,
    organization_id: str,
    client_id: str,
    conn: PlatformConnection,
    discovered: List[Dict[str, Any]],
) -> AutoAttachOutcome:
    """Post-OAuth attachment policy: attach only when unambiguous.

    - exactly one NEW account visible → attach it to the connecting client
      (the solo-advertiser case keeps its one-click flow);
    - several new accounts (MCC / Business Manager roster) → attach none,
      flag needs_selection;
    - accounts already attached in this org stay where they are;
    - accounts held by another Organization are skipped entirely.
    """
    new_details = []
    for d in discovered:
        existing = _existing(db, conn.platform, d["external_id"])
        if existing is None:
            new_details.append(d)
    outcome = AutoAttachOutcome()
    if len(new_details) == 1:
        acct = attach(db, organization_id, client_id, conn, new_details[0])
        if acct is not None:
            outcome.attached.append(acct)
    elif len(new_details) > 1:
        outcome.needs_selection = True
    return outcome


def reassign(db: Session, account: AdAccount, new_client_id: str) -> Dict[str, int]:
    """Move an ad account to another client in the same Organization,
    carrying the denormalized client_id on everything under it: cached
    campaigns/ad groups/ads, still-pending changes, insight history, and
    quality snapshots. Insights and snapshots have no account FK — they're
    matched through the hierarchy's external ids (asset-group snapshots are
    matched best-effort via a live asset-group listing when the connection is
    usable, since asset groups aren't cached locally)."""
    old_client_id = account.client_id
    counts: Dict[str, int] = {}

    campaigns = (
        db.execute(select(Campaign).where(Campaign.ad_account_id == account.id))
        .scalars()
        .all()
    )
    camp_ids = [c.id for c in campaigns]
    camp_exts = [c.external_id for c in campaigns]
    ag_ids: List[str] = []
    ag_exts: List[str] = []
    if camp_ids:
        for chunk in _chunks(camp_ids):
            for ag in (
                db.execute(select(AdGroup).where(AdGroup.campaign_id.in_(chunk)))
                .scalars()
                .all()
            ):
                ag_ids.append(ag.id)
                ag_exts.append(ag.external_id)
    ad_exts: List[str] = []
    if ag_ids:
        for chunk in _chunks(ag_ids):
            ad_exts.extend(
                db.execute(
                    select(Ad.external_id).where(Ad.ad_group_id.in_(chunk))
                ).scalars()
            )

    def _move(model, id_col, ids: List[str], key: str) -> None:
        moved = 0
        for chunk in _chunks(ids):
            res = db.execute(
                update(model)
                .where(id_col.in_(chunk))
                .values(client_id=new_client_id)
            )
            moved += res.rowcount or 0
        counts[key] = moved

    _move(Campaign, Campaign.id, camp_ids, "campaigns")
    if camp_ids:
        for chunk in _chunks(camp_ids):
            db.execute(
                update(AdGroup)
                .where(AdGroup.campaign_id.in_(chunk))
                .values(client_id=new_client_id)
            )
        counts["ad_groups"] = len(ag_ids)
    if ag_ids:
        for chunk in _chunks(ag_ids):
            db.execute(
                update(Ad)
                .where(Ad.ad_group_id.in_(chunk))
                .values(client_id=new_client_id)
            )
        counts["ads"] = len(ad_exts)

    # Changes still awaiting confirmation follow the account; executed/failed
    # ones are history and stay put (like the audit log — never rewritten).
    db.execute(
        update(PendingChange)
        .where(
            PendingChange.ad_account_id == account.id,
            PendingChange.status == CHANGE_PENDING,
        )
        .values(client_id=new_client_id)
    )

    insight_exts = [account.external_id] + camp_exts + ag_exts + ad_exts
    moved_insights = 0
    for chunk in _chunks(insight_exts):
        res = db.execute(
            update(InsightDaily)
            .where(
                InsightDaily.organization_id == account.organization_id,
                InsightDaily.platform == account.platform,
                InsightDaily.entity_external_id.in_(chunk),
            )
            .values(client_id=new_client_id)
        )
        moved_insights += res.rowcount or 0
    counts["insights"] = moved_insights

    counts["quality_snapshots"] = _move_quality_snapshots(
        db, account, new_client_id, old_client_id, ag_exts, ad_exts, camp_exts
    )

    account.client_id = new_client_id
    return counts


def _move_quality_snapshots(
    db: Session,
    account: AdAccount,
    new_client_id: str,
    old_client_id: str,
    ag_exts: List[str],
    ad_exts: List[str],
    camp_exts: List[str],
) -> int:
    moved = 0
    # Ad-strength rows for ads use the ad's external id directly.
    for chunk in _chunks(ad_exts):
        res = db.execute(
            update(QualitySnapshot)
            .where(
                QualitySnapshot.organization_id == account.organization_id,
                QualitySnapshot.platform == account.platform,
                QualitySnapshot.entity_type == "ad",
                QualitySnapshot.entity_external_id.in_(chunk),
            )
            .values(client_id=new_client_id)
        )
        moved += res.rowcount or 0

    # Keyword rows are keyed "<ad_group_external_id>~<criterion_id>" — match
    # on the ad-group prefix.
    if ag_exts:
        ag_ext_set = set(ag_exts)
        kw_ids = [
            row_id
            for row_id, ext in db.execute(
                select(QualitySnapshot.id, QualitySnapshot.entity_external_id).where(
                    QualitySnapshot.organization_id == account.organization_id,
                    QualitySnapshot.platform == account.platform,
                    QualitySnapshot.entity_type == "keyword",
                    QualitySnapshot.client_id == old_client_id,
                )
            )
            if ext.split("~", 1)[0] in ag_ext_set
        ]
        for chunk in _chunks(kw_ids):
            res = db.execute(
                update(QualitySnapshot)
                .where(QualitySnapshot.id.in_(chunk))
                .values(client_id=new_client_id)
            )
            moved += res.rowcount or 0

    # Asset groups (PMax) aren't cached locally; resolve their ids live when
    # the connection still works. Best-effort — a dead token or API hiccup
    # leaves those rows for the next sync to restate, never fails the move.
    if account.platform == PLATFORM_GOOGLE and camp_exts:
        conn = db.get(PlatformConnection, account.connection_id)
        if conn is not None and conn.status == "active":
            asset_group_ids: List[str] = []
            try:
                refresh_token = conn_svc.get_refresh_token(conn)
                for camp_ext in camp_exts:
                    asset_group_ids.extend(
                        g["external_id"]
                        for g in google_ads_api.fetch_asset_groups(
                            refresh_token, account.external_id, camp_ext
                        )
                    )
            except Exception:  # noqa: BLE001 — best-effort by design
                asset_group_ids = []
            for chunk in _chunks(asset_group_ids):
                res = db.execute(
                    update(QualitySnapshot)
                    .where(
                        QualitySnapshot.organization_id == account.organization_id,
                        QualitySnapshot.platform == account.platform,
                        QualitySnapshot.entity_type == "asset_group",
                        QualitySnapshot.entity_external_id.in_(chunk),
                    )
                    .values(client_id=new_client_id)
                )
                moved += res.rowcount or 0
    return moved


def write_account_audit(
    db: Session,
    user: User,
    account: AdAccount,
    action: str,
    before_client: Optional[str],
    after_client: str,
) -> None:
    """Attach/reassign trail in the standard per-action audit pattern
    (guardrail 8): actor, target account, org, timestamp."""
    db.add(
        AuditLogEntry(
            organization_id=account.organization_id,
            client_id=after_client,
            user_id=user.id,
            user_email=user.email,
            user_name=user.full_name,
            platform=account.platform,
            ad_account_external_id=account.external_id,
            entity_type="ad_account",
            entity_external_id=account.external_id,
            entity_name=account.name,
            action=action,  # attach | reassign
            diff=[{"field": "client_id", "before": before_client, "after": after_client}],
            status=AUDIT_SUCCESS,
        )
    )
