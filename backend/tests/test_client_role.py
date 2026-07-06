"""Definition-of-Done: the Client role is genuinely read-only and cannot see
Atlas Reach-internal fields — enforced by the API, not hidden UI."""


def test_client_cannot_create_clients(api, client_a_headers):
    resp = api.post(
        "/api/clients", json={"name": "Sneaky Client"}, headers=client_a_headers
    )
    assert resp.status_code == 403


def test_client_cannot_start_oauth_flows(api, client_a_headers, seeded):
    # Connecting/reauthorizing ad accounts is a team-only write action.
    for platform in ("meta", "google"):
        resp = api.get(
            f"/api/connect/{platform}/start?client_id={seeded['client_a']}",
            headers=client_a_headers,
        )
        assert resp.status_code == 403, platform


def test_internal_notes_absent_for_client_role(api, client_a_headers, seeded):
    resp = api.get(f"/api/clients/{seeded['client_a']}", headers=client_a_headers)
    assert resp.status_code == 200
    body = resp.json()
    # The key must be absent entirely (schema-level), not just null.
    assert "internal_notes" not in body
    assert "INTERNAL" not in resp.text


def test_internal_notes_present_for_team(api, team_headers, seeded):
    resp = api.get(f"/api/clients/{seeded['client_a']}", headers=team_headers)
    assert resp.status_code == 200
    assert resp.json()["internal_notes"].startswith("INTERNAL")


def test_client_list_serialization_excludes_internal(api, client_a_headers):
    resp = api.get("/api/clients", headers=client_a_headers)
    assert all("internal_notes" not in c for c in resp.json())
