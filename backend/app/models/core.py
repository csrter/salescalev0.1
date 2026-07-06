import datetime as dt
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .base import created_at_column, id_column

# Roles: the platform is single-agency (Atlas Reach), so role is a simple
# enum on the user rather than a membership table.
ROLE_TEAM = "team"
ROLE_CLIENT = "client"

PLATFORM_META = "meta"
PLATFORM_GOOGLE = "google"

CONN_ACTIVE = "active"
CONN_DISCONNECTED = "disconnected"  # client revoked or token invalid
CONN_ERROR = "error"


class Agency(Base):
    __tablename__ = "agencies"

    id: Mapped[str] = id_column()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[str] = id_column()
    agency_id: Mapped[str] = mapped_column(ForeignKey("agencies.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    # Atlas Reach-internal — must never be serialized to client-role users.
    internal_notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = created_at_column()

    connections: Mapped[list] = relationship(
        "PlatformConnection", back_populates="client"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = id_column()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(200), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # team | client
    # Required when role == client; identifies the one tenant they can see.
    client_id: Mapped[Optional[str]] = mapped_column(ForeignKey("clients.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class PlatformConnection(Base):
    __tablename__ = "platform_connections"
    __table_args__ = (
        UniqueConstraint("client_id", "platform", name="uq_connection_client_platform"),
    )

    id: Mapped[str] = id_column()
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
