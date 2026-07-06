"""Standardized UTM naming: one builder + one enforcement check.

Inconsistent UTMs quietly break every metric that reconciles platform claims
against the landing-event trail, so the convention is enforced by tooling
rather than hoping everyone types carefully.

Default convention (overridable per client via metric_settings["utm"]):
  utm_source   = canonical platform name ("facebook" for Meta, "google")
  utm_medium   = "paid_social" (meta) | "paid_search" (google)
  utm_campaign = <client-slug>_<campaign-slug>
  utm_content  = <ad-or-creative-slug>          (optional)
  utm_term     = <keyword-slug>                 (optional, Google Search)
Slug rule: lowercase; every run of non-alphanumerics collapses to "-";
underscores only as the section separator in utm_campaign.

A landing event conforms when: utm_source present AND every present utm_*
value matches ^[a-z0-9_-]+$ (no spaces, no uppercase, no free-form junk)
AND utm_campaign starts with the client slug.
"""

import re
from typing import Any, Dict, List, Optional

from ..models.core import Client

CANONICAL_SOURCE = {"meta": "facebook", "google": "google"}
CANONICAL_MEDIUM = {"meta": "paid_social", "google": "paid_search"}

_VALID_VALUE = re.compile(r"^[a-z0-9_-]+$")


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _convention(client: Client) -> Dict[str, Any]:
    return (client.metric_settings or {}).get("utm") or {}


def build(
    client: Client,
    platform: str,
    campaign_name: str,
    content: Optional[str] = None,
    term: Optional[str] = None,
) -> Dict[str, str]:
    """Canonical UTM set for one ad placement. Deterministic: same inputs
    always produce the same tags, which is the whole point."""
    convention = _convention(client)
    source = convention.get("source", {}).get(platform) or CANONICAL_SOURCE.get(
        platform, slugify(platform)
    )
    medium = convention.get("medium", {}).get(platform) or CANONICAL_MEDIUM.get(
        platform, "paid"
    )
    params = {
        "utm_source": source,
        "utm_medium": medium,
        "utm_campaign": f"{slugify(client.name)}_{slugify(campaign_name)}",
    }
    if content:
        params["utm_content"] = slugify(content)
    if term:
        params["utm_term"] = slugify(term)
    return params


def violations_for_event(client: Client, event) -> List[str]:
    """Convention violations for one landing event (empty = conforming).
    Events with no UTMs at all are skipped — organic/direct traffic isn't a
    naming violation; missing UTMs on paid traffic surface via
    reconciliation's no-utm flag instead."""
    utms = {
        "utm_source": event.utm_source,
        "utm_medium": event.utm_medium,
        "utm_campaign": event.utm_campaign,
        "utm_content": event.utm_content,
        "utm_term": event.utm_term,
    }
    present = {k: v for k, v in utms.items() if v}
    if not present:
        return []
    problems = []
    if not utms["utm_source"]:
        problems.append("utm_source missing while other utm_* params present")
    for key, value in present.items():
        if not _VALID_VALUE.match(value):
            problems.append(
                f"{key}={value!r} breaks convention (lowercase a-z0-9_- only)"
            )
    campaign = utms["utm_campaign"]
    client_slug = slugify(client.name)
    if campaign and _VALID_VALUE.match(campaign) and not campaign.startswith(
        f"{client_slug}_"
    ):
        problems.append(
            f"utm_campaign={campaign!r} does not start with client slug "
            f"'{client_slug}_'"
        )
    return problems
