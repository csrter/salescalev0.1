"""Phase 1 role model: Owner > Admin > Member inside one Organization.
Members do day-to-day campaign work; client management, platform
connections, and team membership are Admin/Owner surface."""


def test_member_can_read_org_data(api, member_headers, seeded):
    resp = api.get("/api/clients", headers=member_headers)
    assert resp.status_code == 200
    ids = {c["id"] for c in resp.json()}
    assert {seeded["client_a"], seeded["client_b"]} <= ids


def test_member_cannot_create_clients(api, member_headers):
    resp = api.post(
        "/api/clients", json={"name": "Member Made Co"}, headers=member_headers
    )
    assert resp.status_code == 403


def test_member_cannot_start_oauth(api, member_headers, seeded):
    resp = api.get(
        f"/api/connect/meta/start?client_id={seeded['client_a']}",
        headers=member_headers,
    )
    assert resp.status_code == 403


def test_member_cannot_add_team_members(api, member_headers):
    resp = api.post(
        "/api/orgs/me/members",
        json={
            "email": "new@atlasreach.com",
            "password": "password-123",
            "full_name": "New Person",
            "role": "member",
        },
        headers=member_headers,
    )
    assert resp.status_code == 403


def test_owner_can_add_admin_and_member(api, team_headers):
    for role, email in (("admin", "admin@atlasreach.com"), ("member", "m2@atlasreach.com")):
        resp = api.post(
            "/api/orgs/me/members",
            json={
                "email": email,
                "password": "password-123",
                "full_name": f"Added {role}",
                "role": role,
            },
            headers=team_headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["role"] == role


def test_admin_can_add_member_but_not_admin(api, team_headers):
    # admin@atlasreach.com was created by the owner in the test above.
    login = api.post(
        "/api/auth/login",
        json={"email": "admin@atlasreach.com", "password": "password-123"},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    ok = api.post(
        "/api/orgs/me/members",
        json={
            "email": "m3@atlasreach.com",
            "password": "password-123",
            "full_name": "Admin Added Member",
            "role": "member",
        },
        headers=headers,
    )
    assert ok.status_code == 201

    denied = api.post(
        "/api/orgs/me/members",
        json={
            "email": "a2@atlasreach.com",
            "password": "password-123",
            "full_name": "Admin Added Admin",
            "role": "admin",
        },
        headers=headers,
    )
    assert denied.status_code == 403


def test_client_role_cannot_read_org_endpoints(api, client_a_headers):
    assert api.get("/api/orgs/me", headers=client_a_headers).status_code == 403
    assert api.get("/api/orgs/me/members", headers=client_a_headers).status_code == 403
