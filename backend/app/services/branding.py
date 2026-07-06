"""Phase 9 white-labeling: per-Organization branding + custom domains.

Branding shape (organizations.branding JSON; every key optional):
    {
      "product_name":  str   — replaces "Salescale" everywhere client-facing,
      "logo_url":      str   — shown in the header and on the login page,
      "favicon_url":   str,
      "colors":        {key: "#rrggbb"} — keys in BRAND_COLOR_KEYS; the
                       frontend maps them onto its CSS custom properties,
      "email_from_name":    str,
      "email_from_address": str — branded sender for client-facing email
                       (services/email.py falls back to the neutral default
                       when unset — never the other way around),
      "apply_to_team": bool — whether the Organization's own team-facing
                       screens also rebrand. Default False: agencies usually
                       care about what their *clients* see, not their own
                       internal tool. This is per-Organization data, not a
                       product decision — each tenant picks.
    }

Custom domains: an Organization claims a hostname, we hand back a token,
they publish it as a TXT record at _salescale-verify.<domain>, and only
after verify_custom_domain() confirms the record does the host resolve to
their branding. Resolution by unverified claim is a tenant-impersonation
hole, so resolve_for_host() filters on custom_domain_verified_at.

TLS note: certificate issuance for custom domains is a deployment-layer
concern, not application code — every serious fronting option (Caddy
on-demand TLS, Cloudflare/Fly/Render custom-domain APIs, cert-manager)
provisions Let's Encrypt certs automatically once DNS points at the app;
building bespoke cert management here would duplicate that badly. The app's
job is the part the platform can't do: prove domain ownership and map
Host → tenant, which is what this module implements.
"""

import re
import secrets
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.core import Organization

# The neutral (non-white-labeled) identity. This is the ONLY place the
# product's own name should be hardcoded for a client-facing surface —
# everything else must render whatever resolve()/public_branding() returns.
DEFAULT_BRANDING: Dict[str, Any] = {
    "product_name": "Salescale",
    "logo_url": None,
    "favicon_url": None,
    "colors": {},
    "email_from_name": None,
    "email_from_address": None,
    "apply_to_team": False,
}

# The frontend's themable tokens (see frontend/src/api.ts applyBranding).
BRAND_COLOR_KEYS = {
    "primary",         # buttons/active states   (default --cobalt)
    "primary_strong",  # hover/pressed           (default --cobalt-strong)
    "primary_soft",    # tinted backgrounds      (default --cobalt-soft)
    "header_start",    # header gradient start   (default --navy-950)
    "header_end",      # header gradient end     (default --navy-800)
}

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_HOSTNAME_RE = re.compile(
    r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

DNS_VERIFY_PREFIX = "_salescale-verify"


def merged(org: Organization) -> Dict[str, Any]:
    """The Organization's effective branding: defaults overlaid with
    whatever they've configured."""
    return {**DEFAULT_BRANDING, **(org.branding or {})}


def public_branding(org: Optional[Organization]) -> Dict[str, Any]:
    """The client-safe slice of branding — what an unauthenticated login
    page or a client-role session may see. Never includes email settings or
    anything Organization-internal."""
    b = merged(org) if org is not None else dict(DEFAULT_BRANDING)
    return {
        "product_name": b["product_name"],
        "logo_url": b["logo_url"],
        "favicon_url": b["favicon_url"],
        "colors": b["colors"] or {},
        "apply_to_team": bool(b["apply_to_team"]),
        "is_custom": org is not None and bool(org.branding),
    }


def validate_branding(payload: Dict[str, Any]) -> Optional[str]:
    """Returns an error message, or None when valid."""
    colors = payload.get("colors") or {}
    for key, value in colors.items():
        if key not in BRAND_COLOR_KEYS:
            return f"unknown color key {key!r}"
        if not isinstance(value, str) or not _HEX_RE.match(value):
            return f"color {key!r} must be a #rrggbb hex value"
    return None


# --- custom domains ---


def normalize_domain(domain: str) -> str:
    return domain.strip().lower().rstrip(".").split(":")[0]


def is_valid_domain(domain: str) -> bool:
    return bool(_HOSTNAME_RE.match(domain))


def new_verification_token() -> str:
    return f"salescale-verify={secrets.token_urlsafe(24)}"


def _dns_txt_records(name: str) -> list[str]:
    """TXT lookup, isolated so tests (and offline dev) can monkeypatch it.
    Uses dnspython; imported lazily so the app runs without it until the
    first live verification."""
    import dns.resolver  # type: ignore

    try:
        answers = dns.resolver.resolve(name, "TXT")
    except Exception:
        return []
    records: list[str] = []
    for rdata in answers:
        records.append(b"".join(rdata.strings).decode("utf-8", "replace"))
    return records


def verify_custom_domain(org: Organization) -> bool:
    """True when the TXT record at _salescale-verify.<domain> carries the
    org's token. Caller persists custom_domain_verified_at on success."""
    if not org.custom_domain or not org.custom_domain_token:
        return False
    name = f"{DNS_VERIFY_PREFIX}.{org.custom_domain}"
    return org.custom_domain_token in _dns_txt_records(name)


def resolve_for_host(db: Session, host: str) -> Optional[Organization]:
    """Host header → Organization, for verified custom domains only."""
    domain = normalize_domain(host or "")
    if not domain:
        return None
    return db.execute(
        select(Organization).where(
            Organization.custom_domain == domain,
            Organization.custom_domain_verified_at.is_not(None),
        )
    ).scalar_one_or_none()
