"""Definition-of-Done (the single most important check in Phase 1): a second
Organization created through the same generic signup flow as tenant #1 must
have no visibility into tenant #1's data — via list endpoints, direct-id
access, nested paths, or query filters."""


def test_signup_creates_isolated_org(org2, seeded):
    assert org2["organization_id"] != seeded["org"]


def test_org2_client_list_excludes_org1(api, org2, seeded):
    resp = api.get("/api/clients", headers=org2["headers"])
    assert resp.status_code == 200
    ids = {c["id"] for c in resp.json()}
    assert ids == {org2["client_id"]}
    assert seeded["client_a"] not in ids and seeded["client_b"] not in ids


def test_org2_cannot_fetch_org1_client_by_id(api, org2_headers, seeded):
    # 404, not 403 — existence of other Organizations' objects is not leaked.
    resp = api.get(f"/api/clients/{seeded['client_a']}", headers=org2_headers)
    assert resp.status_code == 404


def test_org2_ad_account_list_empty(api, org2_headers, seeded):
    resp = api.get("/api/ad-accounts", headers=org2_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_org2_cannot_browse_org1_campaigns(api, org2_headers, seeded):
    resp = api.get(
        f"/api/ad-accounts/{seeded['acct_a']}/campaigns?refresh=false",
        headers=org2_headers,
    )
    assert resp.status_code == 404


def test_org2_cannot_filter_into_org1(api, org2_headers, seeded):
    # An explicit ?client_id= pointing at another Organization's client must
    # not widen scope — the org filter still applies.
    resp = api.get(
        f"/api/ad-accounts?client_id={seeded['client_a']}", headers=org2_headers
    )
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        assert resp.json() == []


def test_org2_landing_events_scoped(api, org2_headers, seeded):
    resp = api.get("/api/attribution/landing-events", headers=org2_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_org2_cannot_start_oauth_for_org1_client(api, org2_headers, seeded):
    for platform in ("meta", "google"):
        resp = api.get(
            f"/api/connect/{platform}/start?client_id={seeded['client_a']}",
            headers=org2_headers,
        )
        assert resp.status_code == 404, platform


def test_org2_cannot_list_org1_connections(api, org2_headers, seeded):
    resp = api.get(
        f"/api/clients/{seeded['client_a']}/connections", headers=org2_headers
    )
    assert resp.status_code == 404


def test_org1_cannot_see_org2_client(api, team_headers, org2):
    # Isolation cuts both ways — tenant #1 gets no special visibility.
    resp = api.get(f"/api/clients/{org2['client_id']}", headers=team_headers)
    assert resp.status_code == 404
    listed = {c["id"] for c in api.get("/api/clients", headers=team_headers).json()}
    assert org2["client_id"] not in listed


def test_org2_members_list_is_own_org_only(api, org2_headers):
    resp = api.get("/api/orgs/me/members", headers=org2_headers)
    assert resp.status_code == 200
    emails = {m["email"] for m in resp.json()}
    assert emails == {"owner@rivalagency.com"}


def test_duplicate_signup_email_rejected(api, org2):
    resp = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Copycat Agency",
            "email": "owner@rivalagency.com",
            "password": "whatever-123",
            "full_name": "Copycat",
        },
    )
    assert resp.status_code == 409


def test_org2_audit_log_scoped(api, org2_headers):
    resp = api.get("/api/audit-log", headers=org2_headers)
    assert resp.status_code == 200
    assert resp.json() == []
