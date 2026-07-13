"""AI research fields ("Claygent-lite") — services/research.py and the
research-fields/research-run endpoints in api/crm.py.

Own dedicated org (rf_org, created via self-serve signup) so contact/field
counts here never perturb the seeded Atlas Reach org the metrics/isolation
suites assert exact arithmetic over.
"""

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.core import Organization
from app.models.crm import Contact, ResearchFieldDef
from app.services import ai_insights, research as research_svc


@pytest.fixture(scope="module")
def rf_org(api):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Research Co",
            "email": "owner@researchco.com",
            "password": "research-pass-1",
            "full_name": "RF Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "RF Client"}, headers=headers
    ).json()["id"]
    return {"org": body["organization_id"], "headers": headers, "client": client_id}


def _mk_contact(rf_org, api, **over):
    payload = {
        "client_id": rf_org["client"],
        "first_name": "Rae",
        "last_name": "Roofer",
    }
    payload.update(over)
    r = api.post("/api/crm/contacts", json=payload, headers=rf_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _mk_field(rf_org, api, **over):
    payload = {
        "label": "Recent news",
        "prompt": "What recent news has this business posted?",
    }
    payload.update(over)
    r = api.post(
        "/api/crm/research-fields", json=payload, headers=rf_org["headers"]
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(autouse=True)
def _no_crawl(monkeypatch):
    """Default every test to "no site text" so nothing here depends on a real
    network fetch; tests that care about site_text patch it themselves."""
    monkeypatch.setattr(research_svc.enrichment, "fetch_site_text", lambda w: None)


@pytest.fixture(autouse=True)
def _raise_cap(monkeypatch):
    """rf_org is module-scoped and shared across many tests that each create
    their own field def — raise the starter-tier cap (5) so those defs never
    collide with the plan limit; test_cap_enforced_by_entitlement patches its
    own (dedicated-org) limit explicitly."""
    from app.services import entitlements

    monkeypatch.setattr(entitlements, "research_field_limit", lambda org: 1000)


# --- CRUD + cap + delete scrub -----------------------------------------------


def test_crud_roundtrip_key_immutable_and_dup_key_409(rf_org, api):
    f = _mk_field(rf_org, api, label="Fleet size", prompt="How many trucks?")
    assert f["key"] == "fleet_size"
    assert f["archived"] is False
    assert f["max_words"] == 40

    listed = api.get("/api/crm/research-fields", headers=rf_org["headers"]).json()
    assert any(x["id"] == f["id"] for x in listed)

    r = api.patch(
        f"/api/crm/research-fields/{f['id']}",
        json={"label": "Fleet Size (trucks)", "max_words": 20},
        headers=rf_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["key"] == "fleet_size"  # rename is label-only
    assert r.json()["label"] == "Fleet Size (trucks)"
    assert r.json()["max_words"] == 20

    dup = api.post(
        "/api/crm/research-fields",
        json={"key": "fleet_size", "label": "Dup", "prompt": "x?"},
        headers=rf_org["headers"],
    )
    assert dup.status_code == 409


def test_cap_enforced_by_entitlement(api, monkeypatch):
    from app.services import entitlements

    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Research Cap Co",
            "email": "owner@researchcapco.com",
            "password": "research-cap-pass-1",
            "full_name": "Cap Owner",
        },
    )
    assert r.status_code == 201, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    monkeypatch.setattr(entitlements, "research_field_limit", lambda org: 1)
    ok = api.post(
        "/api/crm/research-fields",
        json={"label": "First field", "prompt": "q1?"},
        headers=headers,
    )
    assert ok.status_code == 201, ok.text
    blocked = api.post(
        "/api/crm/research-fields",
        json={"label": "Second field", "prompt": "q2?"},
        headers=headers,
    )
    assert blocked.status_code == 402


def test_archive_frees_cap_for_unarchive(rf_org, api):
    f = _mk_field(rf_org, api, label="Archive toggle", prompt="q?")
    r = api.patch(
        f"/api/crm/research-fields/{f['id']}",
        json={"archived": True},
        headers=rf_org["headers"],
    )
    assert r.status_code == 200
    assert r.json()["archived"] is True
    r = api.patch(
        f"/api/crm/research-fields/{f['id']}",
        json={"archived": False},
        headers=rf_org["headers"],
    )
    assert r.status_code == 200
    assert r.json()["archived"] is False


def test_delete_scrubs_contact_research_json(rf_org, api):
    f = _mk_field(rf_org, api, label="Scrub me", prompt="scrub?")
    contact = _mk_contact(rf_org, api, first_name="Scrubby")
    db = SessionLocal()
    try:
        c = db.get(Contact, contact)
        c.research = {
            f["key"]: {
                "value": "x",
                "confidence": "low",
                "source_url": None,
                "researched_at": "2026-01-01T00:00:00+00:00",
            }
        }
        db.commit()
    finally:
        db.close()

    resp = api.delete(
        f"/api/crm/research-fields/{f['id']}", headers=rf_org["headers"]
    )
    assert resp.status_code == 200
    assert resp.json()["scrub"] == "scheduled"

    got = api.get(f"/api/crm/contacts/{contact}", headers=rf_org["headers"]).json()
    assert not (got.get("research") or {})


# --- run_for_contacts: caching, force, discard, metering, isolation ---------


def _fake_answer(answer="A tailored fact.", confidence="high", source_url=None):
    import json as _json

    def _call(system, user_content, max_tokens=300):
        return (
            _json.dumps(
                {"answer": answer, "confidence": confidence, "source_url": source_url}
            ),
            10,
            5,
        )

    return _call


def test_run_for_contacts_fills_caches_and_meters(rf_org, api, monkeypatch):
    f = _mk_field(rf_org, api, label="Fact one", prompt="What is a fact?")
    contact = _mk_contact(rf_org, api, first_name="Facty")

    monkeypatch.setattr(research_svc, "_call_model", _fake_answer())
    monkeypatch.setattr(ai_insights, "check_allowance", lambda db, org: None)

    r = api.post(
        "/api/crm/research/run",
        json={"contact_ids": [contact]},
        headers=rf_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 1

    got = api.get(f"/api/crm/contacts/{contact}", headers=rf_org["headers"]).json()
    assert got["research"][f["key"]]["value"] == "A tailored fact."
    assert got["research"][f["key"]]["confidence"] == "high"

    # A second run without force skips the cached field entirely.
    calls = {"n": 0}

    def _counting(system, user_content, max_tokens=300):
        calls["n"] += 1
        return _fake_answer()(system, user_content, max_tokens)

    monkeypatch.setattr(research_svc, "_call_model", _counting)
    db = SessionLocal()
    try:
        org = db.get(Organization, rf_org["org"])
        c = db.get(Contact, contact)
        d = db.execute(
            select(ResearchFieldDef).where(ResearchFieldDef.id == f["id"])
        ).scalar_one()
        result = research_svc.run_for_contact(db, org, c, [d])
        db.commit()
    finally:
        db.close()
    assert result == {"filled": 0, "skipped_cached": 1, "failed": 0}
    assert calls["n"] == 0

    # force=True re-runs and re-bills.
    db = SessionLocal()
    try:
        org = db.get(Organization, rf_org["org"])
        c = db.get(Contact, contact)
        d = db.execute(
            select(ResearchFieldDef).where(ResearchFieldDef.id == f["id"])
        ).scalar_one()
        result = research_svc.run_for_contact(db, org, c, [d], force=True)
        db.commit()
    finally:
        db.close()
    assert result == {"filled": 1, "skipped_cached": 0, "failed": 0}
    assert calls["n"] == 1

    # Metering row landed.
    from app.models.ai import AiUsage

    db = SessionLocal()
    try:
        rows = db.execute(
            select(AiUsage).where(
                AiUsage.organization_id == rf_org["org"],
                AiUsage.feature == "outreach_research",
            )
        ).scalars().all()
        assert len(rows) >= 2
    finally:
        db.close()


def test_discard_on_overlong_and_unparseable(rf_org, api, monkeypatch):
    f = _mk_field(rf_org, api, label="Short fact", prompt="q?", max_words=3)
    contact = _mk_contact(rf_org, api, first_name="Longy")

    monkeypatch.setattr(
        research_svc, "_call_model", _fake_answer(answer="way more than three words here")
    )
    monkeypatch.setattr(ai_insights, "check_allowance", lambda db, org: None)
    db = SessionLocal()
    try:
        org = db.get(Organization, rf_org["org"])
        c = db.get(Contact, contact)
        d = db.execute(
            select(ResearchFieldDef).where(ResearchFieldDef.id == f["id"])
        ).scalar_one()
        result = research_svc.run_for_contact(db, org, c, [d])
        db.commit()
        assert result == {"filled": 0, "skipped_cached": 0, "failed": 1}
        assert not (c.research or {}).get(f["key"])
    finally:
        db.close()

    # Unparseable JSON also discards.
    monkeypatch.setattr(
        research_svc, "_call_model", lambda system, user_content, max_tokens=300: (
            "not json at all", 5, 5
        )
    )
    db = SessionLocal()
    try:
        org = db.get(Organization, rf_org["org"])
        c = db.get(Contact, contact)
        d = db.execute(
            select(ResearchFieldDef).where(ResearchFieldDef.id == f["id"])
        ).scalar_one()
        result = research_svc.run_for_contact(db, org, c, [d])
        db.commit()
        assert result == {"filled": 0, "skipped_cached": 0, "failed": 1}
    finally:
        db.close()


def test_cross_org_contact_id_silently_skipped(rf_org, api, org2, monkeypatch):
    f = _mk_field(rf_org, api, label="Cross org", prompt="q?")
    monkeypatch.setattr(research_svc, "_call_model", _fake_answer())
    monkeypatch.setattr(ai_insights, "check_allowance", lambda db, org: None)

    db = SessionLocal()
    try:
        org = db.get(Organization, rf_org["org"])
        totals = research_svc.run_for_contacts(
            db, org, [org2["client_id"]]  # not even a contact id, definitely not this org's
        )
    finally:
        db.close()
    assert totals == {"processed": 0, "filled": 0, "skipped_cached": 0, "failed": 0}


def test_fail_open_on_ai_error(rf_org, api, monkeypatch):
    f = _mk_field(rf_org, api, label="Boom field", prompt="q?")
    contact = _mk_contact(rf_org, api, first_name="Boomy")

    def _boom(system, user_content, max_tokens=300):
        raise RuntimeError("model timeout")

    monkeypatch.setattr(research_svc, "_call_model", _boom)
    monkeypatch.setattr(ai_insights, "check_allowance", lambda db, org: None)

    db = SessionLocal()
    try:
        org = db.get(Organization, rf_org["org"])
        c = db.get(Contact, contact)
        d = db.execute(
            select(ResearchFieldDef).where(ResearchFieldDef.id == f["id"])
        ).scalar_one()
        result = research_svc.run_for_contact(db, org, c, [d])
        db.commit()
    finally:
        db.close()
    assert result == {"filled": 0, "skipped_cached": 0, "failed": 1}


# --- research.<key> token: renders + unknown-token 422 ----------------------


def test_research_token_renders_and_unknown_token_422s(rf_org, api, monkeypatch):
    f = _mk_field(rf_org, api, label="Renders", prompt="q?")
    contact = _mk_contact(rf_org, api, first_name="Tok")
    db = SessionLocal()
    try:
        c = db.get(Contact, contact)
        c.research = {f["key"]: {"value": "hello world", "confidence": "high", "source_url": None, "researched_at": "now"}}
        db.commit()
    finally:
        db.close()

    from app.services import email_personalize as ep

    db = SessionLocal()
    try:
        contact_obj = db.get(Contact, contact)
        out = ep._render_template(
            "Fact: {{research." + f["key"] + "}}", contact_obj, ep._company_facts(db, contact_obj), {}
        )
    finally:
        db.close()
    assert out == "Fact: hello world"

    # Email step-save: a real research key is accepted...
    from app.services import email_transport

    monkeypatch.setattr(
        email_transport,
        "probe",
        lambda account: {"smtp_ok": True, "imap_ok": True, "detail": None},
    )
    acct = api.post(
        "/api/email-outreach/accounts",
        json={
            "name": "RF Mailbox",
            "from_name": "Rae Research",
            "from_email": "rae@researchco.com",
            "smtp_host": "smtp.researchco.com",
            "smtp_port": 465,
            "smtp_security": "ssl",
            "imap_host": "imap.researchco.com",
            "imap_port": 993,
            "imap_security": "ssl",
            "smtp_username": "rae@researchco.com",
            "smtp_password": "secret",
            "imap_username": "rae@researchco.com",
            "imap_password": "secret",
        },
        headers=rf_org["headers"],
    )
    assert acct.status_code == 201, acct.text
    camp = api.post(
        "/api/email-outreach/campaigns",
        json={"name": "Research Campaign", "account_id": acct.json()["id"]},
        headers=rf_org["headers"],
    )
    assert camp.status_code == 201, camp.text

    ok_steps = api.put(
        f"/api/email-outreach/campaigns/{camp.json()['id']}/steps",
        json={
            "steps": [
                {
                    "position": 1,
                    "subject": "Hi",
                    "body": "Fact: {{research." + f["key"] + "}}",
                }
            ]
        },
        headers=rf_org["headers"],
    )
    assert ok_steps.status_code == 200, ok_steps.text

    bad_steps = api.put(
        f"/api/email-outreach/campaigns/{camp.json()['id']}/steps",
        json={
            "steps": [
                {"position": 1, "subject": "Hi", "body": "Fact: {{research.not_a_real_key}}"}
            ]
        },
        headers=rf_org["headers"],
    )
    assert bad_steps.status_code == 422
    assert "research.not_a_real_key" in bad_steps.text
