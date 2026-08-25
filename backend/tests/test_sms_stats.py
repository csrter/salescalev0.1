"""SMS tracking METRICS — the accuracy half of the module: what the campaign
stats, the analytics endpoint and the message serialization actually claim.

These tests fabricate ledger rows directly rather than driving the engine.
That is deliberate: the shapes under test (a delivery receipt that lands three
days after the send, a fully dead number with zero successes, a green-bubble
mailbox that structurally never reports delivery) are exactly the states the
engine cannot be made to produce on demand, and they are the states the old
numbers got wrong.

Everything runs against a dedicated org (st_org) — module-scoped fixtures
elsewhere leave due enrollments that other suites' run_due() picks up, and
the seeded Atlas Reach org's counts are asserted over by the metrics suite.
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.sms_outreach import SmsCampaign, SmsEnrollment, SmsMessage
from app.services import sms_send as gateway


# --- fixtures ----------------------------------------------------------------


@pytest.fixture()
def creds_ok(monkeypatch):
    """Account create/test probes the provider over the network — stub it."""
    monkeypatch.setattr(gateway, "verify_credentials", lambda account: (True, "ok"))


@pytest.fixture(scope="module")
def st_org(api):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "SMS Stats Co",
            "email": "owner@smsstatsco.com",
            "password": "smsstatsco-pass-1",
            "full_name": "Stats Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "Stats Client"}, headers=headers
    ).json()["id"]
    return {"org": body["organization_id"], "headers": headers, "client": client_id}


_AUTH_TOKEN = "sms-stats-auth-token-0123456789"

# This module's numbers are its own — the sms_accounts (org, from_number)
# unique index bites across a module-scoped org, so every account here takes a
# fresh one from this block.
_NUMBERS = iter(f"+1480556{n:04d}" for n in range(1, 400))


def _mk_account(st_org, api, **over):
    base = {
        "name": "Stats Line",
        "account_sid": "ACstatsaccountsid0000000",
        "auth_token": _AUTH_TOKEN,
        "from_number": next(_NUMBERS),
        "daily_send_cap": 500,
    }
    base.update(over)
    r = api.post("/api/sms/accounts", json=base, headers=st_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def _mk_campaign(st_org, api, account_id, name="Stats Campaign", **over):
    payload = {
        "name": name,
        "account_id": account_id,
        "send_window_start": 0,
        "send_window_end": 24,
        "send_days": [0, 1, 2, 3, 4, 5, 6],
        "timezone": "UTC",
    }
    payload.update(over)
    r = api.post("/api/sms/campaigns", json=payload, headers=st_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def _mk_contact(st_org, api, *, mobile_phone, first="Lee"):
    r = api.post(
        "/api/crm/contacts",
        json={
            "client_id": st_org["client"],
            "first_name": first,
            "last_name": "Prospect",
            "mobile_phone": mobile_phone,
            "sms_opt_in": True,
        },
        headers=st_org["headers"],
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ago(**kw):
    return utcnow() - dt.timedelta(**kw)


def _add_message(st_org, *, account_id, campaign_id=None, **over):
    """Write one outbound ledger row with full control over its timestamps —
    the whole point of these tests is what happens when created_at,
    delivered_at and read_at disagree."""
    db = SessionLocal()
    try:
        fields = {
            "organization_id": st_org["org"],
            "account_id": account_id,
            "campaign_id": campaign_id,
            "direction": "out",
            "kind": "campaign",
            "to_number": "+14805559000",
            "body": "hello",
            "status": "sent",
            "created_at": utcnow(),
        }
        fields.update(over)
        row = SmsMessage(**fields)
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def _add_enrollment(st_org, *, campaign_id, contact_id, **over):
    db = SessionLocal()
    try:
        fields = {
            "organization_id": st_org["org"],
            "campaign_id": campaign_id,
            "contact_id": contact_id,
            "status": "active",
            "current_position": 1,
            "created_at": utcnow(),
        }
        fields.update(over)
        row = SmsEnrollment(**fields)
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def _analytics(st_org, api, campaign_id=None, days=30, hours=None):
    q = f"?hours={hours}" if hours is not None else f"?days={days}"
    q += f"&campaign_id={campaign_id}" if campaign_id else ""
    r = api.get(f"/api/sms/analytics{q}", headers=st_org["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _campaign_row(analytics, campaign_id):
    return next(
        c for c in analytics["by_campaign"] if c["campaign_id"] == campaign_id
    )


def _account_row(analytics, account_id):
    return next(a for a in analytics["accounts"] if a["account_id"] == account_id)


# --- 1. the range selector actually applies ----------------------------------


def test_date_range_windows_totals_and_by_campaign_not_just_by_day(
    st_org, api, creds_ok
):
    """The headline KPIs and the By-campaign table used to be all-time figures
    sitting under a control that said "7 days" — only by_day was windowed."""
    acct = _mk_account(st_org, api)
    camp = _mk_campaign(st_org, api, acct["id"], name="Windowed")
    for _ in range(3):
        _add_message(st_org, account_id=acct["id"], campaign_id=camp["id"])
    for _ in range(5):
        _add_message(
            st_org,
            account_id=acct["id"],
            campaign_id=camp["id"],
            created_at=_ago(days=40),
        )

    week = _campaign_row(_analytics(st_org, api, camp["id"], days=7), camp["id"])
    assert week["sent"] == 3
    assert _analytics(st_org, api, camp["id"], days=7)["totals"]["sent"] == 3

    lifetime = _campaign_row(
        _analytics(st_org, api, camp["id"], days=90), camp["id"]
    )
    assert lifetime["sent"] == 8

    # The window is echoed back so the UI can label what it is showing.
    assert _analytics(st_org, api, camp["id"], days=7)["days"] == 7

    # ...while the campaign detail route keeps reporting LIFETIME stats.
    detail = api.get(
        f"/api/sms/campaigns/{camp['id']}", headers=st_org["headers"]
    ).json()
    assert detail["sent"] == 8


def test_account_rollup_follows_the_selected_window(st_org, api, creds_ok):
    """_analytics_accounts was hardcoded to 7 days regardless of the range."""
    acct = _mk_account(st_org, api)
    _add_message(st_org, account_id=acct["id"])
    _add_message(st_org, account_id=acct["id"], created_at=_ago(days=20))

    assert _account_row(_analytics(st_org, api, days=7), acct["id"])["sent"] == 1
    assert _account_row(_analytics(st_org, api, days=90), acct["id"])["sent"] == 2


# --- 2. per-lead rates + the opt-out red line --------------------------------


def test_per_lead_rates_divide_people_by_people(st_org, api, creds_ok):
    """A 5-step campaign texts each lead 5 times, so replied/sent reports
    roughly a fifth of the true per-lead rate — and the carrier-filtering red
    line reads off the opt-out rate, so it under-fires by the same factor."""
    acct = _mk_account(st_org, api)
    camp = _mk_campaign(st_org, api, acct["id"], name="Five step")
    # 4 leads; one replied, one opted out. Each got 5 sends.
    for i in range(4):
        contact = _mk_contact(st_org, api, mobile_phone=f"480666{i:04d}")
        over = {}
        if i == 0:
            over = {"replied_at": utcnow()}
        elif i == 1:
            over = {"status": "exited", "exit_reason": "opted_out"}
        _add_enrollment(
            st_org, campaign_id=camp["id"], contact_id=contact, **over
        )
        for _ in range(5):
            _add_message(st_org, account_id=acct["id"], campaign_id=camp["id"])

    row = _campaign_row(_analytics(st_org, api, camp["id"], days=7), camp["id"])
    assert row["enrolled"] == 4 and row["sent"] == 20
    # Per lead: 1 of 4. Per message: 1 of 20 — a fifth of the truth.
    assert row["reply_rate_per_lead"] == 0.25
    assert row["opt_out_rate_per_lead"] == 0.25
    assert row["reply_rate"] == 0.05  # kept for compatibility
    assert row["opt_out_rate"] == 0.05

    totals = _analytics(st_org, api, camp["id"], days=7)["totals"]
    assert totals["opt_out_rate_per_lead"] == 0.25
    # 25% is far over the 5% red line the UI alerts on; the message-denominated
    # figure (5%) sits exactly ON it and would have read as borderline.
    assert totals["opt_out_rate"] == 0.05


def test_per_lead_rates_are_null_with_no_cohort(st_org, api, creds_ok):
    """No leads enrolled in the window → undefined, not zero."""
    acct = _mk_account(st_org, api)
    camp = _mk_campaign(st_org, api, acct["id"], name="No cohort")
    _add_message(st_org, account_id=acct["id"], campaign_id=camp["id"])
    row = _campaign_row(_analytics(st_org, api, camp["id"], days=7), camp["id"])
    assert row["enrolled"] == 0
    assert row["reply_rate_per_lead"] is None
    assert row["opt_out_rate_per_lead"] is None


# --- 3. failure rate over ATTEMPTS -------------------------------------------


def test_failure_rate_divides_by_attempts_not_successes(st_org, api, creds_ok):
    """failed/sent reported 50 sent + 50 failed as "100%"."""
    acct = _mk_account(st_org, api)
    for _ in range(3):
        _add_message(st_org, account_id=acct["id"])
    for _ in range(1):
        _add_message(st_org, account_id=acct["id"], status="failed")

    row = _account_row(_analytics(st_org, api, days=7), acct["id"])
    assert row["sent"] == 3 and row["failed"] == 1
    assert row["failure_rate"] == 0.25
    assert row["failure_rate_7d"] == 0.25


def test_fully_dead_number_reports_100_percent_not_nothing(st_org, api, creds_ok):
    """0 successes / N failures is the most severe case, and it used to divide
    by zero and render as "—"."""
    acct = _mk_account(st_org, api)
    for _ in range(4):
        _add_message(st_org, account_id=acct["id"], status="failed")

    row = _account_row(_analytics(st_org, api, days=7), acct["id"])
    assert row["failure_rate"] == 1.0
    assert row["failure_rate_7d"] == 1.0


# --- 4. provider-blind rates must not render as confident zeros --------------


def test_green_bubble_delivery_rate_counts_anything_not_failed(
    st_org, api, creds_ok
):
    """A BlueBubbles mailbox pinned to SMS gets no delivery receipt ever — a
    successful send terminates at "sent". Delivery is therefore measured as
    "attempted and never reported failed", which is a real signal on that
    channel (the verify pass flips silent failures to `failed`), so three
    clean sends read 100%, not 0% and not "—". Read receipts genuinely cannot
    exist there and still read "—"."""
    acct = _mk_account(
        st_org,
        api,
        provider="bluebubbles",
        account_sid=None,
        relay_url="https://relay.example.com",
    )
    camp = _mk_campaign(st_org, api, acct["id"], name="Green bubble")
    for _ in range(3):
        _add_message(
            st_org, account_id=acct["id"], campaign_id=camp["id"], service="SMS"
        )

    row = _campaign_row(_analytics(st_org, api, camp["id"], days=7), camp["id"])
    assert row["sent"] == 3
    assert row["attempted"] == 3
    assert row["delivered"] == 0  # confirmed-receipt subset stays honest
    assert row["delivery_rate"] == 1.0
    assert row["read_measurable"] is False
    assert row["read_rate"] is None
    assert _analytics(st_org, api, camp["id"], days=7)["totals"]["read_rate"] is None


def test_delivery_rate_is_dragged_down_only_by_real_failures(
    st_org, api, creds_ok
):
    """Failures ARE reported on every channel, so they are what the delivery
    rate measures. One rejected send out of four attempts is 75%."""
    acct = _mk_account(
        st_org,
        api,
        provider="bluebubbles",
        account_sid=None,
        relay_url="https://relay.example.com",
    )
    camp = _mk_campaign(st_org, api, acct["id"], name="One bad number")
    for _ in range(3):
        _add_message(
            st_org, account_id=acct["id"], campaign_id=camp["id"], service="SMS"
        )
    _add_message(
        st_org, account_id=acct["id"], campaign_id=camp["id"], status="failed"
    )

    row = _campaign_row(_analytics(st_org, api, camp["id"], days=7), camp["id"])
    assert row["sent"] == 3
    assert row["failed"] == 1
    assert row["attempted"] == 4
    assert row["delivery_rate"] == 0.75
    assert _analytics(st_org, api, camp["id"], days=7)["totals"][
        "delivery_rate"
    ] == 0.75


def test_twilio_read_rate_is_null_but_delivery_rate_is_real(st_org, api, creds_ok):
    """Twilio confirms delivery and never reports a read — so the confirmed
    count is real while the read rate is structurally absent. The delivery
    rate still measures non-failure, so an unconfirmed-but-unfailed send is
    not held against it."""
    acct = _mk_account(st_org, api)
    camp = _mk_campaign(st_org, api, acct["id"], name="Twilio split")
    _add_message(
        st_org,
        account_id=acct["id"],
        campaign_id=camp["id"],
        status="delivered",
        delivered_at=utcnow(),
    )
    _add_message(st_org, account_id=acct["id"], campaign_id=camp["id"])

    row = _campaign_row(_analytics(st_org, api, camp["id"], days=7), camp["id"])
    assert row["delivered"] == 1
    assert row["delivery_rate"] == 1.0
    assert row["read_measurable"] is False
    assert row["read_rate"] is None


def test_an_observed_receipt_always_beats_the_capability_table(
    st_org, api, creds_ok
):
    """The gate suppresses a metric that CANNOT exist — never one that does."""
    acct = _mk_account(
        st_org,
        api,
        provider="bluebubbles",
        account_sid=None,
        relay_url="https://relay.example.com",
    )
    camp = _mk_campaign(st_org, api, acct["id"], name="Observed anyway")
    _add_message(
        st_org,
        account_id=acct["id"],
        campaign_id=camp["id"],
        status="read",
        delivered_at=utcnow(),
        read_at=utcnow(),
    )
    row = _campaign_row(_analytics(st_org, api, camp["id"], days=7), camp["id"])
    assert row["read_measurable"] is True
    assert row["read_rate"] == 1.0


def test_analytics_accounts_carry_the_provider(st_org, api, creds_ok):
    """The qualifier that makes a null/zero rate readable."""
    acct = _mk_account(st_org, api, provider="telnyx", account_sid=None)
    row = _account_row(_analytics(st_org, api, days=7), acct["id"])
    assert row["provider"] == "telnyx"
    assert "channel_health" in row


# --- 5. the unconfirmed bucket -----------------------------------------------


def test_unconfirmed_counts_sent_rows_with_no_verification(st_org, api, creds_ok):
    """"sent" with no receipt and no read-back is provisional — surface it so
    sent, delivered, failed and unconfirmed reconcile."""
    acct = _mk_account(st_org, api)
    camp = _mk_campaign(st_org, api, acct["id"], name="Unconfirmed")
    _add_message(st_org, account_id=acct["id"], campaign_id=camp["id"])
    _add_message(
        st_org,
        account_id=acct["id"],
        campaign_id=camp["id"],
        verified_at=utcnow(),
    )
    _add_message(
        st_org,
        account_id=acct["id"],
        campaign_id=camp["id"],
        status="delivered",
        delivered_at=utcnow(),
    )

    row = _campaign_row(_analytics(st_org, api, camp["id"], days=7), camp["id"])
    assert row["sent"] == 3
    assert row["unconfirmed"] == 1  # not the verified one, not the delivered one
    assert _account_row(_analytics(st_org, api, days=7), acct["id"])[
        "unconfirmed"
    ] == 1
    assert _analytics(st_org, api, camp["id"], days=7)["totals"]["unconfirmed"] == 1


# --- 6. latency --------------------------------------------------------------


def test_delivery_and_reply_latency_medians(st_org, api, creds_ok):
    acct = _mk_account(st_org, api)
    camp = _mk_campaign(st_org, api, acct["id"], name="Latency")
    # 5 deliveries at 10s, and one slow 100s outlier the p90 should catch.
    base = _ago(hours=2)
    for seconds in (10, 10, 10, 10, 10, 100):
        _add_message(
            st_org,
            account_id=acct["id"],
            campaign_id=camp["id"],
            status="delivered",
            created_at=base,
            delivered_at=base + dt.timedelta(seconds=seconds),
        )
    # 5 leads who each replied 60s after their first outbound message.
    for i in range(5):
        contact = _mk_contact(st_org, api, mobile_phone=f"480777{i:04d}")
        enrollment = _add_enrollment(
            st_org,
            campaign_id=camp["id"],
            contact_id=contact,
            replied_at=base + dt.timedelta(seconds=60),
        )
        _add_message(
            st_org,
            account_id=acct["id"],
            campaign_id=camp["id"],
            enrollment_id=enrollment,
            created_at=base,
        )

    row = _campaign_row(_analytics(st_org, api, camp["id"], days=7), camp["id"])
    assert row["median_delivery_seconds"] == 10
    assert row["p90_delivery_seconds"] == 100
    assert row["median_reply_seconds"] == 60


def test_latency_is_null_below_the_minimum_sample(st_org, api, creds_ok):
    """Two observations are noise, not a median."""
    acct = _mk_account(st_org, api)
    camp = _mk_campaign(st_org, api, acct["id"], name="Thin sample")
    base = _ago(hours=1)
    for _ in range(2):
        _add_message(
            st_org,
            account_id=acct["id"],
            campaign_id=camp["id"],
            status="delivered",
            created_at=base,
            delivered_at=base + dt.timedelta(seconds=5),
        )
    row = _campaign_row(_analytics(st_org, api, camp["id"], days=7), camp["id"])
    assert row["median_delivery_seconds"] is None
    assert row["p90_delivery_seconds"] is None
    assert row["median_reply_seconds"] is None


# --- 7. by_day honesty -------------------------------------------------------


def test_delivered_buckets_on_the_delivery_day_not_the_send_day(
    st_org, api, creds_ok
):
    """A receipt landing days after the send used to retroactively mutate the
    send day's bar."""
    acct = _mk_account(st_org, api)
    camp = _mk_campaign(st_org, api, acct["id"], name="Bucketing")
    sent_day = _ago(days=4)
    delivered_day = _ago(days=1)
    _add_message(
        st_org,
        account_id=acct["id"],
        campaign_id=camp["id"],
        status="delivered",
        created_at=sent_day,
        delivered_at=delivered_day,
    )

    by_day = {d["date"]: d for d in _analytics(st_org, api, camp["id"], days=7)["by_day"]}
    sent_key = sent_day.date().isoformat()
    delivered_key = delivered_day.date().isoformat()
    assert by_day[sent_key]["sent"] == 1
    assert by_day[sent_key]["delivered"] == 0
    assert by_day[delivered_key]["delivered"] == 1


def test_legacy_delivered_row_without_a_timestamp_falls_back_to_send_day(
    st_org, api, creds_ok
):
    """Rows written before delivered_at existed still have to appear."""
    acct = _mk_account(st_org, api)
    camp = _mk_campaign(st_org, api, acct["id"], name="Legacy bucket")
    sent_day = _ago(days=2)
    _add_message(
        st_org,
        account_id=acct["id"],
        campaign_id=camp["id"],
        status="delivered",
        created_at=sent_day,
        delivered_at=None,
    )
    by_day = {d["date"]: d for d in _analytics(st_org, api, camp["id"], days=7)["by_day"]}
    assert by_day[sent_day.date().isoformat()]["delivered"] == 1


# --- 8. TCPA exposure: the inbound webhook was never wired -------------------


def test_inbound_webhook_stale_is_surfaced_on_accounts_and_analytics(
    st_org, api, creds_ok
):
    """A non-Twilio active number with real volume and zero inbound messages
    ever means STOP is not being captured — a compliance failure, so it has to
    reach the UI, not just get computed."""
    acct = _mk_account(
        st_org,
        api,
        provider="sendblue",
        account_sid="sendblue-key-id",
    )
    for _ in range(25):
        _add_message(st_org, account_id=acct["id"])

    listed = next(
        a
        for a in api.get("/api/sms/accounts", headers=st_org["headers"]).json()
        if a["id"] == acct["id"]
    )
    assert listed["inbound_webhook_stale"] is True
    assert listed["last_inbound_at"] is None

    row = _account_row(_analytics(st_org, api, days=30), acct["id"])
    assert row["inbound_webhook_stale"] is True

    # One inbound message clears it — the webhook is demonstrably delivering.
    _add_message(
        st_org,
        account_id=acct["id"],
        direction="in",
        status="received",
        to_number=acct["from_number"],
    )
    row = _account_row(_analytics(st_org, api, days=30), acct["id"])
    assert row["inbound_webhook_stale"] is False
    assert row["last_inbound_at"] is not None


# --- 9. message serialization ------------------------------------------------


def test_message_out_carries_service_verified_and_honest_sent_at(
    st_org, api, creds_ok
):
    """service/verified_at were populated but never serialized, and every
    outbound row claimed a sent_at — including ones that never sent."""
    acct = _mk_account(st_org, api)
    contact = _mk_contact(st_org, api, mobile_phone="4808881234", first="Ser")
    verified = utcnow()
    _add_message(
        st_org,
        account_id=acct["id"],
        contact_id=contact,
        service="SMS",
        verified_at=verified,
        delivered_at=verified,
        status="delivered",
    )
    _add_message(
        st_org,
        account_id=acct["id"],
        contact_id=contact,
        status="failed",
        error_detail="Invalid number",
    )

    rows = api.get(
        f"/api/sms/messages?contact_id={contact}", headers=st_org["headers"]
    ).json()
    assert len(rows) == 2
    delivered = next(r for r in rows if r["status"] == "delivered")
    failed = next(r for r in rows if r["status"] == "failed")

    assert delivered["service"] == "SMS"
    assert delivered["verified_at"] is not None
    assert delivered["delivered_at"] is not None
    assert delivered["sent_at"] is not None

    # Never sent — so it reports no send time, but keeps created_at for display.
    assert failed["sent_at"] is None
    assert failed["created_at"] is not None
    assert failed["verified_at"] is None


# --- isolation ---------------------------------------------------------------


def test_analytics_stays_org_scoped(st_org, api, creds_ok, seeded):
    """Every figure on this screen is org-scoped — another org's campaigns and
    numbers never appear, whatever the window."""
    data = _analytics(st_org, api, days=365)
    ours = {c["campaign_id"] for c in data["by_campaign"]}
    db = SessionLocal()
    try:
        foreign = db.execute(
            select(SmsCampaign.id).where(
                SmsCampaign.organization_id != st_org["org"]
            )
        ).scalars().all()
    finally:
        db.close()
    assert ours.isdisjoint(set(foreign))


# --- 8. sub-day windows ------------------------------------------------------


def test_hours_window_is_rolling_and_excludes_older_sends(st_org, api, creds_ok):
    """`hours` is a rolling window ending NOW, not a calendar day. Two sends
    inside the last hour and two well outside it: the hour window sees only
    the recent pair, the day window sees all four."""
    acct = _mk_account(st_org, api)
    camp = _mk_campaign(st_org, api, acct["id"], name="Rolling window")
    now = utcnow()
    for minutes in (5, 30):
        _add_message(
            st_org,
            account_id=acct["id"],
            campaign_id=camp["id"],
            created_at=now - dt.timedelta(minutes=minutes),
        )
    for hours_ago in (3, 6):
        _add_message(
            st_org,
            account_id=acct["id"],
            campaign_id=camp["id"],
            created_at=now - dt.timedelta(hours=hours_ago),
        )

    hour = _analytics(st_org, api, camp["id"], hours=1)
    assert hour["hours"] == 1
    assert _campaign_row(hour, camp["id"])["sent"] == 2

    # A 12-hour window reaches back over all four.
    half_day = _analytics(st_org, api, camp["id"], hours=12)
    assert _campaign_row(half_day, camp["id"])["sent"] == 4


def test_short_windows_bucket_the_series_hourly(st_org, api, creds_ok):
    """A one-hour range plotted in day buckets is a single bar. Sub-day
    windows switch the series to hourly resolution, and the day-based ranges
    keep their date buckets."""
    acct = _mk_account(st_org, api)
    camp = _mk_campaign(st_org, api, acct["id"], name="Hourly buckets")
    now = utcnow()
    for hours_ago in (1, 2, 3):
        _add_message(
            st_org,
            account_id=acct["id"],
            campaign_id=camp["id"],
            created_at=now - dt.timedelta(hours=hours_ago, minutes=5),
        )

    short = _analytics(st_org, api, camp["id"], hours=12)
    assert short["granularity"] == "hour"
    # Three sends an hour apart land in three distinct buckets, each an ISO
    # timestamp rather than a bare date.
    buckets = [b for b in short["by_day"] if b["sent"]]
    assert len(buckets) == 3
    assert all("T" in b["date"] for b in buckets)

    long = _analytics(st_org, api, camp["id"], days=30)
    assert long["granularity"] == "day"
    assert all("T" not in b["date"] for b in long["by_day"])


def test_hours_beats_days_and_is_clamped(st_org, api, creds_ok):
    """Both params given → hours wins (it is the more specific ask), and an
    absurd value is clamped rather than rejected."""
    acct = _mk_account(st_org, api)
    camp = _mk_campaign(st_org, api, acct["id"], name="Clamped")
    _add_message(
        st_org,
        account_id=acct["id"],
        campaign_id=camp["id"],
        created_at=utcnow() - dt.timedelta(hours=6),
    )

    r = api.get(
        f"/api/sms/analytics?days=90&hours=1&campaign_id={camp['id']}",
        headers=st_org["headers"],
    )
    assert r.status_code == 200
    assert r.json()["hours"] == 1
    assert _campaign_row(r.json(), camp["id"])["sent"] == 0  # the 6h-old send

    assert _analytics(st_org, api, camp["id"], hours=0)["hours"] == 1
    assert _analytics(st_org, api, camp["id"], hours=10**6)["hours"] == 365 * 24
