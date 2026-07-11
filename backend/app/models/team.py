"""Phase 13 — team membership, invites, and the membership audit trail.

Multi-org membership model: OrganizationMembership is the source of truth
for which Organizations a user belongs to and with what role. The User row's
organization_id/role remain as the *active* membership mirror — every
existing TenantScope/role check reads them — and are kept in sync whenever a
membership is created, changed, or the user switches Organizations. Client
portal users (role=client) stay single-org on the User row and get no
membership rows; unifying them here would fork the existing client concept,
not simplify it.
"""
import datetime as dt
import hashlib
import secrets
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import created_at_column, id_column

INVITE_PENDING = "pending"
INVITE_ACCEPTED = "accepted"
INVITE_REVOKED = "revoked"
INVITE_EXPIRED = "expired"

INVITE_TTL_DAYS = 7

# Membership audit actions (Part D). One constant per event so the log stays
# greppable; never log free-form action strings.
AUDIT_INVITE_SENT = "invite_sent"
AUDIT_INVITE_RESENT = "invite_resent"
AUDIT_INVITE_REVOKED = "invite_revoked"
AUDIT_INVITE_ACCEPTED = "invite_accepted"
AUDIT_ROLE_CHANGED = "role_changed"
AUDIT_MEMBER_ADDED = "member_added"
AUDIT_MEMBER_REMOVED = "member_removed"
AUDIT_MEMBER_DEACTIVATED = "member_deactivated"
AUDIT_MEMBER_REACTIVATED = "member_reactivated"
AUDIT_OWNERSHIP_TRANSFERRED = "ownership_transferred"


def new_invite_token() -> tuple[str, str]:
    """A fresh single-use invite token. Returns (raw, hash) — the raw value
    goes in the email link and is never stored; only the hash is persisted."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_invite_token(raw)


def hash_invite_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class OrganizationMembership(Base):
    """One user's seat in one Organization (team roles only)."""

    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # owner|admin|member
    created_at: Mapped[dt.datetime] = created_at_column()


class OrganizationInvite(Base):
    """A pending offer of a seat. Stores only the token hash — possession of
    the raw token (delivered to the invited inbox) is what redeems it, which
    is also why redeeming one may mark the matching account email verified."""

    __tablename__ = "organization_invites"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # admin | member
    invited_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default=INVITE_PENDING, nullable=False
    )
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_by_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    accepted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = created_at_column()


class MembershipAuditEntry(Base):
    """Membership event trail (SOC 2 groundwork): who did what to whom, in
    which Organization, when. Actor identity is denormalized like the ads
    audit_log so the trail survives user changes. Append-only."""

    __tablename__ = "membership_audit_log"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    actor_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_email: Mapped[str] = mapped_column(String(320), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    target_user_id: Mapped[Optional[str]] = mapped_column(String(36))
    target_email: Mapped[Optional[str]] = mapped_column(String(320))
    detail: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = created_at_column()
