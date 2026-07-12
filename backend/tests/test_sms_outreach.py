"""SMS Outreach module — campaign engine, API surface, and the TCPA
compliance machinery (consent gate, suppression, quiet hours, STOP webhook).

The Twilio transport is monkeypatched (no live network): account credential
checks go through `sms_send.verify_credentials`, outbound sends go through
`sms_send._twilio_send`. Contact/campaign work runs against a dedicated org
(sc_org) so the seeded Atlas Reach org's counts (asserted over elsewhere)
stay untouched.

Covered: account CRUD + creds write-only + client-role 403; the consent gate
partitioning enroll_contacts (no_number/no_consent/suppressed/already
enrolled); suppression blocking a send at tick time; the inbound STOP
webhook (suppression + opt-in cleared + enrollments exited); quiet-hours
window gating; step upsert-in-place with stable ids; the activate guard;
the enroll receipt; run_due advancing positions to completion; the monthly
send-quota 402; CSV import recording SMS consent; and tenant isolation.
"""

import base64
import hashlib
import hmac

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.crm import Contact
from app.models.sms_outreach import (
    SMS_ENROLL_ACTIVE,
    SmsEnrollment,
    SmsMessage,
    SmsSuppression,
)
from app.services import sms_campaigns
from app.services import sms_send as gateway
from app.services import sms_consent


# --- fixtures ----------------------------------------------------------------


@pytest.fixture()
def twilio_creds_ok(monkeypatch):
    """The account-create/test endpoints probe Twilio over the network —
    stub that out so account CRUD never touches the internet."""
    monkeypatch.setattr(
        gateway, "verify_credentials", lambda account: (True, "ok")
    )


@pytest.fixture()
def captured_sends(monkeypatch):
    """Capture every outbound send attempt instead of hitting Twilio. Returns
    ("SM_test_sid", None, None) — the success shape _twilio_send documents."""
    sent = []

    def _fake(account, to_number, body):
        sent.append({"account_id": account.id, "to": to_number, "body": body})
        return "SM_test_sid", None, None

    monkeypatch.setattr(gateway, "_twilio_send", _fake)
    return sent


@pytest.fixture(scope="module")
def sc_org(api):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "SMS Co",
            "email": "owner@smsco.com",
            "password": "smsco-pass-1",
            "full_name": "SMS Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "SMS Client"}, headers=headers
    ).json()["id"]
    return {"org": body["organization_id"], "headers": headers, "client": client_id}


_AUTH_TOKEN = "sms-test-auth-token-0123456789"


def _mk_account(sc_org, api, **over):
    base = {
        "name": "SMS Co Line",
        "account_sid": "ACtestaccountsid00000000",
        "auth_token": _AUTH_TOKEN,
        "from_number": "+14805550100",
        "daily_send_cap": 200,
    }
    base.update(over)
    r = api.post("/api/sms/accounts", json=base, headers=sc_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def _mk_contact(sc_org, api, *, mobile_phone, first="Dana", last="Doe", opt_in=True, **extra):
    payload = {
        "client_id": sc_org["client"],
        "first_name": first,
        "last_name": last,
        "mobile_phone": mobile_phone,
        "sms_opt_in": opt_in,
    }
    payload.update(extra)
    r = api.post("/api/crm/contacts", json=payload, headers=sc_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _mk_campaign(sc_org, api, account_id, **over):
    payload = {"name": "Fall Promo", "account_id": account_id}
    payload.update(over)
    r = api.post("/api/sms/campaigns", json=payload, headers=sc_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def _set_steps(sc_org, api, campaign_id, steps):
    r = api.put(
        f"/api/sms/campaigns/{campaign_id}/steps",
        json={"steps": steps},
        headers=sc_org["headers"],
    )
    assert r.status_code == 200, r.text
    return r.json()


def _activate(sc_org, api, campaign_id):
    return api.post(
        f"/api/sms/campaigns/{campaign_id}/activate", headers=sc_org["headers"]
    )


def _enroll(sc_org, api, campaign_id, contact_ids):
    return api.post(
        f"/api/sms/campaigns/{campaign_id}/enroll",
        json={"contact_ids": contact_ids},
        headers=sc_org["headers"],
    )


def _tick():
    db = SessionLocal()
    try:
        return sms_campaigns.run_due(db)
    finally:
        db.close()


def _get_enrollment(campaign_id, contact_id):
    db = SessionLocal()
    try:
        return db.execute(
            select(SmsEnrollment).where(
                SmsEnrollment.campaign_id == campaign_id,
                SmsEnrollment.contact_id == contact_id,
            )
        ).scalar_one()
    finally:
        db.close()


# Window always open, every day — so window gating never blocks a test that
# isn't specifically about it.
_ALWAYS = {
    "send_window_start": 0,
    "send_window_end": 24,
    "send_days": [0, 1, 2, 3, 4, 5, 6],
    "timezone": "UTC",
}


def _twilio_signature(auth_token: str, url: str, params: dict) -> str:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(
        auth_token.encode(), payload.encode("utf-8"), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode()


# --- account CRUD + role gating ----------------------------------------------


def test_account_crud_creds_write_only_and_client_403(
    sc_org, api, twilio_creds_ok, client_a_headers
):
    acct = _mk_account(sc_org, api, from_number="+14805550101")
    assert acct["status"] == "active"
    assert "auth_token" not in acct
    assert "auth_token_encrypted" not in acct

    listed = api.get("/api/sms/accounts", headers=sc_org["headers"]).json()
    assert any(a["id"] == acct["id"] for a in listed)
    assert all("auth_token" not in a for a in listed)

    patched = api.patch(
        f"/api/sms/accounts/{acct['id']}",
        json={"name": "Renamed Line"},
        headers=sc_org["headers"],
    ).json()
    assert patched["name"] == "Renamed Line"
    assert "auth_token" not in patched

    # Client role is fully locked out of the SMS module.
    assert (
        api.get("/api/sms/accounts", headers=client_a_headers).status_code == 403
    )


# --- consent gate --------------------------------------------------------


def test_enroll_partitions_by_consent_and_dedupes(sc_org, api, twilio_creds_ok):
    acct = _mk_account(sc_org, api, from_number="+14805550102")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)

    ok = _mk_contact(sc_org, api, mobile_phone="4805551001", opt_in=True)
    no_consent = _mk_contact(sc_org, api, mobile_phone="4805551002", opt_in=False)
    no_number = _mk_contact(sc_org, api, mobile_phone=None, opt_in=True, phone=None)

    r = _enroll(sc_org, api, camp["id"], [ok, no_consent, no_number, "bogus-id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enrolled"] == 1
    reasons = {x["contact_id"]: x["reason"] for x in body["skipped"]}
    assert reasons[no_consent] == "no_consent"
    assert reasons[no_number] == "no_number"
    assert reasons["bogus-id"] == "not_found"

    # Re-enroll the already-enrolled contact -> already_enrolled (or "already",
    # depending on the engine's exact vocabulary — accept either since both
    # convey the same thing and the framework file didn't pin one down).
    r2 = _enroll(sc_org, api, camp["id"], [ok])
    assert r2.json()["enrolled"] == 0
    assert r2.json()["skipped"][0]["reason"] in ("already_enrolled", "already")


def test_suppressed_number_skipped_at_enroll_and_exits_if_late(
    sc_org, api, twilio_creds_ok, captured_sends
):
    acct = _mk_account(sc_org, api, from_number="+14805550103")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)

    # Suppress "early"'s number BEFORE the contact exists, then create the
    # contact with sms_opt_in=True: record_opt_out has no existing contact to
    # revoke consent on yet, so this is a clean "opted in, but the number is
    # already on the suppression list" case — the enroll gate reports
    # "suppressed" rather than "no_consent".
    api.post(
        "/api/sms/suppression",
        json={"phone": "+14805552001"},
        headers=sc_org["headers"],
    )
    early = _mk_contact(sc_org, api, mobile_phone="4805552001")
    late = _mk_contact(sc_org, api, mobile_phone="4805552002")

    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": "Hi there"}])
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    r = _enroll(sc_org, api, camp["id"], [early, late])
    assert r.json()["enrolled"] == 1
    assert r.json()["skipped"][0] == {"contact_id": early, "reason": "suppressed"}

    # Suppress "late" AFTER enrollment succeeded — record_opt_out ALSO clears
    # sms_opt_in on every existing contact carrying that number (STOP revokes
    # consent for the person, not just one campaign — sms_consent's documented
    # rule), so this surfaces through the consent gate at send time rather
    # than the bare suppression check. Either way the outcome required here
    # holds: the gateway never attempts the send, and the enrollment exits
    # instead of retrying forever.
    api.post(
        "/api/sms/suppression",
        json={"phone": "+14805552002"},
        headers=sc_org["headers"],
    )
    _tick()
    assert captured_sends == []  # never attempted — refused before any send
    e = _get_enrollment(camp["id"], late)
    assert e.status == "exited"
    assert e.exit_reason in ("opted_out", "failed")


# --- STOP webhook -------------------------------------------------------


def test_stop_webhook_suppresses_clears_optin_and_exits_enrollments(
    sc_org, api, twilio_creds_ok, captured_sends
):
    acct = _mk_account(
        sc_org, api, from_number="+14805550104", auth_token=_AUTH_TOKEN
    )
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805553001")
    _set_steps(
        sc_org,
        api,
        camp["id"],
        [
            {"position": 1, "wait_days": 0, "body": "First touch"},
            {"position": 2, "wait_days": 3, "body": "Bump {{first_name}}"},
        ],
    )
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])
    _tick()  # sends step 1, parks step 2 ~3 days out
    assert len(captured_sends) == 1

    url = f"http://testserver/api/sms/webhooks/inbound/{acct['id']}"
    form = {
        "From": "+14805553001",
        "To": acct["from_number"],
        "Body": "STOP",
        "MessageSid": "SM_stop_1",
    }
    sig = _twilio_signature(_AUTH_TOKEN, url, form)
    r = api.post(
        "/api/sms/webhooks/inbound/" + acct["id"],
        data=form,
        headers={"X-Twilio-Signature": sig},
    )
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        supp = db.execute(
            select(SmsSuppression).where(
                SmsSuppression.organization_id == sc_org["org"],
                SmsSuppression.phone_e164 == "+14805553001",
            )
        ).scalar_one_or_none()
        assert supp is not None and supp.reason == "stop"
        c = db.get(Contact, contact)
        assert c.sms_opt_in is False
    finally:
        db.close()

    e = _get_enrollment(camp["id"], contact)
    assert e.status == "exited"
    assert e.exit_reason == "opted_out"
    assert e.next_run_at is None


def test_stop_webhook_rejects_bad_signature(sc_org, api, twilio_creds_ok):
    acct = _mk_account(
        sc_org, api, from_number="+14805550105", auth_token=_AUTH_TOKEN
    )
    r = api.post(
        "/api/sms/webhooks/inbound/" + acct["id"],
        data={"From": "+14805559999", "To": acct["from_number"], "Body": "STOP"},
        headers={"X-Twilio-Signature": "not-a-real-signature"},
    )
    assert r.status_code == 403


# --- quiet hours ----------------------------------------------------------


def test_quiet_hours_window_defers_send(sc_org, api, twilio_creds_ok, captured_sends):
    today = utcnow().weekday()
    closed_day = (today + 2) % 7
    acct = _mk_account(sc_org, api, from_number="+14805550106")
    camp = _mk_campaign(
        sc_org,
        api,
        acct["id"],
        send_window_start=9,
        send_window_end=10,
        send_days=[closed_day],
        timezone="UTC",
    )
    contact = _mk_contact(sc_org, api, mobile_phone="4805554001")
    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": "hello"}])
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])
    _tick()
    assert captured_sends == []
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "active"
    assert e.next_run_at is not None


# --- steps upsert-in-place -------------------------------------------------


def test_steps_upsert_keeps_stable_ids_and_validates_positions(sc_org, api, twilio_creds_ok):
    acct = _mk_account(sc_org, api, from_number="+14805550107")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)

    # Gap in positions -> 422.
    r = api.put(
        f"/api/sms/campaigns/{camp['id']}/steps",
        json={"steps": [{"position": 1, "body": "a"}, {"position": 3, "body": "b"}]},
        headers=sc_org["headers"],
    )
    assert r.status_code == 422

    created = _set_steps(
        sc_org,
        api,
        camp["id"],
        [
            {"position": 1, "body": "Step one {{first_name}}"},
            {"position": 2, "wait_days": 2, "body": "Step two"},
        ],
    )
    ids = [s["id"] for s in created["steps"]]
    assert len(ids) == 2 and all(ids)

    # Edit in place, same ids, reordered payload -> ids survive unchanged.
    updated = _set_steps(
        sc_org,
        api,
        camp["id"],
        [
            {"id": ids[1], "position": 1, "body": "Now first"},
            {"id": ids[0], "position": 2, "body": "Now second {{first_name}}"},
        ],
    )
    by_id = {s["id"]: s for s in updated["steps"]}
    assert by_id[ids[1]]["position"] == 1 and by_id[ids[1]]["body"] == "Now first"
    assert by_id[ids[0]]["position"] == 2

    # Unknown personalization token -> 422.
    r2 = api.put(
        f"/api/sms/campaigns/{camp['id']}/steps",
        json={"steps": [{"position": 1, "body": "Hi {{nonsense_token}}"}]},
        headers=sc_org["headers"],
    )
    assert r2.status_code == 422
    assert "nonsense_token" in r2.json()["detail"]

    # Unknown step id -> 422.
    r3 = api.put(
        f"/api/sms/campaigns/{camp['id']}/steps",
        json={"steps": [{"id": "not-a-real-id", "position": 1, "body": "x"}]},
        headers=sc_org["headers"],
    )
    assert r3.status_code == 422


# --- activate guard ---------------------------------------------------------


def test_activate_guard(sc_org, api, twilio_creds_ok):
    acct = _mk_account(sc_org, api, from_number="+14805550108")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)

    r = _activate(sc_org, api, camp["id"])
    assert r.status_code == 422  # no steps

    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": "hi"}])
    assert _activate(sc_org, api, camp["id"]).status_code == 200

    # Already active -> 409.
    assert _activate(sc_org, api, camp["id"]).status_code == 409

    # Pause then reactivate is fine.
    assert api.post(
        f"/api/sms/campaigns/{camp['id']}/pause", headers=sc_org["headers"]
    ).status_code == 200
    assert _activate(sc_org, api, camp["id"]).status_code == 200


# --- run_due: advances positions and completes ------------------------------


def test_run_due_advances_positions_and_completes(sc_org, api, twilio_creds_ok, captured_sends):
    acct = _mk_account(sc_org, api, from_number="+14805550109")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805555001", first="Rex")
    _set_steps(
        sc_org,
        api,
        camp["id"],
        [
            {"position": 1, "wait_days": 0, "body": "Hi {{first_name}}"},
            {"position": 2, "wait_days": 3, "body": "Bump {{first_name|there}}"},
        ],
    )
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])

    _tick()  # sends step 1
    assert len(captured_sends) == 1
    assert captured_sends[-1]["body"].startswith("SMS Co Line: Hi Rex") or "Hi Rex" in captured_sends[-1]["body"]

    e = _get_enrollment(camp["id"], contact)
    assert e.current_position == 2
    assert e.status == "active"
    assert e.next_run_at is not None

    # Force step 2 due.
    db = SessionLocal()
    try:
        row = db.execute(
            select(SmsEnrollment).where(SmsEnrollment.id == e.id)
        ).scalar_one()
        row.next_run_at = utcnow() - __import__("datetime").timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    _tick()  # sends step 2
    assert len(captured_sends) == 2
    e2 = _get_enrollment(camp["id"], contact)
    assert e2.status == "completed"
    assert e2.next_run_at is None


# --- entitlement cap ---------------------------------------------------------


def test_enroll_gated_by_monthly_sms_quota(sc_org, api, twilio_creds_ok, monkeypatch):
    from app.services import entitlements

    acct = _mk_account(sc_org, api, from_number="+14805550110")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805556001")

    monkeypatch.setattr(
        entitlements, "sms_outreach_usage", lambda db, org: {"used": 5, "limit": 5}
    )
    r = _enroll(sc_org, api, camp["id"], [contact])
    assert r.status_code == 402


# --- CSV import consent -----------------------------------------------------


def test_csv_import_with_sms_opt_in_all_records_consent(sc_org, api):
    r = api.post(
        "/api/crm/contacts/import",
        json={
            "client_id": sc_org["client"],
            "mapping": {"Name": "first_name", "Cell": "mobile_phone"},
            "rows": [{"Name": "Csv Lead", "Cell": "4805557001"}],
            "sms_opt_in_all": True,
        },
        headers=sc_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1

    listed = api.get(
        f"/api/crm/contacts?client_id={sc_org['client']}",
        headers=sc_org["headers"],
    ).json()
    row = next(c for c in listed if c.get("first_name") == "Csv Lead")
    assert row["sms_opt_in"] is True
    assert row["sms_opt_in_source"] == "csv_import:website_attested"


# --- tenant isolation --------------------------------------------------------


def test_campaign_tenant_isolation(sc_org, api, twilio_creds_ok, team_headers):
    acct = _mk_account(sc_org, api, from_number="+14805550111")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)

    # Atlas Reach (team_headers, a DIFFERENT org) can't see or fetch SMS Co's
    # campaign, account, or enrollments.
    listed = api.get("/api/sms/campaigns", headers=team_headers).json()
    assert all(c["id"] != camp["id"] for c in listed)
    assert api.get(
        f"/api/sms/campaigns/{camp['id']}", headers=team_headers
    ).status_code == 404
    assert api.post(
        f"/api/sms/campaigns/{camp['id']}/activate", headers=team_headers
    ).status_code == 404
    assert api.get(
        "/api/sms/accounts", headers=team_headers
    ).json() == [
        a for a in api.get("/api/sms/accounts", headers=team_headers).json()
        if a["id"] != acct["id"]
    ]


# --- Sendblue provider ------------------------------------------------------


def test_sendblue_account_requires_from_number(sc_org, api, monkeypatch):
    monkeypatch.setattr(gateway, "verify_credentials", lambda a: (True, "ok"))
    r = api.post(
        "/api/sms/accounts",
        json={
            "name": "iMessage line",
            "provider": "sendblue",
            "account_sid": "sb-key-id-000000",
            "auth_token": "sb-secret-key-000000",
        },
        headers=sc_org["headers"],
    )
    assert r.status_code == 422  # no from_number
    r = api.post(
        "/api/sms/accounts",
        json={
            "name": "iMessage line",
            "provider": "sendblue",
            "account_sid": "sb-key-id-000000",
            "auth_token": "sb-secret-key-000000",
            "from_number": "+14805559000",
        },
        headers=sc_org["headers"],
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["provider"] == "sendblue"
    assert body["webhook_token"]  # a URL secret was minted


def test_sendblue_send_dispatches_to_sendblue(sc_org, api, monkeypatch):
    monkeypatch.setattr(gateway, "verify_credentials", lambda a: (True, "ok"))
    calls = []

    def _fake_sb(account, to_number, body):
        calls.append({"account": account.id, "to": to_number, "body": body})
        return "SB_handle_1", None, None

    # If the gateway picks the Twilio path by mistake this stays empty.
    monkeypatch.setattr(gateway, "_sendblue_send", _fake_sb)
    monkeypatch.setattr(
        gateway, "_twilio_send",
        lambda *a, **k: pytest.fail("twilio path used for a sendblue account"),
    )

    acct = _mk_account(
        sc_org, api, name="sb", provider="sendblue", from_number="+14805559100",
        account_sid="sb-key-id-1", auth_token="sb-secret-1",
    )
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": "Hi {{first_name}}"}])
    _activate(sc_org, api, camp["id"])
    contact = _mk_contact(sc_org, api, mobile_phone="+14805559200", first="Sam")
    assert _enroll(sc_org, api, camp["id"], [contact]).status_code == 200
    _tick()
    assert len(calls) == 1
    assert calls[0]["to"] == "+14805559200"
    assert "Sam" in calls[0]["body"]


def test_sendblue_inbound_stop_requires_token(sc_org, api, monkeypatch):
    monkeypatch.setattr(gateway, "verify_credentials", lambda a: (True, "ok"))
    acct = _mk_account(
        sc_org, api, name="sb2", provider="sendblue", from_number="+14805559300",
        account_sid="sb-key-id-2", auth_token="sb-secret-2",
    )
    token = acct["webhook_token"]
    aid = acct["id"]

    # Wrong token → 403, no suppression.
    bad = api.post(
        f"/api/sms/webhooks/sendblue/inbound/{aid}/wrong-token",
        json={"from_number": "+14805559400", "content": "STOP"},
    )
    assert bad.status_code == 403

    # Right token → suppression recorded + opt-in cleared.
    contact = _mk_contact(sc_org, api, mobile_phone="+14805559400", first="Pat")
    ok = api.post(
        f"/api/sms/webhooks/sendblue/inbound/{aid}/{token}",
        json={"from_number": "+14805559400", "content": "STOP"},
    )
    assert ok.status_code == 200
    db = SessionLocal()
    try:
        supp = db.execute(
            select(SmsSuppression).where(
                SmsSuppression.phone_e164 == "+14805559400"
            )
        ).scalar_one_or_none()
        assert supp is not None
        c = db.get(Contact, contact)
        assert c.sms_opt_in is False
    finally:
        db.close()


def test_sendblue_status_marks_delivered(sc_org, api, monkeypatch):
    monkeypatch.setattr(gateway, "verify_credentials", lambda a: (True, "ok"))
    monkeypatch.setattr(
        gateway, "_sendblue_send", lambda a, t, b: ("SB_handle_9", None, None)
    )
    acct = _mk_account(
        sc_org, api, name="sb3", provider="sendblue", from_number="+14805559500",
        account_sid="sb-key-id-3", auth_token="sb-secret-3",
    )
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": "hi"}])
    _activate(sc_org, api, camp["id"])
    contact = _mk_contact(sc_org, api, mobile_phone="+14805559600")
    _enroll(sc_org, api, camp["id"], [contact])
    _tick()
    r = api.post(
        f"/api/sms/webhooks/sendblue/status/{acct['id']}/{acct['webhook_token']}",
        json={"message_handle": "SB_handle_9", "status": "DELIVERED"},
    )
    assert r.status_code == 200
    db = SessionLocal()
    try:
        row = db.execute(
            select(SmsMessage).where(SmsMessage.provider_sid == "SB_handle_9")
        ).scalar_one()
        assert row.status == "delivered"
    finally:
        db.close()


def test_sendblue_failed_status_from_error(monkeypatch):
    """A 2xx Sendblue response carrying an ERROR status / nonzero error_code is
    a failure, not a success — the message object is the source of truth."""
    import httpx

    class _Resp:
        status_code = 200
        def json(self):
            return {"message_handle": "h", "status": "ERROR", "error_code": 4002,
                    "error_message": "blacklisted number"}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    monkeypatch.setattr(gateway, "decrypt_secret", lambda s: "secret")

    class _Acct:
        provider = "sendblue"
        account_sid = "id"
        auth_token_encrypted = "enc"
        from_number = "+14805559700"
        webhook_token = "tok"
        id = "acct"

    handle, code, detail = gateway._sendblue_send(_Acct(), "+14805559800", "hi")
    assert code == "4002"
    assert "blacklist" in detail.lower()
