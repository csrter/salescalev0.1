"""Phase 3 metrics layer. Read/compute-only — no platform writes.

Every function here ends up in client-facing reports, so each metric's
definition is written out where it's computed. If a number needs auditing
six months from now, the derivation should be traceable from this file
alone.

Shared conventions
------------------
- Money is micros throughout (1_000_000 micros = $1); dollars only at the UI.
- Date ranges are inclusive [since, until].
- "Platform-reported conversions" = the platform's own attribution claim,
  summed from InsightDaily (Meta rows are ad-level, Google rows are
  ad-group-level — one canonical level per platform, so sums never double
  count).
- "Tracked leads" = Salescale contacts created in range, attributed to a
  platform by their landing event: a Meta click id (fbclid) or a Meta-alias
  utm_source ⇒ meta; gclid or Google-alias utm_source ⇒ google; otherwise
  fall back to the contact's form source (meta_instant_form ⇒ meta,
  google_lead_form ⇒ google); anything else is "unattributed".
- Every query filters by organization_id and client_id. Benchmarks compare
  only clients inside the same Organization — never across tenants.
"""

import datetime as dt
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.ads import Campaign, InsightDaily, QualitySnapshot
from ..models.attribution import LandingEvent
from ..models.core import Client
from ..models.crm import Contact, Deal
from . import lead_quality

# utm_source values that count as each platform when reconciling attribution.
PLATFORM_UTM_ALIASES: Dict[str, Set[str]] = {
    "meta": {"facebook", "fb", "meta", "instagram", "ig"},
    "google": {"google", "googleads", "adwords", "google-ads"},
}

# Contact.source values that imply a platform when no landing event exists
# (native lead forms never touch the client's landing page).
FORM_SOURCE_PLATFORM = {
    "meta_instant_form": "meta",
    "google_lead_form": "google",
}

# The canonical insight level per platform (see module docstring).
_INSIGHT_LEVELS = {"meta": "ad", "google": "ad_group"}


# --- shared aggregation helpers ---


def _insight_rows(
    db: Session, client: Client, since: dt.date, until: dt.date
) -> List[InsightDaily]:
    return (
        db.execute(
            select(InsightDaily).where(
                InsightDaily.organization_id == client.organization_id,
                InsightDaily.client_id == client.id,
                InsightDaily.date >= since,
                InsightDaily.date <= until,
            )
        )
        .scalars()
        .all()
    )


def _platform_totals(
    db: Session, client: Client, since: dt.date, until: dt.date
) -> Dict[str, Dict[str, int]]:
    """Spend / platform-reported conversions / clicks / impressions per
    platform, summed at that platform's canonical insight level."""
    totals: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"spend_micros": 0, "conversions": 0, "clicks": 0, "impressions": 0}
    )
    for row in _insight_rows(db, client, since, until):
        if row.entity_type != _INSIGHT_LEVELS.get(row.platform):
            continue
        t = totals[row.platform]
        t["spend_micros"] += row.spend_micros
        t["conversions"] += row.conversions
        t["clicks"] += row.clicks
        t["impressions"] += row.impressions
    return dict(totals)


def _day_bounds(since: dt.date, until: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """Aware-UTC [start, end) datetime bounds for an inclusive date range."""
    start = dt.datetime.combine(since, dt.time.min, tzinfo=dt.timezone.utc)
    end = dt.datetime.combine(
        until + dt.timedelta(days=1), dt.time.min, tzinfo=dt.timezone.utc
    )
    return start, end


def _contacts_in_range(
    db: Session, client: Client, since: dt.date, until: dt.date
) -> List[Contact]:
    start, end = _day_bounds(since, until)
    return (
        db.execute(
            select(Contact).where(
                Contact.organization_id == client.organization_id,
                Contact.client_id == client.id,
                Contact.created_at >= start,
                Contact.created_at < end,
            )
        )
        .scalars()
        .all()
    )


def contact_platforms(
    db: Session, client: Client, contacts: List[Contact]
) -> Dict[str, Optional[str]]:
    """contact_id → "meta" | "google" | None (unattributed), per the tracked-
    leads definition in the module docstring. UTM/click-id evidence from the
    landing event wins over the form source."""
    ids = [c.id for c in contacts]
    events = (
        db.execute(
            select(LandingEvent).where(
                LandingEvent.organization_id == client.organization_id,
                LandingEvent.contact_id.in_(ids) if ids else False,
            )
        )
        .scalars()
        .all()
    )
    by_contact: Dict[str, LandingEvent] = {}
    for e in events:
        by_contact.setdefault(e.contact_id, e)

    out: Dict[str, Optional[str]] = {}
    for c in contacts:
        event = by_contact.get(c.id)
        platform: Optional[str] = None
        if event is not None:
            source = (event.utm_source or "").lower()
            if event.fbclid or source in PLATFORM_UTM_ALIASES["meta"]:
                platform = "meta"
            elif event.gclid or source in PLATFORM_UTM_ALIASES["google"]:
                platform = "google"
        if platform is None:
            platform = FORM_SOURCE_PLATFORM.get(c.source or "")
        out[c.id] = platform
    return out


def _cpl(spend_micros: int, leads: int) -> Optional[float]:
    """Cost per lead in dollars; None (not 0) when there are no leads — a
    zero CPL would read as 'free leads' on a client report."""
    if leads <= 0:
        return None
    return round(spend_micros / leads / 1_000_000, 2)


# --- 1 & 2: blended CAC/ROAS and channel mix ---


def blended_and_mix(
    db: Session, client: Client, since: dt.date, until: dt.date
) -> Dict[str, Any]:
    """Definitions:
    - Tracked CPL (per platform) = platform spend / tracked leads attributed
      to that platform.
    - Platform CPL = platform spend / platform-reported conversions (the
      platform's own claim — shown side by side with tracked CPL on purpose).
    - Blended CPL = total spend across platforms / total tracked leads
      (including unattributed leads: money was spent to get them somewhere).
    - Blended CAC = total spend / won deals in range whose contact is a
      tracked paid lead (won = Deal.status == "won", closed in range).
    - Blended ROAS = sum of those won deals' value_cents / total spend.
      Revenue source is Salescale won-deal value — not platform-reported
      conversion values.
    """
    totals = _platform_totals(db, client, since, until)
    contacts = _contacts_in_range(db, client, since, until)
    platforms = contact_platforms(db, client, contacts)

    leads_by_platform: Dict[str, int] = defaultdict(int)
    for _cid, platform in platforms.items():
        leads_by_platform[platform or "unattributed"] += 1

    total_spend = sum(t["spend_micros"] for t in totals.values())
    total_leads = len(contacts)

    # Won-deal revenue for CAC/ROAS: deals closed in range, contact from paid.
    start, end = _day_bounds(since, until)
    won_deals = (
        db.execute(
            select(Deal).where(
                Deal.organization_id == client.organization_id,
                Deal.client_id == client.id,
                Deal.status == "won",
                Deal.closed_at >= start,
                Deal.closed_at < end,
            )
        )
        .scalars()
        .all()
    )
    paid_contact_ids = {cid for cid, p in platforms.items() if p is not None}
    paid_won = [d for d in won_deals if d.contact_id in paid_contact_ids]
    revenue_cents = sum(d.value_cents or 0 for d in paid_won)

    per_platform = {}
    for platform, t in sorted(totals.items()):
        tracked = leads_by_platform.get(platform, 0)
        per_platform[platform] = {
            "spend_micros": t["spend_micros"],
            "platform_reported_conversions": t["conversions"],
            "tracked_leads": tracked,
            "platform_cpl": _cpl(t["spend_micros"], t["conversions"]),
            "tracked_cpl": _cpl(t["spend_micros"], tracked),
            "spend_share": round(t["spend_micros"] / total_spend, 3)
            if total_spend
            else None,
            "lead_share": round(tracked / total_leads, 3) if total_leads else None,
        }

    return {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "per_platform": per_platform,
        "unattributed_leads": leads_by_platform.get("unattributed", 0),
        "total_spend_micros": total_spend,
        "total_tracked_leads": total_leads,
        "blended_cpl": _cpl(total_spend, total_leads),
        "won_deals_from_paid": len(paid_won),
        "revenue_cents_from_paid": revenue_cents,
        "blended_cac": _cpl(total_spend, len(paid_won)),
        # ROAS = revenue / spend (both converted to a common unit first).
        "blended_roas": round((revenue_cents * 10_000) / total_spend, 2)
        if total_spend and revenue_cents
        else None,
    }


# --- 3: funnel-tier performance ---

# Default tier classification by campaign-name pattern. Overridable per
# client via metric_settings["tier_patterns"] = {"meta": {tier: regex},
# "google": {tier: regex}} — naming conventions are Organization data, not
# product assumptions.
DEFAULT_TIER_PATTERNS = {
    "meta": {
        "cold": r"cold|prospect|tof",
        "warm": r"warm|retarget|rtg|mof",
        "hot": r"hot|remarket|bof",
    },
    "google": {
        "branded": r"brand",
        # everything else in Google is non_branded (assigned below)
    },
}


def _tier_for(name: str, patterns: Dict[str, str], fallback: str) -> str:
    lowered = name.lower()
    for tier, pattern in patterns.items():
        if re.search(pattern, lowered):
            return tier
    return fallback


def funnel_tiers(
    db: Session, client: Client, since: dt.date, until: dt.date
) -> Dict[str, Any]:
    """Spend / platform-reported conversions / CPL per funnel tier.

    Tier = campaign-name pattern match (defaults above, per-client override
    in metric_settings). Insight rows carry their campaign id in raw, which
    joins to the local campaign cache for the name. Rows whose campaign
    isn't cached fall into the platform's fallback tier ("untiered").
    """
    settings_patterns = (client.metric_settings or {}).get("tier_patterns") or {}
    campaign_names: Dict[str, str] = {
        c.external_id: c.name
        for c in db.execute(
            select(Campaign).where(
                Campaign.organization_id == client.organization_id,
                Campaign.client_id == client.id,
            )
        )
        .scalars()
        .all()
    }

    tiers: Dict[str, Dict[str, Dict[str, int]]] = {
        "meta": defaultdict(lambda: {"spend_micros": 0, "conversions": 0}),
        "google": defaultdict(lambda: {"spend_micros": 0, "conversions": 0}),
    }
    fallbacks = {"meta": "untiered", "google": "non_branded"}
    for row in _insight_rows(db, client, since, until):
        if row.entity_type != _INSIGHT_LEVELS.get(row.platform):
            continue
        campaign_ext = (row.raw or {}).get("campaign_id")
        name = campaign_names.get(str(campaign_ext) if campaign_ext else "")
        patterns = {
            **DEFAULT_TIER_PATTERNS.get(row.platform, {}),
            **settings_patterns.get(row.platform, {}),
        }
        tier = (
            _tier_for(name, patterns, fallbacks[row.platform])
            if name
            else fallbacks[row.platform]
        )
        t = tiers[row.platform][tier]
        t["spend_micros"] += row.spend_micros
        t["conversions"] += row.conversions

    return {
        platform: {
            tier: {**vals, "cpl": _cpl(vals["spend_micros"], vals["conversions"])}
            for tier, vals in sorted(platform_tiers.items())
        }
        for platform, platform_tiers in tiers.items()
        if platform_tiers
    }


# --- 4a: creative fatigue (Meta) ---

# Fatigue definition: an ad's CTR in the recent window vs. its own baseline.
#   baseline CTR = clicks/impressions over days [-28, -8)
#   recent CTR   = clicks/impressions over days [-7, 0]
#   fatigue_score = 1 − recent_ctr / baseline_ctr   (clamped to [0, 1])
# Flagged when score ≥ 0.30 (CTR down ≥30% vs. its own baseline) AND both
# windows have ≥ MIN_IMPRESSIONS so small-sample noise can't flag.
FATIGUE_RECENT_DAYS = 7
FATIGUE_BASELINE_DAYS = 21
FATIGUE_FLAG_THRESHOLD = 0.30
FATIGUE_MIN_IMPRESSIONS = 1000


def creative_fatigue(db: Session, client: Client, until: dt.date) -> Dict[str, Any]:
    recent_start = until - dt.timedelta(days=FATIGUE_RECENT_DAYS - 1)
    baseline_start = recent_start - dt.timedelta(days=FATIGUE_BASELINE_DAYS)
    rows = [
        r
        for r in _insight_rows(db, client, baseline_start, until)
        if r.platform == "meta" and r.entity_type == "ad"
    ]
    windows: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(
        lambda: {
            "recent": {"impressions": 0, "clicks": 0},
            "baseline": {"impressions": 0, "clicks": 0},
        }
    )
    names: Dict[str, str] = {}
    for r in rows:
        window = "recent" if r.date >= recent_start else "baseline"
        w = windows[r.entity_external_id][window]
        w["impressions"] += r.impressions
        w["clicks"] += r.clicks
        name = (r.raw or {}).get("ad_name")
        if name:
            names[r.entity_external_id] = name

    ads = []
    for ad_id, w in windows.items():
        ri, rc = w["recent"]["impressions"], w["recent"]["clicks"]
        bi, bc = w["baseline"]["impressions"], w["baseline"]["clicks"]
        if ri < FATIGUE_MIN_IMPRESSIONS or bi < FATIGUE_MIN_IMPRESSIONS or bc == 0:
            continue  # not enough signal in one of the windows
        recent_ctr = rc / ri
        baseline_ctr = bc / bi
        score = max(0.0, min(1.0, 1 - recent_ctr / baseline_ctr))
        ads.append(
            {
                "ad_external_id": ad_id,
                "ad_name": names.get(ad_id, ad_id),
                "recent_ctr": round(recent_ctr, 4),
                "baseline_ctr": round(baseline_ctr, 4),
                "fatigue_score": round(score, 2),
                "flagged": score >= FATIGUE_FLAG_THRESHOLD,
            }
        )
    ads.sort(key=lambda a: a["fatigue_score"], reverse=True)
    return {"ads": ads, "flagged": [a for a in ads if a["flagged"]]}


# --- 4b: quality-score / ad-strength trends (Google) ---

# Flag definition: latest snapshot vs. the earliest snapshot in the window.
#   quality_score (1–10): flag when it dropped by ≥ 1 point.
#   ad_strength (POOR=1 … EXCELLENT=4): flag on any drop.
QS_WINDOW_DAYS = 30


def quality_trends(db: Session, client: Client, until: dt.date) -> Dict[str, Any]:
    since = until - dt.timedelta(days=QS_WINDOW_DAYS)
    rows = (
        db.execute(
            select(QualitySnapshot)
            .where(
                QualitySnapshot.organization_id == client.organization_id,
                QualitySnapshot.client_id == client.id,
                QualitySnapshot.date >= since,
                QualitySnapshot.date <= until,
            )
            .order_by(QualitySnapshot.date)
        )
        .scalars()
        .all()
    )
    series: Dict[tuple, List[QualitySnapshot]] = defaultdict(list)
    for r in rows:
        series[(r.metric, r.entity_type, r.entity_external_id)].append(r)

    entities = []
    for (metric, entity_type, ext_id), snaps in series.items():
        first, last = snaps[0], snaps[-1]
        if first.value is None or last.value is None:
            continue
        delta = last.value - first.value
        flagged = delta <= -1 if metric == "quality_score" else delta < 0
        entities.append(
            {
                "metric": metric,
                "entity_type": entity_type,
                "entity_external_id": ext_id,
                "entity_name": last.entity_name or ext_id,
                "first": first.value,
                "first_date": first.date.isoformat(),
                "latest": last.value,
                "latest_label": last.value_label,
                "latest_date": last.date.isoformat(),
                "delta": delta,
                "flagged": flagged,
            }
        )
    entities.sort(key=lambda e: e["delta"])
    return {"entities": entities, "flagged": [e for e in entities if e["flagged"]]}


# --- 5: lead-quality-adjusted CPL ---


def lead_quality_adjusted_cpl(
    db: Session, client: Client, since: dt.date, until: dt.date
) -> Dict[str, Any]:
    """LQA-CPL = platform spend / qualified leads attributed to the platform.

    "Qualified" comes from the client's configured lead-quality source
    (services/lead_quality.py): Salescale-native by default, or an external
    CRM provider during a transition. Qualified-rate is reported per
    platform so quality is comparable channel-to-channel — a cheap CPL with
    a bad qualified-rate is the exact pattern this metric exists to expose.
    """
    totals = _platform_totals(db, client, since, until)
    contacts = _contacts_in_range(db, client, since, until)
    platforms = contact_platforms(db, client, contacts)
    qualified = lead_quality.qualified_contact_ids(db, client)

    by_platform: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"leads": 0, "qualified": 0}
    )
    for cid, platform in platforms.items():
        key = platform or "unattributed"
        by_platform[key]["leads"] += 1
        if cid in qualified:
            by_platform[key]["qualified"] += 1

    per_platform = {}
    for platform, t in sorted(totals.items()):
        counts = by_platform.get(platform, {"leads": 0, "qualified": 0})
        per_platform[platform] = {
            "spend_micros": t["spend_micros"],
            "leads": counts["leads"],
            "qualified_leads": counts["qualified"],
            "qualified_rate": round(counts["qualified"] / counts["leads"], 3)
            if counts["leads"]
            else None,
            "cpl": _cpl(t["spend_micros"], counts["leads"]),
            "lead_quality_adjusted_cpl": _cpl(
                t["spend_micros"], counts["qualified"]
            ),
        }

    total_spend = sum(t["spend_micros"] for t in totals.values())
    total_qualified = sum(v["qualified"] for v in by_platform.values())
    return {
        "source": client.lead_quality_source,
        "per_platform": per_platform,
        "blended_lead_quality_adjusted_cpl": _cpl(total_spend, total_qualified),
        "total_qualified_leads": total_qualified,
    }


# --- 6: cross-client benchmarking (within one Organization only) ---


def vertical_benchmark(
    db: Session, client: Client, since: dt.date, until: dt.date
) -> Dict[str, Any]:
    """This client's blended tracked CPL vs. the median across the same
    Organization's clients in the same vertical. Tenant rule: peers are
    selected by organization_id + vertical — one Organization's numbers are
    never visible to, or computed from, another's."""
    if not client.vertical:
        return {"vertical": None, "peers": 0, "note": "client has no vertical set"}
    peers = (
        db.execute(
            select(Client).where(
                Client.organization_id == client.organization_id,
                Client.vertical == client.vertical,
            )
        )
        .scalars()
        .all()
    )
    peer_cpls: List[float] = []
    client_cpl: Optional[float] = None
    for peer in peers:
        stats = blended_and_mix(db, peer, since, until)
        cpl = stats["blended_cpl"]
        if peer.id == client.id:
            client_cpl = cpl
        if cpl is not None:
            peer_cpls.append(cpl)
    peer_cpls.sort()
    median = peer_cpls[len(peer_cpls) // 2] if peer_cpls else None
    return {
        "vertical": client.vertical,
        "peers": len(peers),
        "client_blended_cpl": client_cpl,
        "vertical_median_blended_cpl": median,
        "vs_median_pct": round((client_cpl - median) / median * 100, 1)
        if client_cpl is not None and median
        else None,
    }


# --- 7: attribution reconciliation ---

# A discrepancy is flagged when the platform claims meaningfully more (or
# fewer) conversions than the UTM/click-id trail confirms:
#   |platform_reported − utm_confirmed| > max(1, 20% of utm_confirmed)
RECONCILIATION_TOLERANCE_PCT = 0.20


def reconciliation(
    db: Session, client: Client, since: dt.date, until: dt.date
) -> Dict[str, Any]:
    """Platform self-reported conversions vs. what the landing-event trail
    shows. utm_confirmed counts tracked leads whose landing event carries
    that platform's click id or utm_source alias. Leads with no landing
    event / no UTM data are reported separately — every one of those is a
    conversion some platform may be claiming without ground truth."""
    totals = _platform_totals(db, client, since, until)
    contacts = _contacts_in_range(db, client, since, until)
    platforms = contact_platforms(db, client, contacts)

    # Leads confirmed by landing-event evidence only (form-source fallback is
    # NOT confirmation — that's the platform's own form telling us).
    ids = [c.id for c in contacts]
    events = (
        db.execute(
            select(LandingEvent).where(
                LandingEvent.organization_id == client.organization_id,
                LandingEvent.contact_id.in_(ids) if ids else False,
            )
        )
        .scalars()
        .all()
    )
    event_by_contact: Dict[str, LandingEvent] = {}
    for e in events:
        event_by_contact.setdefault(e.contact_id, e)

    utm_confirmed: Dict[str, int] = defaultdict(int)
    no_utm_leads = 0
    for c in contacts:
        e = event_by_contact.get(c.id)
        if e is None or (not e.utm_source and not e.fbclid and not e.gclid):
            no_utm_leads += 1
            continue
        source = (e.utm_source or "").lower()
        if e.fbclid or source in PLATFORM_UTM_ALIASES["meta"]:
            utm_confirmed["meta"] += 1
        elif e.gclid or source in PLATFORM_UTM_ALIASES["google"]:
            utm_confirmed["google"] += 1

    flags: List[Dict[str, Any]] = []
    per_platform = {}
    for platform, t in sorted(totals.items()):
        reported = t["conversions"]
        confirmed = utm_confirmed.get(platform, 0)
        discrepancy = reported - confirmed
        tolerance = max(1, int(confirmed * RECONCILIATION_TOLERANCE_PCT))
        flagged = abs(discrepancy) > tolerance
        per_platform[platform] = {
            "platform_reported": reported,
            "utm_confirmed": confirmed,
            "discrepancy": discrepancy,
            "flagged": flagged,
        }
        if flagged:
            flags.append(
                {
                    "platform": platform,
                    "detail": (
                        f"{platform} reports {reported} conversions but the "
                        f"UTM/click-id trail confirms {confirmed} — "
                        f"{'over' if discrepancy > 0 else 'under'}-credit of "
                        f"{abs(discrepancy)}"
                    ),
                }
            )
    if no_utm_leads:
        flags.append(
            {
                "platform": None,
                "detail": (
                    f"{no_utm_leads} lead(s) have no UTM/click-id trail at all — "
                    "platform-attributed conversions for them cannot be verified"
                ),
            }
        )
    return {
        "per_platform": per_platform,
        "tracked_leads": len(contacts),
        "no_utm_leads": no_utm_leads,
        "flags": flags,
    }
