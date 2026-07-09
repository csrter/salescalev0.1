"""Canonical ad-platform registry — the single source of truth for which
platforms exist and their cross-cutting metadata.

Before this module, "which platforms exist" was duplicated across ~8 places
(a free-string constant in models.core, CONVERSION_PLATFORMS in schemas, the
?platforms= validators in the metrics/ai APIs, the utm-alias / form-source /
insight-level maps in services.metrics, and the integrations provider loop).
Adding a platform meant editing every one. Now they all derive from PLATFORMS
here, so registering a spec below propagates to attribution, the dashboard
platform filter, blended metrics, conversion-config validation, and the
GET /api/platforms discovery endpoint the frontend renders from.

Per-platform request shapes (OAuth endpoints, insight fields, conversion API
payloads, exact click-ID parameter names) are NOT defined here — those live in
each platform's adapter and MUST be verified against the platform's live
developer docs at adapter-build time (CLAUDE.md standing guardrail), not taken
from memory. The click_id_params / utm_aliases below are the capture-side
conventions; treat a STUB platform's values as provisional until its adapter
is built and doc-verified.
"""

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

from .models.core import PLATFORM_GOOGLE, PLATFORM_META

# Lifecycle of an adapter, independent of whether a given org has connected it.
STATUS_LIVE = "live"  # fully implemented + reference-tested (Meta, Google)
STATUS_SCAFFOLD = "scaffold"  # adapter + OAuth connect built; needs live-account validation
STATUS_STUB = "stub"  # registered/visible ("coming soon") — not connectable yet


@dataclass(frozen=True)
class PlatformSpec:
    id: str
    name: str
    status: str
    # URL params this platform stamps on the landing page for click-based
    # attribution (Meta: fbclid, Google: gclid). Provisional for STUBs.
    click_id_params: Tuple[str, ...] = ()
    # utm_source values (lowercased) that reconcile to this platform.
    utm_aliases: FrozenSet[str] = field(default_factory=frozenset)
    # Contact.source values implying this platform when there's no landing
    # event (native lead forms never touch the client's landing page).
    form_sources: FrozenSet[str] = field(default_factory=frozenset)
    # Canonical entity level for insight dedup in the metrics layer.
    insight_level: str = "campaign"
    supports_conversions: bool = False
    supports_lead_forms: bool = False
    supports_byo_creds: bool = False  # org can enter its own app credentials
    connectable: bool = False  # OAuth connect flow implemented

    @property
    def coming_soon(self) -> bool:
        return self.status == STATUS_STUB


PLATFORMS: Dict[str, PlatformSpec] = {}


def register(spec: PlatformSpec) -> PlatformSpec:
    PLATFORMS[spec.id] = spec
    return spec


# --- Reference implementations (Phases 1/2/3/5) ---
register(
    PlatformSpec(
        id=PLATFORM_META,
        name="Meta",
        status=STATUS_LIVE,
        click_id_params=("fbclid",),
        utm_aliases=frozenset({"facebook", "fb", "meta", "instagram", "ig"}),
        form_sources=frozenset({"meta_instant_form"}),
        insight_level="ad",
        supports_conversions=True,
        supports_lead_forms=True,
        supports_byo_creds=True,
        connectable=True,
    )
)
register(
    PlatformSpec(
        id=PLATFORM_GOOGLE,
        name="Google Ads",
        status=STATUS_LIVE,
        click_id_params=("gclid",),
        utm_aliases=frozenset({"google", "googleads", "adwords", "google-ads"}),
        form_sources=frozenset({"google_lead_form"}),
        insight_level="ad_group",
        supports_conversions=True,
        supports_lead_forms=True,
        supports_byo_creds=True,
        connectable=True,
    )
)

# --- Phase 7 additional platforms (registered as STUBs; each adapter's real
# API details are filled in + doc-verified when the adapter is built). ---
register(
    PlatformSpec(
        id="msads",
        name="Microsoft Advertising",
        status=STATUS_STUB,
        click_id_params=("msclkid",),
        utm_aliases=frozenset({"bing", "microsoft", "msads", "microsoft-ads"}),
        insight_level="ad_group",
    )
)
register(
    PlatformSpec(
        id="linkedin",
        name="LinkedIn",
        status=STATUS_STUB,
        click_id_params=("li_fat_id",),
        utm_aliases=frozenset({"linkedin"}),
        insight_level="campaign",
    )
)
register(
    PlatformSpec(
        id="snapchat",
        name="Snapchat",
        status=STATUS_STUB,
        click_id_params=("sccid",),
        utm_aliases=frozenset({"snapchat", "snap"}),
        insight_level="ad",
    )
)
register(
    PlatformSpec(
        id="reddit",
        name="Reddit",
        status=STATUS_STUB,
        click_id_params=("rdt_cid",),
        utm_aliases=frozenset({"reddit"}),
        insight_level="campaign",
    )
)
register(
    PlatformSpec(
        id="tiktok",
        name="TikTok",
        status=STATUS_STUB,
        click_id_params=("ttclid",),
        utm_aliases=frozenset({"tiktok"}),
        insight_level="ad",
    )
)
register(
    PlatformSpec(
        id="pinterest",
        name="Pinterest",
        status=STATUS_STUB,
        click_id_params=("epik",),
        utm_aliases=frozenset({"pinterest"}),
        insight_level="ad",
    )
)
register(
    PlatformSpec(
        id="nextdoor",
        name="Nextdoor",
        status=STATUS_STUB,
        utm_aliases=frozenset({"nextdoor"}),
        insight_level="campaign",
    )
)


# --- Derived accessors (consume these instead of hardcoding platform sets) ---

def get(platform_id: str) -> Optional[PlatformSpec]:
    return PLATFORMS.get(platform_id)


def all_ids() -> FrozenSet[str]:
    """Every registered platform id — the allow-list the dashboard filter and
    ?platforms= grammar validate against."""
    return frozenset(PLATFORMS)


def conversion_platform_ids() -> FrozenSet[str]:
    """Platforms a ConversionConfig may target (a server-side sender exists)."""
    return frozenset(p.id for p in PLATFORMS.values() if p.supports_conversions)


def byo_creds_platform_ids() -> List[str]:
    """Platforms whose per-org app credentials the Integrations page manages,
    in registry order."""
    return [p.id for p in PLATFORMS.values() if p.supports_byo_creds]


def utm_alias_map() -> Dict[str, FrozenSet[str]]:
    return {p.id: p.utm_aliases for p in PLATFORMS.values() if p.utm_aliases}


def form_source_map() -> Dict[str, str]:
    return {src: p.id for p in PLATFORMS.values() for src in p.form_sources}


def insight_levels() -> Dict[str, str]:
    return {p.id: p.insight_level for p in PLATFORMS.values()}


def click_id_param_map() -> Dict[str, Tuple[str, ...]]:
    """platform id → the click-ID URL params it stamps, in registry order.
    Registry order is attribution priority (Meta before Google, matching the
    prior hardcoded fbclid-then-gclid precedence)."""
    return {p.id: p.click_id_params for p in PLATFORMS.values() if p.click_id_params}
