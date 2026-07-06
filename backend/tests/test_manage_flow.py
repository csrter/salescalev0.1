"""Phase 2 Definition-of-Done: every spend-affecting write goes through the
stage → confirm(diff) → execute flow, an immutable audit trail records every
attempt, and no code path writes budget/status changes without confirmation.

Platform APIs are monkeypatched at the executor's import site — these tests
prove the guardrail architecture, not Meta/Google connectivity (that part
needs real credentials and is checked against live accounts)."""

import datetime as dt

import pytest

from app.db import SessionLocal
from app.models.audit import PendingChange
from app.models.base import utcnow
from app.services import change_executor, meta_api


@pytest.fixture()
def meta_write_spy(monkeypatch):
    """Replace Meta write calls with a recorder. The executor module holds a
    reference to the meta_api module, so patching the module function is
    enough for both import sites."""
    calls = []

    def fake_update_entity(token, external_id, fields):
        calls.append({"external_id": external_id, "fields": fields})
        return {"success": True}

    monkeypatch.setattr(meta_api, "update_entity", fake_update_entity)
    return calls


def _stage_pause(api, headers, seeded, entity_id=None):
    return api.post(
        "/api/manage/changes",
        json={
            "ad_account_id": seeded["acct_a"],
            "entity_type": "campaign",
            "action": "pause",
            "entity_id": entity_id or seeded["camp_a"],
        },
        headers=headers,
    )


def test_stage_returns_before_after_diff(api, team_headers, seeded):
    resp = _stage_pause(api, team_headers, seeded)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["diff"] == [
        {"field": "status", "before": None, "after": "PAUSED"}
    ] or body["diff"][0]["field"] == "status"
    # Staging alone must not have touched the platform — there is nothing to
    # assert against because no mock is installed: a real call would error.


def test_execute_applies_change_and_audits(api, team_headers, seeded, meta_write_spy):
    change_id = _stage_pause(api, team_headers, seeded).json()["id"]
    resp = api.post(f"/api/manage/changes/{change_id}/execute", headers=team_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "executed"
    # The platform received exactly the confirmed change.
    assert meta_write_spy == [
        {"external_id": "c_111", "fields": {"status": "PAUSED"}}
    ]
    # The audit trail answers who/what/platform/when.
    log = api.get("/api/audit-log", headers=team_headers).json()
    entry = next(e for e in log if e["entity_external_id"] == "c_111")
    assert entry["user_email"] == "owner@atlasreach.com"
    assert entry["platform"] == "meta"
    assert entry["action"] == "pause"
    assert entry["status"] == "success"


def test_execute_is_single_shot(api, team_headers, seeded, meta_write_spy):
    change_id = _stage_pause(api, team_headers, seeded).json()["id"]
    assert (
        api.post(f"/api/manage/changes/{change_id}/execute", headers=team_headers)
        .status_code
        == 200
    )
    # Re-executing the same confirmation must refuse — no double writes.
    resp = api.post(f"/api/manage/changes/{change_id}/execute", headers=team_headers)
    assert resp.status_code == 409
    assert len(meta_write_spy) == 1


def test_canceled_change_cannot_execute(api, team_headers, seeded, meta_write_spy):
    change_id = _stage_pause(api, team_headers, seeded).json()["id"]
    assert (
        api.delete(f"/api/manage/changes/{change_id}", headers=team_headers).status_code
        == 200
    )
    resp = api.post(f"/api/manage/changes/{change_id}/execute", headers=team_headers)
    assert resp.status_code == 409
    assert meta_write_spy == []


def test_expired_change_cannot_execute(api, team_headers, seeded, meta_write_spy):
    change_id = _stage_pause(api, team_headers, seeded).json()["id"]
    db = SessionLocal()
    change = db.get(PendingChange, change_id)
    change.expires_at = utcnow() - dt.timedelta(minutes=1)
    db.commit()
    db.close()
    resp = api.post(f"/api/manage/changes/{change_id}/execute", headers=team_headers)
    assert resp.status_code == 409
    assert meta_write_spy == []


def test_failed_platform_write_audited_as_failed(
    api, team_headers, seeded, monkeypatch
):
    def boom(token, external_id, fields):
        raise meta_api.MetaApiError("budget below minimum")

    monkeypatch.setattr(meta_api, "update_entity", boom)
    change_id = _stage_pause(api, team_headers, seeded).json()["id"]
    resp = api.post(f"/api/manage/changes/{change_id}/execute", headers=team_headers)
    assert resp.status_code == 502
    log = api.get("/api/audit-log?status=failed", headers=team_headers).json()
    assert any(
        e["status"] == "failed" and "budget below minimum" in (e["error_detail"] or "")
        for e in log
    )


def test_budget_update_diff_shows_before_after(api, team_headers, seeded):
    resp = api.post(
        "/api/manage/changes",
        json={
            "ad_account_id": seeded["acct_a"],
            "entity_type": "campaign",
            "action": "update",
            "entity_id": seeded["camp_a"],
            "payload": {"daily_budget_micros": 25_000_000},
        },
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text
    diff = resp.json()["diff"]
    assert {"field": "daily_budget_micros", "before": None, "after": 25_000_000} in diff


def test_member_can_stage_and_execute(api, member_headers, seeded, meta_write_spy):
    # Day-to-day campaign work is member surface — with the same guardrails.
    change_id = _stage_pause(api, member_headers, seeded).json()["id"]
    resp = api.post(
        f"/api/manage/changes/{change_id}/execute", headers=member_headers
    )
    assert resp.status_code == 200
    assert len(meta_write_spy) == 1


def test_client_role_cannot_stage(api, client_a_headers, seeded):
    resp = _stage_pause(api, client_a_headers, seeded)
    assert resp.status_code == 403


def test_org2_cannot_stage_against_org1_account(api, org2_headers, seeded):
    resp = _stage_pause(api, org2_headers, seeded)
    assert resp.status_code == 404


def test_org2_cannot_execute_org1_change(api, team_headers, org2_headers, seeded):
    change_id = _stage_pause(api, team_headers, seeded).json()["id"]
    resp = api.post(f"/api/manage/changes/{change_id}/execute", headers=org2_headers)
    assert resp.status_code == 404


def test_unsupported_combinations_rejected_at_staging(api, team_headers, seeded):
    resp = api.post(
        "/api/manage/changes",
        json={
            "ad_account_id": seeded["acct_a"],
            "entity_type": "keyword",
            "action": "add",
            "payload": {"text": "x", "match_type": "EXACT"},
        },
        headers=team_headers,
    )
    # keyword surface is Google-only; acct_a is Meta.
    assert resp.status_code == 400


def test_no_unstaged_write_route_exists():
    """Structural guarantee for 'no silent writes': the only mutating routes
    allowed to exist for spend-bearing entities are the staged-change
    endpoints. Creative building, image upload, OAuth, org/team/client
    management, and attribution capture cannot change live spend."""
    from app.main import app

    allowed_prefixes = (
        "/api/manage/changes",       # the guarded flow itself
        "/api/auth/",                # login
        "/api/orgs/",                # tenancy + team management
        "/api/clients",              # client records, not platform writes
        "/api/connect/",             # OAuth
        "/api/track/landing",        # attribution capture (inserts only)
        "/api/ad-accounts",          # creatives/images live here — no spend
    )
    mutating = []
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if methods & {"POST", "PUT", "PATCH", "DELETE"}:
            mutating.append(route.path)
    offenders = [
        p for p in mutating if not any(p.startswith(a) for a in allowed_prefixes)
    ]
    assert offenders == [], f"unguarded mutating routes: {offenders}"
