from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope
from ..models.attribution import LandingEvent
from ..models.base import utcnow
from ..models.core import Client
from ..schemas import LandingEventIn, LandingEventOut

router = APIRouter(tags=["attribution"])


@router.post(
    "/api/track/landing", response_model=LandingEventOut, status_code=201
)
def capture_landing_event(body: LandingEventIn, db: Session = Depends(get_db)):
    """Public capture endpoint — embedded on client landing pages, so no
    auth. It only ever inserts an attribution row for a known client; it
    reads nothing back beyond the created row."""
    client = db.get(Client, body.client_id)
    if client is None:
        raise HTTPException(404, "Unknown client")
    event = LandingEvent(
        client_id=body.client_id,
        session_key=body.session_key,
        landing_url=body.landing_url,
        utm_source=body.utm_source,
        utm_medium=body.utm_medium,
        utm_campaign=body.utm_campaign,
        utm_content=body.utm_content,
        utm_term=body.utm_term,
        referrer=body.referrer,
        fbclid=body.fbclid,
        gclid=body.gclid,
        user_agent=body.user_agent,
        occurred_at=utcnow(),
    )
    db.add(event)
    db.commit()
    return event


@router.get(
    "/api/attribution/landing-events", response_model=List[LandingEventOut]
)
def list_landing_events(
    client_id: Optional[str] = None,
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    stmt = select(LandingEvent).order_by(LandingEvent.occurred_at.desc()).limit(500)
    stmt = scope.filter(stmt, LandingEvent)
    if client_id is not None:
        scope.check_client_id(client_id)
        stmt = stmt.where(LandingEvent.client_id == client_id)
    return db.execute(stmt).scalars().all()
