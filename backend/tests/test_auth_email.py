"""Email verification + self-serve password reset.

Email is in dev mode (no SMTP) so messages are recorded in email_log — the
tests read the real token out of the logged link, exercising the full flow.
"""
import re

from app.db import SessionLocal
from app.models.email import EmailLog

PW = "authmail-pass-123"


def _signup(api, org, email):
    r = api.post(
        "/api/orgs/signup",
        json={"organization_name": org, "email": email, "password": PW, "full_name": "A"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _last_email(to_address: str) -> EmailLog:
    db = SessionLocal()
    row = (
        db.query(EmailLog)
        .filter(EmailLog.to_address == to_address)
        .order_by(EmailLog.created_at.desc())
        .first()
    )
    db.close()
    return row


def _token(body: str) -> str:
    m = re.search(r"[?&](?:verify|reset)=(\S+)", body)
    assert m, f"no token in email body: {body!r}"
    return m.group(1)


def test_signup_sends_verification_email(api):
    _signup(api, "Verify Co", "verify@verifyco.com")
    row = _last_email("verify@verifyco.com")
    assert row is not None and "confirm your email" in row.subject.lower()


def test_verify_email_flow(api):
    _signup(api, "Verify Two", "vt@verifytwo.com")
    token = _token(_last_email("vt@verifytwo.com").body)
    assert api.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    # login now reports the address as verified
    login = api.post("/api/auth/login", json={"email": "vt@verifytwo.com", "password": PW})
    assert login.status_code == 200 and login.json()["email_verified"] is True


def test_verify_email_rejects_bad_token(api):
    assert api.post("/api/auth/verify-email", json={"token": "not-a-jwt"}).status_code == 400


def test_password_reset_flow(api):
    _signup(api, "Reset Flow Co", "rf@resetflow.com")
    # request a reset — always 200 (no account enumeration)
    assert api.post("/api/auth/forgot-password", json={"email": "rf@resetflow.com"}).status_code == 200
    token = _token(_last_email("rf@resetflow.com").body)
    new_pw = "brand-new-pass-9"
    assert api.post("/api/auth/reset-password", json={"token": token, "new_password": new_pw}).status_code == 200
    # old password fails, new one works
    assert api.post("/api/auth/login", json={"email": "rf@resetflow.com", "password": PW}).status_code == 401
    assert api.post("/api/auth/login", json={"email": "rf@resetflow.com", "password": new_pw}).status_code == 200


def test_logout_all_revokes_existing_sessions(api):
    sess = _signup(api, "Logout Co", "logout@logoutco.com")
    h = {"Authorization": f"Bearer {sess['access_token']}"}
    assert api.get("/api/auth/me", headers=h).status_code == 200
    assert api.post("/api/auth/logout-all", headers=h).status_code == 200
    # the same token no longer works — token_version moved past it
    assert api.get("/api/auth/me", headers=h).status_code == 401


def test_password_reset_revokes_existing_sessions(api):
    sess = _signup(api, "Reset Revoke Co", "rr@resetrevoke.com")
    h = {"Authorization": f"Bearer {sess['access_token']}"}
    assert api.get("/api/auth/me", headers=h).status_code == 200
    api.post("/api/auth/forgot-password", json={"email": "rr@resetrevoke.com"})
    token = _token(_last_email("rr@resetrevoke.com").body)
    assert api.post(
        "/api/auth/reset-password", json={"token": token, "new_password": "post-reset-pass-1"}
    ).status_code == 200
    # a session that predates the reset is invalidated (kicks other devices)
    assert api.get("/api/auth/me", headers=h).status_code == 401


def test_signup_rejects_overlong_password(api):
    # >72 bytes would be silently truncated by bcrypt — reject it instead.
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Long Pw Co",
            "email": "longpw@longpwco.com",
            "password": "a" * 73,
            "full_name": "X",
        },
    )
    assert r.status_code == 422


def test_reset_token_is_single_use(api):
    # A reset link is bound to the password hash it was issued for, so once
    # used it can't be replayed (even within its 30-min TTL).
    _signup(api, "Single Use Co", "su@singleuse.com")
    assert api.post("/api/auth/forgot-password", json={"email": "su@singleuse.com"}).status_code == 200
    token = _token(_last_email("su@singleuse.com").body)
    assert api.post(
        "/api/auth/reset-password", json={"token": token, "new_password": "first-new-pass-1"}
    ).status_code == 200
    # replaying the same link is rejected — fingerprint no longer matches
    assert api.post(
        "/api/auth/reset-password", json={"token": token, "new_password": "attacker-pass-2"}
    ).status_code == 400
    # and the attacker's password never took effect
    assert api.post(
        "/api/auth/login", json={"email": "su@singleuse.com", "password": "attacker-pass-2"}
    ).status_code == 401
    assert api.post(
        "/api/auth/login", json={"email": "su@singleuse.com", "password": "first-new-pass-1"}
    ).status_code == 200


def test_forgot_password_unknown_email_is_silent(api):
    # returns ok without sending anything (no enumeration, no email row)
    assert api.post("/api/auth/forgot-password", json={"email": "nobody@nowhere-xyz.com"}).status_code == 200
    assert _last_email("nobody@nowhere-xyz.com") is None


def test_reset_rejects_bad_token(api):
    assert api.post(
        "/api/auth/reset-password", json={"token": "bad", "new_password": "whatever-1234"}
    ).status_code == 400


def test_reset_token_cannot_be_used_as_verify_token(api):
    # purpose binding: a reset token must not verify an email
    _signup(api, "Purpose Co", "purpose@purposeco.com")
    api.post("/api/auth/forgot-password", json={"email": "purpose@purposeco.com"})
    reset_token = _token(_last_email("purpose@purposeco.com").body)
    assert api.post("/api/auth/verify-email", json={"token": reset_token}).status_code == 400


def test_unverified_blocked_from_inviting_and_connecting(api, monkeypatch):
    """Phase 12 task 13: with verification enforced, an unverified account
    can't invite members or start an ad-account connection — even with a
    session issued before the flag was flipped (the login gate alone wouldn't
    catch that)."""
    from app.config import get_settings

    body = _signup(api, "Unverified Gate Co", "gate@unverifiedgate.com")
    h = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post("/api/clients", json={"name": "GC"}, headers=h).json()["id"]

    monkeypatch.setattr(get_settings(), "require_email_verification", True)
    r = api.post(
        "/api/orgs/me/invites",
        json={"email": "newbie@unverifiedgate.com", "role": "member"},
        headers=h,
    )
    assert r.status_code == 403 and "verify" in r.json()["detail"].lower()
    r = api.get(
        "/api/connect/meta/start", params={"client_id": client_id}, headers=h
    )
    assert r.status_code == 403 and "verify" in r.json()["detail"].lower()

    # verify the address → both actions unblock
    token = _token(_last_email("gate@unverifiedgate.com").body)
    assert api.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    r = api.post(
        "/api/orgs/me/invites",
        json={"email": "newbie@unverifiedgate.com", "role": "member"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    # (connect now passes the verified gate; it 503s on missing Meta app
    # credentials, which is the next check in that endpoint.)
    r = api.get(
        "/api/connect/meta/start", params={"client_id": client_id}, headers=h
    )
    assert r.status_code == 503
