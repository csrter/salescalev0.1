"""Public, unauthenticated endpoints for the cold-email Outreach module:
the open-tracking pixel and the one-click unsubscribe.

Both are reached from a recipient's mail client, so they carry no session — the
opaque per-message token (secrets.token_urlsafe(24), stored on the EmailMessage
row) is the only credential. Neither endpoint enumerates: an unknown token
returns exactly what a known one does (a 1x1 GIF / the same unsubscribe page),
so a prober learns nothing. Both are rate-limited per IP.

The unsubscribe path writes to the org-scoped suppression ledger, which the
send gateway consults before every send — closing the opt-out loop required by
CLAUDE.md #9.
"""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models.base import utcnow
from ..models.core import Organization
from ..models.crm import Contact
from ..models.email_outreach import SUPPRESS_UNSUBSCRIBED, EmailMessage
from ..ratelimit import rate_limit
from ..services import branding
from ..services import email_outreach_send as gateway
from ..services.email_outreach_sync import hooks

router = APIRouter(prefix="/api/email-outreach", tags=["email-outreach-public"])

# A 1x1 fully transparent GIF (43 bytes).
_PIXEL = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04"
    b"\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x01D\x00;"
)

_open_limit = rate_limit("email_open", limit=600, window_seconds=60)
_unsub_limit = rate_limit("email_unsub", limit=120, window_seconds=60)


def _pixel_response() -> Response:
    return Response(
        content=_PIXEL,
        media_type="image/gif",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, private"},
    )


@router.get("/o/{token}.gif")
def open_pixel(
    token: str, db: Session = Depends(get_db), _: None = _open_limit
):
    """Always returns the tracking pixel. A valid token stamps opened_at on the
    first hit and increments open_count on every hit; an unknown token is served
    the identical GIF (no enumeration)."""
    msg = db.execute(
        select(EmailMessage).where(EmailMessage.open_token == token)
    ).scalar_one_or_none()
    if msg is not None:
        if msg.opened_at is None:
            msg.opened_at = utcnow()
        msg.open_count = (msg.open_count or 0) + 1
        db.commit()
    return _pixel_response()


def _unsubscribe_page(org: Organization | None) -> HTMLResponse:
    product = branding.public_branding(org)["product_name"]
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Unsubscribed</title></head>"
        "<body style='font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;"
        "max-width:32rem;margin:4rem auto;padding:0 1.25rem;color:#1e293b;text-align:center'>"
        "<h1 style='font-size:1.35rem'>You've been unsubscribed</h1>"
        "<p style='color:#475569'>You will no longer receive emails from "
        f"{product} for this address.</p>"
        "</body></html>"
    )
    return HTMLResponse(content=html)


def _apply_unsubscribe(db: Session, token: str) -> None:
    msg = db.execute(
        select(EmailMessage).where(EmailMessage.unsubscribe_token == token)
    ).scalar_one_or_none()
    if msg is None:
        return
    contact = db.get(Contact, msg.contact_id) if msg.contact_id else None
    email_addr = contact.email if contact and contact.email else None
    if not email_addr:
        return
    sup = gateway.suppress(
        db,
        msg.organization_id,
        email_addr,
        SUPPRESS_UNSUBSCRIBED,
        contact_id=contact.id if contact else None,
    )
    db.commit()
    if sup is not None:
        hooks["on_unsubscribe"](db, sup)
        db.commit()


def _org_for_token(db: Session, token: str) -> Organization | None:
    msg = db.execute(
        select(EmailMessage).where(EmailMessage.unsubscribe_token == token)
    ).scalar_one_or_none()
    return db.get(Organization, msg.organization_id) if msg is not None else None


@router.get("/unsubscribe/{token}")
def unsubscribe_get(
    token: str, db: Session = Depends(get_db), _: None = _unsub_limit
):
    """Human-facing unsubscribe (a click from the email footer). Applies the
    opt-out and shows a branded confirmation. Unknown token → the same page."""
    org = _org_for_token(db, token)
    _apply_unsubscribe(db, token)
    return _unsubscribe_page(org)


@router.post("/unsubscribe/{token}")
def unsubscribe_post(
    token: str, db: Session = Depends(get_db), _: None = _unsub_limit
):
    """RFC 8058 List-Unsubscribe=One-Click POST (fired by the mail client, no
    human). Applies the opt-out and returns 200. Unknown token → 200 too."""
    _apply_unsubscribe(db, token)
    return {"status": "unsubscribed"}
