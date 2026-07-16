"""Cold-email audience preview + QA table (Feature B) and org outreach
context / per-campaign AI writing controls (Feature C).

Own dedicated org (qa_org) — never reuses test_email_campaigns.py's cc_org
(module-scoped fixtures collide if two modules run the same signup).
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.core import Organization
from app.models.crm import Contact
from app.models.email_outreach import EmailEnrollment, EmailStep
from app.services import ai_insights, email_campaigns, email_personalize
from app.services import email_transport

# A window that is always open so window/day gating never blocks these tests.
_ALWAYS = {"send_window_start": 0, "send_window_end": 24, "send_days": [0, 1, 2, 3, 4, 5, 6]}


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
def qa_org(api):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "QA Outreach Co",
            "email": "owner@qaoutreachco.com",
            "password": "qa-outreach-pass-1",
            "full_name": "QA Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "QA Client"}, headers=headers
    ).json()["id"]
    api.put(
        "/api/orgs/me/branding",
        json={"mailing_address": "1 QA Way, Testville NY 10001"},
        headers=headers,
    )
    return {"org": body["organization_id"], "headers": headers, "client": client_id}


def _mk_contact(qa_org, api, *, email_addr, first="Quinn", last="QA", **extra):
    payload = {
        "client_id": qa_org["client"],
        "first_name": first,
        "last_name": last,
        "email": email_addr,
    }
    payload.update(extra)
    r = api.post("/api/crm/contacts", json=payload, headers=qa_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _mk_account(qa_org, api, **over):
    base = {
        "name": "QA Mailbox",
        "from_name": "Quinn QA",
        "from_email": "quinn@qaoutreachco.com",
        "smtp_host": "smtp.qaoutreachco.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "imap_host": "imap.qaoutreachco.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "smtp_username": "quinn@qaoutreachco.com",
        "smtp_password": "mbx-secret",
        "imap_username": "quinn@qaoutreachco.com",
        "imap_password": "mbx-secret",
        "daily_send_cap": 100,
    }
    base.update(over)
    r = api.post("/api/email-outreach/accounts", json=base, headers=qa_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def _mk_campaign(qa_org, api, account_id, **over):
    payload = {"name": "QA Campaign", "account_id": account_id}
    payload.update(over)
    r = api.post(
        "/api/email-outreach/campaigns", json=payload, headers=qa_org["headers"]
    )
    assert r.status_code == 201, r.text
    return r.json()


def _set_steps(qa_org, api, campaign_id, steps):
    r = api.put(
        f"/api/email-outreach/campaigns/{campaign_id}/steps",
        json={"steps": steps},
        headers=qa_org["headers"],
    )
    assert r.status_code == 200, r.text
    return r.json()


def _activate(qa_org, api, campaign_id):
    return api.post(
        f"/api/email-outreach/campaigns/{campaign_id}/activate",
        headers=qa_org["headers"],
    )


def _enroll(qa_org, api, campaign_id, contact_ids):
    r = api.post(
        f"/api/email-outreach/campaigns/{campaign_id}/enroll",
        json={"contact_ids": contact_ids},
        headers=qa_org["headers"],
    )
    assert r.status_code == 200, r.text
    return r.json()


def _tick():
    db = SessionLocal()
    try:
        return email_campaigns.run_due(db)
    finally:
        db.close()


def _get_enrollment(campaign_id, contact_id):
    db = SessionLocal()
    try:
        return db.execute(
            select(EmailEnrollment).where(
                EmailEnrollment.campaign_id == campaign_id,
                EmailEnrollment.contact_id == contact_id,
            )
        ).scalar_one()
    finally:
        db.close()


# --- preview-batch -----------------------------------------------------------


def test_preview_batch_rows_issues_and_snippet_cached(
    qa_org, api, probe_ok, monkeypatch
):
    monkeypatch.setattr(
        email_personalize, "_call_model", lambda system, user_content, max_tokens=300: (
            "A tailored line.", 10, 5
        )
    )
    monkeypatch.setattr(
        email_personalize.ai_insights, "check_allowance", lambda db, org: None
    )

    acct = _mk_account(qa_org, api, from_email="preview@qaoutreachco.com")
    camp = _mk_campaign(qa_org, api, acct["id"], **_ALWAYS)
    good = _mk_contact(qa_org, api, email_addr="good@example.com", first="Gigi")
    blank_name = _mk_contact(qa_org, api, email_addr="blank@example.com", first="")
    _set_steps(
        qa_org, api, camp["id"],
        [{"position": 1, "subject": "Hi {{first_name}}", "body": "Intro {{ai_snippet}}", "ai_instructions": "Say hi."}],
    )
    _enroll(qa_org, api, camp["id"], [good, blank_name])

    r = api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/preview-batch",
        json={"position": 1, "limit": 25, "offset": 0},
        headers=qa_org["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    by_contact = {row["contact"]["email"]: row for row in body["rows"]}
    assert "A tailored line." in by_contact["good@example.com"]["body"]
    assert by_contact["good@example.com"]["overridden"] is False
    assert "no_first_name" not in by_contact["good@example.com"]["issues"]
    assert "no_first_name" in by_contact["blank@example.com"]["issues"]
    # The snippet generated fine here — no ai_snippet_empty flag.
    assert "ai_snippet_empty" not in by_contact["good@example.com"]["issues"]

    # The AI snippet is cached on the enrollment — no leftover braces either.
    db = SessionLocal()
    try:
        e = db.execute(
            select(EmailEnrollment).where(
                EmailEnrollment.campaign_id == camp["id"],
                EmailEnrollment.contact_id == good,
            )
        ).scalar_one()
        assert e.ai_snippets
    finally:
        db.close()


def test_preview_batch_flags_empty_ai_snippet(qa_org, api, probe_ok, monkeypatch):
    """A step that ASKS for an AI snippet (ai_instructions set) but rendered
    without one — unconfigured provider, cap hit, output-guard discard —
    surfaces "ai_snippet_empty" in the preview issues so QA sees the emails
    going out unpersonalized instead of discovering it in sent mail."""

    def _boom(system, user_content, max_tokens=300):
        raise RuntimeError("no key configured")

    monkeypatch.setattr(email_personalize, "_call_model", _boom)
    monkeypatch.setattr(
        email_personalize.ai_insights, "check_allowance", lambda db, org: None
    )

    acct = _mk_account(qa_org, api, from_email="aiempty@qaoutreachco.com")
    camp = _mk_campaign(qa_org, api, acct["id"], **_ALWAYS)
    lead = _mk_contact(qa_org, api, email_addr="aiempty@example.com", first="Ava")
    _set_steps(
        qa_org, api, camp["id"],
        [{"position": 1, "subject": "Hi", "body": "Intro {{ai_snippet}}", "ai_instructions": "Say hi."}],
    )
    _enroll(qa_org, api, camp["id"], [lead])

    r = api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/preview-batch",
        json={"position": 1, "limit": 25, "offset": 0},
        headers=qa_org["headers"],
    )
    assert r.status_code == 200, r.text
    row = r.json()["rows"][0]
    assert "ai_snippet_empty" in row["issues"]
    # The body still rendered (send never blocks on AI) — no leftover braces.
    assert "leftover_tokens" not in row["issues"]


# --- override -----------------------------------------------------------------


def test_override_stored_used_verbatim_and_cleared(
    qa_org, api, probe_ok, captured_sends
):
    acct = _mk_account(qa_org, api, from_email="override@qaoutreachco.com")
    camp = _mk_campaign(qa_org, api, acct["id"], **_ALWAYS)
    contact = _mk_contact(qa_org, api, email_addr="override-lead@example.com", first="Ozzy")
    _set_steps(
        qa_org, api, camp["id"],
        [{"position": 1, "subject": "Template subject", "body": "Template body {{first_name}}"}],
    )
    _activate(qa_org, api, camp["id"])
    _enroll(qa_org, api, camp["id"], [contact])

    e = _get_enrollment(camp["id"], contact)
    r = api.put(
        f"/api/email-outreach/enrollments/{e.id}/override",
        json={"position": 1, "subject": "Hand-edited subject", "body": "Hand-edited body, {{unsubscribe_url}}"},
        headers=qa_org["headers"],
    )
    assert r.status_code == 200, r.text

    _tick()
    assert len(captured_sends) == 1
    assert captured_sends[-1]["Subject"] == "Hand-edited subject"

    # Blank override body is rejected.
    bad = api.put(
        f"/api/email-outreach/enrollments/{e.id}/override",
        json={"position": 1, "body": "   "},
        headers=qa_org["headers"],
    )
    assert bad.status_code == 422

    clear = api.delete(
        f"/api/email-outreach/enrollments/{e.id}/override?position=1",
        headers=qa_org["headers"],
    )
    assert clear.status_code == 200
    db = SessionLocal()
    try:
        row = db.execute(
            select(EmailStep).where(
                EmailStep.campaign_id == camp["id"], EmailStep.position == 1
            )
        ).scalar_one()
        enr = db.get(EmailEnrollment, e.id)
        assert not (enr.overrides or {}).get(row.id)
    finally:
        db.close()


# --- QA gate: require_approval defers / approve sends / exclude exits -------


def test_require_approval_defers_approve_sends_exclude_exits(
    qa_org, api, probe_ok, captured_sends
):
    acct = _mk_account(qa_org, api, from_email="qa-gate@qaoutreachco.com")
    camp = _mk_campaign(qa_org, api, acct["id"], require_approval=True, **_ALWAYS)
    approve_me = _mk_contact(qa_org, api, email_addr="approve@example.com", first="Ada")
    exclude_me = _mk_contact(qa_org, api, email_addr="exclude@example.com", first="Edi")
    _set_steps(qa_org, api, camp["id"], [{"position": 1, "subject": "Hi", "body": "Body {{first_name}}"}])
    _activate(qa_org, api, camp["id"])
    _enroll(qa_org, api, camp["id"], [approve_me, exclude_me])

    # Unapproved enrollments are deferred (parked ~1h out), never sent.
    _tick()
    assert len(captured_sends) == 0
    e_approve = _get_enrollment(camp["id"], approve_me)
    assert e_approve.next_run_at is not None
    delta = e_approve.next_run_at.replace(tzinfo=dt.timezone.utc) - utcnow()
    assert dt.timedelta(minutes=30) < delta < dt.timedelta(hours=2)

    r = api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/qa",
        json={"enrollment_ids": [e_approve.id], "action": "approve"},
        headers=qa_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1

    # Force the approved enrollment's next_run_at into the past so the tick fires now.
    db = SessionLocal()
    try:
        row = db.get(EmailEnrollment, e_approve.id)
        row.next_run_at = utcnow() - dt.timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    _tick()
    assert len(captured_sends) == 1

    e_exclude = _get_enrollment(camp["id"], exclude_me)
    r2 = api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/qa",
        json={"enrollment_ids": [e_exclude.id], "action": "exclude"},
        headers=qa_org["headers"],
    )
    assert r2.status_code == 200
    exc = _get_enrollment(camp["id"], exclude_me)
    assert exc.status == "exited"
    assert exc.exit_reason == "qa_excluded"


def test_qa_cross_campaign_ids_silently_skipped(qa_org, api, probe_ok):
    acct = _mk_account(qa_org, api, from_email="qa-skip@qaoutreachco.com")
    camp = _mk_campaign(qa_org, api, acct["id"], **_ALWAYS)
    r = api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/qa",
        json={"enrollment_ids": ["not-a-real-id"], "action": "approve"},
        headers=qa_org["headers"],
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 0


# --- Feature C: org outreach context + per-campaign ai_tone/ai_example -----


def test_org_outreach_context_roundtrip_and_injected_into_grounding(
    qa_org, api, monkeypatch
):
    r = api.put(
        "/api/orgs/me/outreach-context",
        json={
            "company_description": "We install solar panels.",
            "icp": "Homeowners in Arizona",
            "offer": "Free energy audit",
            "tone_guide": "Friendly, concise",
        },
        headers=qa_org["headers"],
    )
    assert r.status_code == 200, r.text
    got = api.get("/api/orgs/me/outreach-context", headers=qa_org["headers"]).json()
    assert got["icp"] == "Homeowners in Arizona"

    captured = {}

    def _capture(system, user_content, max_tokens=300):
        captured["user_content"] = user_content
        return "A grounded line.", 10, 5

    monkeypatch.setattr(email_personalize, "_call_model", _capture)
    monkeypatch.setattr(
        email_personalize.ai_insights, "check_allowance", lambda db, org: None
    )

    db = SessionLocal()
    try:
        org = db.get(Organization, qa_org["org"])
        contact = Contact(
            organization_id=org.id,
            client_id=qa_org["client"],
            first_name="Solar",
            email="solar@example.com",
        )
        db.add(contact)
        db.flush()
        step = EmailStep(
            organization_id=org.id,
            campaign_id="unused",
            position=1,
            ai_instructions="Mention their location.",
        )
        email_personalize.generate_ai_snippet(db, org, contact, step)
        db.rollback()
    finally:
        db.close()
    assert "Homeowners in Arizona" in captured["user_content"]


def test_campaign_ai_tone_and_example_roundtrip_and_in_prompt(
    qa_org, api, probe_ok, monkeypatch
):
    acct = _mk_account(qa_org, api, from_email="tone@qaoutreachco.com")
    camp = _mk_campaign(
        qa_org, api, acct["id"],
        ai_tone="Warm and casual",
        ai_example="Hey there, hope you're having a great week!",
        **_ALWAYS,
    )
    assert camp["ai_tone"] == "Warm and casual"
    assert camp["ai_example"] == "Hey there, hope you're having a great week!"

    r = api.get(f"/api/email-outreach/campaigns/{camp['id']}", headers=qa_org["headers"])
    assert r.json()["ai_tone"] == "Warm and casual"

    captured = {}

    def _capture(system, user_content, max_tokens=300):
        captured["user_content"] = user_content
        captured["system"] = system
        return "A grounded line.", 10, 5

    monkeypatch.setattr(email_personalize, "_call_model", _capture)
    monkeypatch.setattr(
        email_personalize.ai_insights, "check_allowance", lambda db, org: None
    )
    contact = _mk_contact(qa_org, api, email_addr="tone-lead@example.com", first="Toni")
    _set_steps(
        qa_org, api, camp["id"],
        [{"position": 1, "subject": "Hi", "body": "Body {{ai_snippet}}", "ai_instructions": "Say hi warmly."}],
    )
    db = SessionLocal()
    try:
        from app.models.email_outreach import EmailCampaign

        camp_row = db.get(EmailCampaign, camp["id"])
        contact_row = db.execute(
            select(Contact).where(Contact.email == "tone-lead@example.com")
        ).scalar_one()
        step_row = db.execute(
            select(EmailStep).where(EmailStep.campaign_id == camp["id"])
        ).scalars().first()
        org = db.get(Organization, qa_org["org"])
        email_personalize.generate_ai_snippet(db, org, contact_row, step_row, camp_row)
    finally:
        db.close()
    assert "Warm and casual" in captured["user_content"]
    assert "Hey there, hope you're having a great week!" in captured["user_content"]
    assert "match its voice" in captured["system"].lower()
