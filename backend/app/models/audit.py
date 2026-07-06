import datetime as dt
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import created_at_column, id_column

# Two-step write guardrail: every platform mutation is first recorded as a
# PendingChange (with a before/after summary), and only an explicit second
# /execute call performs the platform write. There is no other write path,
# which is what makes "no silent writes to a live ad account" enforceable
# server-side rather than a UI convention.

CHANGE_PENDING = "pending"
CHANGE_EXECUTED = "executed"
CHANGE_FAILED = "failed"
CHANGE_CANCELED = "canceled"

# Pending changes go stale fast — spend context changes. Confirm within this
# window or re-stage the change.
CHANGE_TTL_MINUTES = 30


class PendingChange(Base):
    __tablename__ = "pending_changes"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    ad_account_id: Mapped[str] = mapped_column(
        ForeignKey("ad_accounts.id"), nullable=False
    )
    # campaign | ad_group | ad | keyword | campaign_negative | asset_group
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Local row id for existing entities; None for creates.
    entity_id: Mapped[Optional[str]] = mapped_column(String(36))
    entity_external_id: Mapped[Optional[str]] = mapped_column(String(100))
    entity_name: Mapped[Optional[str]] = mapped_column(String(400))
    # create | update | pause | resume | add | add_negative | remove
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    # The requested field values, exactly as the executor will apply them.
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    # [{"field", "before", "after"}] — what the confirm dialog renders.
    diff: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=CHANGE_PENDING, nullable=False
    )
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    executed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    executed_by_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = created_at_column()


AUDIT_SUCCESS = "success"
AUDIT_FAILED = "failed"


class AuditLogEntry(Base):
    """Who changed what, on which platform, when — written on every execute
    attempt (success or failure) and never updated or deleted afterwards.
    User identity is denormalized so the trail survives user changes."""

    __tablename__ = "audit_log"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    pending_change_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("pending_changes.id")
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_email: Mapped[str] = mapped_column(String(320), nullable=False)
    user_name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    ad_account_external_id: Mapped[Optional[str]] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_external_id: Mapped[Optional[str]] = mapped_column(String(100))
    entity_name: Mapped[Optional[str]] = mapped_column(String(400))
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    diff: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = created_at_column()
