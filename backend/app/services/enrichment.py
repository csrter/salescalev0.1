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

# <meta name="description"> / <meta property="og:description"> in either
# attribute order — the page's own one-line summary of the business.
_META_DESC_RE = re.compile(
    r'<meta[^>]+(?:name=["\']description["\']|property=["\']og:description["\'])'
    r'[^>]*content=["\']([^"\']{10,600})["\']',
    re.I,
)
_META_DESC_RE_REV = re.compile(
    r'<meta[^>]+content=["\']([^"\']{10,600})["\']'
    r'[^>]*(?:name=["\']description["\']|property=["\']og:description["\'])',
    re.I,
)

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


def _extract_description(html: str) -> Optional[str]:
    m = _META_DESC_RE.search(html) or _META_DESC_RE_REV.search(html)
    if not m:
        return None
    import html as html_mod

    text = html_mod.unescape(m.group(1)).strip()
    return text[:500] or None


def discover_site_description(website: str) -> Optional[str]:
    """The business's own one-line self-description: the homepage's meta /
    og:description tag. Same posture as email discovery — the business's own
    public site only, robots honored, single page, best-effort."""
    settings = get_settings()
    if not settings.lead_finder_crawl_enabled:
        return None
    domain = normalize_domain(website)
    if not domain:
        return None
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
        robots.parse([])
    if not robots.can_fetch(USER_AGENT, base + "/"):
        return None
    try:
        resp = httpx.get(
            base + "/",
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200 or "text/html" not in resp.headers.get(
        "content-type", "text/html"
    ):
        return None
    return _extract_description(resp.text[:500_000])


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


# --- profile provider adapter (owner contact + firmographics) ---
#
# A second, richer adapter tier for providers that license PEOPLE data
# (decision-maker name/title/direct line) and firmographics (revenue,
# headcount, description). Same BYO-only posture as Hunter: the Organization
# connects its own key, there is no operator fallback — people-data ToS
# universally prohibit multi-tenant use of one account. Data minimization
# still applies, just with a wider allow-list: owner identity + business
# contact channels + firmographics, nothing else (no personal addresses,
# no social graphs, no personal emails).


@dataclass
class CompanyProfile:
    description: Optional[str] = None
    estimated_revenue: Optional[str] = None  # provider's printed range/figure
    employee_count: Optional[int] = None


@dataclass
class OwnerCandidate:
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    mobile_phone: Optional[str] = None


class ProfileProvider(Protocol):
    id: str

    def company_profile(self, domain: str) -> Optional[CompanyProfile]: ...

    def find_owner(self, domain: str) -> Optional[OwnerCandidate]: ...


# Titles that identify the person a marketing pitch should reach, in strict
# priority order: at a small business that's the owner/principal; at anything
# big enough to staff marketing, the marketing decision-maker outranks a
# figurehead CEO for THIS pitch. The list doubles as the provider search
# filter and the ranking key (_rank_pitch_target).
OWNER_TITLES = [
    "owner",
    "founder",
    "co-founder",
    "ceo",
    "president",
    "principal",
    "managing partner",
    "chief marketing officer",
    "cmo",
    "vp marketing",
    "vp of marketing",
    "marketing director",
    "director of marketing",
    "marketing manager",
    "general manager",
]


def _rank_pitch_target(person: dict) -> int:
    """Lower is better: index of the first OWNER_TITLES entry the person's
    title contains, so provider result order never trumps our priority."""
    title = (person.get("title") or "").lower()
    for i, wanted in enumerate(OWNER_TITLES):
        if wanted in title:
            return i
    return len(OWNER_TITLES)


class ApolloProvider:
    """Reference ProfileProvider: Apollo.io on the ORGANIZATION'S OWN key.
    Two calls per business, both licensed API endpoints: Organization
    Enrichment (firmographics) and People Search filtered to owner titles,
    followed by a People Match to unlock the matched person's work email and
    phone. Mobile availability depends on the org's Apollo plan — when the
    plan only delivers phone numbers asynchronously, the field simply comes
    back empty and everything else still lands."""

    id = "apollo"
    _BASE = "https://api.apollo.io/api/v1"

    def __init__(self, api_key: str):
        self._headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        }

    def company_profile(self, domain: str) -> Optional[CompanyProfile]:
        try:
            resp = httpx.get(
                f"{self._BASE}/organizations/enrich",
                params={"domain": domain},
                headers=self._headers,
                timeout=20,
            )
            org = resp.json().get("organization") if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
        if not org:
            return None
        revenue = org.get("annual_revenue_printed")
        if not revenue and org.get("annual_revenue"):
            revenue = _print_revenue(org["annual_revenue"])
        desc = (org.get("short_description") or "").strip() or None
        employees = org.get("estimated_num_employees")
        if not (desc or revenue or employees):
            return None
        return CompanyProfile(
            description=desc[:500] if desc else None,
            estimated_revenue=str(revenue)[:60] if revenue else None,
            employee_count=int(employees) if employees else None,
        )

    def find_owner(self, domain: str) -> Optional[OwnerCandidate]:
        try:
            resp = httpx.post(
                f"{self._BASE}/mixed_people/search",
                json={
                    "q_organization_domains_list": [domain],
                    "person_titles": OWNER_TITLES,
                    "page": 1,
                    "per_page": 5,
                },
                headers=self._headers,
                timeout=20,
            )
            people = resp.json().get("people") or [] if resp.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            return None
        if not people:
            return None
        person = min(people, key=_rank_pitch_target)
        out = OwnerCandidate(
            first_name=person.get("first_name"),
            last_name=person.get("last_name"),
            title=person.get("title"),
        )
        # Search results keep email/phone locked; Match by id unlocks what
        # the org's plan includes (and spends one of its credits).
        try:
            resp = httpx.post(
                f"{self._BASE}/people/match",
                json={"id": person.get("id"), "reveal_personal_emails": False},
                headers=self._headers,
                timeout=20,
            )
            matched = resp.json().get("person") if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            matched = None
        if matched:
            email = (matched.get("email") or "").lower()
            if email and "email_not_unlocked" not in email:
                out.email = email
            out.mobile_phone = _pick_mobile(matched.get("phone_numbers") or [])
            out.title = out.title or matched.get("title")
        return out


def _pick_mobile(phone_numbers: list) -> Optional[str]:
    """Prefer an explicit mobile, then any direct number the provider
    returned. Entries look like {"raw_number", "sanitized_number", "type"}."""
    best = None
    for row in phone_numbers:
        num = row.get("sanitized_number") or row.get("raw_number")
        if not num:
            continue
        if (row.get("type") or "").lower() == "mobile":
            return num
        best = best or num
    return best


def _print_revenue(value) -> Optional[str]:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n >= 1_000_000_000:
        return f"${n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"${n / 1_000:.0f}K"
    return f"${n:.0f}"


def profile_provider_for(provider_id: str, api_key: str) -> Optional[ProfileProvider]:
    """Adapter registry for ProfileProviders — same shape as provider_for."""
    if provider_id == "apollo" and api_key:
        return ApolloProvider(api_key)
    return None
