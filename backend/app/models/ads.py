import datetime as dt
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import created_at_column, id_column

# The ads hierarchy is modeled generically so reporting never forks per
# platform: Meta campaign/ad set/ad and Google campaign/ad group/ad both map
# onto campaigns/ad_groups/ads. Platform-specific payloads live in `raw`.
# organization_id and client_id are denormalized onto every row so tenant
# scoping (both levels) is a single indexed filter, not a join chain.


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_campaign_platform_ext"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    ad_account_id: Mapped[str] = mapped_column(
        ForeignKey("ad_accounts.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(400), nullable=False)
    status: Mapped[Optional[str]] = mapped_column(String(50))
    objective: Mapped[Optional[str]] = mapped_column(String(100))
    # Budgets normalized to micros (Google-native; Meta cents * 10_000).
    daily_budget_micros: Mapped[Optional[int]] = mapped_column(BigInteger)
    lifetime_budget_micros: Mapped[Optional[int]] = mapped_column(BigInteger)
    start_time: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    raw: Mapped[Optional[dict]] = mapped_column(JSON)
    synced_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = created_at_column()


class AdGroup(Base):
    """Meta ad set / Google ad group."""

    __tablename__ = "ad_groups"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_ad_group_platform_ext"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(400), nullable=False)
    status: Mapped[Optional[str]] = mapped_column(String(50))
    raw: Mapped[Optional[dict]] = mapped_column(JSON)
    synced_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = created_at_column()


class Ad(Base):
    __tablename__ = "ads"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_ad_platform_ext"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    ad_group_id: Mapped[str] = mapped_column(ForeignKey("ad_groups.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(400), nullable=False)
    status: Mapped[Optional[str]] = mapped_column(String(50))
    creative_id: Mapped[Optional[str]] = mapped_column(ForeignKey("creatives.id"))
    raw: Mapped[Optional[dict]] = mapped_column(JSON)
    synced_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = created_at_column()


class Creative(Base):
    __tablename__ = "creatives"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_creative_platform_ext"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(400))
    title: Mapped[Optional[str]] = mapped_column(String(500))
    body: Mapped[Optional[str]] = mapped_column(String(2000))
    media_type: Mapped[Optional[str]] = mapped_column(String(50))
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String(1000))
    raw: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = created_at_column()


class QualitySnapshot(Base):
    """Daily snapshot of a platform quality signal that the platforms only
    show as a point-in-time value — Google keyword Quality Score, Google
    ad-strength (RSA/PMax). Captured on every insights sync so Phase 3 can
    compute *trends* and flag drops instead of someone eyeballing a chart.
    One row per entity per metric per day."""

    __tablename__ = "quality_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "platform", "entity_type", "entity_external_id", "metric", "date",
            name="uq_quality_snapshot",
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
    entity_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # keyword | ad | asset_group
    entity_external_id: Mapped[str] = mapped_column(String(150), nullable=False)
    entity_name: Mapped[Optional[str]] = mapped_column(String(400))
    metric: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # quality_score (1-10) | ad_strength (ordinal, see metrics.AD_STRENGTH_SCALE)
    value: Mapped[Optional[int]] = mapped_column(Integer)
    value_label: Mapped[Optional[str]] = mapped_column(String(50))  # e.g. "GOOD"
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class InsightDaily(Base):
    """One row per entity per day — the time-series layer Phase 3's blended
    metrics are computed from."""

    __tablename__ = "insights_daily"
    __table_args__ = (
        UniqueConstraint(
            "platform", "entity_type", "entity_external_id", "date",
            name="uq_insight_entity_day",
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
    entity_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # account | campaign | ad_group | ad
    entity_external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    spend_micros: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    conversions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    raw: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = created_at_column()
