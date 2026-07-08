"""limit/offset pagination on the unbounded-growth list endpoints."""

PW = "pager-pass-123"


def _headers(api, email, org="Pager Co"):
    """Sign up (or log in if already created) and return auth headers."""
    r = api.post(
        "/api/orgs/signup",
        json={"organization_name": org, "email": email, "password": PW, "full_name": "P"},
    )
    if r.status_code == 409:
        r = api.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_admin_organizations_limit_and_offset(api):
    admin = _headers(api, "pager@salescale.com")  # in the superadmin allowlist
    for i in range(4):  # ensure enough orgs to page
        _headers(api, f"pgorg{i}@pgtest.com")

    page1 = api.get("/api/admin/organizations?limit=2&offset=0", headers=admin).json()
    page2 = api.get("/api/admin/organizations?limit=2&offset=2", headers=admin).json()
    assert len(page1) == 2 and len(page2) == 2
    assert {o["id"] for o in page1}.isdisjoint({o["id"] for o in page2})


def test_admin_organizations_limit_capped(api):
    admin = _headers(api, "pager@salescale.com")
    # over-max limit rejected by validation (le=200)
    assert api.get("/api/admin/organizations?limit=9999", headers=admin).status_code == 422


def test_clients_pagination(api, team_headers):
    for i in range(3):
        api.post("/api/clients", headers=team_headers, json={"name": f"PgClient{i}"})
    first = api.get("/api/clients?limit=2&offset=0", headers=team_headers).json()
    second = api.get("/api/clients?limit=2&offset=2", headers=team_headers).json()
    assert len(first) == 2 and len(second) == 2
    assert {c["id"] for c in first}.isdisjoint({c["id"] for c in second})
