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

    def _search_stub(
        query, location, api_key, *, min_rating=None, open_now=False, page_token=None
    ):
        calls.append(
            {
                "query": query,
                "location": location,
                "key": api_key,
                "min_rating": min_rating,
                "open_now": open_now,
                "page_token": page_token,
            }
        )
        return _fake_results(), None

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


def _paged_results(page):
    """20 unique results for page 1, 5 for page 2 — distinct place ids."""
    n = 20 if page == 1 else 5
    return [
        PlaceResult(
            place_id=f"pl_p{page}_{i}",
            name=f"Biz {page}-{i}",
            address="1 Main St, Mesa, AZ",
            phone=f"(480) 555-{page}{i:03d}",
            website=f"https://biz{page}{i}.example.com",
            rating=4.0,
            types=["hvac_contractor"],
        )
        for i in range(n)
    ]


def test_search_pagination_meters_each_page(
    api, team_headers, monkeypatch, places_key
):
    """max_results > 20 loops pages; the ledger row records pages_fetched and
    the monthly quota counts pages, not searches."""
    calls = []

    def _stub(query, location, api_key, *, min_rating=None, open_now=False, page_token=None):
        calls.append(page_token)
        if page_token is None:
            return _paged_results(1), "tok-page-2"
        return _paged_results(2), None

    monkeypatch.setattr(places, "search_text", _stub)
    before = api.get("/api/lead-finder/usage", headers=team_headers).json()[
        "searches"
    ]["used"]
    r = api.post(
        "/api/lead-finder/search",
        json={"query": "HVAC contractors", "location": "Mesa AZ", "max_results": 40},
        headers=team_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert calls == [None, "tok-page-2"]  # identical params, token threaded
    assert len(body["results"]) == 25
    assert body["pages_fetched"] == 2
    assert body["quota_clamped"] is False
    assert body["usage"]["used"] == before + 2
    db = SessionLocal()
    row = db.get(LeadFinderSearch, body["search_id"])
    assert row.pages_fetched == 2 and row.results_count == 25
    db.close()


def test_search_filters_pass_through(api, team_headers, fake_places, places_key):
    r = api.post(
        "/api/lead-finder/search",
        json={
            "query": "HVAC contractors",
            "location": "Mesa AZ",
            "min_rating": 4.0,
            "open_now": True,
        },
        headers=team_headers,
    )
    assert r.status_code == 200, r.text
    assert fake_places[-1]["min_rating"] == 4.0
    assert fake_places[-1]["open_now"] is True


def test_search_quota_clamps_pages_not_402(api, monkeypatch, places_key):
    """With 1 page left this month, a 60-result request returns one page and
    flags the clamp instead of refusing the whole search."""
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Clamp Co",
            "email": "owner@clampco.com",
            "password": "clamp-pass-123",
            "full_name": "C",
        },
    )
    assert r.status_code == 201
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    org_id = r.json()["organization_id"]
    db = SessionLocal()
    for i in range(39):  # starter cap is 40 pages/month → 1 left
        db.add(LeadFinderSearch(organization_id=org_id, query=f"q{i}"))
    db.commit()
    db.close()

    calls = []

    def _stub(query, location, api_key, *, min_rating=None, open_now=False, page_token=None):
        calls.append(page_token)
        return _paged_results(1), "tok-page-2"

    monkeypatch.setattr(places, "search_text", _stub)
    r = api.post(
        "/api/lead-finder/search",
        json={"query": "plumbers", "location": "Tempe AZ", "max_results": 60},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert calls == [None]  # only the one page the quota allowed
    assert body["pages_fetched"] == 1
    assert body["quota_clamped"] is True
    assert body["usage"]["used"] == 40  # now at cap


def test_search_mid_pagination_error_keeps_paid_results(
    api, team_headers, monkeypatch, places_key
):
    """A Places failure on page 2 must not throw away billed page-1 results."""

    def _stub(query, location, api_key, *, min_rating=None, open_now=False, page_token=None):
        if page_token is None:
            return _paged_results(1), "tok-page-2"
        raise places.PlacesError("boom")

    monkeypatch.setattr(places, "search_text", _stub)
    r = api.post(
        "/api/lead-finder/search",
        json={"query": "HVAC contractors", "location": "Mesa AZ", "max_results": 40},
        headers=team_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["results"]) == 20
    assert body["pages_fetched"] == 1


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


# --- profile enrichment (owner contact + firmographics) ---


class _FakeApollo:
    """Stands in for enrichment.ApolloProvider — constructed by
    profile_provider_for with the org's resolved key."""

    id = "apollo"

    def __init__(self, api_key):
        self.api_key = api_key

    def company_profile(self, domain):
        from app.services.enrichment import CompanyProfile

        return CompanyProfile(
            description="Family-owned HVAC contractor serving the East Valley.",
            estimated_revenue="$2.5M",
            employee_count=12,
        )

    def find_owner(self, domain):
        from app.services.enrichment import OwnerCandidate

        return OwnerCandidate(
            first_name="Dana",
            last_name="Ruiz",
            title="Owner",
            email="dana@profiletarget.com",
            mobile_phone="+14805559999",
        )


def test_profile_enrichment_pipeline(api, lf_org, monkeypatch):
    """With an org Apollo key connected, the pipeline fills owner identity
    (replacing the business-name placeholder), the owner's mobile, the owner
    email as the top candidate, and company firmographics — and everything
    lands in the team contact payload."""
    from app.models.crm import Company

    db = SessionLocal()
    company = Company(
        organization_id=lf_org["org"],
        client_id=lf_org["client"],
        name="Profile Target Co",
        domain="profiletarget.com",
    )
    db.add(company)
    db.flush()
    contact = Contact(
        organization_id=lf_org["org"],
        client_id=lf_org["client"],
        company_id=company.id,
        first_name="Profile Target Co",  # Lead Finder placeholder
        source="lead_finder",
        source_external_id="pl_profile_1",
        source_detail={"website": "https://profiletarget.com"},
    )
    db.add(contact)
    db.commit()
    cid = contact.id
    db.close()

    r = api.put(
        "/api/lead-finder/providers/apollo",
        json={"api_key": "org-own-apollo-key"},
        headers=lf_org["headers"],
    )
    assert r.status_code == 200, r.text
    monkeypatch.setattr("app.services.enrichment.ApolloProvider", _FakeApollo)
    monkeypatch.setattr(
        "app.services.enrichment.discover_site_emails",
        lambda website: ["info@profiletarget.com"],
    )
    monkeypatch.setattr(
        "app.services.enrichment.discover_site_description", lambda website: None
    )
    fake = _FakeVerifier(verdicts={"dana@profiletarget.com": "valid"})
    monkeypatch.setattr(
        "app.services.email_verification.resolve_provider", lambda db, org_id: fake
    )

    lead_finder_svc.enrich_and_verify(lf_org["org"], [cid])

    db = SessionLocal()
    c = db.get(Contact, cid)
    assert (c.first_name, c.last_name) == ("Dana", "Ruiz")
    assert c.mobile_phone == "+14805559999"
    assert c.job_title == "Owner"
    assert c.source_detail["owner_title"] == "Owner"
    # Owner's own address outranks the generic site address…
    assert c.email == "dana@profiletarget.com"
    assert [row["email"] for row in c.candidate_emails] == [
        "dana@profiletarget.com",
        "info@profiletarget.com",
    ]
    assert c.candidate_emails[0]["source"] == "provider:apollo"
    assert c.verification_status == "valid"
    co = db.get(Company, c.company_id)
    assert co.description.startswith("Family-owned")
    assert co.estimated_revenue == "$2.5M"
    assert co.employee_count == 12
    db.close()

    # …and the team payload carries all of it.
    detail = api.get(f"/api/crm/contacts/{cid}", headers=lf_org["headers"]).json()
    assert detail["job_title"] == "Owner"
    assert detail["mobile_phone"] == "+14805559999"
    assert detail["company_estimated_revenue"] == "$2.5M"
    assert detail["company_employee_count"] == 12
    assert detail["company_description"].startswith("Family-owned")

    # A typed-in name is never overwritten by a later enrichment pass.
    r = api.patch(
        f"/api/crm/contacts/{cid}",
        json={"first_name": "Dana-Marie", "mobile_phone": "+14805550000"},
        headers=lf_org["headers"],
    )
    assert r.status_code == 200, r.text
    lead_finder_svc.enrich_and_verify(lf_org["org"], [cid])
    db = SessionLocal()
    c = db.get(Contact, cid)
    assert c.first_name == "Dana-Marie"
    assert c.mobile_phone == "+14805550000"
    db.close()

    api.delete("/api/lead-finder/providers/apollo", headers=lf_org["headers"])


def test_pitch_target_ranking():
    """Provider result order never trumps our priority list: the owner beats a
    marketing manager, a marketing decision-maker beats an unmatched title."""
    from app.services.enrichment import _rank_pitch_target

    people = [
        {"title": "Office Administrator"},
        {"title": "Marketing Manager"},
        {"title": "Owner & CEO"},
    ]
    best = min(people, key=_rank_pitch_target)
    assert best["title"] == "Owner & CEO"

    people = [
        {"title": "Regional Sales Lead"},
        {"title": "Director of Marketing"},
    ]
    best = min(people, key=_rank_pitch_target)
    assert best["title"] == "Director of Marketing"

    # No title at all ranks last, never crashes.
    assert _rank_pitch_target({}) > _rank_pitch_target({"title": "Owner"})


def test_site_description_without_provider(api, lf_org, monkeypatch):
    """No profile provider connected: the company description still fills
    from the business's own site meta description; names stay untouched."""
    from app.models.crm import Company

    db = SessionLocal()
    company = Company(
        organization_id=lf_org["org"],
        client_id=lf_org["client"],
        name="Site Desc Co",
        domain="sitedesc.com",
    )
    db.add(company)
    db.flush()
    contact = Contact(
        organization_id=lf_org["org"],
        client_id=lf_org["client"],
        company_id=company.id,
        first_name="Site Desc Co",
        source="lead_finder",
        source_external_id="pl_sitedesc_1",
        source_detail={"website": "https://sitedesc.com"},
    )
    db.add(contact)
    db.commit()
    cid = contact.id
    db.close()

    monkeypatch.setattr(
        "app.services.enrichment.discover_site_description",
        lambda website: "Plumbing done right since 1998.",
    )
    monkeypatch.setattr(
        "app.services.enrichment.discover_site_emails", lambda website: []
    )
    lead_finder_svc.enrich_and_verify(lf_org["org"], [cid])

    db = SessionLocal()
    c = db.get(Contact, cid)
    assert c.first_name == "Site Desc Co"  # no provider → no owner rewrite
    assert c.mobile_phone is None
    co = db.get(Company, c.company_id)
    assert co.description == "Plumbing done right since 1998."
    assert co.estimated_revenue is None
    db.close()


def test_meta_description_extraction():
    from app.services.enrichment import _extract_description

    html = (
        "<html><head>"
        '<meta property="og:description" content="Award-winning &amp; local HVAC pros." />'
        "</head><body>hi</body></html>"
    )
    assert _extract_description(html) == "Award-winning & local HVAC pros."
    html_rev = (
        '<head><meta content="Reversed attribute order works too, promise."'
        ' name="description"></head>'
    )
    assert (
        _extract_description(html_rev)
        == "Reversed attribute order works too, promise."
    )
    assert _extract_description("<html><head></head></html>") is None


def test_apollo_listed_as_provider(api, team_headers):
    listing = {
        p["provider"]
        for p in api.get("/api/lead-finder/providers", headers=team_headers).json()
    }
    assert "apollo" in listing
