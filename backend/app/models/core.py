import datetime as dt
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .base import created_at_column, id_column

# Roles. Owner/Admin/Member are Organization team roles; Client is the
# read-only portal role for an Organization's client contacts.
#   owner  — everything, including team membership (and billing, Phase 8)
#   admin  — manage clients, platform connections, and team members
#   member — day-to-day campaign work; no client or team management
#   client — read-only visibility into their own client account
ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLE_CLIENT = "client"
TEAM_ROLES = {ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER}
ADMIN_ROLES = {ROLE_OWNER, ROLE_ADMIN}

PLATFORM_META = "meta"
PLATFORM_GOOGLE = "google"

CONN_ACTIVE = "active"
CONN_DISCONNECTED = "disconnected"  # client revoked or token invalid
CONN_ERROR = "error"


class Organization(Base):
    """The root tenant entity. Every other tenant-owned table carries an
    organization_id and must be filtered by it in every query — an unscoped
    query is a cross-tenant data leak, not a style issue."""

    __tablename__ = "organizations"

    id: Mapped[str] = id_column()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    # Free-form vertical label (e.g. "hvac", "dental") — the grouping key for
    # cross-client benchmarking within the same Organization (Phase 3). Set by
    # the Organization; never compared across Organizations.
    vertical: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    # Where lead-quality truth lives during a transition: "salescale" (native,
    # default) or "external" (client's nurture automation still runs in an
    # external CRM — see services/lead_quality.py for the provider interface).
    lead_quality_source: Mapped[str] = mapped_column(
        String(20), default="salescale", nullable=False
    )
    # Per-client metric configuration (JSON): funnel-tier name patterns, UTM
    # convention overrides, external-CRM provider settings. Everything in it
    # has a documented code default — the column exists so per-client
    # variation is data, not code.
    metric_settings: Mapped[Optional[dict]] = mapped_column(JSON)
    # Organization-internal — must never be serialized to client-role users.
    internal_notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = created_at_column()

    connections: Mapped[list] = relationship(
        "PlatformConnection", back_populates="client"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(200), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # owner | admin | member | client
    # Required when role == client; identifies the one client they can see.
    client_id: Mapped[Optional[str]] = mapped_column(ForeignKey("clients.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class PlatformConnection(Base):
    __tablename__ = "platform_connections"
    __table_args__ = (
        UniqueConstraint("client_id", "platform", name="uq_connection_client_platform"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # meta | google
    status: Mapped[str] = mapped_column(String(20), default=CONN_ACTIVE, nullable=False)
    # Fernet-encrypted; never store plaintext (see security.encrypt_secret).
    access_token_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    token_expires_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    scopes: Mapped[Optional[str]] = mapped_column(Text)
    external_user_id: Mapped[Optional[str]] = mapped_column(String(100))
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    connected_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    disconnected_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    client: Mapped["Client"] = relationship("Client", back_populates="connections")


class AdAccount(Base):
    __tablename__ = "ad_accounts"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_ad_account_platform_ext"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("platform_connections.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    currency: Mapped[Optional[str]] = mapped_column(String(10))
    timezone: Mapped[Optional[str]] = mapped_column(String(100))
    status: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[dt.datetime] = created_at_column()
