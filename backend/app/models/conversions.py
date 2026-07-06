import datetime as dt
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import created_at_column, id_column

# Phase 5: server-side conversion tracking. One logical conversion (a lead
# submitting) fans out to every platform the client has configured — the
# ConversionEvent is the platform-agnostic record, ConversionDispatch is the
# per-platform send log, and ConversionConfig is the per-client destination
# (dataset/pixel on Meta, conversion action on Google). Per-client, never
# hardcoded: two clients of the same Organization send to different pixels.

DISPATCH_SENT = "sent"
DISPATCH_FAILED = "failed"
DISPATCH_SKIPPED = "skipped"  # e.g. no click ID + no identifiers to match on


class ConversionConfig(Base):
    __tablename__ = "conversion_configs"
    __table_args__ = (
        UniqueConstraint(
            "client_id", "platform", name="uq_conversion_config_client_platform"
        ),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Platform-specific destination settings (validated per-platform in the
    # API layer):
    #   meta:   {"dataset_id": ..., "event_name": "Lead",
    #            "test_event_code": optional}
    #   google: {"customer_id": ..., "conversion_action_id": ...,
    #            "ad_user_data_consent": "GRANTED"}
    settings: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class ConversionEvent(Base):
    """The platform-agnostic conversion: one row per lead-level event.

    `event_id` is the cross-channel deduplication key — the browser pixel
    fires the same event with the same id, and Meta dedupes on
    (event_name, event_id) within its 48h window. Click IDs are NOT stored
    here; they live on the linked landing event (single capture layer,
    Phase 1 rule).
    """

    __tablename__ = "conversion_events"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    contact_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("contacts.id"), index=True
    )
    landing_event_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("landing_events.id")
    )
    event_name: Mapped[str] = mapped_column(String(100), nullable=False)
    event_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    event_source_url: Mapped[Optional[str]] = mapped_column(String(2000))
    value_cents: Mapped[Optional[int]] = mapped_column(BigInteger)
    currency: Mapped[Optional[str]] = mapped_column(String(10))
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[dt.datetime] = created_at_column()


class ConversionDispatch(Base):
    """One send attempt per (conversion event, platform) — the audit trail
    for what actually went out. `match_keys` records which identifiers were
    included (["em", "ph", "fbc"], never the values) so match-quality
    problems are diagnosable without logging PII."""

    __tablename__ = "conversion_dispatches"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    conversion_event_id: Mapped[str] = mapped_column(
        ForeignKey("conversion_events.id"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # sent | failed | skipped
    match_keys: Mapped[Optional[list]] = mapped_column(JSON)
    detail: Mapped[Optional[str]] = mapped_column(Text)  # error / skip reason
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempted_at: Mapped[dt.datetime] = created_at_column()
