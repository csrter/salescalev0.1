"""Platform-agnostic conversion dispatch.

The adapter seam for Phase 7: a sender is a callable registered in SENDERS
under its platform key, taking a SendContext and returning the match-key
list it sent. New platforms (Snapchat CAPI, Reddit CAPI, LinkedIn CAPI,
Microsoft offline conversions, Nextdoor) add a function and a registry
entry — dispatch_conversion() and everything above it stay untouched.

Per-platform isolation mirrors insights_sync: every send gets its own
try/except and its own ConversionDispatch row; Meta being down never blocks
the Google upload for the same lead. A sender raises SkipSend when there is
nothing usable to match on (recorded as `skipped`, not an error — a lead
from organic traffic has no click ID and that's normal).
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.attribution import LandingEvent
from ..models.conversions import (
    DISPATCH_FAILED,
    DISPATCH_SENT,
    DISPATCH_SKIPPED,
    ConversionConfig,
    ConversionDispatch,
    ConversionEvent,
)
from ..models.core import (
    CONN_ACTIVE,
    PLATFORM_GOOGLE,
    PLATFORM_META,
    PlatformConnection,
)
from . import connections as conn_svc
from . import google_conversions, meta_capi


class SkipSend(Exception):
    """No identifiers/click ID usable for this platform — log and move on."""


@dataclass
class SendContext:
    config: ConversionConfig
    connection: PlatformConnection
    event: ConversionEvent
    landing: Optional[LandingEvent]
    # Raw (unhashed) lead fields: email, phone, first_name, last_name, city,
    # state, zip, country, plus client_ip_address / client_user_agent / fbc /
    # fbp forwarded from the capturing page. Hashing happens inside each
    # sender to its own platform's spec — raw values never leave this process.
    lead: Dict[str, Any] = field(default_factory=dict)
    is_test: bool = False


def _send_meta(ctx: SendContext) -> List[str]:
    settings = ctx.config.settings or {}
    dataset_id = settings.get("dataset_id")
    if not dataset_id:
        raise SkipSend("no dataset_id configured")

    fbc = ctx.lead.get("fbc")
    if not fbc and ctx.landing is not None and ctx.landing.fbclid:
        fbc = meta_capi.format_fbc(ctx.landing.fbclid, ctx.landing.occurred_at)
    fbp = ctx.lead.get("fbp") or (ctx.landing.fbp if ctx.landing else None)

    lead = dict(ctx.lead)
    lead.setdefault("external_id", ctx.event.contact_id)
    user_data, match_keys = meta_capi.build_user_data(lead, fbc=fbc, fbp=fbp)
    if not user_data:
        raise SkipSend("no customer information to match on")

    payload: Dict[str, Any] = {
        "event_name": ctx.event.event_name,
        "event_time": meta_capi.event_time(ctx.event.occurred_at),
        "event_id": ctx.event.event_id,
        "action_source": "website",
        "user_data": user_data,
    }
    if ctx.event.event_source_url:
        payload["event_source_url"] = ctx.event.event_source_url
    if ctx.event.value_cents is not None:
        payload["custom_data"] = {
            "value": ctx.event.value_cents / 100,
            "currency": (ctx.event.currency or "USD").upper(),
        }

    # test_event_code only rides on explicit test sends — Meta's docs are
    # blunt that it must be removed from production payloads.
    test_code = settings.get("test_event_code") if ctx.is_test else None
    if ctx.is_test and not test_code:
        raise SkipSend("test send requested but no test_event_code configured")

    token = conn_svc.get_access_token(ctx.connection)
    meta_capi.send_events(token, dataset_id, [payload], test_event_code=test_code)
    return match_keys


def _send_google(ctx: SendContext) -> List[str]:
    settings = ctx.config.settings or {}
    customer_id = settings.get("customer_id")
    action_id = settings.get("conversion_action_id")
    if not customer_id or not action_id:
        raise SkipSend("no customer_id/conversion_action_id configured")

    gclid = ctx.landing.gclid if ctx.landing else None
    identifiers, match_keys = google_conversions.build_identifiers(ctx.lead)
    if not gclid and not identifiers:
        raise SkipSend("no gclid and no hashable identifiers")
    if gclid:
        match_keys = ["gclid", *match_keys]

    refresh_token = conn_svc.get_refresh_token(ctx.connection)
    google_conversions.upload_click_conversion(
        refresh_token,
        customer_id=str(customer_id),
        conversion_action_id=str(action_id),
        occurred_at=ctx.event.occurred_at,
        gclid=gclid,
        identifiers=identifiers,
        value=(
            ctx.event.value_cents / 100 if ctx.event.value_cents is not None else None
        ),
        currency=ctx.event.currency,
        # order_id doubles as Google's own dedup key for repeated uploads of
        # the same lead — same value the Meta side dedupes on.
        order_id=ctx.event.event_id,
        ad_user_data_consent=settings.get("ad_user_data_consent", "GRANTED"),
    )
    return match_keys


SENDERS: Dict[str, Callable[[SendContext], List[str]]] = {
    PLATFORM_META: _send_meta,
    PLATFORM_GOOGLE: _send_google,
}


def dispatch_conversion(
    db: Session,
    event: ConversionEvent,
    lead: Dict[str, Any],
    is_test: bool = False,
    platforms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Send `event` to every platform the client has an enabled config and
    an active connection for. Returns one result dict per platform and
    writes a ConversionDispatch row for every attempt."""
    landing = (
        db.get(LandingEvent, event.landing_event_id)
        if event.landing_event_id
        else None
    )
    configs = (
        db.execute(
            select(ConversionConfig).where(
                ConversionConfig.client_id == event.client_id,
                ConversionConfig.enabled.is_(True),
            )
        )
        .scalars()
        .all()
    )
    results: List[Dict[str, Any]] = []
    for config in configs:
        if platforms is not None and config.platform not in platforms:
            continue
        sender = SENDERS.get(config.platform)
        if sender is None:
            continue  # config for a platform with no adapter yet
        status, match_keys, detail = DISPATCH_SENT, None, None
        conn = db.execute(
            select(PlatformConnection).where(
                PlatformConnection.client_id == event.client_id,
                PlatformConnection.platform == config.platform,
                PlatformConnection.status == CONN_ACTIVE,
            )
        ).scalar_one_or_none()
        if conn is None:
            status, detail = DISPATCH_SKIPPED, "no active platform connection"
        else:
            try:
                match_keys = sender(
                    SendContext(
                        config=config,
                        connection=conn,
                        event=event,
                        landing=landing,
                        lead=lead,
                        is_test=is_test,
                    )
                )
            except SkipSend as e:
                status, detail = DISPATCH_SKIPPED, str(e)
            except Exception as e:  # isolate per platform, never propagate
                status, detail = DISPATCH_FAILED, str(e)
        db.add(
            ConversionDispatch(
                organization_id=event.organization_id,
                client_id=event.client_id,
                conversion_event_id=event.id,
                platform=config.platform,
                status=status,
                match_keys=match_keys,
                detail=detail,
                is_test=is_test,
            )
        )
        results.append(
            {
                "platform": config.platform,
                "status": status,
                "match_keys": match_keys,
                "detail": detail,
            }
        )
    db.commit()
    return results
