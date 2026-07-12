"""Phase 12 Part A — Lead Finder: dedupe, import, and the post-import
enrich→verify pipeline.

Found businesses land in the CRM as contacts (default target: the org's
house CRM client — the agency's own prospect pipeline), with
source="lead_finder", the search query stored on source_detail for
attribution, and the Places place_id as source_external_id (the one Places
field Google allows storing indefinitely; it also makes re-imports
idempotent via the existing (client_id, source_external_id) unique
constraint).

Dedupe (task 4) is checked against the WHOLE Organization's CRM, not just
the target client, on normalized keys — phone digits, website domain, and
casefolded business name — so "already in your CRM" is shown inline on
results instead of silently skipping on exact strings.
"""

import datetime as dt
import logging
import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.core import Client, Organization
from ..models.crm import Company, Contact
from .enrichment import normalize_domain
from .lead_ingest import _digits
from .places import PlaceResult

log = logging.getLogger("salescale.lead_finder")

SOURCE_LEAD_FINDER = "lead_finder"


def _norm_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    cleaned = re.sub(r"[^a-z0-9 ]", "", name.lower())
    return re.sub(r"\s+", " ", cleaned).strip() or None


class OrgCrmIndex:
    """Normalized-key snapshot of an Organization's CRM for dedupe marking:
    phone digits and place ids from contacts, domains from companies and
    contact email domains, business names from companies and contacts."""

    def __init__(self, db: Session, organization_id: str):
        self.phones: Set[str] = set()
        self.domains: Set[str] = set()
        self.names: Set[str] = set()
        self.place_ids: Set[str] = set()
        contacts = db.execute(
            select(
                Contact.phone,
                Contact.email,
                Contact.first_name,
                Contact.last_name,
                Contact.source,
                Contact.source_external_id,
            ).where(Contact.organization_id == organization_id)
        ).all()
        for phone, email, first, last, source, ext_id in contacts:
            digits = _digits(phone or "")
            if len(digits) >= 7:
                self.phones.add(digits[-10:])
            if email and "@" in email:
                self.domains.add(email.split("@", 1)[1].lower())
            name = _norm_name(" ".join(p for p in [first, last] if p))
            if name:
                self.names.add(name)
            if source == SOURCE_LEAD_FINDER and ext_id:
                self.place_ids.add(ext_id)
        companies = db.execute(
            select(Company.name, Company.domain, Company.phone).where(
                Company.organization_id == organization_id
            )
        ).all()
        for name, domain, phone in companies:
            norm = _norm_name(name)
            if norm:
                self.names.add(norm)
            dom = normalize_domain(domain)
            if dom:
                self.domains.add(dom)
            digits = _digits(phone or "")
            if len(digits) >= 7:
                self.phones.add(digits[-10:])

    def matches(self, place: PlaceResult) -> bool:
        if place.place_id in self.place_ids:
            return True
        digits = _digits(place.phone or "")
        if len(digits) >= 7 and digits[-10:] in self.phones:
            return True
        dom = normalize_domain(place.website)
        if dom and dom in self.domains:
            return True
        # Name alone is a weak key — require it to be reasonably specific
        # before it counts as "already in your CRM".
        norm = _norm_name(place.name)
        if norm and len(norm) >= 5 and norm in self.names:
            return True
        return False


def import_places(
    db: Session,
    org: Organization,
    client: Client,
    places: Iterable[PlaceResult],
    *,
    search_id: str,
    query: str,
    user_id: Optional[str] = None,
) -> Tuple[List[Contact], List[Dict[str, str]]]:
    """Create org-scoped CRM contacts (+ linked companies) for the selected
    Places results. Returns (created_contacts, skipped) where skipped rows
    carry the reason (already imported by place_id — the idempotency key).
    Contacts enter unverified; the enrich→verify pipeline runs after."""
    created: List[Contact] = []
    skipped: List[Dict[str, str]] = []
    for place in places:
        existing = db.execute(
            select(Contact).where(
                Contact.organization_id == org.id,
                Contact.client_id == client.id,
                Contact.source_external_id == place.place_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            skipped.append({"place_id": place.place_id, "reason": "already_imported"})
            continue
        domain = normalize_domain(place.website)
        company = Company(
            organization_id=org.id,
            client_id=client.id,
            name=place.name,
            domain=domain,
            phone=place.phone,
        )
        db.add(company)
        db.flush()
        contact = Contact(
            organization_id=org.id,
            client_id=client.id,
            company_id=company.id,
            first_name=place.name,
            phone=place.phone,
            source=SOURCE_LEAD_FINDER,
            source_external_id=place.place_id,
            source_detail={
                "search_id": search_id,
                "query": query,
                "address": place.address,
                "website": place.website,
                "rating": place.rating,
                "types": place.types[:5],
            },
        )
        db.add(contact)
        db.flush()
        created.append(contact)
    return created, skipped


def enrich_and_verify(organization_id: str, contact_ids: List[str]) -> None:
    """Background pipeline (task 11): website email + description discovery →
    optional licensed-provider enrichment (emails via Hunter; owner contact +
    firmographics via the org's profile provider) → verification. Runs
    post-response with its own session; every step is best-effort and
    quota-respecting — a failure or exhausted quota leaves contacts
    unverified, never half-written."""
    from fastapi import HTTPException

    from ..db import SessionLocal
    from . import email_verification, integration_creds
    from . import enrichment as enrichment_mod
    from .enrichment import discover_site_emails, provider_for

    db = SessionLocal()
    try:
        org = db.get(Organization, organization_id)
        if org is None:
            return
        contacts = (
            db.execute(
                select(Contact).where(
                    Contact.organization_id == organization_id,
                    Contact.id.in_(contact_ids) if contact_ids else False,
                )
            )
            .scalars()
            .all()
        )
        hunter_key = integration_creds.resolve_key(db, organization_id, "hunter")
        provider = provider_for("hunter", hunter_key)
        apollo_key = integration_creds.resolve_key(db, organization_id, "apollo")
        profile_provider = enrichment_mod.profile_provider_for("apollo", apollo_key)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for c in contacts:
            website = (c.source_detail or {}).get("website")
            domain = normalize_domain(website)
            company = db.get(Company, c.company_id) if c.company_id else None

            # -- company profile: own-site description first (free, every
            # org), then the licensed profile provider for firmographics.
            # Enrichment only ever fills blanks — a human edit wins.
            if company is not None and website and not company.description:
                desc = enrichment_mod.discover_site_description(website)
                if desc:
                    company.description = desc
            if profile_provider is not None and domain and company is not None:
                profile = profile_provider.company_profile(domain)
                if profile is not None:
                    if profile.description and not company.description:
                        company.description = profile.description
                    if profile.estimated_revenue and not company.estimated_revenue:
                        company.estimated_revenue = profile.estimated_revenue
                    if profile.employee_count and not company.employee_count:
                        company.employee_count = profile.employee_count

            # -- owner identity + direct line from the profile provider.
            owner_email: str | None = None
            if profile_provider is not None and domain:
                owner = profile_provider.find_owner(domain)
                if owner is not None:
                    # Lead Finder imports park the business name in
                    # first_name; a real owner name replaces that
                    # placeholder but never a name someone typed in.
                    is_placeholder = (
                        company is not None
                        and c.first_name == company.name
                        and not c.last_name
                    )
                    if owner.first_name and (not c.first_name or is_placeholder):
                        c.first_name = owner.first_name
                        c.last_name = owner.last_name
                    if owner.mobile_phone and not c.mobile_phone:
                        c.mobile_phone = owner.mobile_phone
                    if owner.title and not c.job_title:
                        c.job_title = owner.title
                    if owner.title:
                        c.source_detail = {
                            **(c.source_detail or {}),
                            "owner_title": owner.title,
                        }
                    owner_email = owner.email

            if c.email or not website:
                continue
            candidates = [
                {"email": e, "source": "website", "found_at": now}
                for e in discover_site_emails(website)
            ]
            if owner_email and owner_email not in {r["email"] for r in candidates}:
                # The owner's own address is the best outreach target —
                # ahead of generic info@ found on the site.
                candidates.insert(
                    0,
                    {
                        "email": owner_email,
                        "source": f"provider:{profile_provider.id}",
                        "found_at": now,
                    },
                )
            if provider is not None and domain:
                seen = {row["email"] for row in candidates}
                for cand in provider.find_contacts(domain, c.first_name):
                    if cand.email not in seen:
                        candidates.append(
                            {
                                "email": cand.email,
                                "source": "provider:hunter",
                                "found_at": now,
                            }
                        )
            if candidates:
                c.candidate_emails = candidates
                # Promote the best candidate to the contact email — still
                # strictly unverified until Part C stamps a verdict.
                c.email = candidates[0]["email"]
                email_verification.reset_status(c)
        db.commit()
        try:
            email_verification.verify_contacts(db, org, contacts)
            db.commit()
        except HTTPException as e:
            db.rollback()
            log.info(
                "verification skipped for org %s: %s", organization_id, e.detail
            )
    except Exception:
        db.rollback()
        log.exception("lead finder enrich/verify pipeline failed")
    finally:
        db.close()
