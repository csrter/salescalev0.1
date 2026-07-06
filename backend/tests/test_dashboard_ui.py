"""Phase 4 Definition-of-Done, backend half:

- Dashboard layouts persist per (user, client view), bounded and validated,
  with both tenant-isolation levels enforced.
- The platform filter narrows every blended total consistently (spend,
  tracked leads, won-deal revenue), not just the per-platform table.
- The guarantee tracker counts progress across whichever platforms are
  contributing, and respects the platform filter.
- Spend/pacing series is zero-filled and filterable.

All metric fixture data lives on client B ("Bravo Heating") so
test_metrics.py's traceable client-A arithmetic stays untouched.

Fixture arithmetic (client B, all within the last 14 days):
  Meta   — 4 days × $25, 1 conv/day  → $100 spend, 4 platform conversions.
  Google — 2 days × $50, 1 conv/day  → $100 spend, 2 platform conversions.
  Leads  — bm1, bm2 (fbclid) → meta; bg1 (gclid) → google; bx1 no trail.
  CRM    — bm1 + bg1 in a qualified stage; bg1 won at $1,000 (in range).
Expected: blended spend $200 / 4 tracked leads → CPL $50; meta-only spend
$100 / 2 leads → CPL $50 with zero won deals (the only won deal is Google's).
Guarantee (qualified_leads, target 4, rolling 14d): progress 2 = meta 1 +
google 1; meta-only filter → 1.
"""

import datetime as dt

import pytest

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.ads import Campaign, InsightDaily
from app.models.attribution import LandingEvent
from app.models.crm import Contact, Deal, Pipeline, PipelineStage

TODAY = dt.date.today()


def _d(days_ago: int) -> dt.date:
    return TODAY - dt.timedelta(days=days_ago)


@pytest.fixture(scope="module")
def phase4_seeded(seeded, api):
    db = SessionLocal()
    org = seeded["org"]
    base = {"organization_id": org, "client_id": seeded["client_b"]}

    db.add_all(
        [
            Campaign(**base, ad_account_id=seeded["acct_b"], platform="meta",
                     external_id="b_c1", name="Bravo Cold Leads"),
            Campaign(**base, ad_account_id=seeded["acct_b"], platform="google",
                     external_id="b_g1", name="Bravo Brand Search"),
        ]
    )
    for i in range(4):  # Meta: 4 days × $25, 1 conversion/day
        db.add(
            InsightDaily(**base, platform="meta", entity_type="ad",
                         entity_external_id="b_m_ad", date=_d(i + 1),
                         impressions=800, clicks=40,
                         spend_micros=25_000_000, conversions=1,
                         raw={"campaign_id": "b_c1"})
        )
    for i in range(2):  # Google: 2 days × $50, 1 conversion/day
        db.add(
            InsightDaily(**base, platform="google", entity_type="ad_group",
                         entity_external_id="b_g_ag", date=_d(i + 1),
                         impressions=400, clicks=30,
                         spend_micros=50_000_000, conversions=1,
                         raw={"campaign_id": "b_g1"})
        )

    contacts = {
        "bm1": Contact(**base, first_name="BM1", source="landing_page"),
        "bm2": Contact(**base, first_name="BM2", source="landing_page"),
        "bg1": Contact(**base, first_name="BG1", source="landing_page"),
        "bx1": Contact(**base, first_name="BX1", source="manual"),
    }
    db.add_all(contacts.values())
    db.flush()
    db.add_all(
        [
            LandingEvent(**base, session_key=f"s4-{key}", occurred_at=utcnow(),
                         contact_id=c.id,
                         fbclid="fb-b" if key.startswith("bm") else None,
                         gclid="g-b" if key.startswith("bg") else None,
                         utm_source="facebook" if key.startswith("bm") else "google")
            for key, c in contacts.items()
            if key != "bx1"
        ]
    )

    pipeline = Pipeline(**base, name="Bravo Default", is_default=True)
    db.add(pipeline)
    db.flush()
    stage_qual = PipelineStage(organization_id=org, pipeline_id=pipeline.id,
                               name="Qualified", position=1,
                               is_qualified_stage=True)
    db.add(stage_qual)
    db.flush()
    db.add_all(
        [
            Deal(**base, contact_id=contacts["bm1"].id, pipeline_id=pipeline.id,
                 stage_id=stage_qual.id, name="BM1 job", status="open"),
            Deal(**base, contact_id=contacts["bg1"].id, pipeline_id=pipeline.id,
                 stage_id=stage_qual.id, name="BG1 install", status="won",
                 value_cents=100_000, closed_at=utcnow()),
        ]
    )
    db.commit()
    db.close()
    return seeded


# --- dashboard layout persistence ---

LAYOUT = {
    "widgets": [
        {"type": "overview", "w": 12, "h": 1},
        {"type": "spend_pacing", "w": 8, "h": 2},
        {"type": "guarantee", "w": 4, "h": 2},
    ]
}


def test_layout_roundtrip_and_update(api, team_headers, phase4_seeded):
    cid = phase4_seeded["client_a"]
    resp = api.get(f"/api/dashboard/layout?client_id={cid}", headers=team_headers)
    assert resp.status_code == 200
    assert resp.json()["widgets"] is None  # no saved layout → role default

    resp = api.put(
        f"/api/dashboard/layout?client_id={cid}", json=LAYOUT, headers=team_headers
    )
    assert resp.status_code == 200, resp.text
    resp = api.get(f"/api/dashboard/layout?client_id={cid}", headers=team_headers)
    assert [w["type"] for w in resp.json()["widgets"]] == [
        "overview", "spend_pacing", "guarantee",
    ]

    # Upsert, not insert-twice: a rearranged save replaces the row.
    rearranged = {"widgets": list(reversed(LAYOUT["widgets"]))}
    api.put(
        f"/api/dashboard/layout?client_id={cid}",
        json=rearranged, headers=team_headers,
    )
    resp = api.get(f"/api/dashboard/layout?client_id={cid}", headers=team_headers)
    assert [w["type"] for w in resp.json()["widgets"]] == [
        "guarantee", "spend_pacing", "overview",
    ]


def test_layout_is_per_user_and_per_client(
    api, team_headers, member_headers, phase4_seeded
):
    cid = phase4_seeded["client_a"]
    # The owner's saved layout (previous test) is invisible to the member…
    resp = api.get(f"/api/dashboard/layout?client_id={cid}", headers=member_headers)
    assert resp.json()["widgets"] is None
    # …and the owner's layout for client B is independent of client A's.
    resp = api.get(
        f"/api/dashboard/layout?client_id={phase4_seeded['client_b']}",
        headers=team_headers,
    )
    assert resp.json()["widgets"] is None


def test_layout_validation_rejects_garbage(api, team_headers, phase4_seeded):
    cid = phase4_seeded["client_a"]
    bad_geometry = {"widgets": [{"type": "overview", "w": 40, "h": 1}]}
    assert (
        api.put(
            f"/api/dashboard/layout?client_id={cid}",
            json=bad_geometry, headers=team_headers,
        ).status_code
        == 422
    )
    duplicate = {"widgets": [{"type": "overview", "w": 6, "h": 1}] * 2}
    assert (
        api.put(
            f"/api/dashboard/layout?client_id={cid}",
            json=duplicate, headers=team_headers,
        ).status_code
        == 422
    )


def test_layout_tenant_isolation(
    api, org2_headers, client_a_headers, phase4_seeded
):
    # Org 2 cannot read or write a layout against org 1's client…
    cid = phase4_seeded["client_a"]
    assert (
        api.get(
            f"/api/dashboard/layout?client_id={cid}", headers=org2_headers
        ).status_code
        == 404
    )
    assert (
        api.put(
            f"/api/dashboard/layout?client_id={cid}",
            json=LAYOUT, headers=org2_headers,
        ).status_code
        == 404
    )
    # …a client-role user can save their own view of their own client…
    resp = api.put(
        f"/api/dashboard/layout?client_id={cid}",
        json=LAYOUT, headers=client_a_headers,
    )
    assert resp.status_code == 200
    # …but not of a sibling client.
    assert (
        api.put(
            f"/api/dashboard/layout?client_id={phase4_seeded['client_b']}",
            json=LAYOUT, headers=client_a_headers,
        ).status_code
        == 404
    )


# --- platform filter on blended metrics ---


def test_blended_platform_filter_narrows_every_total(
    api, team_headers, phase4_seeded
):
    cid = phase4_seeded["client_b"]
    full = api.get(
        f"/api/metrics/blended?client_id={cid}", headers=team_headers
    ).json()
    assert full["total_spend_micros"] == 200_000_000
    assert full["total_tracked_leads"] == 4
    assert full["blended_cpl"] == 50.0
    assert full["unattributed_leads"] == 1
    assert full["won_deals_from_paid"] == 1

    meta = api.get(
        f"/api/metrics/blended?client_id={cid}&platforms=meta",
        headers=team_headers,
    ).json()
    assert meta["platforms"] == ["meta"]
    assert set(meta["per_platform"]) == {"meta"}
    assert meta["total_spend_micros"] == 100_000_000
    assert meta["total_tracked_leads"] == 2  # unattributed drops out
    assert meta["unattributed_leads"] == 0
    # The only won deal is Google-attributed — meta-only view must not
    # claim its revenue.
    assert meta["won_deals_from_paid"] == 0
    assert meta["blended_roas"] is None

    assert (
        api.get(
            f"/api/metrics/blended?client_id={cid}&platforms=tiktok",
            headers=team_headers,
        ).status_code
        == 400
    )


def test_spend_daily_series(api, team_headers, phase4_seeded):
    cid = phase4_seeded["client_b"]
    since, until = _d(6).isoformat(), TODAY.isoformat()
    body = api.get(
        f"/api/metrics/spend-daily?client_id={cid}&since={since}&until={until}",
        headers=team_headers,
    ).json()
    assert len(body["days"]) == 7
    assert sum(body["per_platform"]["meta"]["daily_spend_micros"]) == 100_000_000
    assert body["per_platform"]["meta"]["total_spend_micros"] == 100_000_000
    # Zero-filled: 7 slots even though only 4 days have Meta spend.
    assert len(body["per_platform"]["meta"]["daily_spend_micros"]) == 7

    google_only = api.get(
        f"/api/metrics/spend-daily?client_id={cid}&platforms=google",
        headers=team_headers,
    ).json()
    assert set(google_only["per_platform"]) == {"google"}


# --- guarantee tracker ---


def test_guarantee_unconfigured_and_admin_gate(
    api, team_headers, member_headers, phase4_seeded
):
    cid = phase4_seeded["client_b"]
    resp = api.get(
        f"/api/metrics/guarantee?client_id={cid}", headers=team_headers
    )
    assert resp.json() == {"configured": False}
    # Guarantee terms are client management — members can't set them.
    config = {"name": "14-Day Trial Sprint", "metric": "qualified_leads",
              "target": 4, "window_days": 14}
    assert (
        api.put(
            f"/api/clients/{cid}/guarantee", json=config, headers=member_headers
        ).status_code
        == 403
    )


def test_guarantee_progress_sums_across_platforms(
    api, team_headers, client_a_headers, phase4_seeded
):
    cid = phase4_seeded["client_b"]
    config = {"name": "14-Day Trial Sprint", "metric": "qualified_leads",
              "target": 4, "window_days": 14}
    resp = api.put(
        f"/api/clients/{cid}/guarantee", json=config, headers=team_headers
    )
    assert resp.status_code == 200, resp.text

    body = api.get(
        f"/api/metrics/guarantee?client_id={cid}", headers=team_headers
    ).json()
    assert body["configured"] is True
    assert body["target"] == 4
    # DoD: progress across whichever platforms contribute — meta 1 + google 1.
    assert body["progress"] == 2
    assert body["per_platform"] == {"google": 1, "meta": 1}
    assert body["met"] is False
    # Rolling window fully elapsed → expected the full target by now.
    assert body["on_pace"] is False

    meta_only = api.get(
        f"/api/metrics/guarantee?client_id={cid}&platforms=meta",
        headers=team_headers,
    ).json()
    assert meta_only["progress"] == 1

    # Org-1's client-role user (client A) can see their own guarantee
    # endpoint but never client B's.
    assert (
        api.get(
            f"/api/metrics/guarantee?client_id={cid}", headers=client_a_headers
        ).status_code
        == 404
    )


def test_guarantee_won_deals_metric_and_clear(api, team_headers, phase4_seeded):
    cid = phase4_seeded["client_b"]
    config = {"name": "Install guarantee", "metric": "won_deals",
              "target": 2, "window_days": 14}
    api.put(f"/api/clients/{cid}/guarantee", json=config, headers=team_headers)
    body = api.get(
        f"/api/metrics/guarantee?client_id={cid}", headers=team_headers
    ).json()
    assert body["metric"] == "won_deals"
    assert body["progress"] == 1  # bg1's won install, attributed to google
    assert body["per_platform"] == {"google": 1}

    assert (
        api.delete(
            f"/api/clients/{cid}/guarantee", headers=team_headers
        ).status_code
        == 204
    )
    body = api.get(
        f"/api/metrics/guarantee?client_id={cid}", headers=team_headers
    ).json()
    assert body == {"configured": False}


def test_guarantee_rejects_unknown_metric(api, team_headers, phase4_seeded):
    cid = phase4_seeded["client_b"]
    config = {"name": "Bad", "metric": "vibes", "target": 1, "window_days": 7}
    assert (
        api.put(
            f"/api/clients/{cid}/guarantee", json=config, headers=team_headers
        ).status_code
        == 400
    )


# --- raw campaign table source ---


def test_flat_campaign_list_is_scoped(
    api, team_headers, client_a_headers, org2_headers, phase4_seeded
):
    cid = phase4_seeded["client_b"]
    rows = api.get(
        f"/api/campaigns?client_id={cid}", headers=team_headers
    ).json()
    names = {r["name"] for r in rows}
    assert {"Bravo Cold Leads", "Bravo Brand Search"} <= names
    assert all(r["client_id"] == cid for r in rows)

    # Client-role user pinned to client A: sibling client → 404.
    assert (
        api.get(
            f"/api/campaigns?client_id={cid}", headers=client_a_headers
        ).status_code
        == 404
    )
    # Org 2 asking for org 1's client id: scoped filter yields nothing.
    assert (
        api.get(f"/api/campaigns?client_id={cid}", headers=org2_headers).json()
        == []
    )
