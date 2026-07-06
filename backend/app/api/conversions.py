"""Phase 5 team-facing surface: per-client conversion destinations, the
dispatch log, Event Match Quality, and test sends.

Config writes are admin-gated (platform destinations are client management,
like guarantee terms); reads and the log are team-wide. Nothing here is
client-role visible — conversion plumbing is Organization-internal ops.
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope, require_admin, require_team
from ..models.base import utcnow
from ..models.conversions import (
    ConversionConfig,
    ConversionDispatch,
    ConversionEvent,
)
from ..models.core import (
    CONN_ACTIVE,
    PLATFORM_GOOGLE,
    PLATFORM_META,
    Client,
    PlatformConnection,
    User,
)
from ..schemas import (
    CONSENT_STATUSES,
    CONVERSION_PLATFORMS,
    ConversionConfigIn,
    ConversionConfigOut,
    ConversionDispatchOut,
    ConversionLogEntryOut,
    TestSendIn,
)
from ..services import connections as conn_svc
from ..services import google_conversions, meta_capi
from ..services.conversion_dispatch import dispatch_conversion

router = APIRouter(tags=["conversions"])


def _team_client_or_404(db: Session, user: User, client_id: str) -> Client:
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(404, "Not found")
    return client


def _validate_settings(platform: str, settings: dict) -> None:
    if platform == PLATFORM_META:
        if not settings.get("dataset_id"):
            raise HTTPException(400, "meta settings require dataset_id")
    elif platform == PLATFORM_GOOGLE:
        if not settings.get("customer_id") or not settings.get(
            "conversion_action_id"
        ):
            raise HTTPException(
                400,
                "google settings require customer_id and conversion_action_id",
            )
        consent = settings.get("ad_user_data_consent", "GRANTED")
        if consent not in CONSENT_STATUSES:
            raise HTTPException(
                400,
                f"ad_user_data_consent must be one of {sorted(CONSENT_STATUSES)}",
            )


@router.get(
    "/api/clients/{client_id}/conversion-configs",
    response_model=List[ConversionConfigOut],
)
def list_conversion_configs(
    client_id: str,
    user: User = Depends(require_team),
    db: Session = Depends(get_db),
):
    _team_client_or_404(db, user, client_id)
    return (
        db.execute(
            select(ConversionConfig).where(
                ConversionConfig.client_id == client_id
            )
        )
        .scalars()
        .all()
    )


@router.put(
    "/api/clients/{client_id}/conversion-configs/{platform}",
    response_model=ConversionConfigOut,
)
def upsert_conversion_config(
    client_id: str,
    platform: str,
    body: ConversionConfigIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    client = _team_client_or_404(db, user, client_id)
    if platform not in CONVERSION_PLATFORMS:
        raise HTTPException(
            400, f"platform must be one of {sorted(CONVERSION_PLATFORMS)}"
        )
    _validate_settings(platform, body.settings)
    config = db.execute(
        select(ConversionConfig).where(
            ConversionConfig.client_id == client_id,
            ConversionConfig.platform == platform,
        )
    ).scalar_one_or_none()
    if config is None:
        config = ConversionConfig(
            organization_id=client.organization_id,
            client_id=client_id,
            platform=platform,
            settings=body.settings,
            enabled=body.enabled,
        )
        db.add(config)
    else:
        config.settings = body.settings
        config.enabled = body.enabled
    db.commit()
    return config


@router.delete(
    "/api/clients/{client_id}/conversion-configs/{platform}", status_code=204
)
def delete_conversion_config(
    client_id: str,
    platform: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _team_client_or_404(db, user, client_id)
    config = db.execute(
        select(ConversionConfig).where(
            ConversionConfig.client_id == client_id,
            ConversionConfig.platform == platform,
        )
    ).scalar_one_or_none()
    if config is not None:
        db.delete(config)
        db.commit()


@router.get("/api/conversions/log", response_model=List[ConversionLogEntryOut])
def conversion_log(
    client_id: str,
    limit: int = 50,
    user: User = Depends(require_team),
    db: Session = Depends(get_db),
):
    _team_client_or_404(db, user, client_id)
    rows = db.execute(
        select(ConversionDispatch, ConversionEvent)
        .join(
            ConversionEvent,
            ConversionDispatch.conversion_event_id == ConversionEvent.id,
        )
        .where(
            ConversionDispatch.organization_id == user.organization_id,
            ConversionDispatch.client_id == client_id,
        )
        .order_by(ConversionDispatch.attempted_at.desc())
        .limit(min(limit, 200))
    ).all()
    return [
        ConversionLogEntryOut(
            dispatch=ConversionDispatchOut.model_validate(dispatch),
            event_name=event.event_name,
            event_id=event.event_id,
            contact_id=event.contact_id,
            occurred_at=event.occurred_at,
        )
        for dispatch, event in rows
    ]


def _active_connection(
    db: Session, client_id: str, platform: str
) -> PlatformConnection:
    conn = db.execute(
        select(PlatformConnection).where(
            PlatformConnection.client_id == client_id,
            PlatformConnection.platform == platform,
            PlatformConnection.status == CONN_ACTIVE,
        )
    ).scalar_one_or_none()
    if conn is None:
        raise HTTPException(400, f"No active {platform} connection")
    return conn


def _config_or_400(db: Session, client_id: str, platform: str) -> ConversionConfig:
    config = db.execute(
        select(ConversionConfig).where(
            ConversionConfig.client_id == client_id,
            ConversionConfig.platform == platform,
        )
    ).scalar_one_or_none()
    if config is None:
        raise HTTPException(400, f"No {platform} conversion config for client")
    return config


@router.get("/api/conversions/emq")
def event_match_quality(
    client_id: str,
    user: User = Depends(require_team),
    db: Session = Depends(get_db),
):
    """Live Event Match Quality for the client's Meta dataset — the score
    Events Manager shows, surfaced per client in the dashboard (Phase 5
    definition of done)."""
    _team_client_or_404(db, user, client_id)
    config = _config_or_400(db, client_id, PLATFORM_META)
    conn = _active_connection(db, client_id, PLATFORM_META)
    token = conn_svc.get_access_token(conn)
    events = meta_capi.fetch_event_match_quality(
        token, str(config.settings["dataset_id"])
    )
    return {"dataset_id": config.settings["dataset_id"], "events": events}


@router.get("/api/conversions/google/actions")
def google_conversion_actions(
    client_id: str,
    customer_id: Optional[str] = None,
    user: User = Depends(require_team),
    db: Session = Depends(get_db),
):
    """Upload-eligible conversion actions for the config UI. customer_id
    falls back to the configured one so the dropdown works pre- and
    post-configuration."""
    _team_client_or_404(db, user, client_id)
    conn = _active_connection(db, client_id, PLATFORM_GOOGLE)
    if customer_id is None:
        config = _config_or_400(db, client_id, PLATFORM_GOOGLE)
        customer_id = str(config.settings["customer_id"])
    refresh_token = conn_svc.get_refresh_token(conn)
    return {
        "customer_id": customer_id,
        "actions": google_conversions.list_conversion_actions(
            refresh_token, customer_id
        ),
    }


@router.get("/api/conversions/google/readiness")
def google_readiness(
    client_id: str,
    user: User = Depends(require_team),
    db: Session = Depends(get_db),
):
    """Enhanced Conversions for Leads prerequisites — both flags must be
    true in the client's Google account before uploads will match."""
    _team_client_or_404(db, user, client_id)
    config = _config_or_400(db, client_id, PLATFORM_GOOGLE)
    conn = _active_connection(db, client_id, PLATFORM_GOOGLE)
    refresh_token = conn_svc.get_refresh_token(conn)
    return google_conversions.check_readiness(
        refresh_token, str(config.settings["customer_id"])
    )


@router.post("/api/conversions/test-send", status_code=201)
def test_send(
    body: TestSendIn,
    user: User = Depends(require_team),
    db: Session = Depends(get_db),
):
    """Send a synthetic conversion to one platform to verify the pipe in
    that platform's own tooling (Meta Test Events via test_event_code;
    Google via the account's upload diagnostics). Flagged is_test in the
    dispatch log; Meta test sends require a test_event_code in the config
    so they can never pollute production event data."""
    client = _team_client_or_404(db, user, body.client_id)
    if body.platform not in CONVERSION_PLATFORMS:
        raise HTTPException(
            400, f"platform must be one of {sorted(CONVERSION_PLATFORMS)}"
        )
    _config_or_400(db, body.client_id, body.platform)

    event = ConversionEvent(
        organization_id=client.organization_id,
        client_id=client.id,
        event_name=body.event_name,
        event_id=f"test-{uuid.uuid4()}",
        occurred_at=utcnow(),
    )
    db.add(event)
    db.commit()
    lead = {
        "email": body.email,
        "phone": body.phone,
        "first_name": body.first_name,
        "last_name": body.last_name,
        "fbc": body.fbc,
        "fbp": body.fbp,
    }
    # Test sends have no landing event; a gclid supplied for the test rides
    # in on a synthetic landing-less path only if the platform needs it.
    if body.gclid:
        from ..models.attribution import LandingEvent

        landing = LandingEvent(
            organization_id=client.organization_id,
            client_id=client.id,
            session_key=f"test-{event.event_id}",
            gclid=body.gclid,
            occurred_at=utcnow(),
        )
        db.add(landing)
        db.flush()
        event.landing_event_id = landing.id
        db.commit()
    results = dispatch_conversion(
        db, event, lead, is_test=True, platforms=[body.platform]
    )
    return {"conversion_event_id": event.id, "results": results}
