"""Insights time-series tenant isolation (services/insights_sync).

uq_insight_entity_day / uq_quality_snapshot were org-blind: entity ids are
platform-global, so when an external ad account changes hands between
Organizations (the AdAccount row is globally unique per platform+external_id,
so "changes hands" is the real-world collision), the new org's sync found and
OVERWROTE the previous org's rows. These tests pin the org-scoped contract:
same entity + same day under two orgs = two rows, and re-syncing under one
org never touches the other's values. Own orgs (ins_org_a/b), per the
isolation convention.
"""

import datetime as dt
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.ads import InsightDaily, QualitySnapshot
from app.services.insights_sync import _upsert_insight, _upsert_snapshot

DAY = dt.date(2026, 7, 20)


def _signup(api, org, email):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": org,
            "email": email,
            "password": "insights-pass-1",
            "full_name": "I",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def two_orgs(api):
    out = []
    for name, email in (
        ("Insights Org A", "owner@insightsorga.com"),
        ("Insights Org B", "owner@insightsorgb.com"),
    ):
        body = _signup(api, name, email)
        headers = {"Authorization": f"Bearer {body['access_token']}"}
        client_id = api.post(
            "/api/clients", json={"name": f"{name} Client"}, headers=headers
        ).json()["id"]
        out.append(
            SimpleNamespace(
                organization_id=body["organization_id"],
                client_id=client_id,
                platform="meta",
                external_id=f"act_shared_{name[-1]}",
            )
        )
    return out


def _row(entity_id, impressions):
    return {
        "entity_type": "ad",
        "entity_external_id": entity_id,
        "date": DAY,
        "impressions": impressions,
        "clicks": 1,
        "spend_micros": 1_000_000,
        "conversions": 0,
    }


def test_same_entity_day_under_two_orgs_is_two_rows(two_orgs):
    a, b = two_orgs
    with SessionLocal() as db:
        _upsert_insight(db, a, _row("ad_shared_1", 111))
        db.commit()
    with SessionLocal() as db:
        _upsert_insight(db, b, _row("ad_shared_1", 999))
        db.commit()
    with SessionLocal() as db:
        rows = (
            db.execute(
                select(InsightDaily).where(
                    InsightDaily.entity_external_id == "ad_shared_1",
                    InsightDaily.date == DAY,
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2  # org-blind code collapsed these into one
        by_org = {r.organization_id: r for r in rows}
        assert by_org[a.organization_id].impressions == 111
        assert by_org[b.organization_id].impressions == 999


def test_resync_updates_own_row_never_the_other_orgs(two_orgs):
    a, b = two_orgs
    with SessionLocal() as db:
        _upsert_insight(db, b, _row("ad_shared_1", 1000))  # b restates its day
        db.commit()
    with SessionLocal() as db:
        rows = (
            db.execute(
                select(InsightDaily).where(
                    InsightDaily.entity_external_id == "ad_shared_1",
                    InsightDaily.date == DAY,
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2  # update, not a third row
        by_org = {r.organization_id: r for r in rows}
        assert by_org[a.organization_id].impressions == 111  # untouched
        assert by_org[b.organization_id].impressions == 1000


def test_quality_snapshot_org_scoped_the_same_way(two_orgs):
    a, b = two_orgs
    snap = {"entity_type": "keyword", "entity_external_id": "kw_shared_1",
            "value": 7, "value_label": None, "entity_name": "shared kw"}
    with SessionLocal() as db:
        _upsert_snapshot(db, a, snap, "quality_score", DAY)
        _upsert_snapshot(db, b, {**snap, "value": 3}, "quality_score", DAY)
        db.commit()
    with SessionLocal() as db:
        rows = (
            db.execute(
                select(QualitySnapshot).where(
                    QualitySnapshot.entity_external_id == "kw_shared_1",
                    QualitySnapshot.date == DAY,
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        by_org = {r.organization_id: r.value for r in rows}
        assert by_org[a.organization_id] == 7
        assert by_org[b.organization_id] == 3


def test_run_due_polls_due_connections_and_paces(two_orgs, monkeypatch):
    """The background poll syncs active connections on the interval, stamps
    the cursor at attempt start (a broken platform retries on the interval,
    never hot-loops), and manual/auto share the cursor."""
    import app.services.insights_sync as isync
    from app.models.core import AdAccount, PlatformConnection

    a = two_orgs[0]
    with SessionLocal() as db:
        conn = PlatformConnection(
            organization_id=a.organization_id,
            client_id=a.client_id,
            platform="meta",
            status="active",
        )
        db.add(conn)
        db.flush()
        db.add(
            AdAccount(
                organization_id=a.organization_id,
                client_id=a.client_id,
                connection_id=conn.id,
                platform="meta",
                external_id="act_autosync_1",
                name="Autosync",
            )
        )
        db.commit()
        conn_id = conn.id

    calls = []
    # Both fetchers stubbed: the suite-wide DB carries other modules' seeded
    # meta/google connections with NULL cursors, and run_due may legitimately
    # pick those up too — a real fetcher would try their fake tokens.
    monkeypatch.setitem(
        isync.INSIGHTS_FETCHERS,
        "meta",
        lambda db, account, conn, since, until: calls.append(account.external_id) or 0,
    )
    monkeypatch.setitem(
        isync.INSIGHTS_FETCHERS, "google", lambda *a: 0
    )
    with SessionLocal() as db:
        assert isync.run_due(db, limit=200) >= 1
    assert "act_autosync_1" in calls

    # Freshly stamped — a second pass inside the interval skips it.
    calls.clear()
    with SessionLocal() as db:
        isync.run_due(db, limit=200)
    assert "act_autosync_1" not in calls
    with SessionLocal() as db:
        stamped = db.get(PlatformConnection, conn_id).last_insights_sync_at
        assert stamped is not None


def test_run_due_failure_still_stamps_cursor(two_orgs, monkeypatch):
    import app.services.insights_sync as isync
    from app.models.core import PlatformConnection

    a = two_orgs[0]
    with SessionLocal() as db:
        # This module's own connection (created in the previous test) — the
        # suite-wide DB has other orgs' connections we must not depend on.
        conn = db.execute(
            select(PlatformConnection).where(
                PlatformConnection.client_id == a.client_id,
                PlatformConnection.platform == "meta",
            )
        ).scalars().one()
        conn.last_insights_sync_at = None
        db.commit()
        conn_id = conn.id

    def _boom(db, account, conn, since, until):
        raise RuntimeError("platform down")

    monkeypatch.setitem(isync.INSIGHTS_FETCHERS, "meta", _boom)
    monkeypatch.setitem(isync.INSIGHTS_FETCHERS, "google", lambda *a: 0)
    with SessionLocal() as db:
        isync.run_due(db, limit=200)  # must not raise
        row = db.get(PlatformConnection, conn_id)
        assert row.last_insights_sync_at is not None  # retries on interval
        assert row.status == "active"  # outage ≠ revoked
