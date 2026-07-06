"""Phase 9 outbound email log.

Every email the platform composes is recorded here with the *resolved*
sender identity, so "no client-facing email ever went out Salescale-branded
for a white-labeled Organization" is auditable, not assumed. In dev (no SMTP
configured) this table is also the outbox — services/email.py logs instead
of sending, and tests assert against these rows.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import created_at_column, id_column


class EmailLog(Base):
    __tablename__ = "email_log"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    to_address: Mapped[str] = mapped_column(String(320), nullable=False)
    from_name: Mapped[str] = mapped_column(String(200), nullable=False)
    from_address: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text)
    # True when actually handed to an SMTP transport; False = dev log-only.
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()
