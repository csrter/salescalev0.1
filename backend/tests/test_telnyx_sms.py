"""Telnyx SMS provider (v2 Messages API) — adapter + webhook contract.

Network is monkeypatched at httpx; nothing here talks to Telnyx. Own org
(tx_org) per the isolation convention.

What matters here beyond "it posts JSON": Telnyx delivery is ASYNCHRONOUS —
a 2xx only means accepted — so the failure paths (per-recipient status in the
send response, and the terminal statuses on the status webhook) are what stop
a dead send sitting at "sent" forever. That is the exact failure mode that
hid 200 dead BlueBubbles sends, so it is pinned here for this provider.
"""

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.sms_outreach import (
    SMS_DIR_OUT,
    SMS_MSG_DELIVERED,
    SMS_MSG_FAILED,
    SMS_MSG_SENT,
    SmsAccount,
    SmsMessage,
)
from app.services import sms_send


@pytest.fixture(scope="module")
def tx_org(api):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Telnyx Co",
            "email": "owner@telnyxco.com",
            "password": "telnyxco-pass-1",
            "full_name": "TX Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return {
        "org": body["organization_id"],
        "headers": {"Authorization": f"Bearer {body['access_token']}"},
    }


@pytest.fixture()
def creds_ok(monkeypatch):
    monkeypatch.setattr(sms_send, "verify_credentials", lambda a: (True, "ok"))


def _mk_account(tx_org, api, **over):
    base = {
        "name": "Telnyx Line",
        "provider": "telnyx",
        "auth_token": "KEY0123456789abcdefghij",
        "from_number": "+14805551200",
    }
    base.update(over)
    r = api.post("/api/sms/accounts", json=base, headers=tx_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def _account_row(account_id):
    db = SessionLocal()
    try:
        return db.get(SmsAccount, account_id)
    finally:
        db.close()


# --- account creation ---------------------------------------------------


def test_connect_requires_a_sender_and_never_returns_the_key(tx_org, api, creds_ok):
    acct = _mk_account(tx_org, api)
    assert acct["provider"] == "telnyx"
    # No second public identifier exists for Telnyx — a placeholder satisfies
    # the non-null column, and the key is never serialized back.
    assert acct["account_sid"] == "telnyx"
    assert "auth_token" not in acct and "auth_token_encrypted" not in acct
    # Unsigned-webhook provider → a per-account URL token is minted.
    assert acct["webhook_token"]

    # Neither a from number nor a Messaging Profile → 422.
    r = api.post(
        "/api/sms/accounts",
        json={
            "name": "No sender",
            "provider": "telnyx",
            "auth_token": "KEY0123456789abcdefghij",
        },
        headers=tx_org["headers"],
    )
    assert r.status_code == 422
    assert "Messaging Profile" in r.json()["detail"]

    # A Messaging Profile alone is sufficient.
    prof = _mk_account(
        tx_org,
        api,
        name="Profile only",
        from_number=None,
        messaging_service_sid="40017a7b-1111-2222-3333-444455556666",
    )
    assert prof["messaging_service_sid"].startswith("40017a7b")


# --- send adapter -------------------------------------------------------


def test_send_posts_v2_messages_and_returns_the_id(tx_org, api, creds_ok, monkeypatch):
    acct = _mk_account(tx_org, api, name="Send line", from_number="+14805551201")
    captured = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"id": "msg-abc-123", "to": [{"status": "queued"}]}}

    def _post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(sms_send.httpx, "post", _post)
    sid, code, detail = sms_send._telnyx_send(
        _account_row(acct["id"]), "+14805559999", "hello there"
    )
    assert (sid, code, detail) == ("msg-abc-123", None, None)
    assert captured["url"].endswith("/v2/messages")
    assert captured["json"]["to"] == "+14805559999"
    assert captured["json"]["text"] == "hello there"
    assert captured["json"]["from"] == "+14805551201"
    # Bearer auth, and the key is the decrypted secret — not the placeholder.
    assert captured["headers"]["Authorization"].startswith("Bearer ")
    assert "KEY0123456789abcdefghij" in captured["headers"]["Authorization"]


def test_send_surfaces_api_errors_and_terminal_recipient_status(
    tx_org, api, creds_ok, monkeypatch
):
    """Two distinct failure shapes: a non-2xx with an errors[] body, and a
    2xx whose per-recipient status is already terminal."""
    acct = _account_row(_mk_account(tx_org, api, name="Err line",
                                    from_number="+14805551202")["id"])

    class _Err:
        status_code = 422

        @staticmethod
        def json():
            return {"errors": [{"code": "40005", "detail": "Invalid to number"}]}

    monkeypatch.setattr(sms_send.httpx, "post", lambda *a, **k: _Err())
    sid, code, detail = sms_send._telnyx_send(acct, "+1480", "hi")
    assert sid == "" and code == "40005" and "Invalid to number" in detail

    class _Rejected:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"id": "m2", "to": [{"status": "sending_failed"}]}}

    monkeypatch.setattr(sms_send.httpx, "post", lambda *a, **k: _Rejected())
    sid, code, detail = sms_send._telnyx_send(acct, "+14805559999", "hi")
    assert sid == "m2" and code == "sending_failed"


def test_network_failure_raises_provider_error(tx_org, api, creds_ok, monkeypatch):
    import httpx as _httpx

    acct = _account_row(_mk_account(tx_org, api, name="Net line",
                                    from_number="+14805551203")["id"])

    def _boom(*a, **k):
        raise _httpx.ConnectError("dns")

    monkeypatch.setattr(sms_send.httpx, "post", _boom)
    with pytest.raises(sms_send.SmsProviderError):
        sms_send._telnyx_send(acct, "+14805559999", "hi")


def test_verify_credentials_dispatches_to_telnyx(tx_org, api, creds_ok, monkeypatch):
    acct = _account_row(_mk_account(tx_org, api, name="Verify line",
                                    from_number="+14805551204")["id"])

    class _Ok:
        status_code = 200

    class _Unauthorized:
        status_code = 401

    monkeypatch.setattr(sms_send.httpx, "get", lambda *a, **k: _Ok())
    assert sms_send._verify_telnyx(acct) == (True, "ok")

    monkeypatch.setattr(sms_send.httpx, "get", lambda *a, **k: _Unauthorized())
    ok, detail = sms_send._verify_telnyx(acct)
    assert ok is False and "rejected the API key" in detail

    # And the public dispatcher routes a telnyx account to that adapter.
    monkeypatch.undo()
    monkeypatch.setattr(sms_send, "_verify_telnyx", lambda a: (True, "dispatched"))
    assert sms_send.verify_credentials(acct) == (True, "dispatched")


# --- webhooks -----------------------------------------------------------


def _seed_outbound(tx_org, account_id, sid):
    db = SessionLocal()
    try:
        db.add(
            SmsMessage(
                organization_id=tx_org["org"],
                account_id=account_id,
                direction=SMS_DIR_OUT,
                to_number="+14805559999",
                body="hi",
                status=SMS_MSG_SENT,
                provider_sid=sid,
            )
        )
        db.commit()
    finally:
        db.close()


def _status(sid):
    db = SessionLocal()
    try:
        return db.execute(
            select(SmsMessage).where(SmsMessage.provider_sid == sid)
        ).scalar_one().status
    finally:
        db.close()


def test_status_webhook_marks_delivered_and_terminal_failures(tx_org, api, creds_ok):
    """Telnyx's failure vocabulary (delivery_failed / sending_failed /
    expired) must map to FAILED — otherwise a dead send sits at 'sent'."""
    acct = _mk_account(tx_org, api, name="Hook line", from_number="+14805551205")
    token = acct["webhook_token"]
    url = f"/api/sms/webhooks/telnyx/status/{acct['id']}/{token}"

    _seed_outbound(tx_org, acct["id"], "wh-delivered")
    r = api.post(url, json={"data": {"event_type": "message.finalized",
                                     "payload": {"id": "wh-delivered",
                                                 "to": [{"status": "delivered"}]}}})
    assert r.status_code == 200
    assert _status("wh-delivered") == SMS_MSG_DELIVERED

    for i, bad in enumerate(("delivery_failed", "sending_failed", "expired")):
        sid = f"wh-bad-{i}"
        _seed_outbound(tx_org, acct["id"], sid)
        api.post(url, json={"data": {"event_type": "message.finalized",
                                     "payload": {"id": sid, "to": [{"status": bad}],
                                                 "errors": [{"code": "40010"}]}}})
        assert _status(sid) == SMS_MSG_FAILED, bad

    # Wrong token → 403, and the row is untouched.
    _seed_outbound(tx_org, acct["id"], "wh-guard")
    r = api.post(
        f"/api/sms/webhooks/telnyx/status/{acct['id']}/not-the-token",
        json={"data": {"event_type": "message.finalized",
                       "payload": {"id": "wh-guard", "to": [{"status": "delivered"}]}}},
    )
    assert r.status_code == 403
    assert _status("wh-guard") == SMS_MSG_SENT


def test_inbound_webhook_records_reply_and_stop(tx_org, api, creds_ok):
    """Inbound lands as a message; a STOP suppresses the number. Telnyx nests
    `from` as an object and `to` as a list."""
    from app.models.sms_outreach import SMS_DIR_IN, SmsSuppression

    acct = _mk_account(tx_org, api, name="In line", from_number="+14805551206")
    url = f"/api/sms/webhooks/telnyx/inbound/{acct['id']}/{acct['webhook_token']}"

    r = api.post(url, json={"data": {"event_type": "message.received", "payload": {
        "id": "in-1",
        "from": {"phone_number": "+14805557001"},
        "to": [{"phone_number": "+14805551206"}],
        "text": "who is this?",
    }}})
    assert r.status_code == 200
    db = SessionLocal()
    try:
        row = db.execute(
            select(SmsMessage).where(SmsMessage.provider_sid == "in-1")
        ).scalar_one()
        assert row.direction == SMS_DIR_IN and row.body == "who is this?"
    finally:
        db.close()

    api.post(url, json={"data": {"event_type": "message.received", "payload": {
        "id": "in-2",
        "from": {"phone_number": "+14805557002"},
        "to": [{"phone_number": "+14805551206"}],
        "text": "STOP",
    }}})
    db = SessionLocal()
    try:
        sup = db.execute(
            select(SmsSuppression).where(
                SmsSuppression.organization_id == tx_org["org"],
                SmsSuppression.phone_e164 == "+14805557002",
            )
        ).scalar_one_or_none()
        assert sup is not None, "STOP must suppress the number"
    finally:
        db.close()


def test_inbound_url_also_handles_delivery_events(tx_org, api, creds_ok):
    """Telnyx messaging profiles have ONE webhook URL, so delivery events can
    arrive on the inbound route — route them instead of dropping them."""
    acct = _mk_account(tx_org, api, name="Mixed line", from_number="+14805551207")
    _seed_outbound(tx_org, acct["id"], "mixed-1")
    r = api.post(
        f"/api/sms/webhooks/telnyx/inbound/{acct['id']}/{acct['webhook_token']}",
        json={"data": {"event_type": "message.finalized",
                       "payload": {"id": "mixed-1", "to": [{"status": "delivered"}]}}},
    )
    assert r.status_code == 200
    assert _status("mixed-1") == SMS_MSG_DELIVERED
