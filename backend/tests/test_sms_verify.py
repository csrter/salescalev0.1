"""BlueBubbles post-send verification + auto-retry (services/sms_verify).

The AppleScript send path reports success at hand-off; the true outcome
lands asynchronously in the Mac's Messages DB. These tests pin the verify
pass's contract: aged "sent" rows are read back once (verified_at), device
failures become honest FAILED rows and rewind the enrollment so the engine
resends, successes stay put (upgraded to delivered only with a receipt),
and retries per step are capped. Own org (sv_org), per the isolation
convention; the relay lookup is monkeypatched at sms_verify._fetch_state.
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.sms_outreach import (
    SMS_MSG_FAILED,
    SMS_MSG_SENT,
    SmsEnrollment,
    SmsMessage,
)
from app.services import sms_send as gateway
from app.services import sms_verify


@pytest.fixture()
def bb_creds_ok(monkeypatch):
    monkeypatch.setattr(gateway, "verify_credentials", lambda account: (True, "ok"))


@pytest.fixture(scope="module")
def sv_org(api):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Verify Co",
            "email": "owner@verifyco.com",
            "password": "verifyco-pass-1",
            "full_name": "Verify Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "Verify Client"}, headers=headers
    ).json()["id"]
    return {"org": body["organization_id"], "headers": headers, "client": client_id}


@pytest.fixture(scope="module")
def sv_setup(sv_org, api):
    """One BlueBubbles account + active campaign + step, shared by the
    module's tests (accounts are unique per (org, from_number))."""
    import app.services.sms_send as gw

    orig = gw.verify_credentials
    gw.verify_credentials = lambda account: (True, "ok")
    try:
        acct = api.post(
            "/api/sms/accounts",
            json={
                "name": "Verify BB Line",
                "provider": "bluebubbles",
                "auth_token": "bluebubbles-server-password-999",
                "relay_url": "https://verify-relay.example.com",
                "from_number": "+14805559901",
            },
            headers=sv_org["headers"],
        ).json()
        camp = api.post(
            "/api/sms/campaigns",
            json={
                "name": "Verify Campaign",
                "account_id": acct["id"],
                "send_window_start": 0,
                "send_window_end": 24,
                "send_days": [0, 1, 2, 3, 4, 5, 6],
            },
            headers=sv_org["headers"],
        ).json()
        steps = api.put(
            f"/api/sms/campaigns/{camp['id']}/steps",
            json={"steps": [{"position": 1, "body": "Hey {{first_name}}"}]},
            headers=sv_org["headers"],
        ).json()
        step_id = steps["steps"][0]["id"] if isinstance(steps, dict) else steps[0]["id"]
    finally:
        gw.verify_credentials = orig
    return {"account": acct, "campaign": camp, "step_id": step_id}


def _enroll_contact(sv_org, api, sv_setup, phone):
    r = api.post(
        "/api/crm/contacts",
        json={
            "client_id": sv_org["client"],
            "first_name": "Ver",
            "last_name": "Ifiable",
            "mobile_phone": phone,
            "sms_opt_in": True,
        },
        headers=sv_org["headers"],
    )
    contact_id = r.json()["id"]
    enroll = api.post(
        f"/api/sms/campaigns/{sv_setup['campaign']['id']}/enroll",
        json={"contact_ids": [contact_id]},
        headers=sv_org["headers"],
    )
    assert enroll.json()["enrolled"] == 1
    with SessionLocal() as db:
        enr_id = db.execute(
            select(SmsEnrollment.id).where(
                SmsEnrollment.campaign_id == sv_setup["campaign"]["id"],
                SmsEnrollment.contact_id == contact_id,
            )
        ).scalar_one()
    return contact_id, enr_id


def _fabricate_sent(sv_org, sv_setup, enr_id, contact_id, guid, *, age_minutes=10, advance=True):
    """A 'sent' ledger row as the gateway would have written it, aged past
    the verify threshold, with the enrollment advanced as after a send."""
    with SessionLocal() as db:
        db.add(
            SmsMessage(
                organization_id=sv_org["org"],
                account_id=sv_setup["account"]["id"],
                campaign_id=sv_setup["campaign"]["id"],
                enrollment_id=enr_id,
                step_id=sv_setup["step_id"],
                contact_id=contact_id,
                direction="out",
                to_number="+14805550123",
                body="Hey Ver",
                status=SMS_MSG_SENT,
                provider_sid=guid,
                created_at=utcnow() - dt.timedelta(minutes=age_minutes),
            )
        )
        if advance:
            enr = db.get(SmsEnrollment, enr_id)
            enr.current_position = 2
            enr.next_run_at = None
        db.commit()


def test_device_failure_marks_failed_and_rewinds(sv_org, api, sv_setup, monkeypatch):
    contact_id, enr_id = _enroll_contact(sv_org, api, sv_setup, "4805559801")
    _fabricate_sent(sv_org, sv_setup, enr_id, contact_id, "GUID_FAIL_1")
    monkeypatch.setattr(sms_verify, "_fetch_state", lambda *a: {"error": 4})

    n = sms_verify.run_due(SessionLocal())
    assert n >= 1
    with SessionLocal() as db:
        msg = db.execute(
            select(SmsMessage).where(SmsMessage.provider_sid == "GUID_FAIL_1")
        ).scalar_one()
        assert msg.status == SMS_MSG_FAILED
        assert msg.error_code == "4"
        assert msg.verified_at is not None
        enr = db.get(SmsEnrollment, enr_id)
        assert enr.current_position == 1  # rewound to the failed step
        assert enr.next_run_at is not None  # engine will resend


def test_success_verifies_once_and_delivery_receipt_upgrades(
    sv_org, api, sv_setup, monkeypatch
):
    contact_id, enr_id = _enroll_contact(sv_org, api, sv_setup, "4805559802")
    _fabricate_sent(sv_org, sv_setup, enr_id, contact_id, "GUID_OK_1")
    contact2, enr2 = _enroll_contact(sv_org, api, sv_setup, "4805559803")
    _fabricate_sent(sv_org, sv_setup, enr2, contact2, "GUID_DELIV_1")

    def _state(relay, pw, guid):
        if guid == "GUID_DELIV_1":
            return {"error": 0, "dateDelivered": 1784900000000}
        return {"error": 0, "dateDelivered": None}

    monkeypatch.setattr(sms_verify, "_fetch_state", _state)
    sms_verify.run_due(SessionLocal())
    calls = []
    monkeypatch.setattr(
        sms_verify, "_fetch_state", lambda *a: calls.append(a) or {"error": 0}
    )
    sms_verify.run_due(SessionLocal())  # second pass: nothing left to verify
    with SessionLocal() as db:
        ok = db.execute(
            select(SmsMessage).where(SmsMessage.provider_sid == "GUID_OK_1")
        ).scalar_one()
        assert ok.status == SMS_MSG_SENT  # no receipt — stays sent, verified
        assert ok.verified_at is not None
        deliv = db.execute(
            select(SmsMessage).where(SmsMessage.provider_sid == "GUID_DELIV_1")
        ).scalar_one()
        assert deliv.status == "delivered"
        enr = db.get(SmsEnrollment, enr_id)
        assert enr.current_position == 2  # successes never rewind
    assert not [c for c in calls if c[2] in ("GUID_OK_1", "GUID_DELIV_1")]


def test_young_rows_wait_for_the_async_error_stamp(sv_org, api, sv_setup, monkeypatch):
    contact_id, enr_id = _enroll_contact(sv_org, api, sv_setup, "4805559804")
    _fabricate_sent(
        sv_org, sv_setup, enr_id, contact_id, "GUID_YOUNG_1", age_minutes=1
    )
    monkeypatch.setattr(
        sms_verify, "_fetch_state", lambda *a: (_ for _ in ()).throw(AssertionError)
    )
    sms_verify.run_due(SessionLocal())  # must not even look it up
    with SessionLocal() as db:
        msg = db.execute(
            select(SmsMessage).where(SmsMessage.provider_sid == "GUID_YOUNG_1")
        ).scalar_one()
        assert msg.verified_at is None
        assert msg.status == SMS_MSG_SENT


def test_retry_cap_stops_machine_gunning(sv_org, api, sv_setup, monkeypatch):
    contact_id, enr_id = _enroll_contact(sv_org, api, sv_setup, "4805559805")
    # A dead device already produced MAX_STEP_RETRIES failures for this step.
    with SessionLocal() as db:
        for i in range(sms_verify.MAX_STEP_RETRIES + 1):
            db.add(
                SmsMessage(
                    organization_id=sv_org["org"],
                    account_id=sv_setup["account"]["id"],
                    campaign_id=sv_setup["campaign"]["id"],
                    enrollment_id=enr_id,
                    step_id=sv_setup["step_id"],
                    contact_id=contact_id,
                    direction="out",
                    to_number="+14805550123",
                    body="Hey Ver",
                    status=SMS_MSG_FAILED,
                    provider_sid=f"GUID_OLDFAIL_{i}",
                    created_at=utcnow() - dt.timedelta(minutes=30),
                )
            )
        db.commit()
    _fabricate_sent(sv_org, sv_setup, enr_id, contact_id, "GUID_CAPPED_1")
    monkeypatch.setattr(sms_verify, "_fetch_state", lambda *a: {"error": 4})
    sms_verify.run_due(SessionLocal())
    with SessionLocal() as db:
        msg = db.execute(
            select(SmsMessage).where(SmsMessage.provider_sid == "GUID_CAPPED_1")
        ).scalar_one()
        assert msg.status == SMS_MSG_FAILED  # ledger still honest
        enr = db.get(SmsEnrollment, enr_id)
        assert enr.current_position == 2  # but no further retries queued
        assert enr.next_run_at is None
