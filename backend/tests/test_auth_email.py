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
