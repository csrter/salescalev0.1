"""Phase 3 Definition-of-Done: every metric computes from seeded fixture
data with traceable numbers, and attribution reconciliation flags a
deliberately introduced discrepancy between what the platform claims and
what the UTM trail confirms.

Fixture arithmetic (all within the last 30 days):
  Meta   — spend $100, platform-reported conversions 5 (campaign "Cold
           Prospecting Leads"); fatigue ad with CTR 5% baseline → 1.5%
           recent (score 0.7, flagged).
  Google — branded campaign $50 / 2 conv; non-branded $150 / 3 conv.
  Leads  — 5 Salescale contacts: 3 Meta (fbclid), 1 Google (gclid),
           1 with no landing event at all.
  Deals  — meta_1 in a qualified stage; google_1 won at $2,000 (in range).

Expected: blended CPL 300/5=$60 · blended CAC 300/1=$300 · ROAS 2000/300=6.67
LQA-CPL meta 100/1=$100, google 200/1=$200, blended 300/2=$150.
Reconciliation: meta reports 5 vs 3 confirmed → over-credit 2 (flagged);
1 lead with no UTM trail (flagged)."""

import datetime as dt

import pytest

from app.db import SessionLocal
from app.models.ads import Campaign, InsightDaily, QualitySnapshot
from app.models.attribution import LandingEvent
from app.models.base import utcnow
from app.models.core import Client
from app.models.crm import Contact, Deal, Pipeline, PipelineStage

TODAY = dt.date.today()


def _d(days_ago: int) -> dt.date:
    return TODAY - dt.timedelta(days=days_ago)


@pytest.fixture(scope="module")
def metrics_seeded(seeded, api):
    db = SessionLocal()
    org = seeded["org"]
    client_id = seeded["client_a"]
    base = {"organization_id": org, "client_id": client_id}

    # Verticals for the benchmark (both clients in "hvac").
    for cid in (seeded["client_a"], seeded["client_b"]):
        db.get(Client, cid).vertical = "hvac"

    # Campaign name cache for tier classification.
    db.add_all(
        [
            Campaign(**base, ad_account_id=seeded["acct_a"], platform="meta",
                     external_id="c_cold", name="Cold Prospecting Leads"),
            Campaign(**base, ad_account_id=seeded["acct_a"], platform="meta",
                     external_id="c_hot", name="Hot Remarketing"),
            Campaign(**base, ad_account_id=seeded["acct_a"], platform="google",
                     external_id="g_brand", name="Brand Search Alpha"),
            Campaign(**base, ad_account_id=seeded["acct_a"], platform="google",
                     external_id="g_generic", name="AC Repair Generic"),
        ]
    )

    # Meta spend: 5 days × $20, 1 conversion/day (platform-reported total 5).
    for i in range(5):
        db.add(
            InsightDaily(**base, platform="meta", entity_type="ad",
                         entity_external_id="m_ad_cold", date=_d(i + 1),
                         impressions=1000, clicks=50,
                         spend_micros=20_000_000, conversions=1,
                         raw={"campaign_id": "c_cold", "ad_name": "Cold Video A"})
        )
    # Fatigue ad: baseline CTR 5% (days 8–28), recent CTR 1.5% (days 0–6).
    for i in range(29):
        recent = i <= 6
        db.add(
            InsightDaily(**base, platform="meta", entity_type="ad",
                         entity_external_id="m_ad_fatigue", date=_d(i),
                         impressions=200, clicks=3 if recent else 10,
                         spend_micros=0, conversions=0,
                         raw={"campaign_id": "c_hot", "ad_name": "Tired Video B"})
        )
    # Google: branded $50/2conv, non-branded $150/3conv (ad-group level).
    for i in range(2):
        db.add(
            InsightDaily(**base, platform="google", entity_type="ad_group",
                         entity_external_id="g_ag_brand", date=_d(i + 1),
                         impressions=500, clicks=40,
                         spend_micros=25_000_000, conversions=1,
                         raw={"campaign_id": "g_brand"})
        )
    for i in range(3):
        db.add(
            InsightDaily(**base, platform="google", entity_type="ad_group",
                         entity_external_id="g_ag_gen", date=_d(i + 1),
                         impressions=900, clicks=60,
                         spend_micros=50_000_000, conversions=1,
                         raw={"campaign_id": "g_generic"})
        )

    # Quality snapshots: QS 8 → 6 (flag), ad_strength GOOD → GOOD (no flag).
    db.add_all(
        [
            QualitySnapshot(**base, platform="google", entity_type="keyword",
                            entity_external_id="ag1~kw1", entity_name="ac repair",
                            metric="quality_score", value=8, date=_d(20)),
            QualitySnapshot(**base, platform="google", entity_type="keyword",
                            entity_external_id="ag1~kw1", entity_name="ac repair",
                            metric="quality_score", value=6, date=TODAY),
            QualitySnapshot(**base, platform="google", entity_type="asset_group",
                            entity_external_id="pmax1", entity_name="PMax One",
                            metric="ad_strength", value=3, value_label="GOOD",
                            date=_d(10)),
            QualitySnapshot(**base, platform="google", entity_type="asset_group",
                            entity_external_id="pmax1", entity_name="PMax One",
                            metric="ad_strength", value=3, value_label="GOOD",
                            date=TODAY),
        ]
    )

    # Leads: 3 Meta-confirmed, 1 Google-confirmed, 1 with no landing trail.
    contacts = {
        "meta_1": Contact(**base, first_name="M1", source="landing_page"),
        "meta_2": Contact(**base, first_name="M2", source="landing_page"),
        "meta_3": Contact(**base, first_name="M3", source="landing_page"),
        "google_1": Contact(**base, first_name="G1", source="landing_page"),
        "mystery_1": Contact(**base, first_name="X1", source="manual"),
    }
    db.add_all(contacts.values())
    db.flush()
    db.add_all(
        [
            LandingEvent(**base, session_key=f"s-{key}", occurred_at=utcnow(),
                         contact_id=c.id,
                         fbclid="fb123" if key.startswith("meta") else None,
                         gclid="g123" if key.startswith("google") else None,
                         utm_source="facebook" if key.startswith("meta") else "google",
                         utm_medium="paid_social" if key.startswith("meta") else "paid_search",
                         utm_campaign="alpha-hvac_summer-tune-up")
            for key, c in contacts.items()
            if key != "mystery_1"
        ]
    )
    # A convention-breaking landing event for the UTM violations check.
    db.add(
        LandingEvent(**base, session_key="s-bad", occurred_at=utcnow(),
                     utm_source="Facebook Ads", utm_campaign="Summer Promo!!")
    )

    # CRM: qualified stage + one qualified lead + one won deal ($2,000).
    pipeline = Pipeline(**base, name="Default", is_default=True)
    db.add(pipeline)
    db.flush()
    stage_new = PipelineStage(organization_id=org, pipeline_id=pipeline.id,
                              name="New", position=1)
    stage_qual = PipelineStage(organization_id=org, pipeline_id=pipeline.id,
                               name="Qualified", position=2,
                               is_qualified_stage=True)
    db.add_all([stage_new, stage_qual])
    db.flush()
    db.add_all(
        [
            Deal(**base, contact_id=contacts["meta_1"].id, pipeline_id=pipeline.id,
                 stage_id=stage_qual.id, name="M1 job", status="open"),
            Deal(**base, contact_id=contacts["google_1"].id, pipeline_id=pipeline.id,
                 stage_id=stage_qual.id, name="G1 install", status="won",
                 value_cents=200_000, closed_at=utcnow()),
        ]
    )
    db.commit()
    db.close()
    return seeded


def test_blended_cac_roas_and_channel_mix(api, team_headers, metrics_seeded):
    resp = api.get(
        f"/api/metrics/blended?client_id={metrics_seeded['client_a']}",
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_spend_micros"] == 300_000_000
    assert body["total_tracked_leads"] == 5
    assert body["blended_cpl"] == 60.0
    assert body["won_deals_from_paid"] == 1
    assert body["blended_cac"] == 300.0
    assert body["blended_roas"] == 6.67
    meta = body["per_platform"]["meta"]
    assert meta["platform_reported_conversions"] == 5
    assert meta["tracked_leads"] == 3
    assert meta["platform_cpl"] == 20.0
    assert meta["tracked_cpl"] == 33.33
    google = body["per_platform"]["google"]
    assert google["spend_share"] == round(200 / 300, 3)
    assert body["unattributed_leads"] == 1


def test_funnel_tiers(api, team_headers, metrics_seeded):
    resp = api.get(
        f"/api/metrics/funnel-tiers?client_id={metrics_seeded['client_a']}",
        headers=team_headers,
    )
    body = resp.json()
    assert body["meta"]["cold"]["spend_micros"] == 100_000_000
    assert body["meta"]["cold"]["cpl"] == 20.0
    assert "hot" in body["meta"]  # fatigue ad rows land in hot, spend 0
    assert body["google"]["branded"]["cpl"] == 25.0
    assert body["google"]["non_branded"]["cpl"] == 50.0


def test_creative_fatigue_flags_declining_ctr(api, team_headers, metrics_seeded):
    resp = api.get(
        f"/api/metrics/creative-fatigue?client_id={metrics_seeded['client_a']}",
        headers=team_headers,
    )
    body = resp.json()
    flagged = {a["ad_external_id"]: a for a in body["flagged"]}
    assert "m_ad_fatigue" in flagged
    assert flagged["m_ad_fatigue"]["fatigue_score"] == 0.7
    # The healthy cold ad must not be flagged.
    assert "m_ad_cold" not in flagged


def test_quality_trends_flags_qs_drop(api, team_headers, metrics_seeded):
    resp = api.get(
        f"/api/metrics/quality-trends?client_id={metrics_seeded['client_a']}",
        headers=team_headers,
    )
    body = resp.json()
    flagged = {e["entity_external_id"]: e for e in body["flagged"]}
    assert "ag1~kw1" in flagged and flagged["ag1~kw1"]["delta"] == -2
    assert "pmax1" not in flagged  # stable ad strength: no flag


def test_lead_quality_adjusted_cpl(api, team_headers, metrics_seeded):
    resp = api.get(
        "/api/metrics/lead-quality-adjusted-cpl"
        f"?client_id={metrics_seeded['client_a']}",
        headers=team_headers,
    )
    body = resp.json()
    assert body["source"] == "salescale"
    assert body["per_platform"]["meta"]["qualified_leads"] == 1
    assert body["per_platform"]["meta"]["lead_quality_adjusted_cpl"] == 100.0
    assert body["per_platform"]["google"]["lead_quality_adjusted_cpl"] == 200.0
    assert body["blended_lead_quality_adjusted_cpl"] == 150.0


def test_reconciliation_flags_deliberate_discrepancy(
    api, team_headers, metrics_seeded
):
    """DoD: Meta self-reports 5 conversions; the UTM/click-id trail confirms
    only 3 — the deliberately introduced over-credit must be flagged."""
    resp = api.get(
        f"/api/metrics/reconciliation?client_id={metrics_seeded['client_a']}",
        headers=team_headers,
    )
    body = resp.json()
    meta = body["per_platform"]["meta"]
    assert meta["platform_reported"] == 5
    assert meta["utm_confirmed"] == 3
    assert meta["discrepancy"] == 2
    assert meta["flagged"] is True
    assert any("over-credit" in f["detail"] for f in body["flags"])
    assert body["no_utm_leads"] == 1
    assert any("no UTM/click-id trail" in f["detail"] for f in body["flags"])


def test_vertical_benchmark_is_org_scoped(api, team_headers, metrics_seeded):
    resp = api.get(
        f"/api/metrics/benchmark?client_id={metrics_seeded['client_a']}",
        headers=team_headers,
    )
    body = resp.json()
    assert body["vertical"] == "hvac"
    assert body["peers"] == 2  # both org-1 hvac clients; org-2 never counted
    assert body["client_blended_cpl"] == 60.0


def test_benchmark_denied_to_client_role(api, client_a_headers, metrics_seeded):
    resp = api.get(
        f"/api/metrics/benchmark?client_id={metrics_seeded['client_a']}",
        headers=client_a_headers,
    )
    assert resp.status_code == 403


def test_client_role_sees_own_metrics_only(
    api, client_a_headers, metrics_seeded
):
    own = api.get(
        f"/api/metrics/blended?client_id={metrics_seeded['client_a']}",
        headers=client_a_headers,
    )
    assert own.status_code == 200
    other = api.get(
        f"/api/metrics/blended?client_id={metrics_seeded['client_b']}",
        headers=client_a_headers,
    )
    assert other.status_code == 404


def test_org2_cannot_read_org1_metrics(api, org2_headers, metrics_seeded):
    resp = api.get(
        f"/api/metrics/blended?client_id={metrics_seeded['client_a']}",
        headers=org2_headers,
    )
    assert resp.status_code == 404


def test_utm_builder_is_deterministic_and_canonical(
    api, team_headers, metrics_seeded
):
    resp = api.get(
        "/api/utm/build"
        f"?client_id={metrics_seeded['client_a']}&platform=meta"
        "&campaign_name=Summer Tune Up&content=Video A",
        headers=team_headers,
    )
    body = resp.json()
    assert body["params"] == {
        "utm_source": "facebook",
        "utm_medium": "paid_social",
        "utm_campaign": "alpha-hvac_summer-tune-up",
        "utm_content": "video-a",
    }


def test_utm_violations_catch_convention_drift(api, team_headers, metrics_seeded):
    resp = api.get(
        f"/api/utm/violations?client_id={metrics_seeded['client_a']}",
        headers=team_headers,
    )
    body = resp.json()
    bad = [v for v in body["violations"] if v["utm_campaign"] == "Summer Promo!!"]
    assert len(bad) == 1
    assert any("breaks convention" in p for p in bad[0]["problems"])
    # Conforming events must not be reported.
    assert all(
        v["utm_campaign"] != "alpha-hvac_summer-tune-up" for v in body["violations"]
    )
