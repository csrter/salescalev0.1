"""Resend transport for transactional email."""
import app.services.email as email_mod
from app.config import get_settings
from app.db import SessionLocal
from app.models.core import Organization
from app.services.email import _send_via_resend


class _Resp:
    def __init__(self, code, text=""):
        self.status_code = code
        self.text = text


def test_resend_success(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, json=json, auth=headers["Authorization"])
        return _Resp(200)

    monkeypatch.setattr(email_mod.httpx, "post", fake_post)
    ok = _send_via_resend("re_test", "Salescale", "no-reply@x.com", "u@y.com", "Hi", "Body")
    assert ok is True
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["json"]["to"] == ["u@y.com"]
    assert captured["json"]["from"] == "Salescale <no-reply@x.com>"
    assert captured["auth"] == "Bearer re_test"


def test_resend_failure_returns_false(monkeypatch):
    monkeypatch.setattr(email_mod.httpx, "post", lambda *a, **k: _Resp(422, "bad"))
    assert _send_via_resend("re_test", "N", "f@x.com", "t@y.com", "S", "B") is False


def test_resend_network_error_returns_false(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(email_mod.httpx, "post", boom)
    assert _send_via_resend("re_test", "N", "f@x.com", "t@y.com", "S", "B") is False


def test_send_email_dispatches_to_resend(monkeypatch, seeded):
    monkeypatch.setattr(get_settings(), "resend_api_key", "re_test")
    called = {}
    monkeypatch.setattr(
        email_mod, "_send_via_resend", lambda *a, **k: called.setdefault("hit", True) or True
    )
    db = SessionLocal()
    org = db.query(Organization).first()
    entry = email_mod.send_email(db, org, "x@y.com", "Subject", "Body")
    assert called.get("hit") is True
    assert entry.delivered is True
    db.rollback()
    db.close()


def test_send_email_dev_mode_logs_not_delivered(seeded):
    # No RESEND_API_KEY / SMTP in tests → composed and logged, not delivered.
    db = SessionLocal()
    org = db.query(Organization).first()
    entry = email_mod.send_email(db, org, "x@y.com", "Subject", "Body")
    assert entry.delivered is False
    db.rollback()
    db.close()


def test_branded_sender_failure_falls_back_to_platform_default(monkeypatch, seeded):
    """A misconfigured/unverified custom domain must not lock an org's own
    account-lifecycle mail (2FA, reset, verification, invites) out entirely —
    the second attempt with the platform default sender is what saves it."""
    monkeypatch.setattr(get_settings(), "resend_api_key", "re_test")
    db = SessionLocal()
    org = db.query(Organization).first()
    org.branding = {
        **(org.branding or {}),
        "email_from_name": "Custom Sender",
        "email_from_address": "sales@unverified-domain.example",
    }
    db.commit()

    attempts = []

    def fake_send(api_key, from_name, from_address, to_address, subject, body, html=None):
        attempts.append(from_address)
        return from_address == get_settings().email_default_from_address

    monkeypatch.setattr(email_mod, "_send_via_resend", fake_send)
    entry = email_mod.send_email(db, org, "x@y.com", "Subject", "Body")

    assert attempts == [
        "sales@unverified-domain.example",
        get_settings().email_default_from_address,
    ]
    assert entry.delivered is True
    assert entry.from_address == get_settings().email_default_from_address
    db.rollback()
    db.close()


def test_branded_sender_success_never_falls_back(monkeypatch, seeded):
    monkeypatch.setattr(get_settings(), "resend_api_key", "re_test")
    db = SessionLocal()
    org = db.query(Organization).first()
    org.branding = {
        **(org.branding or {}),
        "email_from_name": "Custom Sender",
        "email_from_address": "sales@verified-domain.example",
    }
    db.commit()

    attempts = []
    monkeypatch.setattr(
        email_mod,
        "_send_via_resend",
        lambda *a, **k: attempts.append(a) or True,
    )
    entry = email_mod.send_email(db, org, "x@y.com", "Subject", "Body")

    assert len(attempts) == 1  # no retry when the branded sender works
    assert entry.delivered is True
    assert entry.from_address == "sales@verified-domain.example"
    db.rollback()
    db.close()


def test_both_senders_failing_is_still_logged_undelivered(monkeypatch, seeded):
    monkeypatch.setattr(get_settings(), "resend_api_key", "re_test")
    db = SessionLocal()
    org = db.query(Organization).first()
    org.branding = {
        **(org.branding or {}),
        "email_from_name": "Custom Sender",
        "email_from_address": "sales@unverified-domain.example",
    }
    db.commit()

    monkeypatch.setattr(email_mod, "_send_via_resend", lambda *a, **k: False)
    entry = email_mod.send_email(db, org, "x@y.com", "Subject", "Body")

    assert entry.delivered is False
    db.rollback()
    db.close()
