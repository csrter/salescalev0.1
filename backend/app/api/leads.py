"""Public lead-capture endpoint — the submit-side counterpart of
/api/track/landing.

This is where click-ID capture meets the conversion layer (Phase 5 task 4):
the form embed posts the lead here with the visitor's session key; we join
it to the landing event captured earlier for that session (UTMs + fbclid/
fbp/gclid on ONE row), create the CRM contact, and fan the conversion out
server-side to every platform the client has configured. If no landing ping
ever arrived (blocked script, direct form link), the click IDs and UTMs
posted with the submission create the landing row now — capture degrades
gracefully instead of losing attribution entirely.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models.attribution import LandingEvent
from ..models.base import utcnow
from ..models.conversions import ConversionEvent
from ..models.core import Client
from ..schemas import LeadSubmissionIn
from ..services import lead_ingest
from ..services.conversion_dispatch import dispatch_conversion
from ..services.external_sync import push_contact_update

router = APIRouter(tags=["leads"])


@router.post("/api/track/lead", status_code=201)
def capture_lead(
    body: LeadSubmissionIn, request: Request, db: Session = Depends(get_db)
):
    """No auth (embedded on client landing pages) — same trust model as
    /api/track/landing: inserts scoped to a known client, organization_id
    always derived from the client row, and the response reveals nothing
    about configuration beyond a per-platform sent/skipped status."""
    client = db.get(Client, body.client_id)
    if client is None:
        raise HTTPException(404, "Unknown client")

    # The landing event for this visitor session, captured at landing time.
    landing = db.execute(
        select(LandingEvent)
        .where(
            LandingEvent.client_id == client.id,
            LandingEvent.session_key == body.session_key,
        )
        .order_by(LandingEvent.occurred_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if landing is None:
        landing = LandingEvent(
            organization_id=client.organization_id,
            client_id=client.id,
            session_key=body.session_key,
            landing_url=body.event_source_url,
            utm_source=body.utm_source,
            utm_medium=body.utm_medium,
            utm_campaign=body.utm_campaign,
            utm_content=body.utm_content,
            utm_term=body.utm_term,
            fbclid=body.fbclid,
            fbp=body.fbp,
            gclid=body.gclid,
            user_agent=body.user_agent,
            occurred_at=utcnow(),
        )
        db.add(landing)
    else:
        # The submission may carry signals the landing ping didn't have yet
        # (fbp cookie set late, click ID on a later visit) — backfill, never
        # overwrite, so the original capture stays authoritative.
        for attr in ("fbclid", "fbp", "gclid"):
            if getattr(landing, attr) is None and getattr(body, attr):
                setattr(landing, attr, getattr(body, attr))

    # Create-or-update (Phase 6): a returning lead — same email/phone —
    # updates the contact it already is instead of duplicating it. Each
    # submission still gets its own ConversionEvent below (it *is* a new
    # conversion), and the landing link moves to the latest submission.
    contact, created = lead_ingest.upsert_contact(
        db,
        client,
        email=body.email,
        phone=body.phone,
        first_name=body.first_name,
        last_name=body.last_name,
        source="landing_page",
    )
    landing.contact_id = contact.id

    event = ConversionEvent(
        organization_id=client.organization_id,
        client_id=client.id,
        contact_id=contact.id,
        landing_event_id=landing.id,
        event_name=body.event_name,
        event_id=body.event_id or str(uuid.uuid4()),
        event_source_url=body.event_source_url or landing.landing_url,
        value_cents=body.value_cents,
        currency=body.currency,
        occurred_at=utcnow(),
    )
    db.add(event)
    db.commit()

    lead = {
        "email": body.email,
        "phone": body.phone,
        "first_name": body.first_name,
        "last_name": body.last_name,
        "city": body.city,
        "state": body.state,
        "zip": body.zip,
        "country": body.country,
        "fbc": body.fbc,
        "fbp": body.fbp,
        "client_ip_address": request.client.host if request.client else None,
        "client_user_agent": body.user_agent
        or request.headers.get("user-agent"),
    }
    results = dispatch_conversion(db, event, lead)
    if created:
        push_contact_update(db, client, contact, event="lead.created")
    return {
        "contact_id": contact.id,
        "conversion_event_id": event.id,
        "event_id": event.event_id,
        # Public response: platform + status only, no internal error detail.
        "dispatched": [
            {"platform": r["platform"], "status": r["status"]} for r in results
        ],
    }
