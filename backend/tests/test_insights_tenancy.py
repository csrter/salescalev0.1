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
