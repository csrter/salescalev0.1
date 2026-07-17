"""iMessage channel — the BlueBubbles provider (dev/prototype) added to the SMS
Outreach module next to Twilio/Sendblue, plus the shared channel-health signal
and the /api/webhooks/imessage/* routes.

No live network: the BlueBubbles transport is monkeypatched
(gateway._bluebubbles_send / verify_credentials), exactly as the Twilio tests
stub _twilio_send. Runs against a dedicated org (im_org) with its own from
numbers so the seeded Atlas Reach counts and the sc_org module tests stay
untouched (the sms_accounts (org, from_number) unique index bites otherwise).

Covered: bluebubbles account validation + provider dispatch (send + verify),
the gateway min-spacing guard, channel_health's healthy/degraded/blocked enum,
the BlueBubbles inbound webhook (shared-secret auth, new-lead fallback, STOP,
delivery/read updates), and the Sendblue iMessage alias route.
"""

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.crm import Contact
from app.models.sms_outreach import (
    SMS_ACCOUNT_ACTIVE,
    SMS_ACCOUNT_ERROR,
    SMS_CAMPAIGN_ACTIVE,
    SMS_DIR_OUT,
    SMS_MSG_FAILED,
    SMS_MSG_READ,
    SMS_MSG_SENT,
    SmsAccount,
    SmsCampaign,
    SmsMessage,
    SmsSuppression,
)
from app.services import sms_send as gateway


# --- fixtures ----------------------------------------------------------------


@pytest.fixture()
def bb_creds_ok(monkeypatch):
    """Account create/test probes BlueBubbles over the network — stub it so
    account CRUD never touches the internet and lands status=active."""
    monkeypatch.setattr(gateway, "verify_credentials", lambda account: (True, "ok"))


@pytest.fixture()
def captured_bb(monkeypatch):
    """Capture every BlueBubbles send instead of POSTing to the relay. Returns
    the (guid, None, None) success shape _bluebubbles_send documents, and makes
    the Twilio path explode so a mis-dispatch can't pass silently."""
    sent = []

    def _fake(account, to_number, body):
        sent.append({"account_id": account.id, "to": to_number, "body": body})
        return "BB_test_guid", None, None

    def _boom(*a, **k):
        raise AssertionError("_twilio_send called for a bluebubbles account")

    monkeypatch.setattr(gateway, "_bluebubbles_send", _fake)
    monkeypatch.setattr(gateway, "_twilio_send", _boom)
    return sent


@pytest.fixture(scope="module")
def im_org(api):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "iMessage Co",
            "email": "owner@imessageco.com",
            "password": "imessageco-pass-1",
            "full_name": "iMessage Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "iMessage Client"}, headers=headers
    ).json()["id"]
    return {"org": body["organization_id"], "headers": headers, "client": client_id}


def _mk_bb_account(im_org, api, *, from_number, **over):
    base = {
        "name": "BlueBubbles Line",
        "provider": "bluebubbles",
        "auth_token": "bluebubbles-server-password-123",
        "relay_url": "https://imessage-relay.example.com",
        "from_number": from_number,
    }
    base.update(over)
    r = api.post("/api/sms/accounts", json=base, headers=im_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def _mk_contact(im_org, api, *, mobile_phone, opt_in=True):
    r = api.post(
        "/api/crm/contacts",
        json={
            "client_id": im_org["client"],
            "first_name": "Dana",
            "last_name": "Doe",
            "mobile_phone": mobile_phone,
            "sms_opt_in": opt_in,
        },
        headers=im_org["headers"],
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- account validation + provider dispatch ---------------------------------


def test_bluebubbles_account_requires_relay_url(im_org, api, bb_creds_ok):
    r = api.post(
        "/api/sms/accounts",
        json={
            "name": "No Relay",
            "provider": "bluebubbles",
            "auth_token": "bluebubbles-server-password-123",
            "from_number": "+14805551001",
        },
        headers=im_org["headers"],
    )
    assert r.status_code == 422
    assert "relay" in r.text.lower()


def test_bluebubbles_account_requires_from_number(im_org, api, bb_creds_ok):
    r = api.post(
        "/api/sms/accounts",
        json={
            "name": "No Number",
            "provider": "bluebubbles",
            "auth_token": "bluebubbles-server-password-123",
            "relay_url": "https://imessage-relay.example.com",
        },
        headers=im_org["headers"],
    )
    assert r.status_code == 422


def test_bluebubbles_account_creates_active_and_hides_secret(im_org, api, bb_creds_ok):
    acct = _mk_bb_account(im_org, api, from_number="+14805551002")
    assert acct["provider"] == "bluebubbles"
    assert acct["status"] == SMS_ACCOUNT_ACTIVE
    assert acct["relay_url"] == "https://imessage-relay.example.com"
    # the server password is write-only — never serialized back
    assert "auth_token" not in acct and "auth_token_encrypted" not in acct
    assert acct["channel_health"]["status"] == "healthy"  # no sends yet


def test_send_dispatches_to_bluebubbles(im_org, api, bb_creds_ok, captured_bb):
    acct = _mk_bb_account(im_org, api, from_number="+14805551003")
    _mk_contact(im_org, api, mobile_phone="+14805559003")
    db = SessionLocal()
    try:
        account = db.get(SmsAccount, acct["id"])
        contact = db.execute(
            select(Contact).where(Contact.mobile_phone == "+14805559003")
        ).scalars().first()
        result, row = gateway.send(db, account, contact, "hey there", org_name="iMessage Co")
        db.commit()
    finally:
        db.close()
    assert result == gateway.SENT
    assert row is not None and row.provider_sid == "BB_test_guid"
    assert len(captured_bb) == 1 and captured_bb[0]["to"] == "+14805559003"


class _FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_bluebubbles_first_message_creates_chat_when_missing(monkeypatch):
    """A first-ever message to a recipient 500s with 'Chat does not exist!' on
    message/text; the transport must fall back to chat/new and succeed, instead
    of surfacing a hard FAILED."""
    calls = []

    def _fake_post(url, params=None, json=None, timeout=None):
        calls.append(url)
        if url.endswith("/message/text"):
            return _FakeResp(
                500,
                {
                    "status": 500,
                    "message": "Message Send Error",
                    "error": {"type": "iMessage Error", "message": "Chat does not exist!"},
                },
            )
        if url.endswith("/chat/new"):
            return _FakeResp(
                200,
                {"status": 200, "data": {"messages": [{"guid": "NEWCHAT_guid"}]}},
            )
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(gateway.httpx, "post", _fake_post)
    acct = SmsAccount(
        provider="bluebubbles",
        account_sid="bluebubbles",
        name="x",
        relay_url="https://relay.example.com",
        auth_token_encrypted=None,
    )
    monkeypatch.setattr(gateway, "decrypt_secret", lambda s: "pw")
    guid, code, detail = gateway._bluebubbles_send(acct, "+14805559999", "hi")
    assert guid == "NEWCHAT_guid" and code is None and detail is None
    assert any(u.endswith("/message/text") for u in calls)
    assert any(u.endswith("/chat/new") for u in calls)


def test_bluebubbles_send_surfaces_specific_error_not_generic(monkeypatch):
    """A non-chat-missing failure keeps the specific nested error.message, not
    the generic top-level 'Message Send Error'."""

    def _fake_post(url, params=None, json=None, timeout=None):
        return _FakeResp(
            500,
            {
                "status": 500,
                "message": "Message Send Error",
                "error": {"type": "iMessage Error", "message": "Some other reason"},
            },
        )

    monkeypatch.setattr(gateway.httpx, "post", _fake_post)
    monkeypatch.setattr(gateway, "decrypt_secret", lambda s: "pw")
    acct = SmsAccount(
        provider="bluebubbles",
        account_sid="bluebubbles",
        name="x",
        relay_url="https://relay.example.com",
    )
    guid, code, detail = gateway._bluebubbles_send(acct, "+14805559999", "hi")
    assert guid == "" and code == "500" and detail == "Some other reason"


def test_verify_credentials_dispatches_bluebubbles(monkeypatch):
    calls = []

    def _bb(a):
        calls.append("bb")
        return True, "ok"

    def _tw(a):
        calls.append("twilio")
        return True, "ok"

    monkeypatch.setattr(gateway, "_verify_bluebubbles", _bb)
    monkeypatch.setattr(gateway, "_verify_twilio", _tw)
    acct = SmsAccount(provider="bluebubbles", account_sid="bluebubbles", name="x")
    ok, detail = gateway.verify_credentials(acct)
    assert ok and calls == ["bb"]  # bluebubbles path only, twilio untouched


# --- min-spacing throttle (anti-detection pacing) ---------------------------


def _always_open_campaign(db, account):
    """A minimal campaign whose send window is always open, so tests exercise
    the spacing guard (which sits after the window check) without clock luck."""
    camp = SmsCampaign(
        organization_id=account.organization_id,
        name="Pacing Camp",
        status=SMS_CAMPAIGN_ACTIVE,
        account_id=account.id,
        send_window_start=0,
        send_window_end=24,
        send_days=[0, 1, 2, 3, 4, 5, 6],
    )
    db.add(camp)
    db.flush()
    return camp


def test_bluebubbles_account_defaults_to_conservative_spacing_range(im_org, api, bb_creds_ok):
    # neither bound specified → defaults to the BlueBubbles anti-detection range
    acct = _mk_bb_account(im_org, api, from_number="+14805551040")
    assert acct["min_send_spacing_seconds"] == gateway.BLUEBUBBLES_DEFAULT_SPACING_MIN_SECONDS
    assert acct["max_send_spacing_seconds"] == gateway.BLUEBUBBLES_DEFAULT_SPACING_MAX_SECONDS
    assert acct["min_send_spacing_seconds"] == 20
    assert acct["max_send_spacing_seconds"] == 45
    # explicit values (incl. 0 to opt out) are respected, not overridden by the default
    acct0 = _mk_bb_account(
        im_org, api, from_number="+14805551041",
        min_send_spacing_seconds=0, max_send_spacing_seconds=0,
    )
    assert acct0["min_send_spacing_seconds"] == 0
    assert acct0["max_send_spacing_seconds"] == 0


def test_bluebubbles_account_rejects_max_below_min(im_org, api, bb_creds_ok):
    r = api.post(
        "/api/sms/accounts",
        json={
            "name": "Bad Range",
            "provider": "bluebubbles",
            "auth_token": "bluebubbles-server-password-123",
            "relay_url": "https://imessage-relay.example.com",
            "from_number": "+14805551043",
            "min_send_spacing_seconds": 45,
            "max_send_spacing_seconds": 20,
        },
        headers=im_org["headers"],
    )
    assert r.status_code == 422


def test_campaign_send_defers_within_configured_range(im_org, api, bb_creds_ok, captured_bb):
    """With both min and max set, a deferred send's reschedule target is a
    uniform-random point strictly inside [min, max] — the literal 20-45s-style
    range behavior, not the older floor*jitter fallback."""
    acct = _mk_bb_account(
        im_org, api, from_number="+14805551004",
        min_send_spacing_seconds=20, max_send_spacing_seconds=45,
    )
    _mk_contact(im_org, api, mobile_phone="+14805559004")
    db = SessionLocal()
    try:
        account = db.get(SmsAccount, acct["id"])
        contact = db.execute(
            select(Contact).where(Contact.mobile_phone == "+14805559004")
        ).scalars().first()
        camp = _always_open_campaign(db, account)
        first, _ = gateway.send(db, account, contact, "one", campaign=camp, org_name="iMessage Co")
        second, second_row = gateway.send(db, account, contact, "two", campaign=camp, org_name="iMessage Co")
        # sample several reschedule picks — every one must land in [20, 45]
        now = utcnow()
        gaps = [
            (gateway.next_spacing_time(db, account) - now).total_seconds()
            for _ in range(20)
        ]
        db.commit()
    finally:
        db.close()
    assert first == gateway.SENT
    assert second == gateway.SPACING and second_row is None
    assert len(captured_bb) == 1  # the deferred send never hit the provider
    assert all(20 - 2 <= g <= 45 + 2 for g in gaps)
    assert len(set(round(g) for g in gaps)) > 1  # actually randomized, not a fixed value


def test_spacing_falls_back_to_floor_jitter_when_max_unset(im_org, api, bb_creds_ok):
    """An account with only min set (no max) keeps the older floor*1.0-1.8x
    jitter behavior — backward compatible with pre-range configurations."""
    acct = _mk_bb_account(
        im_org, api, from_number="+14805551044", min_send_spacing_seconds=300
    )
    assert acct["max_send_spacing_seconds"] is None
    db = SessionLocal()
    try:
        account = db.get(SmsAccount, acct["id"])
        nxt = gateway.next_spacing_time(db, account)
        now = utcnow()
    finally:
        db.close()
    gap = (nxt - now).total_seconds()
    assert 300 * 1.0 - 5 <= gap <= 300 * 1.8 + 5


def test_manual_send_is_never_throttled(im_org, api, bb_creds_ok, captured_bb):
    """A human's 1:1 reply in the inbox (campaign is None) bypasses spacing —
    it's already human-timed, and throttling live replies is user-hostile."""
    acct = _mk_bb_account(
        im_org, api, from_number="+14805551042", min_send_spacing_seconds=300
    )
    _mk_contact(im_org, api, mobile_phone="+14805559042")
    db = SessionLocal()
    try:
        account = db.get(SmsAccount, acct["id"])
        contact = db.execute(
            select(Contact).where(Contact.mobile_phone == "+14805559042")
        ).scalars().first()
        first, _ = gateway.send(db, account, contact, "one", kind="manual", org_name="iMessage Co")
        second, _ = gateway.send(db, account, contact, "two", kind="manual", org_name="iMessage Co")
        db.commit()
    finally:
        db.close()
    assert first == gateway.SENT and second == gateway.SENT
    assert len(captured_bb) == 2


# --- channel_health enum ----------------------------------------------------


def _seed_outbound(db, account, *, status, service=None, n=1):
    for _ in range(n):
        db.add(
            SmsMessage(
                organization_id=account.organization_id,
                account_id=account.id,
                direction=SMS_DIR_OUT,
                to_number="+14805559999",
                from_number=account.from_number,
                body="x",
                status=status,
                service=service,
            )
        )
    db.flush()


def test_channel_health_healthy_degraded_blocked(im_org, api, bb_creds_ok):
    acct = _mk_bb_account(im_org, api, from_number="+14805551005")
    db = SessionLocal()
    try:
        account = db.get(SmsAccount, acct["id"])

        _seed_outbound(db, account, status=SMS_MSG_SENT, n=3)
        assert gateway.channel_health(db, account)["status"] == "healthy"

        # an iMessage-capable provider falling back to green/SMS = degraded
        _seed_outbound(db, account, status=SMS_MSG_SENT, service="SMS", n=1)
        assert gateway.channel_health(db, account)["status"] == "degraded"

        # account not active = blocked, regardless of message history
        account.status = SMS_ACCOUNT_ERROR
        account.error_detail = "server password rejected"
        db.flush()
        assert gateway.channel_health(db, account)["status"] == "blocked"
        db.rollback()
    finally:
        db.close()


def test_channel_health_high_failure_rate_blocks(im_org, api, bb_creds_ok):
    acct = _mk_bb_account(im_org, api, from_number="+14805551006")
    db = SessionLocal()
    try:
        account = db.get(SmsAccount, acct["id"])
        _seed_outbound(db, account, status=SMS_MSG_FAILED, n=3)
        _seed_outbound(db, account, status=SMS_MSG_SENT, n=1)
        assert gateway.channel_health(db, account)["status"] == "blocked"
        db.rollback()
    finally:
        db.close()


# --- BlueBubbles inbound webhook --------------------------------------------


def _bb_post(api, account, body, secret=None, **headers):
    hdrs = dict(headers)
    if secret is not None:
        hdrs["X-Salescale-Webhook-Secret"] = secret
    return api.post(
        f"/api/webhooks/imessage/bluebubbles/{account['id']}", json=body, headers=hdrs
    )


def test_bluebubbles_inbound_bad_secret_403(im_org, api, bb_creds_ok):
    acct = _mk_bb_account(im_org, api, from_number="+14805551007")
    r = _bb_post(
        api,
        acct,
        {"type": "new-message", "data": {"text": "hi", "handle": {"address": "+14805559007"}}},
        secret="wrong-secret",
    )
    assert r.status_code == 403


def test_bluebubbles_inbound_creates_lead_and_records(im_org, api, bb_creds_ok):
    acct = _mk_bb_account(im_org, api, from_number="+14805551008")
    number = "+14805559008"
    r = _bb_post(
        api,
        acct,
        {
            "type": "new-message",
            "data": {
                "guid": "bb-in-1",
                "text": "interested, tell me more",
                "isFromMe": False,
                "handle": {"address": number},
            },
        },
        secret=acct["webhook_token"],
    )
    assert r.status_code == 200
    db = SessionLocal()
    try:
        contact = db.execute(
            select(Contact).where(
                Contact.organization_id == im_org["org"], Contact.phone == number
            )
        ).scalars().first()
        assert contact is not None
        assert contact.source == "imessage:bluebubbles"
        assert not contact.sms_opt_in  # inbound is never TCPA consent
        msg = db.execute(
            select(SmsMessage).where(SmsMessage.provider_sid == "bb-in-1")
        ).scalars().first()
        assert msg is not None and msg.contact_id == contact.id
        assert msg.service == "iMessage"
    finally:
        db.close()


def test_bluebubbles_inbound_own_echo_ignored(im_org, api, bb_creds_ok):
    acct = _mk_bb_account(im_org, api, from_number="+14805551009")
    r = _bb_post(
        api,
        acct,
        {"type": "new-message", "data": {"guid": "echo-1", "isFromMe": True, "text": "sent by me"}},
        secret=acct["webhook_token"],
    )
    assert r.status_code == 200
    db = SessionLocal()
    try:
        assert (
            db.execute(
                select(SmsMessage).where(SmsMessage.provider_sid == "echo-1")
            ).scalars().first()
            is None
        )
    finally:
        db.close()


def test_bluebubbles_inbound_stop_suppresses(im_org, api, bb_creds_ok):
    acct = _mk_bb_account(im_org, api, from_number="+14805551010")
    number = "+14805559010"
    r = _bb_post(
        api,
        acct,
        {"type": "new-message", "data": {"guid": "stop-1", "text": "STOP", "handle": {"address": number}}},
        secret=acct["webhook_token"],
    )
    assert r.status_code == 200
    db = SessionLocal()
    try:
        supp = db.execute(
            select(SmsSuppression).where(
                SmsSuppression.organization_id == im_org["org"],
                SmsSuppression.phone_e164 == number,
            )
        ).scalars().first()
        assert supp is not None
    finally:
        db.close()


def test_bluebubbles_updated_message_sets_read_and_delivered(im_org, api, bb_creds_ok):
    acct = _mk_bb_account(im_org, api, from_number="+14805551011")
    db = SessionLocal()
    try:
        account = db.get(SmsAccount, acct["id"])
        row = SmsMessage(
            organization_id=account.organization_id,
            account_id=account.id,
            direction=SMS_DIR_OUT,
            to_number="+14805559011",
            from_number=account.from_number,
            body="hello",
            status=SMS_MSG_SENT,
            provider_sid="bb-out-1",
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    dr = _bb_post(
        api,
        acct,
        {"type": "updated-message", "data": {"guid": "bb-out-1", "dateDelivered": 1720000000000}},
        secret=acct["webhook_token"],
    )
    assert dr.status_code == 200
    rr = _bb_post(
        api,
        acct,
        {"type": "updated-message", "data": {"guid": "bb-out-1", "dateRead": 1720000005000}},
        secret=acct["webhook_token"],
    )
    assert rr.status_code == 200
    db = SessionLocal()
    try:
        row = db.execute(
            select(SmsMessage).where(SmsMessage.provider_sid == "bb-out-1")
        ).scalars().first()
        assert row.status == SMS_MSG_READ  # read is the last, terminal update
        assert row.read_at is not None
    finally:
        db.close()


# --- Sendblue iMessage alias route ------------------------------------------


def test_sendblue_imessage_alias_records_inbound(im_org, api, bb_creds_ok):
    """The /api/webhooks/imessage/sendblue/* aliases delegate to the canonical
    Sendblue handlers — same token auth, same _process_inbound."""
    r = api.post(
        "/api/sms/accounts",
        json={
            "name": "Sendblue Line",
            "provider": "sendblue",
            "account_sid": "sb-api-key-id-000000",
            "auth_token": "sb-api-secret-key-000000",
            "from_number": "+14805551012",
        },
        headers=im_org["headers"],
    )
    assert r.status_code == 201, r.text
    acct = r.json()
    number = "+14805559012"
    resp = api.post(
        f"/api/webhooks/imessage/sendblue/inbound/{acct['id']}/{acct['webhook_token']}",
        json={"from_number": number, "content": "hi from imessage", "message_handle": "sb-in-1"},
    )
    assert resp.status_code == 200
    db = SessionLocal()
    try:
        msg = db.execute(
            select(SmsMessage).where(SmsMessage.provider_sid == "sb-in-1")
        ).scalars().first()
        assert msg is not None and msg.from_number == number
    finally:
        db.close()


def test_sendblue_imessage_alias_bad_token_403(im_org, api, bb_creds_ok):
    r = api.post(
        "/api/sms/accounts",
        json={
            "name": "Sendblue Line 2",
            "provider": "sendblue",
            "account_sid": "sb-api-key-id-000001",
            "auth_token": "sb-api-secret-key-000001",
            "from_number": "+14805551013",
        },
        headers=im_org["headers"],
    )
    assert r.status_code == 201, r.text
    acct = r.json()
    resp = api.post(
        f"/api/webhooks/imessage/sendblue/inbound/{acct['id']}/not-the-real-token",
        json={"from_number": "+14805559013", "content": "x", "message_handle": "sb-in-2"},
    )
    assert resp.status_code == 403
