import datetime as dt
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import created_at_column, id_column

# Salescale CRM entities. The UI/workflows arrive in Phase 6, but the schema
# ships now so ad-side tables (landing_events, insights) can reference leads
# from day one. Every table carries organization_id (and client_id where the
# entity belongs to one client) for two-level tenant scoping.


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    domain: Mapped[Optional[str]] = mapped_column(String(300))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = created_at_column()


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    company_id: Mapped[Optional[str]] = mapped_column(ForeignKey("companies.id"))
    first_name: Mapped[Optional[str]] = mapped_column(String(150))
    last_name: Mapped[Optional[str]] = mapped_column(String(150))
    email: Mapped[Optional[str]] = mapped_column(String(320), index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    # Where the lead came from: meta_instant_form | google_lead_form |
    # landing_page | manual — attribution details live on the landing event.
    source: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[dt.datetime] = created_at_column()


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class PipelineStage(Base):
    __tablename__ = "pipeline_stages"
    __table_args__ = (
        UniqueConstraint("pipeline_id", "position", name="uq_stage_position"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    pipeline_id: Mapped[str] = mapped_column(ForeignKey("pipelines.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # Marks the stage that counts as "qualified" for the guarantee tracker
    # and lead-quality-adjusted CPL (Phase 3/6).
    is_qualified_stage: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    company_id: Mapped[Optional[str]] = mapped_column(ForeignKey("companies.id"))
    pipeline_id: Mapped[str] = mapped_column(ForeignKey("pipelines.id"), nullable=False)
    stage_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_stages.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    value_cents: Mapped[Optional[int]] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(
        String(20), default="open", nullable=False
    )  # open | won | lost
    created_at: Mapped[dt.datetime] = created_at_column()
    closed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    contact_id: Mapped[Optional[str]] = mapped_column(ForeignKey("contacts.id"))
    deal_id: Mapped[Optional[str]] = mapped_column(ForeignKey("deals.id"))
    type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # call | note | email | sms | meeting
    body: Mapped[Optional[str]] = mapped_column(Text)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_by_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = created_at_column()


class CrmTask(Base):
    __tablename__ = "crm_tasks"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    contact_id: Mapped[Optional[str]] = mapped_column(ForeignKey("contacts.id"))
    deal_id: Mapped[Optional[str]] = mapped_column(ForeignKey("deals.id"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    due_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    assigned_to_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = created_at_column()


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("client_id", "name", name="uq_tag_client_name"),)

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class ContactTag(Base):
    __tablename__ = "contact_tags"
    __table_args__ = (
        UniqueConstraint("contact_id", "tag_id", name="uq_contact_tag"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    tag_id: Mapped[str] = mapped_column(ForeignKey("tags.id"), nullable=False)
