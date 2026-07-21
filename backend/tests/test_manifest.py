"""Host-resolved PWA manifest (api/branding.py pwa_manifest).

The frontend's nginx proxies /manifest.webmanifest to this endpoint with the
Host header forwarded, so an agency's verified custom domain serves its
white-label name/colors on the home-screen install — and everything else
(unknown hosts, unverified claims) gets the neutral Salescale manifest,
never a hint that a domain is claimed.
"""

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.core import Organization

PW = "manifest-pass-123"


def _signup(api, org, email):
    r = api.post(
        "/api/orgs/signup",
        json={"organization_name": org, "email": email, "password": PW, "full_name": "M"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_default_manifest_for_unknown_host(api):
    r = api.get("/api/branding/manifest", params={"host": "app.salescale.lol"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert m["name"] == "Salescale"
    assert m["display"] == "standalone"
    assert m["theme_color"] == "#0f2147"
    assert any(i.get("purpose") == "maskable" for i in m["icons"])


def test_verified_custom_domain_serves_tenant_manifest(api):
    b = _signup(api, "Manifest Agency", "owner@manifestagency.com")
    db = SessionLocal()
    org = db.get(Organization, b["organization_id"])
    org.branding = {
        "product_name": "Atlas Reach Portal",
        "colors": {"header_start": "#112244"},
    }
    org.custom_domain = "portal.manifestagency.com"
    org.custom_domain_token = "tok-manifest-test"
    org.custom_domain_verified_at = utcnow()
    db.commit()
    db.close()

    r = api.get("/api/branding/manifest", params={"host": "portal.manifestagency.com"})
    assert r.status_code == 200
    m = r.json()
    assert m["name"] == "Atlas Reach Portal"
    assert len(m["short_name"]) <= 12
    assert m["theme_color"] == "#112244"
    assert m["background_color"] == "#112244"


def test_unverified_domain_stays_neutral(api):
    b = _signup(api, "Unverified Co", "owner@unverifiedco.com")
    db = SessionLocal()
    org = db.get(Organization, b["organization_id"])
    org.branding = {"product_name": "Should Not Leak"}
    org.custom_domain = "portal.unverifiedco.com"
    org.custom_domain_token = "tok-unverified"
    org.custom_domain_verified_at = None  # claimed, never verified
    db.commit()
    db.close()

    r = api.get("/api/branding/manifest", params={"host": "portal.unverifiedco.com"})
    assert r.status_code == 200
    assert r.json()["name"] == "Salescale"
