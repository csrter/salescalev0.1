"""Phase 12 Lead Finder & email verification bookkeeping.

Two metering ledgers, same template as AiUsage (models/ai.py): one row per
billable external call, counted per calendar month against the org's tier
limit in services/entitlements.py. They double as the audit/attribution
record — a LeadFinderSearch row is what an imported contact's
source_detail.search_id points back to.

Google Places caching policy note: place IDs are exempt from Google's
caching restrictions and may be stored indefinitely; every other Places
field may NOT be cached server-side. LeadFinderSearch therefore stores only
the query text and result count — never result payloads. Result data is
returned to the client for display and only persisted when the user imports
a business into their CRM (their own data-entry action).
"""

import datetime as dt
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String

from ..db import Base
from .base import created_at_column, id_column
from sqlalchemy.orm import Mapped, mapped_column

# Contact.verification_status values (Phase 12 task 10). `unverified` is the
# birth state; the other four are provider verdicts.
VERIFICATION_STATUSES = ("unverified", "valid", "risky", "invalid", "unknown")


class LeadFinderSearch(Base):
    """One row per Google Places Text Search made on behalf of an
    Organization — the monthly-quota counter and the attribution anchor for
    imported leads."""

    __tablename__ = "lead_finder_searches"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    query: Mapped[str] = mapped_column(String(300), nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(300))
    results_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Google bills each pagination page as a separate Text Search request, so
    # the monthly quota counts pages, not user-visible searches (a 60-result
    # search = 3). Kept on the one ledger row per user action.
    pages_fetched: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    created_at: Mapped[dt.datetime] = created_at_column()


class EmailVerificationRecord(Base):
    """One row per email sent to the verification provider — the monthly
    verification-quota counter and the per-address verdict history."""

    __tablename__ = "email_verifications"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    contact_id: Mapped[Optional[str]] = mapped_column(ForeignKey("contacts.id"))
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    # VERIFICATION_STATUSES minus "unverified" — a record IS a verification.
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()
