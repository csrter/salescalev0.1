"""Cold-email Outreach module — Phase 2 (campaign engine, personalization,
warmup, analytics).

The SMTP/IMAP transport and the Claude API are monkeypatched (no live services).
Contact/campaign work runs against a dedicated org (cc_org) so the seeded Atlas
Reach org's counts (which the metrics suite asserts over) are untouched.

Covered: enroll_contacts partitioning + the monthly-send entitlement gate; the
process_enrollment state machine across multiple steps incl. window/day gating
and campaign daily cap; the SENT/SUPPRESSED/BLOCKED/CAP_REACHED/FAILED branch
outcomes; exit-on-reply / exit-on-bounce / unsubscribe-stops-all-campaigns; AI
snippet generation + caching + fail-open; warmup cap ramp math + peer send
tagging; activate 422s (steps / mailing address); analytics rate math; the
usage endpoint; and tenant isolation + admin-vs-team gating on the new routes.
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.core import Organization
from app.models.crm import Contact
from app.models.email_outreach import (
    ACCOUNT_ACTIVE,
    EmailAccount,
    EmailCampaign,
    EmailEnrollment,
    EmailMessage,
    EmailStep,
    EmailThread,
    EmailWarmupPeer,
)
from app.services import email_campaigns, email_personalize, email_warmup
from app.services import email_outreach_send as gateway
from app.services import email_outreach_sync as sync
from app.services import email_transport


# --- fixtures ---------------------------------------------------------------


@pytest.fixture()
def probe_ok(monkeypatch):
    monkeypatch.setattr(
        email_transport,
        "probe",
        lambda account: {"smtp_ok": True, "imap_ok": True, "detail": None},
    )


@pytest.fixture()
def captured_sends(monkeypatch):
    sent = []

    def _fake_smtp_send(account, msg):
        sent.append(msg)
        return "250 OK captured"

    monkeypatch.setattr(email_transport, "smtp_send", _fake_smtp_send)
    return sent


@pytest.fixture(scope="module")
def cc_org(api):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Campaign Co",
            "email": "owner@campaignco.com",
            "password": "campaign-pass-1",
            "full_name": "CC Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "CC Client"}, headers=headers
    ).json()["id"]
    # A mailing address so campaign activation passes the CAN-SPAM gate.
    api.put(
        "/api/orgs/me/branding",
        json={"mailing_address": "100 Broadway, New York NY 10005"},
        headers=headers,
    )
    return {"org": body["organization_id"], "headers": headers, "client": client_id}


def _mk_contact(cc_org, api, *, email_addr, first="Dana", last="Doe", **extra):
    payload = {
        "client_id": cc_org["client"],
        "first_name": first,
        "last_name": last,
        "email": email_addr,
    }
    payload.update(extra)
    r = api.post("/api/crm/contacts", json=payload, headers=cc_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _mk_account(cc_org, api, **over):
    base = {
        "name": "CC Mailbox",
        "from_name": "Casey Campaign",
        "from_email": "casey@campaignco.com",
        "smtp_host": "smtp.campaignco.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "imap_host": "imap.campaignco.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "smtp_username": "casey@campaignco.com",
        "smtp_password": "mbx-secret",
        "imap_username": "casey@campaignco.com",
        "imap_password": "mbx-secret",
        "daily_send_cap": 100,
    }
    base.update(over)
    r = api.post("/api/email-outreach/accounts", json=base, headers=cc_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def _mk_campaign(cc_org, api, account_id, **over):
    payload = {"name": "Q3 Roofers", "account_id": account_id}
    payload.update(over)
    r = api.post(
        "/api/email-outreach/campaigns", json=payload, headers=cc_org["headers"]
    )
    assert r.status_code == 201, r.text
    return r.json()


def _set_steps(cc_org, api, campaign_id, steps):
    r = api.put(
        f"/api/email-outreach/campaigns/{campaign_id}/steps",
        json={"steps": steps},
        headers=cc_org["headers"],
    )
    assert r.status_code == 200, r.text
    return r.json()


def _activate(cc_org, api, campaign_id):
    return api.post(
        f"/api/email-outreach/campaigns/{campaign_id}/activate",
        headers=cc_org["headers"],
    )


def _tick():
    db = SessionLocal()
    try:
        return email_campaigns.run_due(db)
    finally:
        db.close()


# A window that is always open (every day, all 24h) so window gating never
# blocks a test that isn't specifically about it.
_ALWAYS = {"send_window_start": 0, "send_window_end": 24, "send_days": [0, 1, 2, 3, 4, 5, 6]}


# --- enrollment partitioning ------------------------------------------------


def test_enroll_partitions_ok_risky_invalid_suppressed_noemail_dupe(
    cc_org, api, probe_ok
):
    acct = _mk_account(cc_org, api, from_email="enr@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], **_ALWAYS)

    ok = _mk_contact(cc_org, api, email_addr="ok@example.com")
    risky = _mk_contact(cc_org, api, email_addr="risky@example.com")
    invalid = _mk_contact(cc_org, api, email_addr="invalid@example.com")
    suppressed = _mk_contact(cc_org, api, email_addr="supp@example.com")
    noemail = _mk_contact(cc_org, api, email_addr="temp@example.com")

    db = SessionLocal()
    try:
        db.get(Contact, risky).verification_status = "risky"
        db.get(Contact, invalid).verification_status = "invalid"
        db.get(Contact, noemail).email = None
        db.commit()
    finally:
        db.close()
    api.post(
        "/api/email-outreach/suppression",
        json={"emails": ["supp@example.com"]},
        headers=cc_org["headers"],
    )

    r = api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/enroll",
        json={"contact_ids": [ok, risky, invalid, suppressed, noemail]},
        headers=cc_org["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enrolled"] == 2  # ok + risky
    assert {x["contact_id"] for x in body["risky"]} == {risky}
    reasons = {x["contact_id"]: x["reason"] for x in body["skipped"]}
    assert reasons[invalid] == "invalid_email"
    assert reasons[suppressed] == "suppressed"
    assert reasons[noemail] == "no_email"

    # Re-enroll the same ok contact → already_enrolled.
    r2 = api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/enroll",
        json={"contact_ids": [ok]},
        headers=cc_org["headers"],
    )
    assert r2.json()["enrolled"] == 0
    assert r2.json()["skipped"][0]["reason"] == "already_enrolled"


def test_enroll_gated_by_monthly_send_quota(cc_org, api, probe_ok, monkeypatch):
    from app.services import entitlements

    acct = _mk_account(cc_org, api, from_email="quota@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(cc_org, api, email_addr="quota-lead@example.com")

    monkeypatch.setattr(
        entitlements, "email_outreach_usage", lambda db, org: {"used": 5, "limit": 5}
    )
    r = api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/enroll",
        json={"contact_ids": [contact]},
        headers=cc_org["headers"],
    )
    assert r.status_code == 402


# --- step state machine -----------------------------------------------------


def _enroll_and_activate(cc_org, api, camp, steps, contact_ids):
    _set_steps(cc_org, api, camp["id"], steps)
    assert _activate(cc_org, api, camp["id"]).status_code == 200
    r = api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/enroll",
        json={"contact_ids": contact_ids},
        headers=cc_org["headers"],
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_two_step_happy_path_sends_and_schedules_next(
    cc_org, api, probe_ok, captured_sends
):
    acct = _mk_account(cc_org, api, from_email="two@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(cc_org, api, email_addr="two-lead@example.com", first="Rex")
    _enroll_and_activate(
        cc_org,
        api,
        camp,
        [
            {"position": 1, "wait_days": 0, "subject": "Hi {{first_name}}", "body": "First touch"},
            {"position": 2, "wait_days": 3, "subject": None, "body": "Bump {{first_name|there}}"},
        ],
        [contact],
    )

    _tick()  # sends step 1
    assert len(captured_sends) == 1
    assert captured_sends[-1]["Subject"] == "Hi Rex"

    db = SessionLocal()
    try:
        e = db.execute(select_enrollment(camp["id"], contact)).scalar_one()
        assert e.current_position == 2
        assert e.status == "active"
        assert e.thread_id is not None
        # next_run_at ~3 days out.
        assert e.next_run_at is not None
        delta = e.next_run_at.replace(tzinfo=dt.timezone.utc) - utcnow()
        assert dt.timedelta(days=2) < delta < dt.timedelta(days=4)
        # Force step 2 due; it should thread (Re:) with no new subject.
        e.next_run_at = utcnow() - dt.timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    _tick()  # sends step 2
    assert len(captured_sends) == 2
    assert captured_sends[-1]["Subject"].startswith("Re:")
    assert captured_sends[-1]["In-Reply-To"]

    e = _get_enrollment(camp["id"], contact)
    assert e.status == "completed"
    assert e.next_run_at is None


def select_enrollment(campaign_id, contact_id):
    return select(EmailEnrollment).where(
        EmailEnrollment.campaign_id == campaign_id,
        EmailEnrollment.contact_id == contact_id,
    )


def _get_enrollment(campaign_id, contact_id):
    db = SessionLocal()
    try:
        return db.execute(select_enrollment(campaign_id, contact_id)).scalar_one()
    finally:
        db.close()


def test_window_gating_parks_until_open(cc_org, api, probe_ok, captured_sends):
    # Window that is closed right now: a single weekday far from today plus a
    # 1-hour window. Use a day-of-week we are definitely not on.
    today = utcnow().weekday()
    closed_day = (today + 2) % 7
    acct = _mk_account(cc_org, api, from_email="win@campaignco.com")
    camp = _mk_campaign(
        cc_org,
        api,
        acct["id"],
        send_window_start=9,
        send_window_end=10,
        send_days=[closed_day],
        timezone="UTC",
    )
    contact = _mk_contact(cc_org, api, email_addr="win-lead@example.com")
    _enroll_and_activate(
        cc_org, api, camp, [{"position": 1, "body": "hello"}], [contact]
    )
    _tick()
    assert captured_sends == []  # outside the window → parked, not sent
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "active"
    assert e.next_run_at is not None  # rescheduled to the next open window


def test_campaign_daily_cap_parks_for_retry(cc_org, api, probe_ok, captured_sends):
    acct = _mk_account(cc_org, api, from_email="cap@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], daily_cap=1, **_ALWAYS)
    c1 = _mk_contact(cc_org, api, email_addr="cap-a@example.com")
    c2 = _mk_contact(cc_org, api, email_addr="cap-b@example.com")
    _enroll_and_activate(
        cc_org, api, camp, [{"position": 1, "body": "hi"}], [c1, c2]
    )
    _tick()
    assert len(captured_sends) == 1  # only one send — cap is 1/day
    # The un-sent enrollment is parked ~1h out, still active.
    parked = [
        _get_enrollment(camp["id"], c) for c in (c1, c2)
    ]
    active = [e for e in parked if e.status == "active" and e.current_position == 1]
    assert len(active) == 1
    assert active[0].next_run_at is not None


def test_suppressed_and_blocked_exit_enrollment(cc_org, api, probe_ok, captured_sends):
    acct = _mk_account(cc_org, api, from_email="exit@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], **_ALWAYS)
    # A contact that becomes suppressed AFTER enrollment (enroll gate passed).
    supp = _mk_contact(cc_org, api, email_addr="late-supp@example.com")
    blocked = _mk_contact(cc_org, api, email_addr="late-blocked@example.com")
    _enroll_and_activate(
        cc_org, api, camp, [{"position": 1, "body": "hi"}], [supp, blocked]
    )
    db = SessionLocal()
    try:
        gateway.suppress(db, cc_org["org"], "late-supp@example.com", "manual")
        db.get(Contact, blocked).verification_status = "invalid"
        db.commit()
    finally:
        db.close()
    _tick()
    assert captured_sends == []
    e_supp = _get_enrollment(camp["id"], supp)
    e_blk = _get_enrollment(camp["id"], blocked)
    assert e_supp.status == "exited" and e_supp.exit_reason == "unsubscribed"
    assert e_blk.status == "exited" and e_blk.exit_reason == "bounced"


def test_failed_send_errors_enrollment(cc_org, api, probe_ok, monkeypatch):
    acct = _mk_account(cc_org, api, from_email="fail@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(cc_org, api, email_addr="fail-lead@example.com")
    _enroll_and_activate(
        cc_org, api, camp, [{"position": 1, "body": "hi"}], [contact]
    )

    def _boom(account, msg):
        raise email_transport.EmailTransportError("smtp down")

    monkeypatch.setattr(email_transport, "smtp_send", _boom)
    _tick()
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "error"
    assert e.next_run_at is None


# --- compliance exits -------------------------------------------------------


def test_reply_exits_enrollment(cc_org, api, probe_ok, captured_sends, monkeypatch):
    acct = _mk_account(cc_org, api, from_email="rep@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(cc_org, api, email_addr="replier2@example.com")
    _enroll_and_activate(
        cc_org, api, camp,
        [{"position": 1, "body": "hi"}, {"position": 2, "wait_days": 1, "body": "bump"}],
        [contact],
    )
    _tick()  # step 1 sends
    out_mid = captured_sends[-1]["Message-ID"]
    inbound = (
        f"From: Replier <replier2@example.com>\r\n"
        f"To: rep@campaignco.com\r\n"
        f"Subject: Re: hi\r\n"
        f"Message-ID: <rep-1@example.com>\r\n"
        f"In-Reply-To: {out_mid}\r\n\r\nSure, interested."
    ).encode()
    monkeypatch.setattr(
        email_transport, "fetch_new", lambda account, last_uid: [(5, inbound)]
    )
    db = SessionLocal()
    try:
        sync.sync_account(db, db.get(EmailAccount, acct["id"]))
        db.commit()
    finally:
        db.close()
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "exited" and e.exit_reason == "replied"
    assert e.replied_at is not None


def test_bounce_exits_enrollment(cc_org, api, probe_ok, captured_sends, monkeypatch):
    acct = _mk_account(cc_org, api, from_email="bnc@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(cc_org, api, email_addr="bouncer2@example.com")
    _enroll_and_activate(
        cc_org, api, camp,
        [{"position": 1, "body": "hi"}, {"position": 2, "wait_days": 1, "body": "bump"}],
        [contact],
    )
    _tick()
    out_mid = captured_sends[-1]["Message-ID"]
    boundary = "bnd"
    dsn = (
        f"From: MAILER-DAEMON@example.com\r\n"
        f"To: bnc@campaignco.com\r\n"
        f"Subject: Delivery failure\r\n"
        f"Message-ID: <dsn-x@example.com>\r\n"
        f'Content-Type: multipart/report; report-type=delivery-status; boundary="{boundary}"\r\n\r\n'
        f"--{boundary}\r\nContent-Type: text/plain\r\n\r\nfailed\r\n"
        f"--{boundary}\r\nContent-Type: message/rfc822\r\n\r\nMessage-ID: {out_mid}\r\n\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    monkeypatch.setattr(
        email_transport, "fetch_new", lambda account, last_uid: [(6, dsn)]
    )
    db = SessionLocal()
    try:
        sync.sync_account(db, db.get(EmailAccount, acct["id"]))
        db.commit()
    finally:
        db.close()
    e = _get_enrollment(camp["id"], contact)
    assert e.status == "exited" and e.exit_reason == "bounced"


def test_unsubscribe_stops_all_campaigns(cc_org, api, probe_ok, captured_sends, monkeypatch):
    acct = _mk_account(cc_org, api, from_email="uns@campaignco.com")
    camp_a = _mk_campaign(cc_org, api, acct["id"], name="A", **_ALWAYS)
    camp_b = _mk_campaign(cc_org, api, acct["id"], name="B", **_ALWAYS)
    contact = _mk_contact(cc_org, api, email_addr="multi-optout@example.com")
    # Two-step campaigns so the enrollments stay ACTIVE after step 1 — the
    # point of the test is that a later opt-out stops the still-pending step 2
    # in BOTH campaigns.
    two_steps = [
        {"position": 1, "body": "hi"},
        {"position": 2, "wait_days": 1, "body": "bump"},
    ]
    for camp in (camp_a, camp_b):
        _enroll_and_activate(cc_org, api, camp, two_steps, [contact])
    _tick()  # both send step 1
    # Reply with an opt-out on campaign A's thread → suppression → stops BOTH.
    out_mid = captured_sends[-1]["Message-ID"]
    inbound = (
        f"From: multi-optout@example.com\r\n"
        f"To: uns@campaignco.com\r\n"
        f"Subject: Re: hi\r\n"
        f"Message-ID: <uns-1@example.com>\r\n"
        f"In-Reply-To: {out_mid}\r\n\r\nPlease unsubscribe me."
    ).encode()
    monkeypatch.setattr(
        email_transport, "fetch_new", lambda account, last_uid: [(7, inbound)]
    )
    db = SessionLocal()
    try:
        sync.sync_account(db, db.get(EmailAccount, acct["id"]))
        db.commit()
    finally:
        db.close()
    ea = _get_enrollment(camp_a["id"], contact)
    eb = _get_enrollment(camp_b["id"], contact)
    assert ea.status == "exited" and ea.exit_reason == "unsubscribed"
    assert eb.status == "exited" and eb.exit_reason == "unsubscribed"


# --- personalization + AI ---------------------------------------------------


def test_token_render_with_fallback_and_custom(cc_org, api, probe_ok):
    acct = _mk_account(cc_org, api, from_email="tok@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(
        cc_org, api, email_addr="tok-lead@example.com", first="", city="Denver"
    )
    _set_steps(
        cc_org, api, camp["id"],
        [{"position": 1, "subject": "Hey {{first_name|there}}", "body": "You're in {{city}}, {{state|somewhere}}."}],
    )
    r = api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/preview",
        json={"contact_id": contact, "position": 1},
        headers=cc_org["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subject"] == "Hey there"  # empty first_name → fallback
    assert body["body"] == "You're in Denver, somewhere."  # missing state → fallback


def test_ai_snippet_generated_cached_and_fails_open(cc_org, api, probe_ok, monkeypatch):
    calls = {"n": 0}

    def _fake_call(system, user_content, max_tokens=300):
        calls["n"] += 1
        return "A tailored line about Denver roofing.", 12, 8

    monkeypatch.setattr(email_personalize, "_call_model", _fake_call)
    monkeypatch.setattr(
        email_personalize.ai_insights, "check_allowance", lambda db, org: None
    )

    acct = _mk_account(cc_org, api, from_email="ai@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(cc_org, api, email_addr="ai-lead@example.com", city="Denver")
    _set_steps(
        cc_org, api, camp["id"],
        [{"position": 1, "body": "Intro. {{ai_snippet}}", "ai_instructions": "Mention their city."}],
    )
    assert _activate(cc_org, api, camp["id"]).status_code == 200
    api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/enroll",
        json={"contact_ids": [contact]},
        headers=cc_org["headers"],
    )

    db = SessionLocal()
    try:
        e = db.execute(select_enrollment(camp["id"], contact)).scalar_one()
        org = db.get(Organization, cc_org["org"])
        step = db.execute(
            select(EmailStep).where(EmailStep.campaign_id == camp["id"])
        ).scalars().first()
        c = db.get(Contact, contact)
        subj1, body1 = email_personalize.render_full(db, org, e, step, None, contact=c)
        assert "A tailored line about Denver roofing." in body1
        # Second render reuses the cached snippet — no second model call.
        subj2, body2 = email_personalize.render_full(db, org, e, step, None, contact=c)
        assert body1 == body2
        assert calls["n"] == 1
        assert e.ai_snippets and step.id in e.ai_snippets
    finally:
        db.close()

    # AI failure → snippet is "" and the template still renders (no crash).
    def _boom(system, user_content, max_tokens=300):
        raise RuntimeError("model timeout")

    monkeypatch.setattr(email_personalize, "_call_model", _boom)
    db = SessionLocal()
    try:
        org = db.get(Organization, cc_org["org"])
        c = db.get(Contact, contact)
        step = db.execute(
            select(EmailStep).where(EmailStep.campaign_id == camp["id"])
        ).scalars().first()
        snippet = email_personalize.generate_ai_snippet(db, org, c, step)
        assert snippet == ""
    finally:
        db.close()


# --- warmup -----------------------------------------------------------------


def test_effective_daily_cap_ramp():
    a = EmailAccount(
        organization_id="o",
        name="w",
        from_name="w",
        from_email="w@w.com",
        smtp_host="h",
        smtp_port=1,
        imap_host="h",
        imap_port=1,
        smtp_username="u",
        imap_username="u",
        daily_send_cap=100,
        warmup_enabled=True,
        warmup_target_daily=100,
    )
    # Warmup off / not started → the raw cap.
    a.warmup_enabled = False
    assert email_warmup.effective_daily_cap(a) == 100
    a.warmup_enabled = True
    a.warmup_started_at = None
    assert email_warmup.effective_daily_cap(a) == 100

    # Day 0 → the floor (20% of 100 = 20).
    a.warmup_started_at = utcnow()
    assert email_warmup.effective_daily_cap(a) == 20
    # Day 14 (half of the 28-day ramp) → floor + half the gap = ~60.
    a.warmup_started_at = utcnow() - dt.timedelta(days=14)
    assert email_warmup.effective_daily_cap(a) == 60
    # Day 28+ → full target.
    a.warmup_started_at = utcnow() - dt.timedelta(days=40)
    assert email_warmup.effective_daily_cap(a) == 100
    # Never exceeds the account's own hard cap.
    a.daily_send_cap = 30
    assert email_warmup.effective_daily_cap(a) == 30
    assert email_warmup.warmup_stage(a) == "target reached"


def _enable_warmup(account_id, *, target=100, days_ago=40):
    """Warmup fields aren't settable through the account API — flip them in the
    DB directly (as an admin config change would)."""
    db = SessionLocal()
    try:
        a = db.get(EmailAccount, account_id)
        a.warmup_enabled = True
        a.warmup_target_daily = target
        a.warmup_started_at = utcnow() - dt.timedelta(days=days_ago)
        db.commit()
    finally:
        db.close()


def test_warmup_peer_send_tags_header_and_skips_meter(cc_org, api, probe_ok, captured_sends):
    from app.services import entitlements

    a1 = _mk_account(cc_org, api, from_email="warm1@campaignco.com")
    a2 = _mk_account(cc_org, api, from_email="warm2@campaignco.com")
    _enable_warmup(a1["id"])
    _enable_warmup(a2["id"])

    db = SessionLocal()
    try:
        org = db.get(Organization, cc_org["org"])
        used_before = entitlements.email_outreach_usage(db, org)["used"]
        res = email_warmup.run_warmup_tick(db, cc_org["org"])
        db.commit()
        used_after = entitlements.email_outreach_usage(db, org)["used"]
    finally:
        db.close()

    assert res["sent"] >= 1
    # Warmup traffic never moves the billable send meter.
    assert used_after == used_before

    warmups = [m for m in captured_sends if m.get("X-Salescale-Warmup") == "1"]
    assert warmups, "expected at least one tagged warmup message"
    for m in warmups:
        assert "List-Unsubscribe" not in m
        assert m["To"] in ("warm1@campaignco.com", "warm2@campaignco.com")

    db = SessionLocal()
    try:
        warmup_rows = db.query(EmailMessage).filter_by(kind="warmup").all()
        # Threadless audit rows (no human-inbox thread) with no contact.
        assert warmup_rows and all(
            r.thread_id is None and r.contact_id is None for r in warmup_rows
        )
        pair = db.query(EmailWarmupPeer).filter_by(account_id=a1["id"]).first()
        assert pair is not None and pair.last_sent_at is not None
    finally:
        db.close()


def test_warmup_received_hook_records_receipt(cc_org, api, probe_ok):
    a1 = _mk_account(cc_org, api, from_email="wrx1@campaignco.com")
    a2 = _mk_account(cc_org, api, from_email="wrx2@campaignco.com")
    _enable_warmup(a1["id"])
    _enable_warmup(a2["id"])
    raw = (
        b"From: wrx2@campaignco.com\r\nTo: wrx1@campaignco.com\r\n"
        b"Subject: Touching base\r\nMessage-ID: <wu-1@campaignco.com>\r\n"
        b"X-Salescale-Warmup: 1\r\n\r\nhi"
    )
    db = SessionLocal()
    try:
        a1_row = db.get(EmailAccount, a1["id"])
        parsed = email_transport.parse_message(raw)
        email_warmup.on_warmup_received(db, a1_row, parsed)
        db.commit()
        pair = (
            db.query(EmailWarmupPeer)
            .filter_by(account_id=a1["id"], peer_account_id=a2["id"])
            .first()
        )
        assert pair is not None and pair.last_received_at is not None
    finally:
        db.close()


# --- activation gates -------------------------------------------------------


def test_activate_requires_steps_and_mailing_address(cc_org, api, probe_ok):
    acct = _mk_account(cc_org, api, from_email="act@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], **_ALWAYS)
    # No steps yet → 422.
    r = _activate(cc_org, api, camp["id"])
    assert r.status_code == 422
    assert "step" in r.json()["detail"].lower()

    _set_steps(cc_org, api, camp["id"], [{"position": 1, "body": "hi"}])

    # Temporarily blank the mailing address → 422 on the CAN-SPAM gate.
    api.put(
        "/api/orgs/me/branding",
        json={"mailing_address": ""},
        headers=cc_org["headers"],
    )
    r2 = _activate(cc_org, api, camp["id"])
    assert r2.status_code == 422
    assert "mailing address" in r2.json()["detail"].lower()

    # Restore and activate.
    api.put(
        "/api/orgs/me/branding",
        json={"mailing_address": "100 Broadway, New York NY 10005"},
        headers=cc_org["headers"],
    )
    assert _activate(cc_org, api, camp["id"]).status_code == 200


def test_steps_must_be_contiguous_and_not_while_active(cc_org, api, probe_ok):
    acct = _mk_account(cc_org, api, from_email="steps@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], **_ALWAYS)
    r = api.put(
        f"/api/email-outreach/campaigns/{camp['id']}/steps",
        json={"steps": [{"position": 1, "body": "a"}, {"position": 3, "body": "b"}]},
        headers=cc_org["headers"],
    )
    assert r.status_code == 422  # gap 1,3

    _set_steps(cc_org, api, camp["id"], [{"position": 1, "body": "a"}])
    assert _activate(cc_org, api, camp["id"]).status_code == 200
    r2 = api.put(
        f"/api/email-outreach/campaigns/{camp['id']}/steps",
        json={"steps": [{"position": 1, "body": "changed"}]},
        headers=cc_org["headers"],
    )
    assert r2.status_code == 409  # must pause first


# --- analytics + usage ------------------------------------------------------


def test_analytics_rate_math(cc_org, api, probe_ok):
    acct = _mk_account(cc_org, api, from_email="an@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], **_ALWAYS)
    _set_steps(cc_org, api, camp["id"], [{"position": 1, "body": "hi"}])

    # Hand-construct a known dataset: 4 sent, 1 bounced, 2 opened, 1 replied,
    # 1 unsubscribed. delivered = 4-1 = 3.
    db = SessionLocal()
    try:
        thread = EmailThread(
            organization_id=cc_org["org"], account_id=acct["id"],
            contact_id=None, subject="t", message_count=0,
        )
        # thread needs a contact (nullable=False); make one.
        c = Contact(
            organization_id=cc_org["org"], client_id=cc_org["client"],
            first_name="An", email="an-lead@example.com",
        )
        db.add(c)
        db.flush()
        thread.contact_id = c.id
        db.add(thread)
        db.flush()
        now = utcnow()
        for i in range(4):
            m = EmailMessage(
                organization_id=cc_org["org"], thread_id=thread.id,
                account_id=acct["id"], contact_id=c.id, direction="out",
                status="sent", kind="campaign", campaign_id=camp["id"],
                sent_at=now,
            )
            if i == 0:
                m.bounced_at = now
                m.status = "bounced"
            if i < 2:
                m.opened_at = now
            db.add(m)
        # enrollments: one replied, one unsubscribed, plus 2 plain.
        for reason, replied in (("unsubscribed", None), (None, now), (None, None), (None, None)):
            cc = Contact(
                organization_id=cc_org["org"], client_id=cc_org["client"],
                first_name="E", email=f"e{reason}{replied}@example.com",
            )
            db.add(cc)
            db.flush()
            db.add(
                EmailEnrollment(
                    organization_id=cc_org["org"], campaign_id=camp["id"],
                    contact_id=cc.id, status="active", current_position=1,
                    exit_reason=reason, replied_at=replied,
                )
            )
        db.commit()
    finally:
        db.close()

    r = api.get(
        f"/api/email-outreach/analytics?campaign_id={camp['id']}",
        headers=cc_org["headers"],
    )
    assert r.status_code == 200, r.text
    t = r.json()["totals"]
    assert t["sent"] == 4 and t["bounced"] == 1 and t["delivered"] == 3
    assert t["opened"] == 2 and t["replied"] == 1 and t["unsubscribed"] == 1
    assert t["delivery_rate"] == round(3 / 4, 4)
    assert t["open_rate"] == round(2 / 3, 4)
    assert t["reply_rate"] == round(1 / 3, 4)
    assert t["bounce_rate"] == round(1 / 4, 4)
    assert t["unsubscribe_rate"] == round(1 / 3, 4)
    assert len(r.json()["by_step"]) == 1
    assert any(a["from_email"] == "an@campaignco.com" for a in r.json()["accounts"])


def test_usage_endpoint(cc_org, api, probe_ok):
    r = api.get("/api/email-outreach/usage", headers=cc_org["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert "used" in body["sends"] and "limit" in body["sends"]
    assert body["plan"] == "starter"


# --- isolation + role gating ------------------------------------------------


def test_campaign_tenant_isolation(cc_org, api, probe_ok, team_headers):
    acct = _mk_account(cc_org, api, from_email="iso2@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], **_ALWAYS)
    # Atlas Reach (team_headers) can't see or fetch Campaign Co's campaign.
    listed = api.get("/api/email-outreach/campaigns", headers=team_headers).json()
    assert all(c["id"] != camp["id"] for c in listed)
    assert api.get(
        f"/api/email-outreach/campaigns/{camp['id']}", headers=team_headers
    ).status_code == 404
    assert api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/activate", headers=team_headers
    ).status_code == 404


def test_campaign_role_gating(cc_org, api, probe_ok, client_a_headers):
    acct = _mk_account(cc_org, api, from_email="role@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"], **_ALWAYS)
    # Client role: fully locked out.
    assert api.get(
        "/api/email-outreach/campaigns", headers=client_a_headers
    ).status_code == 403

    # Member (team, non-admin): can view analytics, cannot mutate campaigns.
    api.post(
        "/api/orgs/me/members",
        json={
            "email": "member2@campaignco.com",
            "password": "member2-pass-1",
            "full_name": "CC Member",
            "role": "member",
        },
        headers=cc_org["headers"],
    )
    login = api.post(
        "/api/auth/login",
        json={"email": "member2@campaignco.com", "password": "member2-pass-1"},
    ).json()
    mh = {"Authorization": f"Bearer {login['access_token']}"}
    assert api.get("/api/email-outreach/analytics", headers=mh).status_code == 200
    assert api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/pause", headers=mh
    ).status_code == 403
    assert api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/enroll",
        json={"contact_ids": []},
        headers=mh,
    ).status_code in (403, 422)


def test_campaign_serialization_is_flat_not_nested(cc_org, api, probe_ok):
    """Found via live browser verification: an earlier version nested stats
    under a "stats" key, but the frontend (and the analytics by_campaign
    shape) expects steps_count/enrolled/sent/*_rate as top-level fields on
    both the list item and the single-campaign detail response."""
    acct = _mk_account(cc_org, api, from_email="flat@campaignco.com")
    camp = _mk_campaign(cc_org, api, acct["id"])

    flat_keys = {
        "steps_count", "enrolled", "active_enrollments", "sent", "delivered",
        "opened", "replied", "bounced", "unsubscribed", "delivery_rate",
        "open_rate", "reply_rate", "bounce_rate", "unsubscribe_rate",
    }

    listed = api.get(
        "/api/email-outreach/campaigns", headers=cc_org["headers"]
    ).json()
    row = next(c for c in listed if c["id"] == camp["id"])
    assert flat_keys <= row.keys()
    assert "stats" not in row

    detail = api.get(
        f"/api/email-outreach/campaigns/{camp['id']}", headers=cc_org["headers"]
    ).json()
    assert flat_keys <= detail.keys()
    assert "stats" not in detail
    assert detail["enrolled"] == 0
    assert detail["steps"] == []
