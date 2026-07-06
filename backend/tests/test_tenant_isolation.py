"""Definition-of-Done: one client's data must be unreachable from another
client's session — via list endpoints, direct-id access, and nested paths."""


def test_unauthenticated_requests_rejected(api):
    assert api.get("/api/clients").status_code == 401
    assert api.get("/api/ad-accounts").status_code == 401


def test_client_list_only_shows_own_client(api, client_a_headers, seeded):
    resp = api.get("/api/clients", headers=client_a_headers)
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert ids == [seeded["client_a"]]


def test_client_cannot_fetch_other_client_by_id(api, client_a_headers, seeded):
    resp = api.get(f"/api/clients/{seeded['client_b']}", headers=client_a_headers)
    # 404, not 403 — existence of other tenants' objects is not leaked
    assert resp.status_code == 404


def test_client_ad_accounts_scoped(api, client_a_headers, seeded):
    resp = api.get("/api/ad-accounts", headers=client_a_headers)
    assert resp.status_code == 200
    assert [a["id"] for a in resp.json()] == [seeded["acct_a"]]


def test_client_cannot_browse_other_clients_campaigns(
    api, client_a_headers, seeded
):
    resp = api.get(
        f"/api/ad-accounts/{seeded['acct_b']}/campaigns?refresh=false",
        headers=client_a_headers,
    )
    assert resp.status_code == 404


def test_client_cannot_filter_into_other_tenant(api, client_a_headers, seeded):
    # Explicit ?client_id= pointing at another tenant must not widen scope.
    resp = api.get(
        f"/api/ad-accounts?client_id={seeded['client_b']}",
        headers=client_a_headers,
    )
    assert resp.status_code == 404


def test_landing_events_scoped(api, client_a_headers, seeded):
    resp = api.get("/api/attribution/landing-events", headers=client_a_headers)
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) >= 1
    assert all(e["client_id"] == seeded["client_a"] for e in events)


def test_team_sees_all_clients(api, team_headers, seeded):
    resp = api.get("/api/clients", headers=team_headers)
    ids = {c["id"] for c in resp.json()}
    assert {seeded["client_a"], seeded["client_b"]} <= ids


def test_client_own_campaigns_readable(api, client_a_headers, seeded):
    resp = api.get(
        f"/api/ad-accounts/{seeded['acct_a']}/campaigns?refresh=false",
        headers=client_a_headers,
    )
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()]
    # Own campaigns visible (other fixtures may add more to this account),
    # the other tenant's campaign never.
    assert "Alpha Campaign" in names
    assert "Bravo Campaign" not in names
