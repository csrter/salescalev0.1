"""Social login (Sign in with Google / Meta)."""
import app.api.social_auth as sa
from app.config import get_settings
from app.db import SessionLocal
from app.security import create_action_token


def _configure_google(monkeypatch):
    monkeypatch.setattr(get_settings(), "google_login_client_id", "gid")
    monkeypatch.setattr(get_settings(), "google_login_client_secret", "gsecret")


def test_start_unconfigured_returns_503(api):
    assert api.get("/api/auth/oauth/google/start").status_code == 503


def test_unknown_provider_404(api):
    assert api.get("/api/auth/oauth/tiktok/start").status_code == 404


def test_start_configured_returns_consent_url(api, monkeypatch):
    _configure_google(monkeypatch)
    r = api.get("/api/auth/oauth/google/start")
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "state=" in url and "scope=openid" in url


def test_find_or_create_new_then_existing():
    db = SessionLocal()
    u1 = sa.find_or_create_social_user(db, "Social@New.com", "Sam Social", "google", True)
    assert u1.email == "social@new.com"
    assert u1.role == "owner"
    assert u1.email_verified is True  # provider verified it
    assert u1.auth_provider == "google"
    u2 = sa.find_or_create_social_user(db, "social@new.com", "Sam Social", "google", True)
    assert u2.id == u1.id  # second time logs in, doesn't duplicate
    db.close()


def test_unverified_social_cannot_attach_to_existing_account():
    # A local (password) account exists; a social login whose provider did NOT
    # verify the email (e.g. Meta) must not take it over.
    from fastapi import HTTPException

    from app.models.core import Organization, ROLE_OWNER, User
    from app.security import hash_password

    db = SessionLocal()
    org = Organization(name="Victim Co")
    db.add(org)
    db.flush()
    db.add(
        User(
            organization_id=org.id,
            email="victim@example.com",
            hashed_password=hash_password("real-password-123"),
            full_name="Victim",
            role=ROLE_OWNER,
        )
    )
    db.commit()
    # Meta login (email_verified=False) for the same email is refused.
    try:
        sa.find_or_create_social_user(db, "victim@example.com", "x", "meta", False)
        assert False, "unverified social login attached to a password account"
    except HTTPException as e:
        assert e.status_code == 409
    # A verified provider (Google) may attach.
    u = sa.find_or_create_social_user(db, "victim@example.com", "x", "google", True)
    assert u.email == "victim@example.com"
    db.close()


def test_callback_creates_session_and_me_works(api, monkeypatch):
    _configure_google(monkeypatch)
    monkeypatch.setattr(
        sa,
        "_exchange_and_fetch",
        lambda provider, code: ("oauthuser@example.com", "OAuth User", True),
    )
    state = create_action_token("social:google", "nonce", minutes=5)
    r = api.get(
        f"/api/auth/oauth/google/callback?code=abc&state={state}", follow_redirects=False
    )
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert "#access_token=" in loc
    token = loc.split("#access_token=")[1]
    me = api.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["email_verified"] is True and body["role"] == "owner"


def test_callback_rejects_bad_state(api, monkeypatch):
    _configure_google(monkeypatch)
    r = api.get(
        "/api/auth/oauth/google/callback?code=abc&state=not-a-token", follow_redirects=False
    )
    assert r.status_code == 400
