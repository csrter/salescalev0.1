"""Phase 9 AI-insights bookkeeping.

One AiUsage row per Claude API call made on behalf of an Organization.
This is both the usage-cap counter (entitlements.ai_monthly_query_limit is
enforced against a count of these rows) and the cost ledger — actual token
usage per request, priced at the model's published rates, so per-tenant AI
cost is queryable when Phase 8 needs to sanity-check pricing tiers.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String

from ..db import Base
from .base import created_at_column, id_column
from sqlalchemy.orm import Mapped, mapped_column

AI_FEATURES = {"explain", "summary"}


class AiUsage(Base):
    __tablename__ = "ai_usage"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[Optional[str]] = mapped_column(ForeignKey("clients.id"))
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    feature: Mapped[str] = mapped_column(String(20), nullable=False)  # AI_FEATURES
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Estimated cost in micro-USD (1_000_000 = $1), same micros convention as
    # spend. Estimated because prices are a code-side table, not billed data.
    cost_micro_usd: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()
