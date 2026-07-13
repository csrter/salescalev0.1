"""Warmup engine strategy + personalization correctness + live-edit steps.

Covers the research-encoded warmup behaviors (ramp curve, weekday gating,
jittered pacing, peer rotation, deterministic ~35% threaded auto-replies with
depth cap, junk rescue accounting, progress/health numbers, bounce throttle),
the personalization casing/tidy/unknown-token layer, the warmup API plumbing
(toggle stamps the ramp clock), and step upsert-in-place while active.
Transport is monkeypatched throughout — no live SMTP/IMAP.
"""

import datetime as dt
from email.utils import parseaddr

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.email_outreach import (
    KIND_WARMUP,
    EmailAccount,
    EmailEnrollment,
    EmailMessage,
    EmailStep,
    EmailWarmupPeer,
)
from app.services import email_personalize, email_warmup
from app.services import email_outreach_send as gateway
from app.services import email_outreach_sync as sync
from app.services import email_transport

from tests.test_email_campaigns import (  # reuse the helpers, NOT the org
    _enable_warmup,
    _mk_account,
    _mk_campaign,
    _mk_contact,
    _set_steps,
    captured_sends,
    probe_ok,
)


@pytest.fixture(scope="module")
def cc_org(api):
    """This module's own org — the campaigns module's cc_org fixture would
    re-run its signup here (module-scoped) and collide on the email."""
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Warmup Co",
            "email": "owner@warmupco.com",
            "password": "warmup-pass-1",
            "full_name": "WU Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "WU Client"}, headers=headers
    ).json()["id"]
    api.put(
        "/api/orgs/me/branding",
        json={"mailing_address": "200 Broadway, New York NY 10005"},
        headers=headers,
    )
    return {"org": body["organization_id"], "headers": headers, "client": client_id}

WEDNESDAY_NOON = dt.datetime(2026, 7, 8, 12, 0, tzinfo=dt.timezone.utc)
SATURDAY_NOON = dt.datetime(2026, 7, 11, 12, 0, tzinfo=dt.timezone.utc)


def _acct(**over):
    """A detached EmailAccount for pure-math tests (no DB)."""
    base = dict(
        organization_id="o",
        name="A",
        from_name="A",
        from_email="a@x.com",
        smtp_host="s",
        imap_host="i",
        smtp_port=1,
        imap_port=1,
        smtp_username="u",
        imap_username="u",
        daily_send_cap=100,
        warmup_enabled=True,
        warmup_target_daily=100,
        warmup_started_at=WEDNESDAY_NOON,
    )
    base.update(over)
    return EmailAccount(**base)


# --- ramp math ---------------------------------------------------------------


def test_warmup_volume_ramp_curve():
    a = _acct(warmup_started_at=WEDNESDAY_NOON)
    # Day 0 → the research floor of 5/day.
    assert email_warmup.warmup_volume_today(a, WEDNESDAY_NOON) == 5
    # Day 14 → halfway from 5 to the 40 ceiling ≈ 22-23.
    day14 = WEDNESDAY_NOON + dt.timedelta(days=14)
    assert email_warmup.warmup_volume_today(a, day14) in (22, 23)
    # Day 28+ → maintenance: 20% of the (fully ramped) cold cap, floor 10.
    day40 = WEDNESDAY_NOON + dt.timedelta(days=40)
    assert email_warmup.warmup_volume_today(a, day40) == 20  # 0.2 * 100
    # Small target: maintenance floor of 10 applies (0.2*50=10).
    small = _acct(warmup_target_daily=50, daily_send_cap=50)
    assert email_warmup.warmup_volume_today(small, day40) == 10
    # Hard ceiling: even a huge target never exceeds 40 warmups/day during
    # the ramp (day 28 boundary hits maintenance, so check day 27).
    big = _acct(warmup_target_daily=500, daily_send_cap=10000)
    day27 = WEDNESDAY_NOON + dt.timedelta(days=27)
    assert email_warmup.warmup_volume_today(big, day27) <= 40
    # Weekends run lighter (WEEKEND_RATIO of the weekday figure), never zero
    # while warming — a hard weekday/weekend cliff reads as scripted.
    sat_day3 = SATURDAY_NOON  # 3 days after WEDNESDAY_NOON start → weekday vol 8-9
    weekday_equiv = email_warmup.warmup_volume_today(
        a, SATURDAY_NOON + dt.timedelta(days=2)
    )
    sat_vol = email_warmup.warmup_volume_today(a, sat_day3)
    assert 0 < sat_vol < weekday_equiv
    # Warmup off → 0.
    off = _acct(warmup_enabled=False)
    assert email_warmup.warmup_volume_today(off, WEDNESDAY_NOON) == 0


def test_warmup_blended_ready_day10():
    a = _acct(warmup_started_at=WEDNESDAY_NOON)
    assert email_warmup.warmup_blended_ready(a, WEDNESDAY_NOON) is False
    day9 = WEDNESDAY_NOON + dt.timedelta(days=9)
    assert email_warmup.warmup_blended_ready(a, day9) is False
    day10 = WEDNESDAY_NOON + dt.timedelta(days=10)
    assert email_warmup.warmup_blended_ready(a, day10) is True
    a.warmup_enabled = False
    assert email_warmup.warmup_blended_ready(a, day10) is False


def test_warmup_progress_deterministic():
    a = _acct(warmup_started_at=WEDNESDAY_NOON - dt.timedelta(days=14))
    assert email_warmup.warmup_progress(a, WEDNESDAY_NOON) == 50
    a.warmup_started_at = WEDNESDAY_NOON
    assert email_warmup.warmup_progress(a, WEDNESDAY_NOON) == 0
    a.warmup_started_at = WEDNESDAY_NOON - dt.timedelta(days=100)
    assert email_warmup.warmup_progress(a, WEDNESDAY_NOON) == 100
    a.warmup_enabled = False
    assert email_warmup.warmup_progress(a, WEDNESDAY_NOON) == 0


def test_send_gap_jitter_deterministic_and_bounded():
    g1 = email_warmup._send_gap_seconds(20, "acct-1", WEDNESDAY_NOON, 3)
    g2 = email_warmup._send_gap_seconds(20, "acct-1", WEDNESDAY_NOON, 3)
    assert g1 == g2  # reproducible
    base = (email_warmup.WINDOW_END_HOUR - email_warmup.WINDOW_START_HOUR) * 3600 / 20
    assert 0.75 * base <= g1 <= 1.25 * base
    # Different send counts jitter differently (not metronomic).
    gaps = {
        round(email_warmup._send_gap_seconds(20, "acct-1", WEDNESDAY_NOON, i))
        for i in range(8)
    }
    assert len(gaps) > 1


# --- engine behavior ----------------------------------------------------------


def test_tick_weekend_and_window_gating(cc_org, api, probe_ok, captured_sends):
    a1 = _mk_account(cc_org, api, from_email="gate1@campaignco.com")
    a2 = _mk_account(cc_org, api, from_email="gate2@campaignco.com")
    _enable_warmup(a1["id"], days_ago=5)
    _enable_warmup(a2["id"], days_ago=5)
    db = SessionLocal()
    try:
        # Weekends still send (reduced budget), so an in-window Saturday tick
        # is allowed through.
        assert (
            email_warmup.run_warmup_tick(db, cc_org["org"], now=SATURDAY_NOON)["sent"]
            >= 1
        )
        # Outside the 08–18 window nothing sends, weekday or not.
        late = WEDNESDAY_NOON.replace(hour=22)
        assert email_warmup.run_warmup_tick(db, cc_org["org"], now=late)["sent"] == 0
        db.rollback()
    finally:
        db.close()


def test_tick_rotates_peers_and_counts(cc_org, api, probe_ok, captured_sends):
    a1 = _mk_account(cc_org, api, from_email="rot1@campaignco.com")
    a2 = _mk_account(cc_org, api, from_email="rot2@campaignco.com")
    a3 = _mk_account(cc_org, api, from_email="rot3@campaignco.com")
    for a in (a1, a2, a3):
        _enable_warmup(a["id"], days_ago=40)
    db = SessionLocal()
    try:
        # Repeated ticks with the pace gap zeroed: rot1 should alternate
        # between its two peers rather than hammering one.
        sent_to = []
        for i in range(4):
            tick_now = WEDNESDAY_NOON + dt.timedelta(seconds=i)

            def _no_gap(budget, account_id, now, count):
                return 0.0

            orig = email_warmup._send_gap_seconds
            email_warmup._send_gap_seconds = _no_gap
            try:
                email_warmup.run_warmup_tick(db, cc_org["org"], now=tick_now)
            finally:
                email_warmup._send_gap_seconds = orig
            db.commit()
        rows = (
            db.query(EmailWarmupPeer)
            .filter(EmailWarmupPeer.account_id == a1["id"])
            .all()
        )
        assert sum(r.sent_count for r in rows) >= 2
        # Rotation used more than one peer instead of hammering a single pair
        # (other warmup accounts created by earlier tests also count as peers).
        assert len([r for r in rows if r.sent_count > 0]) >= 2
    finally:
        db.close()


def _warmup_raw(from_addr, to_addr, *, depth=0, msg_id="<w1@x>", subject="Touching base"):
    return (
        f"From: {from_addr}\r\nTo: {to_addr}\r\nSubject: {subject}\r\n"
        f"Message-ID: {msg_id}\r\nX-Salescale-Warmup: 1\r\n"
        f"X-Salescale-Warmup-Depth: {depth}\r\n\r\nhello"
    ).encode()


def test_warmup_reply_probability_and_threading(cc_org, api, probe_ok, captured_sends):
    a1 = _mk_account(cc_org, api, from_email="rep1@campaignco.com")
    a2 = _mk_account(cc_org, api, from_email="rep2@campaignco.com")
    _enable_warmup(a1["id"], days_ago=5)
    _enable_warmup(a2["id"], days_ago=5)

    # Find message-ids on both sides of the 35% cut so the test pins both
    # branches deterministically.
    reply_id = next(
        f"<r{i}@x>" for i in range(200) if email_warmup._hash_pick(100, f"<r{i}@x>") < 35
    )
    silent_id = next(
        f"<s{i}@x>" for i in range(200) if email_warmup._hash_pick(100, f"<s{i}@x>") >= 35
    )

    db = SessionLocal()
    try:
        acct1 = db.get(EmailAccount, a1["id"])
        before = len(captured_sends)
        email_warmup.on_warmup_received(
            db,
            acct1,
            email_transport.parse_message(
                _warmup_raw("rep2@campaignco.com", "rep1@campaignco.com", msg_id=reply_id)
            ),
        )
        db.commit()
        assert len(captured_sends) == before + 1
        reply = captured_sends[-1]
        assert reply["X-Salescale-Warmup"] == "1"
        assert reply["X-Salescale-Warmup-Depth"] == "1"
        assert reply["In-Reply-To"] == reply_id
        assert reply["Subject"].startswith("Re: ")

        # The silent id records receipt but does not reply.
        before = len(captured_sends)
        email_warmup.on_warmup_received(
            db,
            acct1,
            email_transport.parse_message(
                _warmup_raw("rep2@campaignco.com", "rep1@campaignco.com", msg_id=silent_id)
            ),
        )
        db.commit()
        assert len(captured_sends) == before

        # Depth cap: an incoming reply-to-a-reply (depth 2) never re-replies.
        before = len(captured_sends)
        email_warmup.on_warmup_received(
            db,
            acct1,
            email_transport.parse_message(
                _warmup_raw(
                    "rep2@campaignco.com", "rep1@campaignco.com",
                    msg_id=reply_id, depth=2,
                )
            ),
        )
        db.commit()
        assert len(captured_sends) == before

        # Receipts were recorded on the (receiver → sender) pair each time.
        pair = (
            db.query(EmailWarmupPeer)
            .filter_by(account_id=a1["id"], peer_account_id=a2["id"])
            .one()
        )
        assert pair.received_count == 3
    finally:
        db.close()


def test_junk_rescue_charges_sender(cc_org, api, probe_ok):
    a1 = _mk_account(cc_org, api, from_email="junk1@campaignco.com")
    a2 = _mk_account(cc_org, api, from_email="junk2@campaignco.com")
    _enable_warmup(a1["id"], days_ago=5)
    _enable_warmup(a2["id"], days_ago=5)
    db = SessionLocal()
    try:
        receiver = db.get(EmailAccount, a2["id"])
        # a1's warmup mail was found in a2's spam folder → a1's reputation.
        email_warmup.on_warmup_junk(db, receiver, "junk1@campaignco.com")
        db.commit()
        pair = (
            db.query(EmailWarmupPeer)
            .filter_by(account_id=a1["id"], peer_account_id=a2["id"])
            .one()
        )
        assert pair.junk_count == 1
    finally:
        db.close()


def test_sync_runs_hygiene_for_warmup_accounts(cc_org, api, probe_ok, monkeypatch):
    a1 = _mk_account(cc_org, api, from_email="hyg1@campaignco.com")
    a2 = _mk_account(cc_org, api, from_email="hyg2@campaignco.com")
    _enable_warmup(a1["id"], days_ago=5)
    _enable_warmup(a2["id"], days_ago=5)
    monkeypatch.setattr(email_transport, "fetch_new", lambda account, last_uid: [])
    monkeypatch.setattr(
        email_transport,
        "warmup_inbox_hygiene",
        lambda account: {"rescued_from": ["hyg1@campaignco.com"], "seen": 2},
    )
    db = SessionLocal()
    try:
        receiver = db.get(EmailAccount, a2["id"])
        res = sync.sync_account(db, receiver)
        db.commit()
        assert res["outcomes"].get("warmup_hygiene") == 3
        pair = (
            db.query(EmailWarmupPeer)
            .filter_by(account_id=a1["id"], peer_account_id=a2["id"])
            .one()
        )
        assert pair.junk_count == 1
    finally:
        db.close()


# --- health + throttle --------------------------------------------------------


def test_warmup_health_formula(cc_org, api, probe_ok):
    a1 = _mk_account(cc_org, api, from_email="hp1@campaignco.com")
    a2 = _mk_account(cc_org, api, from_email="hp2@campaignco.com")
    _enable_warmup(a1["id"], days_ago=10)
    _enable_warmup(a2["id"], days_ago=10)
    db = SessionLocal()
    try:
        acct1 = db.get(EmailAccount, a1["id"])
        acct2 = db.get(EmailAccount, a2["id"])
        # Not enough data yet → None.
        assert email_warmup.warmup_health(db, acct1) is None
        pair = email_warmup._pair(db, acct1, acct2)
        pair.sent_count = 20
        # Peer confirmed receiving 18 of them (receiver's row).
        rev = email_warmup._pair(db, acct2, acct1)
        rev.received_count = 18
        db.commit()
        assert email_warmup.warmup_health(db, acct1) == 100  # clean slate
        # 10% junk share → −30 (capped).
        pair.junk_count = 2
        db.commit()
        assert email_warmup.warmup_health(db, acct1) == 70
        # Poor peer delivery (< 60% confirmed) → extra −15.
        rev.received_count = 5
        db.commit()
        assert email_warmup.warmup_health(db, acct1) == 55
    finally:
        db.close()


def test_bounce_throttle_halves_cap(cc_org, api, probe_ok):
    acct_json = _mk_account(cc_org, api, from_email="thr@campaignco.com")
    _enable_warmup(acct_json["id"], days_ago=40, target=100)
    db = SessionLocal()
    try:
        acct = db.get(EmailAccount, acct_json["id"])
        assert email_warmup.effective_daily_cap(acct, db) == 100
        # 3 bounces out of 20 real sends in the window = 15% > 2% → halve.
        for i in range(20):
            db.add(
                EmailMessage(
                    organization_id=cc_org["org"],
                    account_id=acct.id,
                    direction="out",
                    status="sent",
                    kind="campaign",
                    subject="x",
                    body_text="x",
                    bounced_at=utcnow() if i < 3 else None,
                )
            )
        db.commit()
        assert email_warmup.bounce_rate_7d(db, acct) == 15.0
        assert email_warmup.effective_daily_cap(acct, db) == 50
        # Without a db handle the ramp math is unchanged (no throttle).
        assert email_warmup.effective_daily_cap(acct) == 100
    finally:
        db.close()


# --- API plumbing ---------------------------------------------------------------


def test_warmup_toggle_via_api_stamps_ramp_clock(cc_org, api, probe_ok):
    acct = _mk_account(cc_org, api, from_email="tog@campaignco.com")
    assert acct["warmup_enabled"] is False
    assert acct["warmup_progress"] == 0

    r = api.patch(
        f"/api/email-outreach/accounts/{acct['id']}",
        json={"warmup_enabled": True, "warmup_target_daily": 60},
        headers=cc_org["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["warmup_enabled"] is True
    assert body["warmup_target_daily"] == 60
    assert body["warmup_started_at"] is not None
    assert body["warmup_progress"] == 0  # day 0
    assert body["warmup_stage"] == "week 1 of 4"
    assert body["warmup_health"] is None  # no data yet

    # Disable, then re-enable → the ramp clock restarts (fresh timestamp).
    api.patch(
        f"/api/email-outreach/accounts/{acct['id']}",
        json={"warmup_enabled": False},
        headers=cc_org["headers"],
    )
    db = SessionLocal()
    try:
        row = db.get(EmailAccount, acct["id"])
        row.warmup_started_at = utcnow() - dt.timedelta(days=20)  # pretend old
        db.commit()
    finally:
        db.close()
    r = api.patch(
        f"/api/email-outreach/accounts/{acct['id']}",
        json={"warmup_enabled": True},
        headers=cc_org["headers"],
    )
    assert r.json()["warmup_progress"] == 0  # restarted, not resumed at 71%


# --- personalization: casing, tidy, unknown tokens ------------------------------


def _render(body, contact_kwargs):
    from app.models.crm import Contact

    contact = Contact(organization_id="o", client_id="c", **contact_kwargs)
    return email_personalize._render_template(body, contact, None, {})


def test_casing_normalization():
    # all-lower / ALL-CAPS values are re-cased; mixed case is preserved.
    assert _render("Hi {{first_name}}", {"first_name": "john"}) == "Hi John"
    assert _render("Hi {{first_name}}", {"first_name": "JOHN"}) == "Hi John"
    assert _render("Hi {{first_name}}", {"first_name": "McDonald"}) == "Hi McDonald"
    assert _render("{{last_name}}", {"last_name": "o'brien"}) == "O'Brien"
    assert _render("{{last_name}}", {"last_name": "smith-jones"}) == "Smith-Jones"
    assert _render("{{city}}", {"city": "new york"}) == "New York"
    # 2-letter states uppercase; full names title-case.
    assert _render("{{state}}", {"state": "az"}) == "AZ"
    assert _render("{{state}}", {"state": "arizona"}) == "Arizona"
    # Email is never case-mangled.
    assert (
        _render("{{email}}", {"email": "dana@x.com"}) == "dana@x.com"
    )


def test_tidy_removes_empty_token_artifacts():
    got = _render("You're in {{city}}, {{state|}}.", {"city": "Denver", "state": ""})
    assert got == "You're in Denver."
    got = _render("Hi {{first_name|}},\nwelcome", {"first_name": None})
    assert got == "Hi,\nwelcome"
    got = _render("A  {{first_name|}}  B", {"first_name": ""})
    assert got == "A B"
    # Blank line left by an empty token collapses.
    got = _render("Top\n\n{{first_name|}}\n\nBottom", {"first_name": ""})
    assert "\n\n\n" not in got


def test_unknown_tokens_and_save_validation(cc_org, api, probe_ok):
    assert email_personalize.unknown_tokens("Hi {{first_name}} {{frst_name}}") == [
        "frst_name"
    ]
    assert email_personalize.unknown_tokens("{{custom.industry}}", {"industry"}) == []
    assert email_personalize.unknown_tokens("{{custom.industry}}", set()) == [
        "custom.industry"
    ]
    assert email_personalize.unknown_tokens("{{unsubscribe_url}} {{ai_snippet}}") == []

    acct = _mk_account(cc_org, api, from_email="tok@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"])
    r = api.put(
        f"/api/email-outreach/campaigns/{camp['id']}/steps",
        json={"steps": [{"position": 1, "body": "Hi {{frst_name}}"}]},
        headers=cc_org["headers"],
    )
    assert r.status_code == 422
    assert "{{frst_name}}" in r.json()["detail"]


# --- step upsert-in-place --------------------------------------------------------


def test_steps_upsert_preserves_ids_and_snippet_cache(cc_org, api, probe_ok):
    acct = _mk_account(cc_org, api, from_email="ups@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"])
    out = _set_steps(
        cc_org,
        api,
        camp["id"],
        [
            {"position": 1, "body": "one"},
            {"position": 2, "body": "two"},
        ],
    )
    ids = [s["id"] for s in out["steps"]]

    # Simulate a cached AI snippet keyed by the step id.
    cid = _mk_contact(cc_org, api, email_addr="ups-c@example.com")
    db = SessionLocal()
    try:
        enr = EmailEnrollment(
            organization_id=cc_org["org"],
            campaign_id=camp["id"],
            contact_id=cid,
            ai_snippets={ids[0]: "cached snippet"},
        )
        db.add(enr)
        db.commit()
        enr_id = enr.id
    finally:
        db.close()

    # Edit step 1's body, reorder (swap), add a third — sending ids back.
    r = api.put(
        f"/api/email-outreach/campaigns/{camp['id']}/steps",
        json={
            "steps": [
                {"id": ids[1], "position": 1, "body": "two edited"},
                {"id": ids[0], "position": 2, "body": "one edited"},
                {"position": 3, "body": "three"},
            ]
        },
        headers=cc_org["headers"],
    )
    assert r.status_code == 200, r.text
    steps = r.json()["steps"]
    assert [s["body"] for s in steps] == ["two edited", "one edited", "three"]
    # The two original rows kept their identities.
    assert steps[0]["id"] == ids[1]
    assert steps[1]["id"] == ids[0]
    db = SessionLocal()
    try:
        enr = db.get(EmailEnrollment, enr_id)
        assert enr.ai_snippets == {ids[0]: "cached snippet"}  # cache intact
    finally:
        db.close()

    # Dropping a step id deletes that row; unknown ids 422.
    r = api.put(
        f"/api/email-outreach/campaigns/{camp['id']}/steps",
        json={"steps": [{"id": ids[0], "position": 1, "body": "only"}]},
        headers=cc_org["headers"],
    )
    assert r.status_code == 200
    assert [s["id"] for s in r.json()["steps"]] == [ids[0]]
    r = api.put(
        f"/api/email-outreach/campaigns/{camp['id']}/steps",
        json={"steps": [{"id": "nope", "position": 1, "body": "x"}]},
        headers=cc_org["headers"],
    )
    assert r.status_code == 422


def test_archive_endpoint_and_activate_guard(cc_org, api, probe_ok):
    acct = _mk_account(cc_org, api, from_email="arc@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"])
    _set_steps(cc_org, api, camp["id"], [{"position": 1, "body": "x"}])
    r = api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/archive",
        headers=cc_org["headers"],
    )
    assert r.status_code == 200
    assert r.json()["status"] == "archived"
    # Archived campaigns can't be re-activated.
    r = api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/activate",
        headers=cc_org["headers"],
    )
    assert r.status_code == 409


# --- warmup timezone (window/weekend/daily budget follow the mailbox zone) ----


def test_account_local_zone_and_fallback():
    a = _acct(warmup_timezone="America/Phoenix")
    # 20:00 UTC Wednesday is 13:00 in Phoenix (UTC-7, no DST).
    local = email_warmup.account_local(a, WEDNESDAY_NOON.replace(hour=20))
    assert (local.hour, local.weekday()) == (13, 2)
    # Unset and invalid zones degrade to UTC — never stall the engine.
    for tz in (None, "Not/AZone"):
        a.warmup_timezone = tz
        assert email_warmup.account_local(a, WEDNESDAY_NOON).hour == 12


def test_weekend_reduction_follows_local_calendar():
    # Monday 02:00 UTC 2026-07-13 is still Sunday 19:00 in Phoenix.
    monday_utc = dt.datetime(2026, 7, 13, 2, 0, tzinfo=dt.timezone.utc)
    utc_acct = _acct(warmup_started_at=monday_utc - dt.timedelta(days=5))
    phx_acct = _acct(
        warmup_started_at=monday_utc - dt.timedelta(days=5),
        warmup_timezone="America/Phoenix",
    )
    full = email_warmup.warmup_volume_today(utc_acct, monday_utc)
    reduced = email_warmup.warmup_volume_today(phx_acct, monday_utc)
    assert reduced == max(1, round(full * email_warmup.WEEKEND_RATIO))


def test_tick_window_follows_account_timezone(cc_org, api, probe_ok, captured_sends):
    p1 = _mk_account(cc_org, api, from_email="phx1@campaignco.com")
    p2 = _mk_account(cc_org, api, from_email="phx2@campaignco.com")
    for a in (p1, p2):
        _enable_warmup(a["id"], days_ago=5)
        r = api.patch(
            f"/api/email-outreach/accounts/{a['id']}",
            json={"warmup_timezone": "America/Phoenix"},
            headers=cc_org["headers"],
        )
        assert r.status_code == 200, r.text
        assert r.json()["warmup_timezone"] == "America/Phoenix"
    # Garbage zones are rejected at the API boundary.
    r = api.patch(
        f"/api/email-outreach/accounts/{p1['id']}",
        json={"warmup_timezone": "Mars/OlympusMons"},
        headers=cc_org["headers"],
    )
    assert r.status_code == 422

    db = SessionLocal()
    try:
        def _no_gap(budget, account_id, now, count):
            return 0.0

        orig = email_warmup._send_gap_seconds
        email_warmup._send_gap_seconds = _no_gap
        try:
            # 10:00 UTC is inside the UTC window but 03:00 in Phoenix — the
            # Phoenix mailboxes must sit this tick out (other, UTC-zoned
            # accounts in this module's org are free to send).
            before = len(captured_sends)
            email_warmup.run_warmup_tick(db, cc_org["org"], now=WEDNESDAY_NOON.replace(hour=10))
            phx = {"phx1@campaignco.com", "phx2@campaignco.com"}
            froms = {parseaddr(m["From"])[1] for m in captured_sends[before:]}
            assert not (froms & phx)
            # 20:00 UTC is OUTSIDE the UTC window but 13:00 in Phoenix — now
            # ONLY the Phoenix mailboxes may send.
            before = len(captured_sends)
            res = email_warmup.run_warmup_tick(db, cc_org["org"], now=WEDNESDAY_NOON.replace(hour=20))
            new_froms = {parseaddr(m["From"])[1] for m in captured_sends[before:]}
            assert res["sent"] >= 1
            assert new_froms and new_froms <= phx
        finally:
            email_warmup._send_gap_seconds = orig
        db.commit()

        # Daily budget resets at LOCAL midnight: a Phoenix window (15:00–01:00
        # UTC) straddles UTC midnight, and a UTC reset would hand out a second
        # budget mid-day. A send at 23:30 UTC Wednesday (16:30 Phoenix) still
        # counts "today" at 00:30 UTC Thursday (17:30 Phoenix, same local day).
        acct = db.get(EmailAccount, p1["id"])
        rows = (
            db.query(EmailMessage)
            .filter(EmailMessage.account_id == acct.id, EmailMessage.kind == KIND_WARMUP)
            .all()
        )
        assert rows, "the 20:00 UTC tick above should have sent from phx1"
        for m in rows:
            m.created_at = dt.datetime(2026, 7, 8, 23, 30, tzinfo=dt.timezone.utc)
        db.commit()
        thu_0030_utc = dt.datetime(2026, 7, 9, 0, 30, tzinfo=dt.timezone.utc)
        assert email_warmup.warmup_sends_today(db, acct, thu_0030_utc) >= 1
        acct.warmup_timezone = None  # same instant under UTC = yesterday
        assert email_warmup.warmup_sends_today(db, acct, thu_0030_utc) == 0
        db.rollback()
    finally:
        db.close()
