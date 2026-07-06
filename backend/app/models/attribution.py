import datetime as dt
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import created_at_column, id_column


class LandingEvent(Base):
    """Platform-agnostic attribution capture — one row per landing.

    This is the single capture layer: UTMs + referrer now, and the same rows
    already carry fbclid/gclid so Phase 5's server-side conversion work
    extends this table rather than adding a second mechanism. `contact_id`
    is set when the visitor later submits a lead form, tying spend-side
    attribution to the Salescale contact.
    """

    __tablename__ = "landing_events"

    id: Mapped[str] = id_column()
    # Derived from the client at capture time — the public endpoint never
    # trusts a caller-supplied organization_id.
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    # Anonymous visitor/session key (cookie value) so multiple pageviews and
    # the eventual lead submission can be joined to the same landing.
    session_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    landing_url: Mapped[Optional[str]] = mapped_column(String(2000))
    utm_source: Mapped[Optional[str]] = mapped_column(String(300))
    utm_medium: Mapped[Optional[str]] = mapped_column(String(300))
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(300))
    utm_content: Mapped[Optional[str]] = mapped_column(String(300))
    utm_term: Mapped[Optional[str]] = mapped_column(String(300))
    referrer: Mapped[Optional[str]] = mapped_column(String(2000))
    # Click IDs captured at the same point (Phase 5 consumes these for CAPI /
    # Enhanced Conversions).
    fbclid: Mapped[Optional[str]] = mapped_column(String(500))
    # Meta browser-id cookie (_fbp), captured alongside fbclid — CAPI matches
    # meaningfully better with both. Do not confuse with fbc (derived from
    # fbclid at send time).
    fbp: Mapped[Optional[str]] = mapped_column(String(500))
    gclid: Mapped[Optional[str]] = mapped_column(String(500))
    user_agent: Mapped[Optional[str]] = mapped_column(String(1000))
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    contact_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("contacts.id"), index=True
    )
    created_at: Mapped[dt.datetime] = created_at_column()
