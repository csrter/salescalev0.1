"""Phase 12 — Lead Finder API.

Search (Google Places Text Search, metered per org per month), import into
the CRM (dedupe-marked, source=lead_finder, background enrich→verify), the
self-service usage view, and BYO provider keys.

Search results are returned for display and never cached server-side —
only the query text + result count are recorded (lead_finder_searches),
plus place IDs of imported businesses, per Google's caching policy (place
IDs are the one field storable indefinitely). Guardrail 6: every byte of
lead data here comes from the licensed Places API, the business's own
website, or the org's own enrichment-provider key. Nothing touches any
Meta surface.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope, require_admin, require_team
from ..models.base import utcnow
from ..models.core import Client, Organization, User
from ..models.integrations import IntegrationCredential
from ..models.lead_finder import LeadFinderSearch
from ..ratelimit import enforce_bucket
from ..security import encrypt_secret
from ..services import entitlements, integration_creds, places
from ..services import lead_finder as lead_finder_svc

router = APIRouter(prefix="/api/lead-finder", tags=["lead-finder"])


class SearchIn(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    location: Optional[str] = Field(default=None, max_length=300)


class PlaceOut(BaseModel):
    place_id: str
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    rating: Optional[float] = None
    types: List[str] = []
    in_crm: bool = False


class SearchOut(BaseModel):
    search_id: str
    results: List[PlaceOut]
    usage: Dict[str, Any]


class ImportIn(BaseModel):
    search_id: str
    client_id: str
    places: List[PlaceOut] = Field(max_length=places.MAX_RESULTS)


class ProviderStatusOut(BaseModel):
    provider: str
    configured: bool
    source: str  # organization | global | none


class ProviderKeyIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=500)


def _org(db: Session, user: User) -> Organization:
    return db.get(Organization, user.organization_id)


@router.post("/search", response_model=SearchOut)
def search(
    body: SearchIn,
    user: User = Depends(require_team),
    db: Session = Depends(get_db),
):
    """One metered Places Text Search. The ledger row is written even when
    zero results come back — Google bills the request either way, so the
    quota must count it either way."""
    org = _org(db, user)
    entitlements.enforce_can_search_leads(db, org)
    # Per-org burst brake on top of the monthly quota (each search is a paid
    # upstream call; the monthly meter alone would allow it all in one minute).
    enforce_bucket(f"lead_finder:{org.id}", limit=10, window_seconds=60)
    api_key = integration_creds.resolve_key(db, org.id, "google_places")
    try:
        found = places.search_text(body.query, body.location, api_key)
    except places.PlacesNotConfigured as e:
        raise HTTPException(503, str(e))
    except places.PlacesError as e:
        raise HTTPException(502, f"Google Places error: {e}")
    row = LeadFinderSearch(
        organization_id=org.id,
        user_id=user.id,
        query=body.query.strip(),
        location=(body.location or "").strip() or None,
        results_count=len(found),
    )
    db.add(row)
    db.commit()
    index = lead_finder_svc.OrgCrmIndex(db, org.id)
    results = [
        PlaceOut(
            place_id=p.place_id,
            name=p.name,
            address=p.address,
            phone=p.phone,
            website=p.website,
            rating=p.rating,
            types=p.types,
            in_crm=index.matches(p),
        )
        for p in found
    ]
    return SearchOut(
        search_id=row.id,
        results=results,
        usage=entitlements.lead_finder_usage(db, org),
    )


@router.post("/import")
def import_selected(
    body: ImportIn,
    background: BackgroundTasks,
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    """Import selected results as CRM contacts (source=lead_finder, search
    query attached for attribution), then kick off the enrich→verify
    pipeline after the response. Idempotent per place_id."""
    org = _org(db, user)
    search_row = db.get(LeadFinderSearch, body.search_id)
    if search_row is None or search_row.organization_id != org.id:
        raise HTTPException(404, "Not found")
    client = db.get(Client, body.client_id)
    if client is None:
        raise HTTPException(404, "Not found")
    scope.check_organization_id(client.organization_id)
    selected = [
        places.PlaceResult(
            place_id=p.place_id,
            name=p.name,
            address=p.address,
            phone=p.phone,
            website=p.website,
            rating=p.rating,
            types=p.types,
        )
        for p in body.places
    ]
    created, skipped = lead_finder_svc.import_places(
        db,
        org,
        client,
        selected,
        search_id=search_row.id,
        query=search_row.query,
        user_id=user.id,
    )
    db.commit()
    if created:
        background.add_task(
            lead_finder_svc.enrich_and_verify, org.id, [c.id for c in created]
        )
    return {
        "created": len(created),
        "contact_ids": [c.id for c in created],
        "skipped": skipped,
    }


@router.get("/usage")
def usage(user: User = Depends(require_team), db: Session = Depends(get_db)):
    """Self-service "X of Y used" (guardrail 5) for both Phase 12 meters."""
    org = _org(db, user)
    return {
        "searches": entitlements.lead_finder_usage(db, org),
        "verifications": entitlements.email_verification_usage(db, org),
        "plan": org.plan,
    }


# --- BYO provider keys (google_places, hunter, zerobounce) ---

_PROVIDERS = tuple(integration_creds.KEY_PROVIDERS)


def _provider_status(db: Session, org_id: str, provider: str) -> ProviderStatusOut:
    source = integration_creds.key_source(db, org_id, provider)
    return ProviderStatusOut(
        provider=provider, configured=source != "none", source=source
    )


@router.get("/providers", response_model=List[ProviderStatusOut])
def list_providers(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_provider_status(db, user.organization_id, p) for p in _PROVIDERS]


@router.put("/providers/{provider}", response_model=ProviderStatusOut)
def set_provider_key(
    provider: str,
    body: ProviderKeyIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Store the org's own key, write-only (encrypted at rest, never returned)
    — same posture as /api/integrations."""
    if provider not in _PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    row = db.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.organization_id == user.organization_id,
            IntegrationCredential.provider == provider,
        )
    ).scalar_one_or_none()
    if row is None:
        row = IntegrationCredential(
            organization_id=user.organization_id, provider=provider
        )
        db.add(row)
    row.secret_encrypted = encrypt_secret(body.api_key.strip())
    row.updated_at = utcnow()
    db.commit()
    return _provider_status(db, user.organization_id, provider)


@router.delete("/providers/{provider}", response_model=ProviderStatusOut)
def delete_provider_key(
    provider: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if provider not in _PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    row = db.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.organization_id == user.organization_id,
            IntegrationCredential.provider == provider,
        )
    ).scalar_one_or_none()
    if row is not None:
        db.delete(row)
        db.commit()
    return _provider_status(db, user.organization_id, provider)
