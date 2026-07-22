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
from app.models.core import Organization
from app.models.crm import Contact
from app.models.sms_outreach import (
    SMS_ENROLL_ACTIVE,
    SmsCampaign,
    SmsEnrollment,
    SmsMessage,
    SmsStep,
    SmsSuppression,
)
from app.services import email_personalize, lead_notify, sms_campaigns
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


# --- personalization upgrade: tokens, #if/spin validation, AI, failsafes ----


def test_step_save_422s_on_bad_if_and_spin(sc_org, api, twilio_creds_ok):
    acct = _mk_account(sc_org, api, from_number="+14805550200")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)

    r = api.put(
        f"/api/sms/campaigns/{camp['id']}/steps",
        json={"steps": [{"position": 1, "body": "{{#if bogus}}x{{/if}}"}]},
        headers=sc_org["headers"],
    )
    assert r.status_code == 422
    assert "bogus" in r.json()["detail"]

    r2 = api.put(
        f"/api/sms/campaigns/{camp['id']}/steps",
        json={"steps": [{"position": 1, "body": "{{#if job_title}}x"}]},
        headers=sc_org["headers"],
    )
    assert r2.status_code == 422
    assert "#if without" in r2.json()["detail"]

    r3 = api.put(
        f"/api/sms/campaigns/{camp['id']}/steps",
        json={"steps": [{"position": 1, "body": "{{spin:only one}}"}]},
        headers=sc_org["headers"],
    )
    assert r3.status_code == 422
    assert "spin with <2 variants" in r3.json()["detail"]

    # SMS's known-token set is narrower than email's — a valid EMAIL-only
    # token is still unknown here.
    r4 = api.put(
        f"/api/sms/campaigns/{camp['id']}/steps",
        json={"steps": [{"position": 1, "body": "{{company_description}}"}]},
        headers=sc_org["headers"],
    )
    assert r4.status_code == 422
    assert "company_description" in r4.json()["detail"]

    ok = _set_steps(
        sc_org,
        api,
        camp["id"],
        [
            {
                "position": 1,
                "body": "{{#if job_title}}Hi {{job_title}}{{else}}Hi{{/if}} {{spin:one|two}}",
                "ai_instructions": "Mention their city.",
            }
        ],
    )
    assert ok["steps"][0]["ai_instructions"] == "Mention their city."


def test_sms_ai_snippet_generated_cached_and_metered_once(
    sc_org, api, twilio_creds_ok, monkeypatch
):
    calls = {"n": 0}

    def _fake_call(system, user_content, max_tokens=300):
        calls["n"] += 1
        return "Loved your recent Denver expansion.", 10, 6

    monkeypatch.setattr(email_personalize, "_call_model", _fake_call)
    monkeypatch.setattr(
        email_personalize.ai_insights, "check_allowance", lambda db, org: None
    )

    acct = _mk_account(sc_org, api, from_number="+14805550201")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805559001", city="Denver")
    _set_steps(
        sc_org,
        api,
        camp["id"],
        [{"position": 1, "body": "Hi {{first_name}}! {{ai_snippet}}", "ai_instructions": "Mention their city."}],
    )
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])

    db = SessionLocal()
    try:
        e = db.execute(
            select(SmsEnrollment).where(
                SmsEnrollment.campaign_id == camp["id"],
                SmsEnrollment.contact_id == contact,
            )
        ).scalar_one()
        org = db.get(Organization, sc_org["org"])
        step = db.execute(
            select(SmsStep).where(SmsStep.campaign_id == camp["id"])
        ).scalars().first()
        c = db.get(Contact, contact)
        body1 = sms_campaigns.render_full(db, org, e, step, contact=c)
        assert "Loved your recent Denver expansion." in body1
        body2 = sms_campaigns.render_full(db, org, e, step, contact=c)
        assert body1 == body2
        assert calls["n"] == 1  # cached, not re-billed
        assert e.ai_snippets and step.id in e.ai_snippets
        # Exit manually — this enrollment is still `active`/due, and a later
        # test's run_due() call processes ALL due enrollments in the shared
        # module-scoped DB, not just its own campaign's.
        sms_campaigns.exit_manual(db, e)
        db.commit()
    finally:
        db.close()

    # Preview (enrollment=None) generates fresh, never caches.
    def _boom(system, user_content, max_tokens=300):
        raise RuntimeError("model timeout")

    monkeypatch.setattr(email_personalize, "_call_model", _boom)
    db = SessionLocal()
    try:
        org = db.get(Organization, sc_org["org"])
        c = db.get(Contact, contact)
        step = db.execute(
            select(SmsStep).where(SmsStep.campaign_id == camp["id"])
        ).scalars().first()
        snippet = sms_campaigns.generate_ai_snippet(db, org, c, step)
        # Transient failure → None (never cached, retried next render);
        # the render path's `or ""` keeps sends unblocked either way.
        assert snippet is None
    finally:
        db.close()


def test_sms_transient_ai_failure_not_cached_and_retried(
    sc_org, api, twilio_creds_ok, monkeypatch
):
    """Mirror of the email module's fix: a transient AI failure must NOT be
    cached as an empty snippet on the enrollment — the next render retries
    once the key/cap is fixed. Also pins the SMS snippet path to the cheap
    OUTREACH model (resolve_outreach, Haiku on Anthropic)."""
    from app.services import ai_provider

    monkeypatch.setattr(
        email_personalize.ai_insights, "check_allowance", lambda db, org: None
    )
    acct = _mk_account(sc_org, api, from_number="+14805550777")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805558801", first="Rita")
    _set_steps(
        sc_org, api, camp["id"],
        [{"position": 1, "body": "Hi {{first_name}}! {{ai_snippet}}", "ai_instructions": "Mention their city."}],
    )
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])

    def _boom(system, user_content, max_tokens=300):
        raise RuntimeError("model timeout")

    monkeypatch.setattr(email_personalize, "_call_model", _boom)
    db = SessionLocal()
    try:
        e = db.execute(
            select(SmsEnrollment).where(
                SmsEnrollment.campaign_id == camp["id"],
                SmsEnrollment.contact_id == contact,
            )
        ).scalar_one()
        org = db.get(Organization, sc_org["org"])
        step = db.execute(
            select(SmsStep).where(SmsStep.campaign_id == camp["id"])
        ).scalars().first()
        c = db.get(Contact, contact)
        body = sms_campaigns.render_full(db, org, e, step, contact=c)
        assert body.strip() == "Hi Rita!"  # send not blocked, just unpersonalized
        assert not (e.ai_snippets or {})  # the failure was NOT cached
        db.commit()
    finally:
        db.close()

    # "Key fixed" — the next render generates and caches this time.
    seen = {}

    def _ok(system, user_content, max_tokens=300):
        seen["model"] = ai_provider.current().model
        return "Loved the new Mesa location.", 5, 3

    monkeypatch.setattr(email_personalize, "_call_model", _ok)
    db = SessionLocal()
    try:
        e = db.execute(
            select(SmsEnrollment).where(
                SmsEnrollment.campaign_id == camp["id"],
                SmsEnrollment.contact_id == contact,
            )
        ).scalar_one()
        org = db.get(Organization, sc_org["org"])
        step = db.execute(
            select(SmsStep).where(SmsStep.campaign_id == camp["id"])
        ).scalars().first()
        c = db.get(Contact, contact)
        body = sms_campaigns.render_full(db, org, e, step, contact=c)
        assert "Loved the new Mesa location." in body
        assert e.ai_snippets and step.id in e.ai_snippets
        # Exit — this enrollment is still active/due in the shared module DB.
        sms_campaigns.exit_manual(db, e)
        db.commit()
    finally:
        db.close()
    # Default provider is gemini; outreach snippets use its cheap tier.
    assert seen["model"] == "gemini-2.5-flash"


def test_sms_render_empty_exits_enrollment(sc_org, api, twilio_creds_ok, captured_sends):
    acct = _mk_account(sc_org, api, from_number="+14805550202")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805559002")  # no company
    _set_steps(
        sc_org, api, camp["id"], [{"position": 1, "body": "{{#if company}}Hi {{company}}{{/if}}"}]
    )
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])
    sms_campaigns.run_due(SessionLocal())

    e = _get_enrollment(camp["id"], contact)
    assert e.status == "exited"
    assert e.exit_reason == "render_empty"
    assert captured_sends == []


def test_sms_render_error_exits_enrollment_on_unclosed_conditional(
    sc_org, api, twilio_creds_ok, captured_sends
):
    """An unclosed {{#if}} would only reach the engine via a template saved
    before this guardrail, or a bug in the save-time validator — plant it
    directly to prove the engine is defensive regardless."""
    acct = _mk_account(sc_org, api, from_number="+14805550203")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805559003", job_title="Owner")

    db = SessionLocal()
    try:
        campaign_row = db.get(SmsCampaign, camp["id"])
        db.add(
            SmsStep(
                organization_id=campaign_row.organization_id,
                campaign_id=camp["id"],
                position=1,
                wait_days=0,
                body_template="Hi {{#if job_title}}there",
            )
        )
        campaign_row.status = "active"
        db.commit()
    finally:
        db.close()

    _enroll(sc_org, api, camp["id"], [contact])
    sms_campaigns.run_due(SessionLocal())

    e = _get_enrollment(camp["id"], contact)
    assert e.status == "exited"
    assert e.exit_reason == "render_error"
    assert captured_sends == []


def test_sms_too_long_exits_enrollment(sc_org, api, twilio_creds_ok, captured_sends):
    acct = _mk_account(sc_org, api, from_number="+14805550204")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805559004")
    long_body = "A" * 500  # > 3 segments (3 * 153 = 459)
    assert sms_campaigns.segment_count(long_body) > sms_campaigns.MAX_RENDERED_SEGMENTS
    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": long_body}])
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])
    sms_campaigns.run_due(SessionLocal())

    e = _get_enrollment(camp["id"], contact)
    assert e.status == "exited"
    assert e.exit_reason == "too_long"
    assert captured_sends == []


def test_compliance_footer_defaults_on_and_can_be_disabled_per_campaign(
    sc_org, api, twilio_creds_ok, captured_sends
):
    # Default: step 1 gets the org-name prefix + "Reply STOP to opt out".
    acct = _mk_account(sc_org, api, from_number="+14805550301")
    camp_on = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    assert camp_on["include_compliance_footer"] is True
    contact_on = _mk_contact(sc_org, api, mobile_phone="4805559101", first="Onward")
    _set_steps(sc_org, api, camp_on["id"], [{"position": 1, "body": "Hi {{first_name}}"}])
    assert _activate(sc_org, api, camp_on["id"]).status_code == 200
    _enroll(sc_org, api, camp_on["id"], [contact_on])
    _tick()
    assert len(captured_sends) == 1
    assert "reply stop" in captured_sends[-1]["body"].lower()

    # Opted off: same first-step body, no footer, no org-name prefix.
    acct2 = _mk_account(sc_org, api, from_number="+14805550302")
    camp_off = _mk_campaign(
        sc_org, api, acct2["id"], include_compliance_footer=False, **_ALWAYS
    )
    assert camp_off["include_compliance_footer"] is False
    contact_off = _mk_contact(sc_org, api, mobile_phone="4805559102", first="Skip")
    _set_steps(sc_org, api, camp_off["id"], [{"position": 1, "body": "Hi {{first_name}}"}])
    assert _activate(sc_org, api, camp_off["id"]).status_code == 200
    _enroll(sc_org, api, camp_off["id"], [contact_off])
    _tick()
    assert len(captured_sends) == 2
    assert captured_sends[-1]["body"] == "Hi Skip"
    assert "stop" not in captured_sends[-1]["body"].lower()

    # STOP handling is unaffected by the toggle either way.
    e = _get_enrollment(camp_off["id"], contact_off)
    assert e.status in ("active", "completed")


# --- one-off manual send (individual messenger) ------------------------------


def test_compose_sends_one_off_with_no_compliance_footer(
    sc_org, api, twilio_creds_ok, captured_sends
):
    acct = _mk_account(sc_org, api, from_number="+14805550401")
    contact = _mk_contact(sc_org, api, mobile_phone="4805559201", first="Riley")
    r = api.post(
        "/api/sms/compose",
        json={"account_id": acct["id"], "contact_id": contact, "body": "Hey, following up!"},
        headers=sc_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent"
    assert len(captured_sends) == 1
    assert captured_sends[-1]["body"] == "Hey, following up!"
    assert "stop" not in captured_sends[-1]["body"].lower()

    # Shows up in the flat message log, kind=manual, with contact + sent_at
    # attached — the Messages tab groups conversations by contact.id, so a
    # missing contact here would silently merge every lead into one thread.
    msgs = api.get(
        f"/api/sms/messages?contact_id={contact}", headers=sc_org["headers"]
    ).json()
    assert msgs[0]["kind"] == "manual"
    assert msgs[0]["contact"]["id"] == contact
    assert msgs[0]["contact"]["first_name"] == "Riley"
    assert msgs[0]["sent_at"] is not None
    assert msgs[0]["received_at"] is None


def test_compose_blocked_without_consent(sc_org, api, twilio_creds_ok, captured_sends):
    acct = _mk_account(sc_org, api, from_number="+14805550402")
    contact = _mk_contact(
        sc_org, api, mobile_phone="4805559202", first="NoConsent", opt_in=False
    )
    r = api.post(
        "/api/sms/compose",
        json={"account_id": acct["id"], "contact_id": contact, "body": "Hi there"},
        headers=sc_org["headers"],
    )
    assert r.status_code == 409
    assert captured_sends == []


def test_compose_rejects_over_segment_cap(sc_org, api, twilio_creds_ok, captured_sends):
    acct = _mk_account(sc_org, api, from_number="+14805550403")
    contact = _mk_contact(sc_org, api, mobile_phone="4805559203", first="Long")
    r = api.post(
        "/api/sms/compose",
        json={"account_id": acct["id"], "contact_id": contact, "body": "A" * 500},
        headers=sc_org["headers"],
    )
    assert r.status_code == 422
    assert captured_sends == []


def test_compose_cross_org_contact_404s(sc_org, api, twilio_creds_ok, team_headers):
    # team_headers belongs to a different org (see test_campaign_tenant_isolation).
    acct = _mk_account(sc_org, api, from_number="+14805550404")
    contact = _mk_contact(sc_org, api, mobile_phone="4805559204")
    r = api.post(
        "/api/sms/compose",
        json={"account_id": acct["id"], "contact_id": contact, "body": "Hi"},
        headers=team_headers,
    )
    assert r.status_code == 404


# --- read tracking (Sendblue/iMessage read receipts + our own inbox) --------


def test_sendblue_read_receipt_sets_status_and_read_at(sc_org, api, monkeypatch):
    monkeypatch.setattr(gateway, "verify_credentials", lambda a: (True, "ok"))
    monkeypatch.setattr(
        gateway, "_sendblue_send", lambda a, t, b: ("SB_handle_read1", None, None)
    )
    acct = _mk_account(
        sc_org, api, name="sb-read", provider="sendblue", from_number="+14805559700",
        account_sid="sb-key-id-4", auth_token="sb-secret-4",
    )
    contact = _mk_contact(sc_org, api, mobile_phone="4805559701", first="Reader")
    r = api.post(
        "/api/sms/compose",
        json={"account_id": acct["id"], "contact_id": contact, "body": "Hi there"},
        headers=sc_org["headers"],
    )
    assert r.status_code == 200, r.text

    r = api.post(
        f"/api/sms/webhooks/sendblue/status/{acct['id']}/{acct['webhook_token']}",
        json={"message_handle": "SB_handle_read1", "status": "READ"},
    )
    assert r.status_code == 200

    msgs = api.get(
        f"/api/sms/messages?contact_id={contact}", headers=sc_org["headers"]
    ).json()
    assert msgs[0]["status"] == "read"
    assert msgs[0]["read_at"] is not None


def test_twilio_status_webhook_ignores_unknown_status(sc_org, api, twilio_creds_ok, monkeypatch):
    # A status string _apply_status doesn't recognize is a safe no-op, not a
    # crash — the row's existing status/read_at are left untouched. Uses its
    # own unique provider_sid rather than the captured_sends fixture's
    # shared hardcoded "SM_test_sid" — _apply_status looks a message up by
    # sid+org, and that literal is reused by many other tests sharing this
    # module-scoped org, which makes a plain lookup ambiguous.
    def _fake_send(account, to_number, body):
        return "SM_unique_ignore_status_test", None, None

    monkeypatch.setattr(gateway, "_twilio_send", _fake_send)
    acct = _mk_account(sc_org, api, from_number="+14805550501")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": "hi"}])
    _activate(sc_org, api, camp["id"])
    contact = _mk_contact(sc_org, api, mobile_phone="4805559501")
    _enroll(sc_org, api, camp["id"], [contact])
    _tick()
    params = {"MessageSid": "SM_unique_ignore_status_test", "MessageStatus": "queued"}
    auth_token = _AUTH_TOKEN
    url = f"http://testserver/api/sms/webhooks/status/{acct['id']}"
    sig = _twilio_signature(auth_token, url, params)
    r = api.post(
        f"/api/sms/webhooks/status/{acct['id']}",
        data=params,
        headers={"X-Twilio-Signature": sig},
    )
    assert r.status_code == 200
    db = SessionLocal()
    try:
        row = db.execute(
            select(SmsMessage).where(
                SmsMessage.provider_sid == "SM_unique_ignore_status_test"
            )
        ).scalar_one()
        assert row.read_at is None
        assert row.status == "sent"
    finally:
        db.close()


def test_mark_read_only_clears_inbound_unread_for_that_contact(sc_org, api, twilio_creds_ok, captured_sends):
    acct = _mk_account(sc_org, api, from_number="+14805550502")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": "hi"}])
    _activate(sc_org, api, camp["id"])
    contact = _mk_contact(sc_org, api, mobile_phone="4805559502", first="Marked")
    _enroll(sc_org, api, camp["id"], [contact])
    _tick()  # step 1 goes out (direction=out, unaffected by mark-read)

    # Simulate an inbound reply.
    params = {"From": "+14805559502", "To": acct["from_number"], "Body": "sounds good"}
    auth_token = _AUTH_TOKEN
    url = f"http://testserver/api/sms/webhooks/inbound/{acct['id']}"
    sig = _twilio_signature(auth_token, url, params)
    r = api.post(
        f"/api/sms/webhooks/inbound/{acct['id']}", data=params,
        headers={"X-Twilio-Signature": sig},
    )
    assert r.status_code == 200

    msgs = api.get(
        f"/api/sms/messages?contact_id={contact}", headers=sc_org["headers"]
    ).json()
    inbound = [m for m in msgs if m["direction"] == "in"]
    assert len(inbound) == 1
    assert inbound[0]["read_at"] is None

    r = api.post(
        "/api/sms/messages/mark-read", json={"contact_id": contact},
        headers=sc_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["marked"] == 1

    msgs = api.get(
        f"/api/sms/messages?contact_id={contact}", headers=sc_org["headers"]
    ).json()
    inbound = [m for m in msgs if m["direction"] == "in"]
    outbound = [m for m in msgs if m["direction"] == "out"]
    assert inbound[0]["read_at"] is not None
    assert outbound[0]["read_at"] is None  # mark-read never touches outbound rows

    # Idempotent — nothing left to mark.
    r = api.post(
        "/api/sms/messages/mark-read", json={"contact_id": contact},
        headers=sc_org["headers"],
    )
    assert r.json()["marked"] == 0


def test_mark_read_cross_org_contact_404s(sc_org, api, team_headers):
    contact = _mk_contact(sc_org, api, mobile_phone="4805559503")
    r = api.post(
        "/api/sms/messages/mark-read", json={"contact_id": contact},
        headers=team_headers,
    )
    assert r.status_code == 404


# --- render failsafes (business-name greeting + AI city inference) -----------


def test_sms_failsafe_business_name_greeting_city_is_plain_field(
    sc_org, api, twilio_creds_ok, monkeypatch
):
    """The AI-inference-when-blank city failsafe is disabled (proved
    unreliable in practice); {{city}} is now a plain contact.city lookup,
    same as {{state}} — no AI call is ever attempted for it. The
    business-name greeting failsafe is unrelated and still active."""
    calls = {"n": 0}

    def _fake_call(system, user_content, max_tokens=300):
        calls["n"] += 1
        return "mesa", 8, 3

    monkeypatch.setattr(email_personalize, "_call_model", _fake_call)
    monkeypatch.setattr(
        email_personalize.ai_insights, "check_allowance", lambda db, org: None
    )

    acct = _mk_account(sc_org, api, from_number="+14805550881")
    camp = _mk_campaign(sc_org, api, acct["id"])
    contact = _mk_contact(
        sc_org,
        api,
        mobile_phone="4805559301",
        first=None,
        last=None,
        phone="4805551000",  # create-check needs email/phone/name; leads have a business line
        company_name="DESERT AIR HVAC LLC",
        state="AZ",
    )
    _set_steps(
        sc_org,
        api,
        camp["id"],
        [{"position": 1, "body": "Hi {{first_name|there}} — great work in {{city}}!"}],
    )
    db = SessionLocal()
    try:
        c = db.get(Contact, contact)
        step = (
            db.execute(select(SmsStep).where(SmsStep.campaign_id == camp["id"]))
            .scalars()
            .first()
        )
        body = sms_campaigns.render_body(db, c, step)
        # The business-name failsafe beats the explicit |there fallback (a
        # named greeting is the point), with acronym-aware proper casing.
        # City is blank on this contact and nothing infers it — the tidy
        # pass collapses the emptied {{city}} token and its trailing space.
        assert body == "Hi Desert Air HVAC LLC — great work in!"
        assert c.city is None
        assert calls["n"] == 0  # no AI call attempted for city, ever
    finally:
        db.close()


def test_sms_city_fallback_token_when_blank(sc_org, api, twilio_creds_ok):
    """No AI inference is attempted for {{city}} (disabled — see render_body);
    a blank city with an explicit template |fallback just renders the
    fallback, same as any other missing token."""
    acct = _mk_account(sc_org, api, from_number="+14805550882")
    camp = _mk_campaign(sc_org, api, acct["id"])
    contact = _mk_contact(
        sc_org, api, mobile_phone="4805559302", first=None, last=None, phone="4805551001"
    )  # no name AND no company
    _set_steps(
        sc_org,
        api,
        camp["id"],
        [{"position": 1, "body": "Hi {{first_name|there}}, how is {{city|your area}}?"}],
    )
    db = SessionLocal()
    try:
        c = db.get(Contact, contact)
        step = (
            db.execute(select(SmsStep).where(SmsStep.campaign_id == camp["id"]))
            .scalars()
            .first()
        )
        body = sms_campaigns.render_body(db, c, step)
        assert body == "Hi there, how is your area?"
        assert c.city is None
    finally:
        db.close()


def test_clean_city_guard_discards_garbage():
    """_clean_city is kept (uncalled by render_body for now — see its
    docstring) but still unit-tested on its own, since it's a one-line
    re-enable away from being live again."""
    assert sms_campaigns._clean_city("call 480-555-1212") == ""
    assert sms_campaigns._clean_city("It is probably Mesa, Arizona.") != "Mesa"


def test_reactivation_rearms_parked_enrollments(sc_org, api, twilio_creds_ok, captured_sends):
    """A tick that catches the campaign paused parks its enrollments
    (next_run_at NULL); reactivating must re-arm them or the audience is
    dormant forever (run_due only scans non-NULL next_run_at) — found live
    with 31 parked enrollments in production."""
    acct = _mk_account(sc_org, api, from_number="+14805550883")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805559401", first="Parked")
    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": "Hi {{first_name}}"}])
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])
    assert _get_enrollment(camp["id"], contact).next_run_at is not None

    # Pause, then let a tick catch the due enrollment → parked, still active.
    r = api.post(f"/api/sms/campaigns/{camp['id']}/pause", headers=sc_org["headers"])
    assert r.status_code == 200
    _tick()
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "active" and e.next_run_at is None

    # Reactivate → re-armed with a real schedule again.
    r = api.post(f"/api/sms/campaigns/{camp['id']}/activate", headers=sc_org["headers"])
    assert r.status_code == 200, r.text
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "active" and e.next_run_at is not None

    # Account-reconnect path re-arms too: park again via pause+tick+activate-
    # while-account-down is overkill — directly park and hit the test button.
    db = SessionLocal()
    try:
        en = db.execute(
            select(SmsEnrollment).where(SmsEnrollment.id == e.id)
        ).scalar_one()
        en.next_run_at = None
        db.commit()
    finally:
        db.close()
    r = api.post(f"/api/sms/accounts/{acct['id']}/test", headers=sc_org["headers"])
    assert r.status_code == 200 and r.json()["ok"] is True
    e = _get_enrollment(camp["id"], contact)
    assert e.next_run_at is not None

    # Exit so later tests' run_due in the shared module DB never sends it.
    db = SessionLocal()
    try:
        en = db.execute(
            select(SmsEnrollment).where(SmsEnrollment.id == e.id)
        ).scalar_one()
        sms_campaigns.exit_manual(db, en)
        db.commit()
    finally:
        db.close()


def test_account_reconnect_revives_errored_enrollments(
    sc_org, api, twilio_creds_ok, monkeypatch
):
    """A hard provider failure ends its enrollment in `error` (broken
    account). Reconnecting the account must revive it too — re-arming only
    the PARKED rows would drop whoever hit the broken account first, forever
    (mirror of the email module's fix)."""
    acct = _mk_account(sc_org, api, from_number="+14805550778")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805558802", first="Errol")
    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": "Hi {{first_name}}"}])
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])

    # Hard provider failure at tick time → enrollment errors out.
    monkeypatch.setattr(
        gateway,
        "_twilio_send",
        lambda account, to_number, body: ("", "30001", "provider down"),
    )
    _tick()
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "error" and e.next_run_at is None

    # Account fixed → the /test button re-arms parked AND revives errored.
    r = api.post(f"/api/sms/accounts/{acct['id']}/test", headers=sc_org["headers"])
    assert r.status_code == 200 and r.json()["ok"] is True
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "active"
    assert e.exit_reason is None and e.ended_at is None
    assert e.next_run_at is not None

    # Exit so later tests' run_due in the shared module DB never sends it.
    db = SessionLocal()
    try:
        en = db.execute(
            select(SmsEnrollment).where(SmsEnrollment.id == e.id)
        ).scalar_one()
        sms_campaigns.exit_manual(db, en)
        db.commit()
    finally:
        db.close()



# --- lead SMS notifications (text-the-team alerts, services/lead_notify.py) --
# Each test gets its OWN fresh org (unique signup email): notify_new_lead
# picks the org's first ACTIVE SmsAccount, and sc_org accumulates dozens of
# accounts across this whole module — reusing any shared org would make
# "which account sent it" / "does this org already have an account"
# nondeterministic depending on test order.

import uuid as _uuid


@pytest.fixture()
def ln_org(api):
    email = f"owner-{_uuid.uuid4().hex[:12]}@leadnotify.co"
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Lead Notify Co",
            "email": email,
            "password": "leadnotify-pass-1",
            "full_name": "Notify Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "Notify Client"}, headers=headers
    ).json()["id"]
    return {"org": body["organization_id"], "headers": headers, "client": client_id}


def _enable_notifications(api, ln_org, phones):
    r = api.put(
        "/api/orgs/me/lead-notifications",
        json={"enabled": True, "phones": phones},
        headers=ln_org["headers"],
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_lead_notifications_settings_roundtrip(ln_org, api):
    r = api.get("/api/orgs/me/lead-notifications", headers=ln_org["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False
    assert body["phones"] == []
    assert body["message_template"] is None
    assert body["default_template"] == lead_notify.DEFAULT_TEMPLATE

    # Formatting variants normalize to the same E.164 and dedupe.
    saved = _enable_notifications(api, ln_org, ["(480) 555-9991", "+14805559991"])
    assert saved["enabled"] is True
    assert saved["phones"] == ["+14805559991"]
    assert saved["message_template"] is None  # untouched, still default

    r = api.get("/api/orgs/me/lead-notifications", headers=ln_org["headers"])
    assert r.json()["phones"] == ["+14805559991"]

    # Unparseable phone -> 422, nothing saved.
    r = api.put(
        "/api/orgs/me/lead-notifications",
        json={"enabled": True, "phones": ["not-a-phone"]},
        headers=ln_org["headers"],
    )
    assert r.status_code == 422

    # Over the cap -> 400.
    r = api.put(
        "/api/orgs/me/lead-notifications",
        json={"enabled": True, "phones": [f"+1480555{n:04d}" for n in range(11)]},
        headers=ln_org["headers"],
    )
    assert r.status_code == 400

    # A custom template saves and reads back; a blank one resets to default.
    r = api.put(
        "/api/orgs/me/lead-notifications",
        json={
            "enabled": True,
            "phones": ["+14805559991"],
            "message_template": "Lead: {{name}} ({{brand}})",
        },
        headers=ln_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["message_template"] == "Lead: {{name}} ({{brand}})"

    r = api.put(
        "/api/orgs/me/lead-notifications",
        json={"enabled": True, "phones": ["+14805559991"], "message_template": ""},
        headers=ln_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["message_template"] is None

    # An unknown token is rejected at save time.
    r = api.put(
        "/api/orgs/me/lead-notifications",
        json={
            "enabled": True,
            "phones": ["+14805559991"],
            "message_template": "Lead: {{nickname}}",
        },
        headers=ln_org["headers"],
    )
    assert r.status_code == 422


def test_new_lead_triggers_sms_notification_to_configured_numbers(
    ln_org, api, twilio_creds_ok, captured_sends
):
    acct = _mk_account(ln_org, api, from_number="+14805559992")
    _enable_notifications(api, ln_org, ["+14805559991"])

    r = api.post(
        "/api/track/lead",
        json={
            "client_id": ln_org["client"],
            "session_key": "notify-sess-1",
            "email": "newlead@example.com",
            "phone": "4805551234",
            "first_name": "Newt",
            "last_name": "Leadman",
            "zip": "85001",
        },
    )
    assert r.status_code == 201, r.text

    assert len(captured_sends) == 1
    assert captured_sends[0]["to"] == "+14805559991"
    assert captured_sends[0]["account_id"] == acct["id"]
    body = captured_sends[0]["body"]
    assert body == (
        "*NEW LEAD*\n"
        "Name: Newt Leadman\n"
        "Phone: 4805551234\n"
        f"Brand: Notify Client\n"
        "Email: newlead@example.com\n"
        "Zip Code: 85001"
    )

    db = SessionLocal()
    try:
        msg = db.execute(
            select(SmsMessage).where(
                SmsMessage.to_number == "+14805559991",
                SmsMessage.organization_id == ln_org["org"],
            )
        ).scalar_one()
        assert msg.kind == "notification"
        assert msg.contact_id is None
        assert msg.status == "sent"
    finally:
        db.close()

    # A resubmission of the SAME lead updates rather than re-notifying.
    r = api.post(
        "/api/track/lead",
        json={
            "client_id": ln_org["client"],
            "session_key": "notify-sess-2",
            "email": "newlead@example.com",
        },
    )
    assert r.status_code == 201
    assert len(captured_sends) == 1  # unchanged


def test_lead_notification_off_by_default_sends_nothing(
    ln_org, api, twilio_creds_ok, captured_sends
):
    _mk_account(ln_org, api, from_number="+14805559993")
    r = api.post(
        "/api/track/lead",
        json={
            "client_id": ln_org["client"],
            "session_key": "notify-sess-off",
            "email": "quietlead@example.com",
        },
    )
    assert r.status_code == 201
    assert captured_sends == []


def test_lead_notification_skips_silently_with_no_active_account(ln_org, api):
    """Enabled + a configured phone but no active SMS account: the lead
    still gets created successfully, nothing crashes."""
    _enable_notifications(api, ln_org, ["+14805559991"])
    # No SmsAccount at all for this fresh org.
    r = api.post(
        "/api/track/lead",
        json={
            "client_id": ln_org["client"],
            "session_key": "notify-sess-noaccount",
            "email": "noaccountlead@example.com",
        },
    )
    assert r.status_code == 201, r.text

    db = SessionLocal()
    try:
        assert (
            db.execute(
                select(SmsMessage).where(
                    SmsMessage.kind == "notification",
                    SmsMessage.organization_id == ln_org["org"],
                )
            ).scalar_one_or_none()
            is None
        )
    finally:
        db.close()


def test_lead_notification_provider_failure_never_blocks_lead_creation(
    ln_org, api, twilio_creds_ok, monkeypatch
):
    """A Twilio outage while sending the alert must not cost the lead that
    was just successfully created — the notification is a best-effort side
    effect, never load-bearing for the request that triggered it."""
    _mk_account(ln_org, api, from_number="+14805559994")
    _enable_notifications(api, ln_org, ["+14805559991"])

    def _boom(account, to_number, body):
        raise gateway.SmsProviderError("Twilio is unreachable: simulated outage")

    monkeypatch.setattr(gateway, "_twilio_send", _boom)

    r = api.post(
        "/api/track/lead",
        json={
            "client_id": ln_org["client"],
            "session_key": "notify-sess-outage",
            "email": "outagelead@example.com",
        },
    )
    assert r.status_code == 201, r.text

    db = SessionLocal()
    try:
        contact = db.execute(
            select(Contact).where(Contact.email == "outagelead@example.com")
        ).scalar_one_or_none()
        assert contact is not None  # the lead itself was created fine
        failed = db.execute(
            select(SmsMessage).where(
                SmsMessage.kind == "notification",
                SmsMessage.to_number == "+14805559991",
                SmsMessage.organization_id == ln_org["org"],
                SmsMessage.status == "failed",
            )
        ).scalar_one_or_none()
        assert failed is not None
    finally:
        db.close()


def test_lead_notification_prefers_bluebubbles_over_other_active_accounts(
    ln_org, api, twilio_creds_ok, monkeypatch
):
    """BlueBubbles reads as a human ping from a real number rather than a
    shortcode blast, so it's preferred over any other connected provider —
    even one created first."""
    _mk_account(ln_org, api, from_number="+14805559995")  # twilio, created first

    def _boom_twilio(*a, **k):
        raise AssertionError("_twilio_send called even though BlueBubbles is connected")

    monkeypatch.setattr(gateway, "_twilio_send", _boom_twilio)

    bb_sent = []

    def _fake_bb(account, to_number, body):
        bb_sent.append({"account_id": account.id, "to": to_number, "body": body})
        return "BB_test_guid", None, None

    monkeypatch.setattr(gateway, "_bluebubbles_send", _fake_bb)

    r = api.post(
        "/api/sms/accounts",
        json={
            "name": "BlueBubbles Line",
            "provider": "bluebubbles",
            "auth_token": "bluebubbles-server-password-123",
            "relay_url": "https://imessage-relay.example.com",
            "from_number": "+14805559996",
        },
        headers=ln_org["headers"],
    )
    assert r.status_code == 201, r.text
    bb_acct = r.json()

    _enable_notifications(api, ln_org, ["+14805559991"])

    r = api.post(
        "/api/track/lead",
        json={
            "client_id": ln_org["client"],
            "session_key": "notify-sess-bb",
            "email": "bblead@example.com",
        },
    )
    assert r.status_code == 201, r.text

    assert len(bb_sent) == 1
    assert bb_sent[0]["account_id"] == bb_acct["id"]
    assert bb_sent[0]["to"] == "+14805559991"


def test_client_lead_notifications_settings_roundtrip(ln_org, api):
    r = api.get(
        f"/api/clients/{ln_org['client']}/lead-notifications",
        headers=ln_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "enabled": False,
        "phones": [],
        "message_template": None,
        "default_template": lead_notify.DEFAULT_TEMPLATE,
    }

    r = api.put(
        f"/api/clients/{ln_org['client']}/lead-notifications",
        json={"enabled": True, "phones": ["(480) 555-9997", "+14805559997"]},
        headers=ln_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "enabled": True,
        "phones": ["+14805559997"],
        "message_template": None,
        "default_template": lead_notify.DEFAULT_TEMPLATE,
    }

    r = api.get(
        f"/api/clients/{ln_org['client']}/lead-notifications",
        headers=ln_org["headers"],
    )
    assert r.json() == {
        "enabled": True,
        "phones": ["+14805559997"],
        "message_template": None,
        "default_template": lead_notify.DEFAULT_TEMPLATE,
    }

    r = api.put(
        f"/api/clients/{ln_org['client']}/lead-notifications",
        json={"enabled": True, "phones": ["garbage"]},
        headers=ln_org["headers"],
    )
    assert r.status_code == 422

    r = api.put(
        f"/api/clients/{ln_org['client']}/lead-notifications",
        json={"enabled": True, "phones": [f"+1480555{n:04d}" for n in range(11)]},
        headers=ln_org["headers"],
    )
    assert r.status_code == 400


def test_client_lead_notifications_combine_with_org_deduped(
    ln_org, api, twilio_creds_ok, captured_sends
):
    """Org-wide ops numbers and this client's own numbers both get texted,
    deduped when the same number appears in both — and the client-level
    setting alone (org notifications left off) is enough to fire."""
    _mk_account(ln_org, api, from_number="+14805559998")

    # Only the client-level number is configured; org notifications stay off.
    r = api.put(
        f"/api/clients/{ln_org['client']}/lead-notifications",
        json={"enabled": True, "phones": ["+14805559991"]},
        headers=ln_org["headers"],
    )
    assert r.status_code == 200, r.text

    r = api.post(
        "/api/track/lead",
        json={
            "client_id": ln_org["client"],
            "session_key": "notify-sess-client-only",
            "email": "clientonlylead@example.com",
        },
    )
    assert r.status_code == 201, r.text
    assert sorted(s["to"] for s in captured_sends) == ["+14805559991"]

    # Now also enable org-wide with an overlapping AND a distinct number.
    _enable_notifications(api, ln_org, ["+14805559991", "+14805559999"])
    captured_sends.clear()

    r = api.post(
        "/api/track/lead",
        json={
            "client_id": ln_org["client"],
            "session_key": "notify-sess-both",
            "email": "bothlead@example.com",
        },
    )
    assert r.status_code == 201, r.text
    # +14805559991 appears in both configs but is texted only once.
    assert sorted(s["to"] for s in captured_sends) == [
        "+14805559991",
        "+14805559999",
    ]


def test_client_lead_notification_template_overrides_org(
    ln_org, api, twilio_creds_ok, captured_sends
):
    """A per-client template overrides the org-wide one for that client's
    leads — every recipient (org-wide + client) gets the client body. Clearing
    it falls back to the org template; unknown tokens are rejected at save."""
    _mk_account(ln_org, api, from_number="+14805559989")
    # Org-wide: one number + a distinctive org template.
    r = api.put(
        "/api/orgs/me/lead-notifications",
        json={
            "enabled": True,
            "phones": ["+14805559991"],
            "message_template": "ORG {{name}}",
        },
        headers=ln_org["headers"],
    )
    assert r.status_code == 200, r.text
    # Per-client: its own number + its own template.
    r = api.put(
        f"/api/clients/{ln_org['client']}/lead-notifications",
        json={
            "enabled": True,
            "phones": ["+14805559992"],
            "message_template": "Hi {{first_name}} for {{brand}}",
        },
        headers=ln_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["message_template"] == "Hi {{first_name}} for {{brand}}"

    r = api.post(
        "/api/track/lead",
        json={
            "client_id": ln_org["client"],
            "session_key": "notify-tmpl",
            "email": "tmpl@example.com",
            "first_name": "Cara",
            "last_name": "Lee",
        },
    )
    assert r.status_code == 201, r.text
    # Both recipients get the CLIENT template, not the org one.
    assert {s["body"] for s in captured_sends} == {"Hi Cara for Notify Client"}
    assert sorted(s["to"] for s in captured_sends) == [
        "+14805559991",
        "+14805559992",
    ]

    # Unknown token rejected at save.
    r = api.put(
        f"/api/clients/{ln_org['client']}/lead-notifications",
        json={"enabled": True, "phones": ["+14805559992"], "message_template": "{{bogus}}"},
        headers=ln_org["headers"],
    )
    assert r.status_code == 422

    # Clearing the template falls back to the org-wide template.
    r = api.put(
        f"/api/clients/{ln_org['client']}/lead-notifications",
        json={"enabled": True, "phones": ["+14805559992"], "message_template": ""},
        headers=ln_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["message_template"] is None
    captured_sends.clear()
    r = api.post(
        "/api/track/lead",
        json={
            "client_id": ln_org["client"],
            "session_key": "notify-tmpl-2",
            "email": "tmpl2@example.com",
            "first_name": "Deb",
        },
    )
    assert r.status_code == 201, r.text
    assert {s["body"] for s in captured_sends} == {"ORG Deb"}


def test_render_notification_body_default_and_custom_template():
    from app.models.core import Client
    from app.models.crm import Contact

    client = Client(id="c1", organization_id="o1", name="Acme HVAC")
    contact = Contact(
        id="ct1",
        organization_id="o1",
        client_id="c1",
        first_name="Jane",
        last_name="Doe",
        phone="4805551234",
        email="jane@example.com",
        zip="85001",
        source="landing_page_webhook",
    )
    assert lead_notify.render_notification_body(None, None, client, contact) == (
        "*NEW LEAD*\n"
        "Name: Jane Doe\n"
        "Phone: 4805551234\n"
        "Brand: Acme HVAC\n"
        "Email: jane@example.com\n"
        "Zip Code: 85001"
    )
    custom = lead_notify.render_notification_body(
        None, "{{first_name}} for {{brand}} via {{source}}", client, contact
    )
    assert custom == "Jane for Acme HVAC via landing page webhook"

    # Missing values render blank, never "None".
    bare = Contact(id="ct2", organization_id="o1", client_id="c1")
    assert lead_notify.render_notification_body(None, None, client, bare) == (
        "*NEW LEAD*\nName: New lead\nPhone: \nBrand: Acme HVAC\nEmail: \nZip Code: "
    )


def test_unknown_tokens_rejects_unrecognized_placeholders():
    assert lead_notify.unknown_tokens("Hi {{name}}, brand {{brand}}") == []
    assert lead_notify.unknown_tokens("Hi {{nickname}} {{zip}}") == ["nickname"]


# --- audit fixes: daily-cap counts receipts, footer-aware segment cap,
# AI-config surfacing, inbound-webhook liveness --------------------------------


def test_daily_cap_counts_delivered_rows(sc_org, api, twilio_creds_ok, captured_sends):
    """A message flips sent -> delivered -> read within seconds of a status
    webhook; the daily cap (TCPA volume + cost guard) must keep counting it
    once delivered, or delivered messages fall out of the counter and the cap
    becomes unbounded."""
    import datetime as _dt

    acct = _mk_account(sc_org, api, from_number="+14805550601")
    camp = _mk_campaign(sc_org, api, acct["id"], daily_cap=1, **_ALWAYS)
    a = _mk_contact(sc_org, api, mobile_phone="4805556601", first="Ada")
    b = _mk_contact(sc_org, api, mobile_phone="4805556602", first="Bo")
    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": "Hi {{first_name}}"}])
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [a, b])

    _tick()  # daily_cap=1 → exactly ONE send, the other enrollment parks
    assert len(captured_sends) == 1

    # Flip the sent row to delivered (as the status webhook would) and free the
    # parked enrollment. The cap must STILL count the delivered row.
    db = SessionLocal()
    try:
        row = db.execute(
            select(SmsMessage).where(
                SmsMessage.campaign_id == camp["id"],
                SmsMessage.direction == "out",
            )
        ).scalar_one()
        row.status = "delivered"
        parked = db.execute(
            select(SmsEnrollment).where(
                SmsEnrollment.campaign_id == camp["id"],
                SmsEnrollment.status == SMS_ENROLL_ACTIVE,
            )
        ).scalar_one()
        parked.next_run_at = utcnow() - _dt.timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    _tick()  # delivered row keeps counting → still capped, no new send
    assert len(captured_sends) == 1


def test_segment_cap_measured_with_compliance_footer(
    sc_org, api, twilio_creds_ok, captured_sends
):
    """The gateway prepends "OrgName: " + the STOP footer on step 1, so a body
    that fits at exactly 3 segments bare ships over the cap once the footer is
    added — the guard must measure the footered form."""
    body = "A" * 459  # exactly 3 segments bare (3 * 153)
    assert sms_campaigns.segment_count(body) == sms_campaigns.MAX_RENDERED_SEGMENTS

    # Footer ON (default): pushed over 3 segments → the enrollment exits
    # too_long before any send.
    acct = _mk_account(sc_org, api, from_number="+14805550602")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    on = _mk_contact(sc_org, api, mobile_phone="4805556603", first="Cy")
    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": body}])
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [on])
    _tick()
    e = _get_enrollment(camp["id"], on)
    assert e.status == "exited"
    assert e.exit_reason == "too_long"
    assert captured_sends == []

    # Footer OFF: the identical body stays at exactly 3 segments and sends —
    # proving it was the footer, not the body, that tripped the cap.
    acct2 = _mk_account(sc_org, api, from_number="+14805550603")
    camp2 = _mk_campaign(
        sc_org, api, acct2["id"], include_compliance_footer=False, **_ALWAYS
    )
    off = _mk_contact(sc_org, api, mobile_phone="4805556604", first="De")
    _set_steps(sc_org, api, camp2["id"], [{"position": 1, "body": body}])
    assert _activate(sc_org, api, camp2["id"]).status_code == 200
    _enroll(sc_org, api, camp2["id"], [off])
    _tick()
    e2 = _get_enrollment(camp2["id"], off)
    assert e2.status == "completed"
    assert len(captured_sends) == 1


def test_preview_flags_empty_ai_snippet(sc_org, api, twilio_creds_ok):
    """A step with ai_instructions whose snippet came back empty (AI provider
    unconfigured — fail-open) is flagged so the UI can warn; a plain step is
    not."""
    acct = _mk_account(sc_org, api, from_number="+14805550604")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805556605", first="Fen")
    _set_steps(
        sc_org,
        api,
        camp["id"],
        [
            {
                "position": 1,
                "body": "Hi {{first_name}}. {{ai_snippet}}",
                "ai_instructions": "Mention their business.",
            },
            {"position": 2, "body": "Plain follow-up {{first_name}}"},
        ],
    )
    r1 = api.post(
        f"/api/sms/campaigns/{camp['id']}/preview",
        json={"contact_id": contact, "position": 1},
        headers=sc_org["headers"],
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["ai_snippet_empty"] is True
    assert "{{" not in r1.json()["body"]  # empty snippet rendered, not left literal

    r2 = api.post(
        f"/api/sms/campaigns/{camp['id']}/preview",
        json={"contact_id": contact, "position": 2},
        headers=sc_org["headers"],
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["ai_snippet_empty"] is False  # no ai_instructions on this step


def test_analytics_reports_ai_configured(sc_org, api, twilio_creds_ok):
    """The Dashboard payload carries whether the active AI provider resolves a
    key — {{ai_snippet}} fails open to "" when it doesn't, so the org needs to
    know it's inert. No AI key in the test env → False."""
    r = api.get("/api/sms/analytics", headers=sc_org["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert "ai_configured" in body
    assert body["ai_configured"] is False


def test_account_out_flags_stale_inbound_webhook(sc_org, api, twilio_creds_ok):
    """A non-Twilio active account with real outbound volume but zero inbound
    messages ever = its inbound webhook was never registered, so STOP is never
    captured (no Twilio-21610-style self-heal exists for Sendblue/BlueBubbles).
    _account_out surfaces last_inbound_at + inbound_webhook_stale."""
    import datetime as _dt

    from app.api.sms_outreach import _INBOUND_STALE_MIN_SENDS

    acct = _mk_account(
        sc_org,
        api,
        name="bb-stale",
        provider="bluebubbles",
        account_sid=None,
        from_number="+14805556606",
        relay_url="https://relay.example.com",
        min_send_spacing_seconds=0,
        max_send_spacing_seconds=0,
    )
    aid = acct["id"]
    # Fresh account, no sends yet → not stale (a new account legitimately has
    # no replies).
    assert acct["last_inbound_at"] is None
    assert acct["inbound_webhook_stale"] is False

    # Log outbound sends up to the threshold, and no inbound.
    db = SessionLocal()
    try:
        for i in range(_INBOUND_STALE_MIN_SENDS):
            db.add(
                SmsMessage(
                    organization_id=sc_org["org"],
                    account_id=aid,
                    direction="out",
                    kind="campaign",
                    to_number=f"+1480555{7000 + i:04d}",
                    from_number="+14805556606",
                    body="hello",
                    status="sent",
                )
            )
        db.commit()
    finally:
        db.close()

    accts = api.get("/api/sms/accounts", headers=sc_org["headers"]).json()
    row = next(a for a in accts if a["id"] == aid)
    assert row["inbound_webhook_stale"] is True
    assert row["last_inbound_at"] is None

    # An inbound message clears the warning and sets last_inbound_at.
    db = SessionLocal()
    try:
        db.add(
            SmsMessage(
                organization_id=sc_org["org"],
                account_id=aid,
                direction="in",
                kind="inbound",
                to_number="+14805556606",
                from_number="+14805557999",
                body="hey there",
                status="received",
                created_at=utcnow() - _dt.timedelta(minutes=5),
            )
        )
        db.commit()
    finally:
        db.close()

    accts = api.get("/api/sms/accounts", headers=sc_org["headers"]).json()
    row = next(a for a in accts if a["id"] == aid)
    assert row["inbound_webhook_stale"] is False
    assert row["last_inbound_at"] is not None


# --- auto-enroll new leads into a client-scoped campaign ---------------------


def test_auto_enroll_requires_a_client(sc_org, api, twilio_creds_ok):
    """auto_enroll_new_leads is meaningless without a client to scope which
    leads flow in — the API refuses to turn it on (create OR patch) unless
    client_id is set, and refuses to clear the client while it's on."""
    acct = _mk_account(sc_org, api, from_number="+14805550170")

    # create: flag on, no client → 422
    r = api.post(
        "/api/sms/campaigns",
        json={
            "name": "No client",
            "account_id": acct["id"],
            "auto_enroll_new_leads": True,
        },
        headers=sc_org["headers"],
    )
    assert r.status_code == 422, r.text

    # create: flag on, with client → ok, reflected in the response
    r = api.post(
        "/api/sms/campaigns",
        json={
            "name": "Scoped",
            "account_id": acct["id"],
            "client_id": sc_org["client"],
            "auto_enroll_new_leads": True,
        },
        headers=sc_org["headers"],
    )
    assert r.status_code == 201, r.text
    assert r.json()["auto_enroll_new_leads"] is True
    assert r.json()["client_id"] == sc_org["client"]
    cid = r.json()["id"]

    # patch: can't clear the client while auto-enroll stays on
    r = api.patch(
        f"/api/sms/campaigns/{cid}",
        json={"client_id": None},
        headers=sc_org["headers"],
    )
    assert r.status_code == 422, r.text

    # patch: turning the flag on for an already-client-scoped campaign is fine
    r = api.post(
        "/api/sms/campaigns",
        json={"name": "Later", "account_id": acct["id"], "client_id": sc_org["client"]},
        headers=sc_org["headers"],
    )
    later_id = r.json()["id"]
    assert r.json()["auto_enroll_new_leads"] is False
    r = api.patch(
        f"/api/sms/campaigns/{later_id}",
        json={"auto_enroll_new_leads": True},
        headers=sc_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["auto_enroll_new_leads"] is True


def test_auto_enroll_new_lead_end_to_end(sc_org, api, twilio_creds_ok, captured_sends):
    """A brand-new lead arriving for the campaign's client is auto-enrolled and
    gets the first qualifying text on the next tick — the full path: real
    lead-creation endpoint (generic landing-page webhook) → the trigger wired
    next to notify_new_lead → enroll_contacts → run_due send."""
    # The org attests its intake funnel collects SMS consent, so a fresh
    # inbound lead is textable (else the consent gate correctly skips it).
    assert (
        api.put(
            "/api/orgs/me/sms-opt-in-default",
            json={"sms_opt_in_default": True},
            headers=sc_org["headers"],
        ).status_code
        == 200
    )
    try:
        acct = _mk_account(sc_org, api, from_number="+14805550171")
        camp = _mk_campaign(
            sc_org,
            api,
            acct["id"],
            name="Qualify HVAC leads",
            client_id=sc_org["client"],
            auto_enroll_new_leads=True,
            **_ALWAYS,
        )
        _set_steps(
            sc_org,
            api,
            camp["id"],
            [
                {
                    "position": 1,
                    "wait_days": 0,
                    "body": "Hi {{first_name|there}}! Quick Q — repair or replacement?",
                }
            ],
        )
        assert _activate(sc_org, api, camp["id"]).status_code == 200

        key = api.post(
            f"/api/clients/{sc_org['client']}/lead-forms/landing-page/rotate",
            headers=sc_org["headers"],
        ).json()["external_key"]
        r = api.post(
            f"/api/webhooks/landing-form/{sc_org['client']}/{key}",
            json={"Full Name": "Pat Lead", "Phone": "+14805559911"},
        )
        assert r.status_code == 200, r.text
        contact_id = r.json()["contact_id"]

        enr = _get_enrollment(camp["id"], contact_id)
        assert enr.status == SMS_ENROLL_ACTIVE

        _tick()
        assert any(s["to"] == "+14805559911" for s in captured_sends), captured_sends
    finally:
        api.put(
            "/api/orgs/me/sms-opt-in-default",
            json={"sms_opt_in_default": False},
            headers=sc_org["headers"],
        )


def test_auto_enroll_skips_lead_without_consent(sc_org, api, twilio_creds_ok):
    """The trigger never force-texts: a new lead with no recorded SMS opt-in
    (org default off) is NOT enrolled — the consent gate holds, silently."""
    acct = _mk_account(sc_org, api, from_number="+14805550172")
    camp = _mk_campaign(
        sc_org,
        api,
        acct["id"],
        name="Qualify (consent-gated)",
        client_id=sc_org["client"],
        auto_enroll_new_leads=True,
        **_ALWAYS,
    )
    _set_steps(
        sc_org, api, camp["id"], [{"position": 1, "wait_days": 0, "body": "Hi!"}]
    )
    assert _activate(sc_org, api, camp["id"]).status_code == 200

    key = api.post(
        f"/api/clients/{sc_org['client']}/lead-forms/landing-page/rotate",
        headers=sc_org["headers"],
    ).json()["external_key"]
    r = api.post(
        f"/api/webhooks/landing-form/{sc_org['client']}/{key}",
        json={"Full Name": "Unconsented Lead", "Phone": "+14805559922"},
    )
    assert r.status_code == 200, r.text
    contact_id = r.json()["contact_id"]

    db = SessionLocal()
    try:
        found = db.execute(
            select(SmsEnrollment).where(
                SmsEnrollment.campaign_id == camp["id"],
                SmsEnrollment.contact_id == contact_id,
            )
        ).scalar_one_or_none()
    finally:
        db.close()
    assert found is None


# --- org + client timezone settings & campaign inheritance -------------------


def test_org_timezone_endpoint_set_normalize_clear(sc_org, api):
    h = sc_org["headers"]
    try:
        r = api.put("/api/orgs/me/timezone", json={"timezone": "America/Phoenix"}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["timezone"] == "America/Phoenix"
        # reflected on GET /me
        assert api.get("/api/orgs/me", headers=h).json()["timezone"] == "America/Phoenix"
        # abbreviations canonicalize to a real IANA key
        assert api.put("/api/orgs/me/timezone", json={"timezone": "PST"}, headers=h).json()[
            "timezone"
        ] == "America/Los_Angeles"
        # garbage rejected
        assert api.put(
            "/api/orgs/me/timezone", json={"timezone": "Mars/Olympus"}, headers=h
        ).status_code == 422
        # null clears
        assert api.put("/api/orgs/me/timezone", json={"timezone": None}, headers=h).json()[
            "timezone"
        ] is None
    finally:
        api.put("/api/orgs/me/timezone", json={"timezone": None}, headers=h)


def test_client_timezone_endpoint(sc_org, api):
    h, cid = sc_org["headers"], sc_org["client"]
    try:
        r = api.put(f"/api/clients/{cid}/timezone", json={"timezone": "America/Phoenix"}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["timezone"] == "America/Phoenix"
        assert api.get(f"/api/clients/{cid}", headers=h).json()["timezone"] == "America/Phoenix"
        assert api.put(
            f"/api/clients/{cid}/timezone", json={"timezone": "Nowhere"}, headers=h
        ).status_code == 422
    finally:
        api.put(f"/api/clients/{cid}/timezone", json={"timezone": None}, headers=h)


def test_sms_campaign_inherits_timezone_client_over_org_over_default(
    sc_org, api, twilio_creds_ok
):
    h, cid = sc_org["headers"], sc_org["client"]
    acct = _mk_account(sc_org, api, from_number="+14805550180")
    try:
        # org default applies when the campaign has no client
        api.put("/api/orgs/me/timezone", json={"timezone": "America/Chicago"}, headers=h)
        assert _mk_campaign(sc_org, api, acct["id"], name="tz-org")["timezone"] == "America/Chicago"

        # a client's own timezone wins over the org default
        api.put(f"/api/clients/{cid}/timezone", json={"timezone": "America/Phoenix"}, headers=h)
        assert (
            _mk_campaign(sc_org, api, acct["id"], name="tz-client", client_id=cid)["timezone"]
            == "America/Phoenix"
        )

        # an explicit timezone in the request wins over both
        assert (
            _mk_campaign(
                sc_org, api, acct["id"], name="tz-explicit", client_id=cid,
                timezone="America/Denver",
            )["timezone"]
            == "America/Denver"
        )

        # nothing set anywhere → the SMS default
        api.put("/api/orgs/me/timezone", json={"timezone": None}, headers=h)
        api.put(f"/api/clients/{cid}/timezone", json={"timezone": None}, headers=h)
        assert _mk_campaign(sc_org, api, acct["id"], name="tz-default")["timezone"] == "America/New_York"
    finally:
        api.put("/api/orgs/me/timezone", json={"timezone": None}, headers=h)
        api.put(f"/api/clients/{cid}/timezone", json={"timezone": None}, headers=h)


# --- lead-reply relay (two-way, BlueBubbles) --------------------------------

_OPERATOR = "+14807207351"


@pytest.fixture()
def captured_provider(monkeypatch):
    """Capture every provider-level send (forwards + relayed replies) instead
    of hitting BlueBubbles/Twilio."""
    sent = []

    def _fake(account, to_number, body):
        sent.append({"to": to_number, "body": body, "provider": account.provider})
        return "SIDrelay", None, None

    monkeypatch.setattr(gateway, "_provider_send", _fake)
    return sent


def _mk_bb_account(sc_org, from_number):
    from app.models.sms_outreach import SMS_ACCOUNT_ACTIVE, SmsAccount

    db = SessionLocal()
    try:
        a = SmsAccount(
            organization_id=sc_org["org"],
            name="BB relay",
            provider="bluebubbles",
            account_sid="bluebubbles",
            from_number=from_number,
            relay_url="https://relay.test",
            status=SMS_ACCOUNT_ACTIVE,
            daily_send_cap=500,
        )
        db.add(a)
        db.commit()
        return a.id
    finally:
        db.close()


def _relay_inbound(account_id, from_raw, body):
    from app.api import sms_webhooks
    from app.models.sms_outreach import SmsAccount

    db = SessionLocal()
    try:
        acct = db.get(SmsAccount, account_id)
        sms_webhooks._process_inbound(
            db,
            acct,
            from_raw=from_raw,
            to_raw=acct.from_number,
            body=body,
            provider_sid=f"g-{from_raw}-{len(body)}",
            create_missing=True,
            service="iMessage",
        )
        db.commit()
    finally:
        db.close()


def _enable_relay(sc_org, api, phone=_OPERATOR):
    r = api.put(
        "/api/orgs/me/lead-relay",
        json={"enabled": True, "phone": phone},
        headers=sc_org["headers"],
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_lead_relay_config_requires_phone_to_enable(sc_org, api):
    try:
        assert (
            api.put(
                "/api/orgs/me/lead-relay",
                json={"enabled": True},
                headers=sc_org["headers"],
            ).status_code
            == 422
        )
        r = _enable_relay(sc_org, api)
        assert r["enabled"] is True
        assert r["phone"] == _OPERATOR  # normalized to E.164
        got = api.get("/api/orgs/me/lead-relay", headers=sc_org["headers"]).json()
        assert got == {"enabled": True, "phone": _OPERATOR}
    finally:
        api.put(
            "/api/orgs/me/lead-relay",
            json={"enabled": False},
            headers=sc_org["headers"],
        )


def test_lead_reply_forwards_then_operator_reply_routes_back(
    sc_org, api, captured_provider
):
    """Full loop: a lead's reply forwards to the operator (labeled with the
    lead's code), and the operator's tagged reply routes back to that lead via
    BlueBubbles."""
    _enable_relay(sc_org, api)
    bb = _mk_bb_account(sc_org, "+14805550190")
    lead = "+14805551234"  # code = 1234
    try:
        # 1) lead texts in → forwarded to the operator, carrying the code
        _relay_inbound(bb, lead, "What time can you come out?")
        fwd = [s for s in captured_provider if s["to"] == _OPERATOR]
        assert len(fwd) == 1, captured_provider
        assert "1234" in fwd[0]["body"]
        assert "What time can you come out?" in fwd[0]["body"]

        # 2) operator replies "1234 <message>" → relayed to the lead
        captured_provider.clear()
        _relay_inbound(bb, _OPERATOR, "1234 We can come by at 3pm today")
        to_lead = [s for s in captured_provider if s["to"] == lead]
        assert len(to_lead) == 1, captured_provider
        assert to_lead[0]["body"] == "We can come by at 3pm today"
        # and it's recorded as an outbound manual message on the lead
        db = SessionLocal()
        try:
            row = db.execute(
                select(SmsMessage).where(
                    SmsMessage.to_number == lead,
                    SmsMessage.direction == "out",
                    SmsMessage.kind == "manual",
                )
            ).scalar_one()
            assert row.body == "We can come by at 3pm today"
        finally:
            db.close()
    finally:
        api.put(
            "/api/orgs/me/lead-relay",
            json={"enabled": False},
            headers=sc_org["headers"],
        )


def test_operator_reply_without_code_gets_help_and_texts_no_lead(
    sc_org, api, captured_provider
):
    _enable_relay(sc_org, api)
    bb = _mk_bb_account(sc_org, "+14805550191")
    lead = "+14805555678"
    try:
        _relay_inbound(bb, lead, "hello")  # so a lead exists
        captured_provider.clear()
        _relay_inbound(bb, _OPERATOR, "on my way")  # no leading code
        # operator gets a help nudge; nothing is sent to the lead
        assert all(s["to"] != lead for s in captured_provider), captured_provider
        assert any(s["to"] == _OPERATOR for s in captured_provider)
    finally:
        api.put(
            "/api/orgs/me/lead-relay",
            json={"enabled": False},
            headers=sc_org["headers"],
        )


def test_24_7_window_is_always_open():
    """A campaign with a full 0–24 window on all seven days is inside its send
    window at any hour, any day — the backend contract the frontend '24/7'
    toggle relies on (no schema change; send_window_end already allows 24)."""
    import datetime as _dt

    camp = SmsCampaign(
        organization_id="o",
        name="always",
        account_id="a",
        timezone="America/Phoenix",
        send_window_start=0,
        send_window_end=24,
        send_days=[0, 1, 2, 3, 4, 5, 6],
    )
    overnight = _dt.datetime(2026, 7, 20, 9, 0, tzinfo=_dt.timezone.utc)  # 2am Phoenix
    assert gateway.in_send_window(camp, overnight) is True
    # a normal 8am–9pm Mon–Fri campaign is (correctly) closed at that hour
    camp.send_window_start, camp.send_window_end, camp.send_days = 8, 21, [0, 1, 2, 3, 4]
    assert gateway.in_send_window(camp, overnight) is False



# --- reply-triggered steps + response branching ------------------------------


def _inbound_reply(api, acct, from_number, body, sid="SM_reply_x"):
    """POST a correctly-signed Twilio inbound webhook for a genuine reply."""
    url = f"http://testserver/api/sms/webhooks/inbound/{acct['id']}"
    form = {
        "From": from_number,
        "To": acct["from_number"],
        "Body": body,
        "MessageSid": sid,
    }
    sig = _twilio_signature(_AUTH_TOKEN, url, form)
    return api.post(
        f"/api/sms/webhooks/inbound/{acct['id']}",
        data=form,
        headers={"X-Twilio-Signature": sig},
    )


def _force_due(enrollment_id):
    import datetime as _dt

    db = SessionLocal()
    try:
        e = db.get(SmsEnrollment, enrollment_id)
        e.next_run_at = utcnow() - _dt.timedelta(seconds=5)
        db.commit()
    finally:
        db.close()


def test_reply_step_waits_schedules_after_reply_and_branches(
    sc_org, api, twilio_creds_ok, captured_sends
):
    """The full loop: step 1 sends -> enrollment parks 'awaiting reply' -> the
    lead replies -> the reply step schedules wait_minutes after the reply
    (NOT exiting, despite exit_on_reply's default) -> the branch matching what
    they said sends -> sequence completes. Also proves the inbound ledger row
    is stamped with campaign/enrollment/step attribution."""
    import datetime as _dt

    acct = _mk_account(sc_org, api, from_number="+14805550710")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805557101", first="Rina")
    detail = _set_steps(
        sc_org,
        api,
        camp["id"],
        [
            {"position": 1, "body": "First touch"},
            {
                "position": 2,
                "trigger": "reply",
                "wait_minutes": 30,
                "body": "Thanks for getting back to me!",
                "branches": [
                    {
                        "label": "Yes",
                        "keywords": ["yes", "interested"],
                        "body": "Great {{first_name}} — when works for a call?",
                    },
                    {"label": "No", "keywords": ["no"], "body": "No worries, {{first_name}}."},
                ],
            },
        ],
    )
    step1_id = detail["steps"][0]["id"]
    assert detail["steps"][1]["trigger"] == "reply"
    assert detail["steps"][1]["wait_minutes"] == 30
    assert len(detail["steps"][1]["branches"]) == 2
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])

    _tick()  # sends step 1, parks at the reply step awaiting the lead
    assert len(captured_sends) == 1
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "active"
    assert e.current_position == 2
    assert e.next_run_at is None
    assert e.awaiting_reply_since is not None

    # No amount of ticking sends the reply step while nobody replied.
    _tick()
    assert len(captured_sends) == 1

    before = utcnow()
    r = _inbound_reply(api, acct, "+14805557101", "Yes, interested!", sid="SM_reply_1")
    assert r.status_code == 200, r.text

    e = _get_enrollment(camp["id"], contact)
    assert e.status == "active"  # reply step takes precedence over exit_on_reply
    assert e.awaiting_reply_since is None
    assert e.replied_at is not None
    assert e.last_reply_body == "Yes, interested!"
    assert e.next_run_at is not None
    # Scheduled ~30 minutes after the reply.
    nra = e.next_run_at if e.next_run_at.tzinfo else e.next_run_at.replace(
        tzinfo=_dt.timezone.utc
    )
    delta = (nra - before).total_seconds()
    assert 29 * 60 <= delta <= 32 * 60

    # The inbound ledger row carries the reply's attribution: the campaign,
    # the enrollment, and the step the lead was replying to (step 1).
    db = SessionLocal()
    try:
        row = db.execute(
            select(SmsMessage).where(
                SmsMessage.provider_sid == "SM_reply_1",
                SmsMessage.organization_id == sc_org["org"],
            )
        ).scalar_one()
        assert row.campaign_id == camp["id"]
        assert row.enrollment_id == e.id
        assert row.step_id == step1_id
    finally:
        db.close()

    # Not due yet — the timed delay is respected.
    _tick()
    assert len(captured_sends) == 1

    _force_due(e.id)
    _tick()
    assert len(captured_sends) == 2
    assert "Great Rina — when works for a call?" in captured_sends[-1]["body"]

    e = _get_enrollment(camp["id"], contact)
    assert e.status == "completed"
    assert e.last_reply_body is None  # consumed by the send


def test_reply_step_default_body_when_no_branch_matches(
    sc_org, api, twilio_creds_ok, captured_sends
):
    acct = _mk_account(sc_org, api, from_number="+14805550711")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805557102", first="Deb")
    _set_steps(
        sc_org,
        api,
        camp["id"],
        [
            {"position": 1, "body": "First touch"},
            {
                "position": 2,
                "trigger": "reply",
                "body": "Thanks {{first_name}} — mind sharing more?",
                "branches": [
                    {"label": "Yes", "keywords": ["yes"], "body": "Branch body"}
                ],
            },
        ],
    )
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])
    _tick()
    assert len(captured_sends) == 1

    # Word-boundary matching: "know" must not fire the "no"/"yes" branches.
    r = _inbound_reply(
        api, acct, "+14805557102", "let me know more", sid="SM_reply_2"
    )
    assert r.status_code == 200
    e = _get_enrollment(camp["id"], contact)
    _force_due(e.id)
    _tick()
    assert len(captured_sends) == 2
    assert "Thanks Deb — mind sharing more?" in captured_sends[-1]["body"]


def test_reply_branch_keyword_word_boundaries():
    """'no' must not match inside 'know'; matching is case-insensitive."""
    step = SmsStep(
        organization_id="o",
        campaign_id="c",
        position=2,
        trigger="reply",
        branches=[
            {"label": "No", "keywords": ["no"], "body": "ok"},
            {"label": "Yes", "keywords": ["yes"], "body": "great"},
        ],
    )
    assert sms_campaigns.match_branch_keywords(step, "I know about it") is None
    assert sms_campaigns.match_branch_keywords(step, "NO thanks")["label"] == "No"
    assert sms_campaigns.match_branch_keywords(step, "well... Yes!")["label"] == "Yes"
    assert sms_campaigns.match_branch_keywords(step, "") is None


def test_ai_branching_classifies_when_keywords_miss(
    sc_org, api, twilio_creds_ok, captured_sends, monkeypatch
):
    """A reply with no keyword hit routes through the AI classifier when
    ai_branching is on; the classified branch's body sends. (classify_reply is
    stubbed — its own guard behavior is fail-open and returns None on any
    error, which the default-body test above already exercises.)"""
    acct = _mk_account(sc_org, api, from_number="+14805550712")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805557103", first="Ana")
    _set_steps(
        sc_org,
        api,
        camp["id"],
        [
            {"position": 1, "body": "First touch"},
            {
                "position": 2,
                "trigger": "reply",
                "ai_branching": True,
                "body": "Default response",
                "branches": [
                    {"label": "Interested", "keywords": ["yes"], "body": "AI matched {{first_name}}"}
                ],
            },
        ],
    )
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])
    _tick()
    assert len(captured_sends) == 1

    monkeypatch.setattr(
        sms_campaigns, "classify_reply", lambda db, org, step, text: "Interested"
    )
    r = _inbound_reply(
        api, acct, "+14805557103", "sure go ahead", sid="SM_reply_3"
    )
    assert r.status_code == 200
    e = _get_enrollment(camp["id"], contact)
    _force_due(e.id)
    _tick()
    assert len(captured_sends) == 2
    assert "AI matched Ana" in captured_sends[-1]["body"]


def test_reply_exits_when_no_reply_step_and_exit_on_reply(
    sc_org, api, twilio_creds_ok, captured_sends
):
    """The pre-reply-step behavior is unchanged for plain drip campaigns: a
    reply exits the enrollment (exit_on_reply default true) — and now also
    records the reply on the enrollment for tracking."""
    acct = _mk_account(sc_org, api, from_number="+14805550713")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805557104")
    _set_steps(
        sc_org,
        api,
        camp["id"],
        [
            {"position": 1, "body": "First touch"},
            {"position": 2, "wait_days": 3, "body": "Bump"},
        ],
    )
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])
    _tick()

    r = _inbound_reply(api, acct, "+14805557104", "sounds good", sid="SM_reply_4")
    assert r.status_code == 200
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "exited"
    assert e.exit_reason == "replied"
    assert e.replied_at is not None
    assert e.last_reply_body == "sounds good"


def test_rearm_never_force_fires_awaiting_reply(
    sc_org, api, twilio_creds_ok, captured_sends
):
    """Pause -> reactivate re-arms parked enrollments, but an enrollment
    awaiting a lead's reply must stay parked — re-arming it would force-send
    a reply step nobody replied to."""
    acct = _mk_account(sc_org, api, from_number="+14805550714")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805557105")
    _set_steps(
        sc_org,
        api,
        camp["id"],
        [
            {"position": 1, "body": "First touch"},
            {"position": 2, "trigger": "reply", "body": "Reply handler"},
        ],
    )
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])
    _tick()
    assert len(captured_sends) == 1

    assert api.post(
        f"/api/sms/campaigns/{camp['id']}/pause", headers=sc_org["headers"]
    ).status_code == 200
    assert _activate(sc_org, api, camp["id"]).status_code == 200

    e = _get_enrollment(camp["id"], contact)
    assert e.status == "active"
    assert e.next_run_at is None  # NOT re-armed
    assert e.awaiting_reply_since is not None
    _tick()
    assert len(captured_sends) == 1  # nothing force-fired


def test_step_validation_rejects_branch_misuse(sc_org, api, twilio_creds_ok):
    acct = _mk_account(sc_org, api, from_number="+14805550715")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    # branches on a schedule step -> 422 (pydantic model validator)
    r = api.put(
        f"/api/sms/campaigns/{camp['id']}/steps",
        json={
            "steps": [
                {
                    "position": 1,
                    "body": "Hi",
                    "branches": [{"label": "x", "keywords": ["a"], "body": "b"}],
                }
            ]
        },
        headers=sc_org["headers"],
    )
    assert r.status_code == 422
    # unknown token inside a BRANCH body -> same 422 as the step body
    r = api.put(
        f"/api/sms/campaigns/{camp['id']}/steps",
        json={
            "steps": [
                {"position": 1, "body": "Hi"},
                {
                    "position": 2,
                    "trigger": "reply",
                    "body": "ok",
                    "branches": [
                        {"label": "x", "keywords": ["a"], "body": "Hi {{bogus_token}}"}
                    ],
                },
            ]
        },
        headers=sc_org["headers"],
    )
    assert r.status_code == 422
    assert "bogus_token" in r.text


def test_campaign_stats_count_read_receipts_and_replies(
    sc_org, api, twilio_creds_ok, captured_sends
):
    """A read receipt must not remove a message from sent/delivered (the bug
    this session fixed), and the new read/replies fields flow through the
    campaign stats and the analytics endpoint."""
    acct = _mk_account(sc_org, api, from_number="+14805550716")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805557106")
    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": "First touch"}])
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])
    _tick()
    assert len(captured_sends) == 1

    # Simulate the provider's receipts: delivered -> read (iMessage).
    db = SessionLocal()
    try:
        row = db.execute(
            select(SmsMessage).where(
                SmsMessage.campaign_id == camp["id"],
                SmsMessage.direction == "out",
            )
        ).scalar_one()
        row.status = "read"
        row.read_at = utcnow()
        db.commit()
    finally:
        db.close()

    r = _inbound_reply(api, acct, "+14805557106", "who is this?", sid="SM_reply_5")
    assert r.status_code == 200

    detail = api.get(
        f"/api/sms/campaigns/{camp['id']}", headers=sc_org["headers"]
    ).json()
    assert detail["sent"] == 1  # read receipt did NOT remove it from sent
    assert detail["delivered"] == 1
    assert detail["read"] == 1
    assert detail["read_rate"] == 1.0
    assert detail["replies"] == 1
    assert detail["replied"] == 1
    # Per-step funnel on the full serialization.
    st = detail["steps"][0]["stats"]
    assert st["sent"] == 1 and st["read"] == 1 and st["replies"] == 1

    analytics = api.get(
        f"/api/sms/analytics?campaign_id={camp['id']}", headers=sc_org["headers"]
    ).json()
    assert analytics["totals"]["read"] == 1
    assert analytics["totals"]["replies"] == 1
    assert analytics["totals"]["read_rate"] == 1.0
    day = analytics["by_day"][-1]
    assert day["sent"] == 1 and day["read"] == 1


# --- catch-up: leads who replied BEFORE the campaign had reply handling ------


def test_catch_up_past_repliers_get_the_reply_step(
    sc_org, api, twilio_creds_ok, captured_sends
):
    """A lead who replied while the campaign was plain drip (exit_on_reply
    exited them with no answer) gets the reply step retroactively: the
    catch-up endpoint re-activates the enrollment at the reply step, primes
    branch matching with the lead's ACTUAL historical reply text, and the
    next tick sends the matched response. Idempotent — a second catch-up
    queues nothing."""
    acct = _mk_account(sc_org, api, from_number="+14805550720")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805557201", first="Gus")
    _set_steps(
        sc_org, api, camp["id"],
        [
            {"position": 1, "body": "First touch"},
            {"position": 2, "wait_days": 3, "body": "Bump"},
        ],
    )
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])
    _tick()
    assert len(captured_sends) == 1

    # The pre-feature world: reply arrives, no reply step exists -> exited.
    r = _inbound_reply(api, acct, "+14805557201", "yes very interested", sid="SM_cu_1")
    assert r.status_code == 200
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "exited" and e.exit_reason == "replied"

    # NOW the org adds a reply step with branches (keeping existing step ids
    # via upsert isn't needed here — replace with first step + reply handler).
    _set_steps(
        sc_org, api, camp["id"],
        [
            {"position": 1, "body": "First touch"},
            {
                "position": 2,
                "trigger": "reply",
                "body": "Default response",
                "branches": [
                    {"label": "Yes", "keywords": ["interested"], "body": "Awesome {{first_name}} — call tomorrow?"}
                ],
            },
        ],
    )

    # Dry run reports the candidate without touching anything.
    dry = api.post(
        f"/api/sms/campaigns/{camp['id']}/catch-up-replies",
        json={"dry_run": True},
        headers=sc_org["headers"],
    )
    assert dry.status_code == 200, dry.text
    assert dry.json()["queued"] == 1
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "exited"  # dry run mutated nothing

    real = api.post(
        f"/api/sms/campaigns/{camp['id']}/catch-up-replies",
        json={"dry_run": False},
        headers=sc_org["headers"],
    )
    assert real.status_code == 200, real.text
    assert real.json()["queued"] == 1

    e = _get_enrollment(camp["id"], contact)
    assert e.status == "active"
    assert e.current_position == 2
    assert e.last_reply_body == "yes very interested"
    assert e.next_run_at is not None

    _force_due(e.id)
    _tick()
    assert len(captured_sends) == 2
    assert "Awesome Gus — call tomorrow?" in captured_sends[-1]["body"]
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "completed"

    # Idempotency: the enrollment now completed WITH a reply-step send on
    # record — a second catch-up must skip it, never double-text.
    again = api.post(
        f"/api/sms/campaigns/{camp['id']}/catch-up-replies",
        json={"dry_run": False},
        headers=sc_org["headers"],
    ).json()
    assert again["queued"] == 0
    assert any(s["reason"] == "already_responded" for s in again["skipped"])
    _tick()
    assert len(captured_sends) == 2


def test_catch_up_skips_revoked_consent_and_needs_reply_step(
    sc_org, api, twilio_creds_ok, captured_sends
):
    acct = _mk_account(sc_org, api, from_number="+14805550721")
    camp = _mk_campaign(sc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805557202")
    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": "First touch"}, {"position": 2, "wait_days": 3, "body": "Bump"}])
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])
    _tick()
    r = _inbound_reply(api, acct, "+14805557202", "tell me more", sid="SM_cu_2")
    assert r.status_code == 200

    # No reply step yet -> the endpoint says so and queues nothing.
    none_yet = api.post(
        f"/api/sms/campaigns/{camp['id']}/catch-up-replies",
        json={"dry_run": True},
        headers=sc_org["headers"],
    ).json()
    assert none_yet.get("no_reply_step") is True and none_yet["queued"] == 0

    _set_steps(
        sc_org, api, camp["id"],
        [
            {"position": 1, "body": "First touch"},
            {"position": 2, "trigger": "reply", "body": "Answer"},
        ],
    )
    # Consent revoked after they replied -> skipped, never queued.
    r = api.patch(
        f"/api/crm/contacts/{contact}",
        json={"sms_opt_in": False},
        headers=sc_org["headers"],
    )
    assert r.status_code == 200, r.text
    out = api.post(
        f"/api/sms/campaigns/{camp['id']}/catch-up-replies",
        json={"dry_run": False},
        headers=sc_org["headers"],
    ).json()
    assert out["queued"] == 0
    assert any(s["reason"] == "no_consent" for s in out["skipped"])
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "exited"  # untouched


def test_catch_up_finds_repliers_without_replied_at_marker(
    sc_org, api, twilio_creds_ok, captured_sends
):
    """An exit_on_reply=false campaign never stamped replied_at pre-feature —
    the lead replied and just kept dripping to completion. Catch-up must find
    them from message evidence (inbound after the campaign's own send), not
    the marker."""
    acct = _mk_account(sc_org, api, from_number="+14805550722")
    camp = _mk_campaign(sc_org, api, acct["id"], exit_on_reply=False, **_ALWAYS)
    contact = _mk_contact(sc_org, api, mobile_phone="4805557203", first="Ivy")
    _set_steps(sc_org, api, camp["id"], [{"position": 1, "body": "First touch"}])
    assert _activate(sc_org, api, camp["id"]).status_code == 200
    _enroll(sc_org, api, camp["id"], [contact])
    _tick()
    assert len(captured_sends) == 1
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "completed"

    r = _inbound_reply(api, acct, "+14805557203", "yes tell me more", sid="SM_cu_3")
    assert r.status_code == 200

    # Simulate the PRE-FEATURE data shape: no replied_at / last_reply_* on the
    # enrollment (today's webhook records them; old data has only the inbound
    # message row itself).
    db = SessionLocal()
    try:
        row = db.get(SmsEnrollment, e.id)
        row.replied_at = None
        row.last_reply_at = None
        row.last_reply_body = None
        db.commit()
    finally:
        db.close()

    _set_steps(
        sc_org, api, camp["id"],
        [
            {"position": 1, "body": "First touch"},
            {
                "position": 2,
                "trigger": "reply",
                "body": "Default",
                "branches": [
                    {"label": "Yes", "keywords": ["yes"], "body": "Perfect {{first_name}} — sending details now."}
                ],
            },
        ],
    )
    out = api.post(
        f"/api/sms/campaigns/{camp['id']}/catch-up-replies",
        json={"dry_run": False},
        headers=sc_org["headers"],
    ).json()
    assert out["queued"] == 1, out

    e = _get_enrollment(camp["id"], contact)
    assert e.status == "active" and e.current_position == 2
    _force_due(e.id)
    _tick()
    assert len(captured_sends) == 2
    assert "Perfect Ivy — sending details now." in captured_sends[-1]["body"]
