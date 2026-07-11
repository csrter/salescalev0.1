"""Phase 12 — Lead Finder & email verification.

External calls (Google Places, ZeroBounce, the site crawler, Hunter) are
monkeypatched — the tests pin the pipeline around them: role gates, monthly
metering + 402s, dedupe marking, import idempotency + attribution,
enrichment candidate handling, verification statuses/ledger, the shared
outreach gate, and tenant isolation on every new table.

Contact-creating tests run against a dedicated org (lf_org), never the
seeded Atlas Reach org: the metrics suite computes lead counts and
benchmarks over Atlas Reach's clients, and imports landing there would
silently shift those numbers.
"""

import pytest

from app.db import SessionLocal
from app.models.core import Organization
from app.models.crm import Contact
from app.models.lead_finder import EmailVerificationRecord, LeadFinderSearch
from app.services import email_verification, lead_finder as lead_finder_svc, places
from app.services.enrichment import normalize_domain
from app.services.places import PlaceResult


def _fake_results():
    return [
        PlaceResult(
            place_id="pl_hvac_1",
            name="Desert Air HVAC",
            address="12 N Scottsdale Rd, Scottsdale, AZ",
            phone="(480) 555-0101",
            website="https://www.desertairhvac.com/",
            rating=4.7,
            types=["hvac_contractor"],
        ),
        PlaceResult(
            place_id="pl_hvac_2",
            name="Cactus Cooling",
            address="99 E Main St, Mesa, AZ",
            phone="(480) 555-0202",
            website="https://cactuscooling.example.net",
            rating=4.2,
            types=["hvac_contractor"],
        ),
    ]


@pytest.fixture()
def fake_places(monkeypatch):
    calls = []

    def _search_stub(query, location, api_key):
        calls.append({"query": query, "location": location, "key": api_key})
        return _fake_results()

    monkeypatch.setattr(places, "search_text", _search_stub)
    return calls


@pytest.fixture()
def places_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-places-key")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def lf_org(api):
    """Dedicated Organization for contact-creating tests (see module doc)."""
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Lead Finder Co",
            "email": "owner@leadfinderco.com",
            "password": "leadfinder-pass-1",
            "full_name": "LF",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "LF Client"}, headers=headers
    ).json()["id"]
    return {"org": body["organization_id"], "headers": headers, "client": client_id}


class _FakeVerifier:
    id = "fake"

    def __init__(self, verdicts=None):
        self.verdicts = verdicts or {}
        self.batches = []

    def verify(self, emails):
        self.batches.append(list(emails))
        return {e: self.verdicts.get(e, "valid") for e in emails}


def _search(api, headers, query="HVAC contractors", location="Scottsdale AZ"):
    return api.post(
        "/api/lead-finder/search",
        json={"query": query, "location": location},
        headers=headers,
    )


# --- Part A: search ---


def test_search_returns_results_and_meters(api, team_headers, fake_places, places_key):
    r = _search(api, team_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["results"]) == 2
    assert body["results"][0]["name"] == "Desert Air HVAC"
    assert body["usage"]["used"] >= 1
    # ledger row exists, org-scoped, stores the query — never result payloads
    db = SessionLocal()
    row = db.get(LeadFinderSearch, body["search_id"])
    assert row is not None and row.query == "HVAC contractors"
    assert row.results_count == 2
    db.close()


def test_search_client_role_forbidden(api, client_a_headers, fake_places, places_key):
    assert _search(api, client_a_headers).status_code == 403


def test_search_unconfigured_is_503(api, team_headers):
    # No org key, no global key (conftest pins operator creds empty).
    r = _search(api, team_headers, query="plumbers", location="Tempe AZ")
    assert r.status_code == 503


def test_search_quota_402_at_cap(api, fake_places, places_key):
    # Fresh starter org (cap: 40/month) — burn the quota with ledger rows
    # directly, then the next real search must 402.
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Quota Co",
            "email": "owner@quotaco.com",
            "password": "quota-pass-123",
            "full_name": "Q",
        },
    )
    assert r.status_code == 201
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    org_id = r.json()["organization_id"]
    db = SessionLocal()
    for i in range(40):
        db.add(LeadFinderSearch(organization_id=org_id, query=f"q{i}"))
    db.commit()
    db.close()
    r = _search(api, headers)
    assert r.status_code == 402, r.text
    assert "plan" in r.json()["detail"].lower()


def test_search_usage_is_tenant_scoped(api, team_headers, org2_headers):
    """Org #1's searches never appear in org #2's usage — quota isolation."""
    u1 = api.get("/api/lead-finder/usage", headers=team_headers).json()
    u2 = api.get("/api/lead-finder/usage", headers=org2_headers).json()
    assert u1["searches"]["used"] >= 1
    assert u2["searches"]["used"] == 0


# --- Part A: dedupe + import ---


def test_import_creates_lead_finder_contacts(
    api, lf_org, fake_places, places_key, monkeypatch
):
    # Keep the background pipeline synchronous-and-inert for this test.
    monkeypatch.setattr(
        "app.api.lead_finder.lead_finder_svc.enrich_and_verify", lambda *a: None
    )
    headers = lf_org["headers"]
    search = _search(api, headers).json()
    r = api.post(
        "/api/lead-finder/import",
        json={
            "search_id": search["search_id"],
            "client_id": lf_org["client"],
            "places": search["results"],
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 2
    db = SessionLocal()
    c = (
        db.query(Contact)
        .filter(
            Contact.client_id == lf_org["client"],
            Contact.source_external_id == "pl_hvac_1",
        )
        .one()
    )
    assert c.source == "lead_finder"
    assert c.source_detail["query"] == "HVAC contractors"
    assert c.source_detail["search_id"] == search["search_id"]
    assert c.verification_status == "unverified"
    assert c.organization_id == lf_org["org"]
    db.close()

    # Re-import of the same places is idempotent (place_id key), reported not
    # silently swallowed.
    r2 = api.post(
        "/api/lead-finder/import",
        json={
            "search_id": search["search_id"],
            "client_id": lf_org["client"],
            "places": search["results"],
        },
        headers=headers,
    )
    assert r2.json()["created"] == 0
    assert {s["reason"] for s in r2.json()["skipped"]} == {"already_imported"}

    # And the next search marks those businesses as already in the CRM.
    again = _search(api, headers).json()
    assert all(res["in_crm"] for res in again["results"])


def test_dedupe_matches_normalized_phone_and_domain(api, lf_org):
    """"Already in your CRM" must match on normalized keys, not exact strings:
    +1-480-555-0101 == (480) 555-0101, and a bare domain matches the full
    website URL. (Relies on the Desert Air import from the previous test.)"""
    db = SessionLocal()
    probes = [
        PlaceResult(
            place_id="pl_new",
            name="Totally New LLC",
            address=None,
            phone="+1 480 555 0101",  # same digits as imported Desert Air
            website=None,
            rating=None,
            types=[],
        ),
        PlaceResult(
            place_id="pl_new2",
            name="Another New Co",
            address=None,
            phone=None,
            website="http://desertairhvac.com/contact",  # same domain, no www
            rating=None,
            types=[],
        ),
        PlaceResult(
            place_id="pl_actually_new",
            name="Genuinely Unseen Plumbing",
            address=None,
            phone="(602) 555-9999",
            website="https://unseenplumbing.example.org",
            rating=None,
            types=[],
        ),
    ]
    index = lead_finder_svc.OrgCrmIndex(db, lf_org["org"])
    assert index.matches(probes[0]) is True
    assert index.matches(probes[1]) is True
    assert index.matches(probes[2]) is False
    db.close()


def test_import_rejects_cross_tenant_search_and_client(
    api, lf_org, org2, fake_places, places_key
):
    headers = lf_org["headers"]
    search = _search(api, headers).json()
    # org2 cannot import lf_org's search…
    r = api.post(
        "/api/lead-finder/import",
        json={
            "search_id": search["search_id"],
            "client_id": org2["client_id"],
            "places": search["results"],
        },
        headers=org2["headers"],
    )
    assert r.status_code == 404
    # …and lf_org cannot land contacts in org2's client.
    r = api.post(
        "/api/lead-finder/import",
        json={
            "search_id": search["search_id"],
            "client_id": org2["client_id"],
            "places": search["results"],
        },
        headers=headers,
    )
    assert r.status_code == 404


# --- Part B: enrichment ---


def test_normalize_domain():
    assert normalize_domain("https://www.Foo-Bar.com/contact") == "foo-bar.com"
    assert normalize_domain("foo.com") == "foo.com"
    assert normalize_domain(None) is None


def test_enrich_and_verify_pipeline(api, lf_org, monkeypatch):
    """Website discovery fills candidate_emails, promotes the first candidate
    to contact.email (still unverified), then verification stamps a verdict
    and writes ledger rows."""
    db = SessionLocal()
    contact = Contact(
        organization_id=lf_org["org"],
        client_id=lf_org["client"],
        first_name="Enrich Target Co",
        source="lead_finder",
        source_external_id="pl_enrich_1",
        source_detail={"website": "https://enrichtarget.com"},
    )
    db.add(contact)
    db.commit()
    cid = contact.id
    db.close()

    monkeypatch.setattr(
        "app.services.enrichment.discover_site_emails",
        lambda website: ["info@enrichtarget.com", "sales@enrichtarget.com"],
    )
    fake = _FakeVerifier(verdicts={"info@enrichtarget.com": "valid"})
    monkeypatch.setattr(
        "app.services.email_verification.resolve_provider", lambda db, org_id: fake
    )
    lead_finder_svc.enrich_and_verify(lf_org["org"], [cid])

    db = SessionLocal()
    c = db.get(Contact, cid)
    assert c.email == "info@enrichtarget.com"
    assert [row["email"] for row in c.candidate_emails] == [
        "info@enrichtarget.com",
        "sales@enrichtarget.com",
    ]
    assert c.verification_status == "valid"
    assert c.verified_at is not None
    ledger = (
        db.query(EmailVerificationRecord)
        .filter(EmailVerificationRecord.contact_id == cid)
        .all()
    )
    assert len(ledger) == 1 and ledger[0].result == "valid"
    assert ledger[0].organization_id == lf_org["org"]
    db.close()


# --- Part C: verification ---


def test_bulk_verify_endpoint(api, lf_org, monkeypatch):
    headers = lf_org["headers"]
    r = api.post(
        "/api/crm/contacts",
        json={
            "client_id": lf_org["client"],
            "first_name": "Verify",
            "last_name": "Me",
            "email": "verifyme@bulk.com",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    fake = _FakeVerifier(verdicts={"verifyme@bulk.com": "risky"})
    monkeypatch.setattr(
        "app.services.email_verification.resolve_provider", lambda db, org_id: fake
    )
    r = api.post(
        "/api/crm/contacts/verify",
        json={"contact_ids": [cid]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["verified"][cid]["verification_status"] == "risky"
    assert r.json()["usage"]["used"] >= 1
    # …and the badge round-trips through the contact detail read.
    detail = api.get(f"/api/crm/contacts/{cid}", headers=headers).json()
    assert detail["verification_status"] == "risky"


def test_bulk_verify_client_role_forbidden(api, client_a_headers):
    r = api.post(
        "/api/crm/contacts/verify",
        json={"contact_ids": ["whatever"]},
        headers=client_a_headers,
    )
    assert r.status_code == 403


def test_bulk_verify_cross_tenant_404(api, lf_org, org2_headers):
    victim = api.get(
        "/api/crm/contacts",
        params={"client_id": lf_org["client"]},
        headers=lf_org["headers"],
    ).json()
    assert victim, "expected lf_org contacts to exist"
    r = api.post(
        "/api/crm/contacts/verify",
        json={"contact_ids": [victim[0]["id"]]},
        headers=org2_headers,
    )
    assert r.status_code == 404


def test_verify_quota_402(api):
    """Batch metering: a request that doesn't fit the monthly cap is 402 and
    verifies nothing."""
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Verify Quota Co",
            "email": "owner@verifyquota.com",
            "password": "vquota-pass-123",
            "full_name": "V",
        },
    )
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    org_id = r.json()["organization_id"]
    client_id = api.post(
        "/api/clients", json={"name": "VQ Client"}, headers=headers
    ).json()["id"]
    contact = api.post(
        "/api/crm/contacts",
        json={"client_id": client_id, "first_name": "C", "email": "c@vq.com"},
        headers=headers,
    ).json()
    db = SessionLocal()
    for i in range(250):  # starter cap: 250/month
        db.add(
            EmailVerificationRecord(
                organization_id=org_id,
                email=f"burn{i}@vq.com",
                result="valid",
                provider="fake",
            )
        )
    db.commit()
    db.close()
    r = api.post(
        "/api/crm/contacts/verify",
        json={"contact_ids": [contact["id"]]},
        headers=headers,
    )
    assert r.status_code == 402
    detail = api.get(f"/api/crm/contacts/{contact['id']}", headers=headers).json()
    assert detail["verification_status"] == "unverified"


def test_email_change_resets_verification(api, lf_org, monkeypatch):
    headers = lf_org["headers"]
    fake = _FakeVerifier()
    monkeypatch.setattr(
        "app.services.email_verification.resolve_provider", lambda db, org_id: fake
    )
    c = api.post(
        "/api/crm/contacts",
        json={
            "client_id": lf_org["client"],
            "first_name": "Reset",
            "email": "reset1@rs.com",
        },
        headers=headers,
    ).json()
    api.post(
        "/api/crm/contacts/verify",
        json={"contact_ids": [c["id"]]},
        headers=headers,
    )
    r = api.patch(
        f"/api/crm/contacts/{c['id']}",
        json={"email": "reset2@rs.com"},
        headers=headers,
    )
    assert r.json()["verification_status"] == "unverified"
    assert r.json()["verified_at"] is None


def test_contact_list_filters_by_verification(api, lf_org):
    headers = lf_org["headers"]
    rows = api.get(
        "/api/crm/contacts",
        params={"client_id": lf_org["client"], "verification": "risky"},
        headers=headers,
    ).json()
    assert rows and all(r["verification_status"] == "risky" for r in rows)
    assert (
        api.get(
            "/api/crm/contacts",
            params={"client_id": lf_org["client"], "verification": "nonsense"},
            headers=headers,
        ).status_code
        == 400
    )


def test_csv_import_verify_flag_queues_verification(api, lf_org, monkeypatch):
    ran = {}

    def _fake_pipeline(org_id, contact_ids):
        ran["org_id"] = org_id
        ran["contact_ids"] = contact_ids

    monkeypatch.setattr(
        "app.api.crm.lead_finder_svc.enrich_and_verify", _fake_pipeline
    )
    r = api.post(
        "/api/crm/contacts/import",
        json={
            "client_id": lf_org["client"],
            "mapping": {"Email": "email", "Name": "first_name"},
            "rows": [{"Email": "csvv1@imp.com", "Name": "Csv One"}],
            "verify": True,
        },
        headers=lf_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1
    assert r.json()["verification_queued"] is True
    assert ran["org_id"] == lf_org["org"] and len(ran["contact_ids"]) == 1


# --- Part C: the shared outreach gate ---


def test_sendable_gate_excludes_invalid_and_flags_risky():
    def _c(status):
        c = Contact(organization_id="o", client_id="c", email=f"{status}@x.com")
        c.verification_status = status
        return c

    valid, invalid_c, risky_c = _c("valid"), _c("invalid"), _c("risky")
    ok, excluded, risky = email_verification.sendable([valid, invalid_c, risky_c])
    assert valid in ok and risky_c in ok
    assert excluded == [invalid_c]
    assert risky == [risky_c]
    with pytest.raises(email_verification.EmailBlockedError):
        email_verification.assert_can_email(invalid_c)
    email_verification.assert_can_email(valid)  # no raise


# --- providers (BYO keys) ---


def test_provider_keys_write_only_and_admin_gated(
    api, team_headers, member_headers, org2_headers
):
    r = api.put(
        "/api/lead-finder/providers/google_places",
        json={"api_key": "org-own-places-key"},
        headers=team_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "provider": "google_places",
        "configured": True,
        "source": "organization",
    }
    # members can't manage keys
    assert (
        api.put(
            "/api/lead-finder/providers/google_places",
            json={"api_key": "nope-nope-nope"},
            headers=member_headers,
        ).status_code
        == 403
    )
    # unknown provider 404s
    assert (
        api.put(
            "/api/lead-finder/providers/instagram_scraper",
            json={"api_key": "should-never-exist"},
            headers=team_headers,
        ).status_code
        == 404
    )
    # org #2 sees its own (unconfigured) status, not org #1's key
    listing = {
        p["provider"]: p
        for p in api.get("/api/lead-finder/providers", headers=org2_headers).json()
    }
    assert listing["google_places"]["source"] in ("none", "global")
    # cleanup so other tests see no org key
    api.delete("/api/lead-finder/providers/google_places", headers=team_headers)


# --- ZeroBounce adapter mapping (unit) ---


def test_zerobounce_status_mapping():
    m = email_verification.ZeroBounceProvider._MAP
    assert m["valid"] == "valid"
    assert m["invalid"] == "invalid"
    assert m["catch-all"] == "risky"
    assert m["do_not_mail"] == "risky"
    assert m["unknown"] == "unknown"


def test_null_provider_marks_unknown(api, lf_org):
    db = SessionLocal()
    org = db.get(Organization, lf_org["org"])
    c = Contact(
        organization_id=org.id,
        client_id=lf_org["client"],
        first_name="Nullprov",
        email="nullprov@x.com",
    )
    db.add(c)
    db.flush()
    results = email_verification.verify_contacts(db, org, [c])
    assert results[c.id] == "unknown"
    db.rollback()
    db.close()
