"""Session viewing / revoke, logout-everywhere, and the org 2FA policy."""
import pyotp

PW = "session-pass-123456"


def _signup(api, org, email):
    r = api.post(
        "/api/orgs/signup",
        json={"organization_name": org, "email": email, "password": PW, "full_name": "S"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _login(api, email):
    return api.post("/api/auth/login", json={"email": email, "password": PW}).json()


def _h(sess):
    return {"Authorization": f"Bearer {sess['access_token']}"}


def test_sessions_listed_and_individually_revocable(api):
    a = _signup(api, "Sess Co", "sess@sessco.com")  # session A (signup)
    b = _login(api, "sess@sessco.com")  # session B (second login)
    ha, hb = _h(a), _h(b)

    listing = api.get("/api/auth/sessions", headers=hb).json()
    assert len(listing) >= 2
    assert sum(1 for s in listing if s["current"]) == 1  # exactly the caller's
    other = next(s for s in listing if not s["current"])

    # revoke the other device; its token stops working, current keeps working
    assert api.delete(f"/api/auth/sessions/{other['id']}", headers=hb).status_code == 200
    assert api.get("/api/auth/me", headers=ha).status_code == 401
    assert api.get("/api/auth/me", headers=hb).status_code == 200


def test_logout_everywhere_kills_all(api):
    a = _signup(api, "Logout Every Co", "le@leco.com")
    h = _h(a)
    assert api.get("/api/auth/me", headers=h).status_code == 200
    assert api.post("/api/auth/logout-all", headers=h).status_code == 200
    # even the token that called logout-all is now dead
    assert api.get("/api/auth/me", headers=h).status_code == 401


def test_org_require_mfa_gates_team_members(api):
    owner = _signup(api, "Policy Co", "owner@policyco.com")
    ho = _h(owner)
    m = api.post(
        "/api/orgs/me/members",
        headers=ho,
        json={"email": "member@policyco.com", "password": PW, "full_name": "M", "role": "member"},
    )
    assert m.status_code == 201

    # before the policy, no gating
    assert _login(api, "member@policyco.com").get("mfa_setup_required") in (False, None)

    # owner turns the policy on
    r = api.put("/api/orgs/me/require-mfa", headers=ho, json={"require_mfa": True})
    assert r.status_code == 200 and r.json()["require_mfa"] is True

    # now the member (and the owner, also without 2FA) are gated to enrollment
    assert _login(api, "member@policyco.com")["mfa_setup_required"] is True
    assert api.get("/api/auth/me", headers=ho).json()["mfa_setup_required"] is True

    # only the owner may change the policy
    hm = _h(_login(api, "member@policyco.com"))
    assert api.put("/api/orgs/me/require-mfa", headers=hm, json={"require_mfa": False}).status_code == 403


def test_require_mfa_is_enforced_server_side(api):
    owner = _signup(api, "Enforce Co", "owner@enforceco.com")
    ho = _h(owner)
    api.post(
        "/api/orgs/me/members",
        headers=ho,
        json={"email": "m@enforceco.com", "password": PW, "full_name": "M", "role": "member"},
    )
    api.put("/api/orgs/me/require-mfa", headers=ho, json={"require_mfa": True})
    hm = _h(_login(api, "m@enforceco.com"))

    # App-data routers are hard-blocked (not just the UI) until 2FA is on...
    assert api.get("/api/clients", headers=hm).status_code == 403
    assert api.get("/api/clients", headers=ho).status_code == 403  # owner too
    # ...but the enrollment / auth / org-read path stays open so they can comply
    assert api.get("/api/mfa", headers=hm).status_code == 200
    assert api.get("/api/auth/me", headers=hm).status_code == 200
    assert api.get("/api/orgs/me", headers=hm).status_code == 200

    # enroll TOTP, then app access is restored
    secret = api.post("/api/mfa/totp/setup", headers=hm).json()["secret"]
    api.post("/api/mfa/totp/enable", headers=hm, json={"code": pyotp.TOTP(secret).now()})
    assert api.get("/api/clients", headers=hm).status_code == 200
