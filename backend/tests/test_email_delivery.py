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
