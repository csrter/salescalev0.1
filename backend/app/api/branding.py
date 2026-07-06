"""Phase 9 white-labeling endpoints.

Two surfaces:
- Public: GET /api/branding/resolve — what a login page shows for a given
  hostname. Returns only the client-safe branding slice; an unknown or
  unverified host gets the neutral default (never a hint about tenants).
- Organization (team/admin): branding config and custom-domain lifecycle
  under /api/orgs/me/*, all gated through the Phase 8 entitlement seam
  (entitlements.can_use_white_labeling) — one function to wire tiers into.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin, require_team
from ..models.base import utcnow
from ..models.core import Organization, User
from ..schemas import BrandingIn, CustomDomainIn
from ..services import branding, entitlements

router = APIRouter(tags=["branding"])


@router.get("/api/branding/resolve")
def resolve_branding(request: Request, host: str = "", db: Session = Depends(get_db)):
    """Public branding for a hostname (the ?host= override exists for the
    dev setup, where the SPA is served from localhost). Verified custom
    domains resolve to their Organization; everything else is the neutral
    default — resolution must never leak that a domain has been *claimed*.
    """
    lookup = host or request.headers.get("host", "")
    org = branding.resolve_for_host(db, lookup)
    return branding.public_branding(org)


def _org_for(db: Session, user: User) -> Organization:
    return db.get(Organization, user.organization_id)


def _require_white_labeling(org: Organization) -> None:
    # The single Phase 8 seam — today always allowed, later tier-gated.
    if not entitlements.can_use_white_labeling(org):
        raise HTTPException(403, "White-labeling is not enabled for this plan")


@router.get("/api/orgs/me/branding")
def get_branding(user: User = Depends(require_team), db: Session = Depends(get_db)):
    """Full branding config + domain status. Team-only: email sender and
    domain verification state are Organization-internal."""
    org = _org_for(db, user)
    return {
        "branding": branding.merged(org),
        "custom_domain": {
            "domain": org.custom_domain,
            "verified": org.custom_domain_verified_at is not None,
            "verification_token": org.custom_domain_token,
            "txt_record_name": (
                f"{branding.DNS_VERIFY_PREFIX}.{org.custom_domain}"
                if org.custom_domain
                else None
            ),
        },
        "white_labeling_available": entitlements.can_use_white_labeling(org),
    }


@router.put("/api/orgs/me/branding")
def set_branding(
    body: BrandingIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    org = _org_for(db, user)
    _require_white_labeling(org)
    payload = body.model_dump()
    error = branding.validate_branding(payload)
    if error:
        raise HTTPException(400, error)
    org.branding = payload
    db.commit()
    return {"branding": branding.merged(org)}


@router.delete("/api/orgs/me/branding", status_code=204)
def clear_branding(
    user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    org = _org_for(db, user)
    org.branding = None
    db.commit()


@router.put("/api/orgs/me/custom-domain")
def set_custom_domain(
    body: CustomDomainIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Claim a hostname. Claiming issues a fresh verification token and
    resets verified state — pointing the domain elsewhere always requires
    re-proving control."""
    org = _org_for(db, user)
    _require_white_labeling(org)
    domain = branding.normalize_domain(body.domain)
    if not branding.is_valid_domain(domain):
        raise HTTPException(400, "Not a valid domain name")
    taken = db.execute(
        select(Organization).where(
            Organization.custom_domain == domain, Organization.id != org.id
        )
    ).scalar_one_or_none()
    if taken is not None:
        # 409 without naming the other tenant.
        raise HTTPException(409, "This domain is already in use")
    org.custom_domain = domain
    org.custom_domain_token = branding.new_verification_token()
    org.custom_domain_verified_at = None
    db.commit()
    return {
        "domain": domain,
        "verified": False,
        "verification_token": org.custom_domain_token,
        "txt_record_name": f"{branding.DNS_VERIFY_PREFIX}.{domain}",
        "instructions": (
            "Create a DNS TXT record at the name above containing the "
            "verification token, plus a CNAME/A record pointing the domain "
            "at your Salescale deployment, then click Verify. TLS "
            "certificates are provisioned automatically by the hosting "
            "layer once DNS resolves."
        ),
    }


@router.post("/api/orgs/me/custom-domain/verify")
def verify_custom_domain(
    user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    org = _org_for(db, user)
    _require_white_labeling(org)
    if not org.custom_domain:
        raise HTTPException(400, "No custom domain configured")
    if branding.verify_custom_domain(org):
        org.custom_domain_verified_at = utcnow()
        db.commit()
        return {"domain": org.custom_domain, "verified": True}
    return {
        "domain": org.custom_domain,
        "verified": False,
        "detail": (
            f"TXT record at {branding.DNS_VERIFY_PREFIX}.{org.custom_domain} "
            "does not (yet) contain the verification token — DNS changes can "
            "take a while to propagate."
        ),
    }


@router.delete("/api/orgs/me/custom-domain", status_code=204)
def clear_custom_domain(
    user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    org = _org_for(db, user)
    org.custom_domain = None
    org.custom_domain_token = None
    org.custom_domain_verified_at = None
    db.commit()
