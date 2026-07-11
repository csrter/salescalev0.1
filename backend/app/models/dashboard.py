import datetime as dt
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import created_at_column, id_column, utcnow


class CrmListPreference(Base):
    """Per-(user, client view) CRM lead-list column choice — Phase 14, the same
    Phase 4 customizable-UI pattern as DashboardLayout (one JSON blob per user
    per client view, no cross-user reads). `columns` is the ordered list of
    custom-field keys the user has chosen to show as columns; system columns are
    always shown. No row means "show none of the custom columns by default"."""

    __tablename__ = "crm_list_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "client_id", name="uq_crm_cols_user_client"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False)
    columns: Mapped[list] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=utcnow
    )
    created_at: Mapped[dt.datetime] = created_at_column()


class DashboardLayout(Base):
    """One saved widget layout per (user, client view) — Phase 4's
    customizable dashboard. `widgets` is an ordered list of
    {"type": str, "w": int, "h": int} in grid units; the frontend widget
    registry owns what each type renders and what sizes are legal, so the
    backend stores it as bounded opaque JSON rather than duplicating that
    knowledge. No row means the user sees the role default."""

    __tablename__ = "dashboard_layouts"
    __table_args__ = (
        UniqueConstraint("user_id", "client_id", name="uq_layout_user_client"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False)
    widgets: Mapped[list] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=utcnow
    )
    created_at: Mapped[dt.datetime] = created_at_column()
