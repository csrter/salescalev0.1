"""Org-level "house" CRM — the agency's own prospect pipeline.

The house pipeline is modeled as one synthetic Client row per org, flagged
is_house, materialized get-or-create by GET /api/orgs/me/house-client. The
existing per-client CRM then runs against that id unchanged. What this pins
down:
- get-or-create is idempotent (one house client per org);
- team roles reach it, the client portal role can't;
- it's hidden from the client roster and doesn't consume a plan client slot;
- CRM board + contact creation work against the house id;
- it's tenant-isolated like every other client row.
"""

import pytest

PW = "house-pass-123"


def _signup(api, org, email):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": org,
            "email": email,
            "password": PW,
            "full_name": "H",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_house_client_get_or_create_idempotent(api, team_headers):
    r1 = api.get("/api/orgs/me/house-client", headers=team_headers)
    assert r1.status_code == 200, r1.text
    cid = r1.json()["client_id"]
    assert cid
    # Second call returns the SAME row — get-or-create, not create-again.
    r2 = api.get("/api/orgs/me/house-client", headers=team_headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["client_id"] == cid


def test_member_reaches_house_client(api, member_headers):
    r = api.get("/api/orgs/me/house-client", headers=member_headers)
    assert r.status_code == 200, r.text
    assert r.json()["client_id"]


def test_client_role_forbidden(api, client_a_headers):
    r = api.get("/api/orgs/me/house-client", headers=client_a_headers)
    assert r.status_code == 403, r.text


def test_house_client_hidden_from_roster(api, team_headers):
    house_id = api.get("/api/orgs/me/house-client", headers=team_headers).json()[
        "client_id"
    ]
    listed = {c["id"] for c in api.get("/api/clients", headers=team_headers).json()}
    assert house_id not in listed
    # ...but it's still fetchable by id for team roles (lead-forms, sync config).
    assert api.get(f"/api/clients/{house_id}", headers=team_headers).status_code == 200


def test_house_client_does_not_consume_a_plan_slot(api):
    """A fresh starter org (5-client cap): materializing the house client must
    not eat a slot — all 5 real clients still fit, the 6th is blocked."""
    h = {
        "Authorization": f"Bearer {_signup(api, 'House Cap Co', 'cap@housecap.com')['access_token']}"
    }
    assert api.get("/api/orgs/me/house-client", headers=h).status_code == 200
    for i in range(5):
        assert (
            api.post("/api/clients", headers=h, json={"name": f"C{i}"}).status_code
            == 201
        )
    r = api.post("/api/clients", headers=h, json={"name": "C6"})
    assert r.status_code == 402, r.text


def test_crm_works_against_house_client(api, team_headers):
    house_id = api.get("/api/orgs/me/house-client", headers=team_headers).json()[
        "client_id"
    ]
    # Board auto-provisions the default pipeline for the house client.
    board = api.get(f"/api/crm/board?client_id={house_id}", headers=team_headers)
    assert board.status_code == 200, board.text
    assert board.json()["pipeline"]["id"]
    assert len(board.json()["stages"]) == 4  # DEFAULT_STAGES

    created = api.post(
        "/api/crm/contacts",
        headers=team_headers,
        json={"client_id": house_id, "first_name": "Prospect", "last_name": "One"},
    )
    assert created.status_code == 201, created.text
    contact_id = created.json()["id"]
    listed = api.get(f"/api/crm/contacts?client_id={house_id}", headers=team_headers)
    assert contact_id in {c["id"] for c in listed.json()}


def test_house_client_is_tenant_isolated(api, team_headers, org2_headers):
    """Another org's user can't touch org #1's house pipeline."""
    house_id = api.get("/api/orgs/me/house-client", headers=team_headers).json()[
        "client_id"
    ]
    r = api.get(f"/api/crm/board?client_id={house_id}", headers=org2_headers)
    assert r.status_code == 404, r.text
