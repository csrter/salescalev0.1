"""Meta Instant Form polling fallback (services/meta_lead_poll).

Graph calls are monkeypatched at meta_lead_poll._get and
meta_leadgen.fetch_lead; the pass must discover new leads, push them through
the SAME ingest path as the webhook (upsert → notify → auto-enroll fire only
on created), honor the poll interval, and stay idempotent across overlapping
polls. Own org (pl_org), per the isolation convention.
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.core import PlatformConnection
from app.models.crm import Contact, LeadFormConfig
from app.services import connections as conn_svc
from app.services import meta_lead_poll, meta_leadgen

PAGE = "page777"


@pytest.fixture(scope="module")
def pl_org(api):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Poll Co",
            "email": "owner@pollco.com",
            "password": "pollco-pass-1",
            "full_name": "Poll Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "Poll Client"}, headers=headers
    ).json()["id"]
    with SessionLocal() as db:
        db.add(
            LeadFormConfig(
                organization_id=body["organization_id"],
                client_id=client_id,
                platform="meta",
                external_key=PAGE,
                enabled=True,
            )
        )
        db.add(
            PlatformConnection(
                organization_id=body["organization_id"],
                client_id=client_id,
                platform="meta",
                status="active",
            )
        )
        db.commit()
    return {"org": body["organization_id"], "headers": headers, "client": client_id}


@pytest.fixture()
def fake_graph(monkeypatch):
    calls = []

    def _get(url, params):
        calls.append(url)
        if url.endswith("/me/accounts"):
            return {"data": [{"id": PAGE, "access_token": "ptok"}]}
        if url.endswith("/leadgen_forms"):
            return {"data": [{"id": "form1", "status": "ACTIVE"}]}
        if url.endswith("/form1/leads"):
            return {
                "data": [{"id": "LEAD001", "created_time": "2026-07-24T12:00:00+0000"}]
            }
        raise AssertionError(f"unexpected Graph url {url}")

    monkeypatch.setattr(meta_lead_poll, "_get", _get)
    monkeypatch.setattr(conn_svc, "get_access_token", lambda conn: "user-tok")
    monkeypatch.setattr(
        meta_leadgen,
        "fetch_lead",
        lambda token, leadgen_id: {
            "field_data": [
                {"name": "full_name", "values": ["Poll Lead"]},
                {"name": "phone_number", "values": ["+14805557788"]},
                {"name": "email", "values": ["poll.lead@example.com"]},
            ]
        },
    )
    return calls


def _reset_cursor(minutes_ago=None):
    with SessionLocal() as db:
        cfg = db.execute(
            select(LeadFormConfig).where(LeadFormConfig.external_key == PAGE)
        ).scalar_one()
        cfg.last_polled_at = (
            None if minutes_ago is None else utcnow() - dt.timedelta(minutes=minutes_ago)
        )
        db.commit()


def test_poll_ingests_new_lead_through_webhook_path(pl_org, api, fake_graph):
    _reset_cursor(None)
    with SessionLocal() as db:
        created = meta_lead_poll.run_due(db)
    assert created == 1
    with SessionLocal() as db:
        contact = db.execute(
            select(Contact).where(Contact.source_external_id == "LEAD001")
        ).scalar_one()
        assert contact.client_id == pl_org["client"]
        assert contact.source == "meta_instant_form"
        assert contact.first_name == "Poll"
        assert contact.last_name == "Lead"
        cfg = db.execute(
            select(LeadFormConfig).where(LeadFormConfig.external_key == PAGE)
        ).scalar_one()
        assert cfg.last_polled_at is not None


def test_poll_interval_gates_and_repoll_is_idempotent(pl_org, api, fake_graph):
    # Fresh cursor from the previous poll — inside the interval, no Graph calls.
    before = len(fake_graph)
    with SessionLocal() as db:
        assert meta_lead_poll.run_due(db) == 0
    assert len(fake_graph) == before

    # Stale cursor — polls again, same lead comes back, upsert dedupes:
    # no second contact, no "created" (so no double notify/auto-enroll).
    _reset_cursor(minutes_ago=10)
    with SessionLocal() as db:
        assert meta_lead_poll.run_due(db) == 0
    with SessionLocal() as db:
        n = len(
            db.execute(
                select(Contact.id).where(Contact.client_id == pl_org["client"])
            ).all()
        )
        assert n == 1


def test_poll_failure_is_contained_and_retries_on_interval(pl_org, api, monkeypatch):
    # The exact production state: token valid but Graph refuses the app.
    def _refuse(url, params):
        raise RuntimeError("Cannot call API for app X on behalf of user Y")

    monkeypatch.setattr(meta_lead_poll, "_get", _refuse)
    monkeypatch.setattr(conn_svc, "get_access_token", lambda conn: "user-tok")
    _reset_cursor(minutes_ago=10)
    with SessionLocal() as db:
        assert meta_lead_poll.run_due(db) == 0  # swallowed, never raises
    with SessionLocal() as db:
        cfg = db.execute(
            select(LeadFormConfig).where(LeadFormConfig.external_key == PAGE)
        ).scalar_one()
        # Cursor still stamped — the broken page retries on the interval
        # instead of hot-looping every 60s tick.
        assert (utcnow() - cfg.last_polled_at.replace(tzinfo=dt.timezone.utc)
                if cfg.last_polled_at.tzinfo is None
                else utcnow() - cfg.last_polled_at).total_seconds() < 60
        # ...and the reason is PERSISTED, not just logged: this failure ran
        # for three weeks in production with no surface in the product.
        assert "Cannot call API for app X" in cfg.last_poll_error


def test_disconnected_client_reports_instead_of_silently_polling_nothing(
    pl_org, api, monkeypatch
):
    """A revoked Meta connection used to make _poll_page return 0 — identical
    to a healthy page with no new leads, so a client could stop delivering
    leads entirely and look fine."""
    monkeypatch.setattr(meta_lead_poll, "_get", lambda url, params: {"data": []})
    _reset_cursor(minutes_ago=10)
    with SessionLocal() as db:
        conn = db.execute(
            select(PlatformConnection).where(
                PlatformConnection.client_id == pl_org["client"]
            )
        ).scalar_one()
        conn.status = "disconnected"
        db.commit()
    try:
        with SessionLocal() as db:
            assert meta_lead_poll.run_due(db) == 0
        with SessionLocal() as db:
            cfg = db.execute(
                select(LeadFormConfig).where(LeadFormConfig.external_key == PAGE)
            ).scalar_one()
            assert "not connected" in (cfg.last_poll_error or "")
    finally:
        with SessionLocal() as db:
            conn = db.execute(
                select(PlatformConnection).where(
                    PlatformConnection.client_id == pl_org["client"]
                )
            ).scalar_one()
            conn.status = "active"
            db.commit()


def test_successful_poll_clears_the_error_and_stamps_arrival(
    pl_org, api, fake_graph, monkeypatch
):
    """Recovery is self-clearing, and last_lead_at records that a lead really
    ARRIVED — a poll succeeding against a page with no new leads is not the
    same as the route working."""
    with SessionLocal() as db:
        cfg = db.execute(
            select(LeadFormConfig).where(LeadFormConfig.external_key == PAGE)
        ).scalar_one()
        cfg.last_poll_error = "stale failure from an earlier tick"
        # Cleared so the arrival assertion below can't pass on an earlier
        # test's stamp.
        cfg.last_lead_at = None
        db.commit()
    # A lead id this module hasn't ingested yet — dedupe means a repeat of
    # LEAD001 would come back "updated", which correctly does NOT count as a
    # fresh arrival.
    def _get(url, params):
        if url.endswith("/me/accounts"):
            return {"data": [{"id": PAGE, "access_token": "ptok"}]}
        if url.endswith("/leadgen_forms"):
            return {"data": [{"id": "form1", "status": "ACTIVE"}]}
        if url.endswith("/form1/leads"):
            return {
                "data": [{"id": "LEAD_ARRIVAL", "created_time": "2026-07-24T12:00:00+0000"}]
            }
        raise AssertionError(f"unexpected Graph url {url}")

    monkeypatch.setattr(meta_lead_poll, "_get", _get)
    monkeypatch.setattr(
        meta_leadgen,
        "fetch_lead",
        lambda token, leadgen_id: {
            "field_data": [
                {"name": "full_name", "values": ["Arrival Lead"]},
                {"name": "phone_number", "values": ["+14805550099"]},
                {"name": "email", "values": ["arrival.lead@example.com"]},
            ]
        },
    )
    monkeypatch.setattr(conn_svc, "get_access_token", lambda conn: "user-tok")
    _reset_cursor(minutes_ago=10)
    with SessionLocal() as db:
        assert meta_lead_poll.run_due(db) == 1
    with SessionLocal() as db:
        cfg = db.execute(
            select(LeadFormConfig).where(LeadFormConfig.external_key == PAGE)
        ).scalar_one()
        assert cfg.last_poll_error is None
        assert cfg.last_lead_at is not None
