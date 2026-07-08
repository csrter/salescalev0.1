"""Platform super-admin (cross-tenant) + org-admin member management.

The super-admin surface (`/api/admin/*`) is the ONE sanctioned place that
reads across Organizations — every route must reject non-superadmins. The
org-admin member endpoints must stay scoped to the caller's own Organization.
"""
import pytest

SUPERADMIN_EMAIL = "platform@salescale.com"  # matches SUPERADMIN_EMAILS in conftest
PW = "signup-pass-123"


def _signup(api, org, email, name="U"):
    r = api.post(
        "/api/orgs/signup",
        json={"organization_name": org, "email": email, "password": PW, "full_name": name},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def super_headers(api):
    body = _signup(api, "Salescale HQ", SUPERADMIN_EMAIL, name="Platform Admin")
    assert body["is_superadmin"] is True
    return {"Authorization": f"Bearer {body['access_token']}"}


# --- super-admin gate ---


def test_regular_signup_is_not_superadmin(api):
    body = _signup(api, "Regular Agency", "regular@agency-x.com")
    assert body["is_superadmin"] is False


def test_non_superadmin_blocked_from_every_admin_route(api, org2_headers, seeded):
    org = seeded["org"]
    assert api.get("/api/admin/stats", headers=org2_headers).status_code == 403
    assert api.get("/api/admin/organizations", headers=org2_headers).status_code == 403
    assert api.get(f"/api/admin/organizations/{org}", headers=org2_headers).status_code == 403
    assert api.get("/api/admin/signups", headers=org2_headers).status_code == 403
    assert (
        api.patch(
            f"/api/admin/organizations/{org}", headers=org2_headers, json={"plan": "pro"}
        ).status_code
        == 403
    )
    assert (
        api.post(
            f"/api/admin/users/{seeded['client_a']}/reset-password", headers=org2_headers
        ).status_code
        == 403
    )


def test_admin_requires_auth(api, seeded):
    assert api.get("/api/admin/stats").status_code == 401


# --- cross-tenant reads ---


def test_superadmin_lists_all_orgs(api, super_headers, seeded, org2):
    resp = api.get("/api/admin/organizations", headers=super_headers)
    assert resp.status_code == 200
    names = {o["name"] for o in resp.json()}
    # sees Organizations it is NOT a member of — the sanctioned bypass
    assert {"Atlas Reach", "Rival Agency", "Salescale HQ"} <= names
    row = next(o for o in resp.json() if o["name"] == "Atlas Reach")
    assert row["user_count"] >= 1 and "plan" in row and "status" in row


def test_superadmin_stats(api, super_headers):
    d = api.get("/api/admin/stats", headers=super_headers).json()
    assert d["organizations"] >= 3 and d["users"] >= 3


def test_superadmin_org_detail(api, super_headers, seeded):
    d = api.get(f"/api/admin/organizations/{seeded['org']}", headers=super_headers).json()
    assert d["name"] == "Atlas Reach"
    assert any(u["email"] == "owner@atlasreach.com" for u in d["users"])


def test_signups_series_is_zero_filled(api, super_headers):
    pts = api.get("/api/admin/signups?days=14", headers=super_headers).json()
    assert len(pts) == 14
    assert all("date" in p and "count" in p for p in pts)


# --- destructive per-org actions (own throwaway orgs to avoid cross-test bleed) ---


def test_suspend_blocks_login_then_reactivate(api, super_headers):
    org_id = _signup(api, "Suspend Target", "suspend@target-co.com")["organization_id"]
    r = api.patch(
        f"/api/admin/organizations/{org_id}", headers=super_headers, json={"status": "suspended"}
    )
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    assert api.post(
        "/api/auth/login", json={"email": "suspend@target-co.com", "password": PW}
    ).status_code == 403
    api.patch(
        f"/api/admin/organizations/{org_id}", headers=super_headers, json={"status": "active"}
    )
    assert api.post(
        "/api/auth/login", json={"email": "suspend@target-co.com", "password": PW}
    ).status_code == 200


def test_reset_password(api, super_headers):
    body = _signup(api, "Reset Target", "reset@target-co.com")
    detail = api.get(
        f"/api/admin/organizations/{body['organization_id']}", headers=super_headers
    ).json()
    uid = detail["users"][0]["id"]
    temp = api.post(f"/api/admin/users/{uid}/reset-password", headers=super_headers).json()[
        "temporary_password"
    ]
    assert temp and len(temp) >= 8
    assert api.post(
        "/api/auth/login", json={"email": "reset@target-co.com", "password": PW}
    ).status_code == 401
    assert api.post(
        "/api/auth/login", json={"email": "reset@target-co.com", "password": temp}
    ).status_code == 200


def test_plan_change_validates(api, super_headers):
    org_id = _signup(api, "Plan Target", "plan@target-co.com")["organization_id"]
    assert api.patch(
        f"/api/admin/organizations/{org_id}", headers=super_headers, json={"plan": "pro"}
    ).json()["plan"] == "pro"
    assert api.patch(
        f"/api/admin/organizations/{org_id}", headers=super_headers, json={"plan": "bogus"}
    ).status_code == 400


# --- org-admin member management (owner-scoped, NOT cross-tenant) ---


def test_owner_manages_members(api, team_headers):
    r = api.post(
        "/api/orgs/me/members",
        headers=team_headers,
        json={"email": "nm@atlasreach.com", "password": "member-pass-1", "full_name": "NM", "role": "member"},
    )
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    assert api.patch(f"/api/orgs/me/members/{mid}", headers=team_headers, json={"role": "admin"}).json()["role"] == "admin"
    assert api.patch(f"/api/orgs/me/members/{mid}", headers=team_headers, json={"is_active": False}).json()["is_active"] is False
    # deactivated member can no longer log in
    assert api.post("/api/auth/login", json={"email": "nm@atlasreach.com", "password": "member-pass-1"}).status_code == 401


def test_member_update_is_owner_only(api, team_headers, member_headers):
    mid = api.post(
        "/api/orgs/me/members",
        headers=team_headers,
        json={"email": "nm2@atlasreach.com", "password": "member-pass-2", "full_name": "NM2", "role": "member"},
    ).json()["id"]
    assert api.patch(f"/api/orgs/me/members/{mid}", headers=member_headers, json={"role": "admin"}).status_code == 403


def test_owner_cannot_edit_another_orgs_member(api, team_headers, super_headers, org2):
    # A member id that belongs to org2, obtained via the admin surface.
    org2_users = api.get(
        f"/api/admin/organizations/{org2['organization_id']}", headers=super_headers
    ).json()["users"]
    victim = org2_users[0]["id"]
    # org1 owner must not be able to touch it — 404 (existence not leaked)
    assert api.patch(f"/api/orgs/me/members/{victim}", headers=team_headers, json={"role": "member"}).status_code == 404


def test_owner_cannot_modify_own_owner_account(api, team_headers, seeded):
    # The acting owner's own row is role=owner and is rejected (400), which
    # also prevents self-lockout.
    members = api.get("/api/orgs/me/members", headers=team_headers).json()
    owner_id = next(m["id"] for m in members if m["role"] == "owner")
    assert api.patch(f"/api/orgs/me/members/{owner_id}", headers=team_headers, json={"is_active": False}).status_code == 400
