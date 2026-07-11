"""Phase 12 Part B — contact-email enrichment.

Two sources, both explicitly non-scraping (guardrail 6):

1. The imported business's OWN public website: homepage plus a short list of
   conventional contact paths — never a general web crawl. robots.txt is
   honored, the user agent is honest, fetches are rate-limited and time out
   fast.
2. A licensed provider the Organization connects with ITS OWN key (BYO —
   Hunter's terms prohibit multi-tenant use of a shared key, so there is
   deliberately no operator fallback for it). Adapter interface so the
   provider is swappable, same philosophy as the ad-platform adapters.

Everything discovered lands as a CANDIDATE email (contacts.candidate_emails),
never as a verified address — Part C's verification pipeline owns promotion
to a verdict.
"""

import logging
import re
import time
import urllib.robotparser
from dataclasses import dataclass
from typing import List, Optional, Protocol
from urllib.parse import urljoin, urlparse

import httpx

from ..config import get_settings

log = logging.getLogger("salescale.enrichment")

USER_AGENT = "SalescaleLeadFinder/1.0 (+https://salescale.app/leadfinder-bot)"

# Conventional contact paths only (task 6) — checked in order after "/".
_CONTACT_PATHS = ["/contact", "/contact-us", "/about", "/about-us", "/team"]

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Addresses that match the pattern but are never a business contact: asset
# filenames, package/vendor artifacts, and example/placeholder domains.
_JUNK_LOCAL = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js)$", re.I)
_JUNK_DOMAINS = (
    "example.com",
    "example.org",
    "sentry.io",
    "wixpress.com",
    "sentry-next.wixpress.com",
    "schema.org",
)


def normalize_domain(url_or_domain: Optional[str]) -> Optional[str]:
    """Registrable host for dedupe/matching: lowercase, scheme and www
    stripped. Accepts either a bare domain or a full URL."""
    if not url_or_domain:
        return None
    raw = url_or_domain.strip().lower()
    if "://" not in raw:
        raw = "http://" + raw
    host = urlparse(raw).netloc.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _extract_emails(html: str, site_domain: str) -> List[str]:
    found: List[str] = []
    for match in _EMAIL_RE.findall(html):
        email = match.strip().strip(".").lower()
        local, _, domain = email.partition("@")
        if _JUNK_LOCAL.search(local) or domain in _JUNK_DOMAINS:
            continue
        if email not in found:
            found.append(email)
    # Prefer addresses on the business's own domain — third-party addresses in
    # page footers (agencies, platforms) are usually not the business.
    own = [e for e in found if e.endswith("@" + site_domain)]
    return own or found


def discover_site_emails(website: str) -> List[str]:
    """Candidate emails published on the business's own site. Best-effort:
    any network failure returns what was found so far — enrichment must never
    block the import pipeline."""
    settings = get_settings()
    if not settings.lead_finder_crawl_enabled:
        return []
    domain = normalize_domain(website)
    if not domain:
        return []
    base = f"https://{domain}"
    timeout = settings.lead_finder_crawl_timeout_seconds

    robots = urllib.robotparser.RobotFileParser()
    try:
        resp = httpx.get(
            urljoin(base, "/robots.txt"),
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )
        robots.parse(resp.text.splitlines() if resp.status_code == 200 else [])
    except httpx.HTTPError:
        robots.parse([])  # unreachable robots.txt = no stated restrictions

    emails: List[str] = []
    fetched = 0
    for path in ["/"] + _CONTACT_PATHS:
        if fetched >= settings.lead_finder_crawl_max_pages:
            break
        url = urljoin(base, path)
        if not robots.can_fetch(USER_AGENT, url):
            continue
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            continue
        finally:
            fetched += 1
            time.sleep(0.5)  # polite pacing between page fetches
        if resp.status_code != 200 or "text/html" not in resp.headers.get(
            "content-type", "text/html"
        ):
            continue
        for e in _extract_emails(resp.text[:500_000], domain):
            if e not in emails:
                emails.append(e)
    return emails


# --- licensed provider adapter (task 7) ---


@dataclass
class EnrichmentCandidate:
    email: str
    name: Optional[str] = None
    role: Optional[str] = None


class EnrichmentProvider(Protocol):
    """Input: the business's domain (and name, for providers that use it);
    output: candidate business contacts. Data minimization (task 8): name,
    role, and business email only — adapters must not return anything more."""

    id: str

    def find_contacts(
        self, domain: str, business_name: Optional[str] = None
    ) -> List[EnrichmentCandidate]: ...


class HunterProvider:
    """Reference adapter: Hunter Domain Search, on the ORGANIZATION'S OWN
    key (BYO). Requests only the first page of generic+personal emails and
    maps to the minimal candidate shape."""

    id = "hunter"

    def __init__(self, api_key: str):
        self._key = api_key

    def find_contacts(
        self, domain: str, business_name: Optional[str] = None
    ) -> List[EnrichmentCandidate]:
        try:
            resp = httpx.get(
                "https://api.hunter.io/v2/domain-search",
                params={"domain": domain, "api_key": self._key, "limit": 10},
                timeout=15,
            )
            data = resp.json().get("data", {}) if resp.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            return []
        out: List[EnrichmentCandidate] = []
        for row in data.get("emails", []):
            email = (row.get("value") or "").lower()
            if not email:
                continue
            name = " ".join(
                p for p in [row.get("first_name"), row.get("last_name")] if p
            )
            out.append(
                EnrichmentCandidate(
                    email=email, name=name or None, role=row.get("position")
                )
            )
        return out


def provider_for(provider_id: str, api_key: str) -> Optional[EnrichmentProvider]:
    """Adapter registry. New providers: implement EnrichmentProvider, add a
    branch here and an entry in integration_creds.KEY_PROVIDERS."""
    if provider_id == "hunter" and api_key:
        return HunterProvider(api_key)
    return None
