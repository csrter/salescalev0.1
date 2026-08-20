"""SMS status-tracking integrity: the monotonic ladder, webhook receipt
handling, and inbound idempotency.

Delivery reports arrive out of order as a matter of course (Sendblue emits
DELIVERED and READ separately, providers retry callbacks, the BlueBubbles
verify pass reads state back minutes later) and `status` is one destructive
column — so these pin that no writer can walk a row BACKWARDS, and that a
retried inbound webhook can't duplicate a reply.

Own org (tk_org) per the isolation convention; _apply_status is exercised
directly rather than through signed provider webhooks, since the routing is
already covered in test_sms_outreach / test_imessage_outreach and the
behavior under test is the shared normalizer.
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.api.sms_webhooks import _apply_status, _process_inbound
from app.db import SessionLocal
from app.models.base import utcnow
from app.models.sms_outreach import (
    SMS_MSG_DELIVERED,
    SMS_MSG_FAILED,
    SMS_MSG_READ,
    SMS_MSG_SENT,
    SmsAccount,
    SmsMessage,
    advance_status,
)
from app.services import sms_send as gateway


# --- the ladder itself (pure) ----------------------------------------------


def test_status_ladder_never_moves_backwards():
    assert advance_status(SMS_MSG_SENT, SMS_MSG_DELIVERED) == SMS_MSG_DELIVERED
    assert advance_status(SMS_MSG_DELIVERED, SMS_MSG_READ) == SMS_MSG_READ
    # The real out-of-order case: Sendblue's DELIVERED landing after its READ.
    assert advance_status(SMS_MSG_READ, SMS_MSG_DELIVERED) == SMS_MSG_READ
    assert advance_status(SMS_MSG_READ, SMS_MSG_SENT) == SMS_MSG_READ


def test_status_ladder_failure_rules():
    assert advance_status(SMS_MSG_SENT, SMS_MSG_FAILED) == SMS_MSG_FAILED
    # A message that demonstrably arrived cannot be un-delivered by a late
    # failure callback — that stale report is the wrong one, not the receipt.
    assert advance_status(SMS_MSG_DELIVERED, SMS_MSG_FAILED) == SMS_MSG_DELIVERED
    assert advance_status(SMS_MSG_READ, SMS_MSG_FAILED) == SMS_MSG_READ
    # Conversely, proof of arrival does override an earlier failure guess.
    assert advance_status(SMS_MSG_FAILED, SMS_MSG_DELIVERED) == SMS_MSG_DELIVERED


def test_status_ladder_ignores_unknown_words():
    assert advance_status(SMS_MSG_SENT, "accepted") == SMS_MSG_SENT
    assert advance_status(SMS_MSG_SENT, "") == SMS_MSG_SENT


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module")
def tk_org(api):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Tracking Co",
            "email": "owner@smstrackingco.com",
            "password": "tracking-co-pass-1",
            "full_name": "Tracking Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "Tracking Client"}, headers=headers
    ).json()["id"]
    return {"org": body["organization_id"], "headers": headers, "client": client_id}


@pytest.fixture(scope="module")
def tk_account(tk_org, api):
    orig = gateway.verify_credentials
    gateway.verify_credentials = lambda account: (True, "ok")
    try:
        acct = api.post(
            "/api/sms/accounts",
            json={
                "name": "Tracking Line",
                "provider": "twilio",
                "account_sid": "AC_tracking",
                "auth_token": "tracking-token-1",
                "from_number": "+14805559701",
            },
            headers=tk_org["headers"],
        ).json()
    finally:
        gateway.verify_credentials = orig
    return acct


def _row(tk_org, tk_account, sid, status=SMS_MSG_SENT, direction="out"):
    with SessionLocal() as db:
        db.add(
            SmsMessage(
                organization_id=tk_org["org"],
                account_id=tk_account["id"],
                direction=direction,
                to_number="+14805550199",
                body="hello",
                status=status,
                provider_sid=sid,
                created_at=utcnow() - dt.timedelta(minutes=1),
            )
        )
        db.commit()


def _get(sid):
    with SessionLocal() as db:
        return db.execute(
            select(SmsMessage).where(SmsMessage.provider_sid == sid)
        ).scalar_one()


def _apply(tk_account, sid, status, **kw):
    with SessionLocal() as db:
        acct = db.get(SmsAccount, tk_account["id"])
        _apply_status(db, acct, sid, status, kw.pop("error_code", None), **kw)
        db.commit()


# --- webhook receipt handling ----------------------------------------------


def test_late_delivered_does_not_erase_a_read(tk_org, tk_account):
    """The analytics-visible version of the ladder bug: by_day buckets reads
    on status=='read', so a downgrade silently dropped the read entirely."""
    _row(tk_org, tk_account, "SID_READ_THEN_DELIVERED")
    _apply(tk_account, "SID_READ_THEN_DELIVERED", "read")
    assert _get("SID_READ_THEN_DELIVERED").status == SMS_MSG_READ
    _apply(tk_account, "SID_READ_THEN_DELIVERED", "delivered")
    row = _get("SID_READ_THEN_DELIVERED")
    assert row.status == SMS_MSG_READ
    assert row.read_at is not None


def test_receipts_stamp_delivery_timestamps(tk_org, tk_account):
    """delivered_at is what makes time-to-delivery measurable at all — only
    the status string was stored before."""
    _row(tk_org, tk_account, "SID_TIMESTAMPS")
    _apply(tk_account, "SID_TIMESTAMPS", "delivered")
    row = _get("SID_TIMESTAMPS")
    assert row.delivered_at is not None
    assert row.failed_at is None


def test_failure_records_code_detail_and_time(tk_org, tk_account):
    _row(tk_org, tk_account, "SID_UNDELIVERED")
    _apply(tk_account, "SID_UNDELIVERED", "undelivered", error_code=30006)
    row = _get("SID_UNDELIVERED")
    assert row.status == SMS_MSG_FAILED
    assert row.error_code == "30006"
    # 'undelivered' is a carrier rejection, a different cause from a
    # provider-side 'failed'; the reason must survive into the breakdown.
    assert "arrier" in (row.error_detail or "")
    assert row.failed_at is not None


def test_delivery_confirmation_clears_a_stale_failure(tk_org, tk_account):
    _row(tk_org, tk_account, "SID_FAIL_THEN_OK", status=SMS_MSG_FAILED)
    _apply(tk_account, "SID_FAIL_THEN_OK", "delivered")
    row = _get("SID_FAIL_THEN_OK")
    assert row.status == SMS_MSG_DELIVERED
    assert row.error_code is None and row.failed_at is None


def test_delivery_unconfirmed_is_neither_success_nor_failure(tk_org, tk_account):
    """Telnyx's 'carrier returned no DLR'. Counting it as delivered would
    overstate delivery; counting it failed would understate it."""
    _row(tk_org, tk_account, "SID_UNCONFIRMED")
    _apply(tk_account, "SID_UNCONFIRMED", "delivery_unconfirmed")
    row = _get("SID_UNCONFIRMED")
    assert row.status == SMS_MSG_SENT
    assert row.delivered_at is None
    assert "no delivery confirmation" in (row.error_detail or "")


def test_receipt_never_touches_an_inbound_row(tk_org, tk_account):
    """Inbound rows carry a provider_sid too, and read_at means the OPPOSITE
    thing on them (our team read the conversation, not the recipient)."""
    _row(tk_org, tk_account, "SID_SHARED", status="received", direction="in")
    _apply(tk_account, "SID_SHARED", "read")
    row = _get("SID_SHARED")
    assert row.status == "received"
    assert row.read_at is None


def test_duplicate_sid_does_not_raise(tk_org, tk_account):
    """Duplicate SIDs are reachable (the BlueBubbles duplicate-send rescue
    probe can return an existing message's guid). Raising here 500s the
    webhook, so the provider retries forever and every later receipt is lost.
    """
    _row(tk_org, tk_account, "SID_DUP")
    _row(tk_org, tk_account, "SID_DUP")
    _apply(tk_account, "SID_DUP", "delivered")  # must not raise
    with SessionLocal() as db:
        rows = (
            db.execute(select(SmsMessage).where(SmsMessage.provider_sid == "SID_DUP"))
            .scalars()
            .all()
        )
    assert any(r.status == SMS_MSG_DELIVERED for r in rows)


# --- inbound idempotency ----------------------------------------------------


def test_retried_inbound_webhook_does_not_duplicate_the_reply(tk_org, tk_account):
    """Providers retry on any non-2xx and BlueBubbles can re-fire
    new-message. Without a guard the retry both duplicates the ledger row and
    re-runs handle_reply, which re-schedules the reply step and texts the lead
    a second time."""
    with SessionLocal() as db:
        acct = db.get(SmsAccount, tk_account["id"])
        for _ in range(2):
            _process_inbound(
                db,
                acct,
                from_raw="+14805550444",
                to_raw=acct.from_number,
                body="sounds good",
                provider_sid="SID_INBOUND_RETRY",
            )
        db.commit()
    with SessionLocal() as db:
        rows = (
            db.execute(
                select(SmsMessage).where(
                    SmsMessage.provider_sid == "SID_INBOUND_RETRY",
                    SmsMessage.direction == "in",
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
