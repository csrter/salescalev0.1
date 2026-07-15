"""Keyword update/pause/resume — extends the Phase-2 stage->confirm->execute
guardrail (test_manage_flow.py) to the Google-only keyword surface, which had
add/remove but no in-place edit or status toggle. Own dedicated org (kw_org):
keywords have no local cache table, so the fixture needs a real Campaign +
AdGroup under a Google AdAccount, which the shared `seeded` fixture (Meta-only)
doesn't provide.

Platform calls are monkeypatched — this proves the guardrail wiring (staging,
diff, dispatch to google_ads_api.update_keyword with the right args, audit),
not live Google Ads connectivity.
"""

import pytest

from app.db import SessionLocal
from app.models.ads import AdGroup, Campaign
from app.models.core import AdAccount, PlatformConnection
from app.security import encrypt_secret
from app.services import google_ads_api


@pytest.fixture(scope="module")
def kw_org(api):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Keyword Co",
            "email": "owner@keywordco.com",
            "password": "keywordco-pass-1",
            "full_name": "Keyword Owner",
        },
    )
    assert r.status_code == 201, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    org_id = r.json()["organization_id"]

    cr = api.post("/api/clients", json={"name": "Keyword Client"}, headers=headers)
    assert cr.status_code == 201, cr.text
    client_id = cr.json()["id"]

    db = SessionLocal()
    conn = PlatformConnection(
        organization_id=org_id,
        client_id=client_id,
        platform="google",
        access_token_encrypted=encrypt_secret("fake-access"),
        refresh_token_encrypted=encrypt_secret("fake-refresh"),
        status="active",
    )
    db.add(conn)
    db.flush()

    account = AdAccount(
        organization_id=org_id,
        client_id=client_id,
        connection_id=conn.id,
        platform="google",
        external_id="999888",
        name="Keyword Google Account",
    )
    db.add(account)
    db.flush()

    campaign = Campaign(
        organization_id=org_id,
        client_id=client_id,
        ad_account_id=account.id,
        platform="google",
        external_id="c_kw_1",
        name="Keyword Campaign",
    )
    db.add(campaign)
    db.flush()

    ad_group = AdGroup(
        organization_id=org_id,
        client_id=client_id,
        campaign_id=campaign.id,
        platform="google",
        external_id="ag_kw_1",
        name="Keyword Ad Group",
    )
    db.add(ad_group)
    db.commit()

    ids = {
        "org": org_id,
        "headers": headers,
        "client_id": client_id,
        "account_id": account.id,
        "ad_group_id": ad_group.id,
    }
    db.close()
    return ids


@pytest.fixture()
def keyword_update_spy(monkeypatch):
    calls = []

    def fake_update_keyword(
        refresh_token,
        customer_id,
        ad_group_external_id,
        criterion_id,
        match_type=None,
        cpc_bid_micros=None,
        status=None,
    ):
        calls.append(
            {
                "customer_id": customer_id,
                "ad_group_external_id": ad_group_external_id,
                "criterion_id": criterion_id,
                "match_type": match_type,
                "cpc_bid_micros": cpc_bid_micros,
                "status": status,
            }
        )

    monkeypatch.setattr(google_ads_api, "update_keyword", fake_update_keyword)
    return calls


def _stage(api, headers, kw_org, **overrides):
    body = {
        "ad_account_id": kw_org["account_id"],
        "entity_type": "keyword",
        "action": "update",
        "entity_external_id": "crit_1",
        "entity_name": "emergency furnace repair",
        "payload": {"ad_group_id": kw_org["ad_group_id"]},
    }
    body.update(overrides)
    return api.post("/api/manage/changes", json=body, headers=headers)


def test_update_stages_diff_for_bid_and_match_type(api, kw_org):
    resp = _stage(
        api,
        kw_org["headers"],
        kw_org,
        payload={
            "ad_group_id": kw_org["ad_group_id"],
            "match_type": "EXACT",
            "bid_micros": 2_500_000,
        },
    )
    assert resp.status_code == 200, resp.text
    diff = resp.json()["diff"]
    assert {"field": "match_type", "before": None, "after": "EXACT"} in diff
    assert {"field": "bid_micros", "before": None, "after": 2_500_000} in diff


def test_update_requires_entity_external_id(api, kw_org):
    resp = api.post(
        "/api/manage/changes",
        json={
            "ad_account_id": kw_org["account_id"],
            "entity_type": "keyword",
            "action": "update",
            "payload": {"ad_group_id": kw_org["ad_group_id"], "match_type": "EXACT"},
        },
        headers=kw_org["headers"],
    )
    assert resp.status_code == 400


def test_update_executes_and_dispatches_to_google(
    api, kw_org, keyword_update_spy
):
    change_id = _stage(
        api,
        kw_org["headers"],
        kw_org,
        payload={
            "ad_group_id": kw_org["ad_group_id"],
            "match_type": "PHRASE",
            "bid_micros": 1_750_000,
        },
    ).json()["id"]
    resp = api.post(
        f"/api/manage/changes/{change_id}/execute", headers=kw_org["headers"]
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "executed"
    assert keyword_update_spy == [
        {
            "customer_id": "999888",
            "ad_group_external_id": "ag_kw_1",
            "criterion_id": "crit_1",
            "match_type": "PHRASE",
            "cpc_bid_micros": 1_750_000,
            "status": None,
        }
    ]


def test_pause_and_resume_toggle_status_only(api, kw_org, keyword_update_spy):
    pause_id = api.post(
        "/api/manage/changes",
        json={
            "ad_account_id": kw_org["account_id"],
            "entity_type": "keyword",
            "action": "pause",
            "entity_external_id": "crit_2",
            "entity_name": "hvac repair near me",
            "payload": {"ad_group_id": kw_org["ad_group_id"]},
        },
        headers=kw_org["headers"],
    ).json()["id"]
    resp = api.post(
        f"/api/manage/changes/{pause_id}/execute", headers=kw_org["headers"]
    )
    assert resp.status_code == 200, resp.text
    assert keyword_update_spy[-1] == {
        "customer_id": "999888",
        "ad_group_external_id": "ag_kw_1",
        "criterion_id": "crit_2",
        "match_type": None,
        "cpc_bid_micros": None,
        "status": "PAUSED",
    }

    resume_id = api.post(
        "/api/manage/changes",
        json={
            "ad_account_id": kw_org["account_id"],
            "entity_type": "keyword",
            "action": "resume",
            "entity_external_id": "crit_2",
            "entity_name": "hvac repair near me",
            "payload": {"ad_group_id": kw_org["ad_group_id"]},
        },
        headers=kw_org["headers"],
    ).json()["id"]
    resp = api.post(
        f"/api/manage/changes/{resume_id}/execute", headers=kw_org["headers"]
    )
    assert resp.status_code == 200, resp.text
    assert keyword_update_spy[-1]["status"] == "ENABLED"


def test_add_still_threads_bid_through(api, kw_org, monkeypatch):
    calls = []

    def fake_add_keyword(
        refresh_token,
        customer_id,
        ad_group_external_id,
        text,
        match_type,
        negative=False,
        cpc_bid_micros=None,
    ):
        calls.append({"text": text, "cpc_bid_micros": cpc_bid_micros})
        return {"criterion_id": "crit_new", "resource_name": "x"}

    monkeypatch.setattr(google_ads_api, "add_keyword", fake_add_keyword)
    change_id = api.post(
        "/api/manage/changes",
        json={
            "ad_account_id": kw_org["account_id"],
            "entity_type": "keyword",
            "action": "add",
            "entity_name": "furnace tune up",
            "payload": {
                "ad_group_id": kw_org["ad_group_id"],
                "text": "furnace tune up",
                "match_type": "BROAD",
                "negative": False,
                "bid_micros": 3_000_000,
            },
        },
        headers=kw_org["headers"],
    ).json()["id"]
    resp = api.post(
        f"/api/manage/changes/{change_id}/execute", headers=kw_org["headers"]
    )
    assert resp.status_code == 200, resp.text
    assert calls == [{"text": "furnace tune up", "cpc_bid_micros": 3_000_000}]
