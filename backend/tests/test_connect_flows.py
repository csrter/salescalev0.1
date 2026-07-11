"""Connect flows: OAuth callbacks, the account picker, and reassignment.

The agency-scale problem under test: a Google MCC / Meta Business Manager
login sees MANY ad accounts. The callback must NOT dump them all onto the one
client being connected — it auto-attaches only when exactly one new account
is visible, and otherwise the Admin distributes accounts through the picker
endpoints (list / attach / reassign).

All platform APIs are monkeypatched. Every account-creating test runs in a
dedicated org (connect_org), never the seeded Atlas Reach org — the isolation
and metrics suites assert over Atlas Reach's account/campaign counts. Atlas
Reach's act_111 doubles as the "another Organization already holds this
account" case (read-only: nothing here mutates it).
"""

import datetime as dt

import pytest

from app.db import SessionLocal
from app.models.ads import Ad, AdGroup, Campaign, InsightDaily, QualitySnapshot
from app.models.audit import PendingChange
from app.models.core import AdAccount, PlatformConnection
from app.models.base import utcnow
from app.security import create_state_token, encrypt_secret
from app.services import google_ads_api, meta_api


@pytest.fixture(scope="module")
def connect_org(api):
    """Dedicated Organization with two clients and active meta + google
    connections on each (tokens are fakes; the platform calls are patched)."""
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Connect Co",
            "email": "owner@connectco.com",
            "password": "connectco-pass-1",
            "full_name": "Connect Owner",
        },
    )
    assert r.status_code == 201, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    org_id = r.json()["organization_id"]
    clients = {}
    for name in ("Client One", "Client Two"):
        cr = api.post("/api/clients", json={"name": name}, headers=headers)
        assert cr.status_code == 201, cr.text
        clients[name] = cr.json()["id"]

    db = SessionLocal()
    conns = {}
    for client_key, client_id in clients.items():
        for platform in ("meta", "google"):
            conn = PlatformConnection(
                organization_id=org_id,
                client_id=client_id,
                platform=platform,
                access_token_encrypted=encrypt_secret("fake-access"),
                refresh_token_encrypted=encrypt_secret("fake-refresh"),
            )
            db.add(conn)
            db.flush()
            conns[(client_key, platform)] = conn.id
    db.commit()
    db.close()
    return {
        "org": org_id,
        "headers": headers,
        "client_one": clients["Client One"],
        "client_two": clients["Client Two"],
        "conns": conns,
    }


def _google_discovery(monkeypatch, children):
    """Patch the Google side to look like one MCC with `children` under it."""
    monkeypatch.setattr(
        google_ads_api, "list_accessible_customers", lambda rt: ["9990001111"]
    )
    monkeypatch.setattr(
        google_ads_api,
        "fetch_customer_details",
        lambda rt, cid: {
            "external_id": cid,
            "name": "Manager",
            "currency": None,
            "timezone": None,
            "status": "ENABLED",
            "is_manager": True,
        },
    )
    monkeypatch.setattr(
        google_ads_api,
        "list_manager_child_accounts",
        lambda rt, mid: [
            {
                "external_id": ext,
                "name": f"Account {ext}",
                "currency": "USD",
                "timezone": "America/Phoenix",
                "status": "ENABLED",
                "is_manager": False,
            }
            for ext in children
        ],
    )


def _patch_google_exchange(monkeypatch):
    monkeypatch.setattr(
        google_ads_api,
        "exchange_code_for_tokens",
        lambda code: {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 3600,
        },
    )


def _google_accounts(api, connect_org, client_id):
    r = api.get(
        f"/api/ad-accounts?client_id={client_id}", headers=connect_org["headers"]
    )
    assert r.status_code == 200, r.text
    return [a for a in r.json() if a["platform"] == "google"]


# --- callbacks ----------------------------------------------------------------


def test_google_mcc_callback_attaches_nothing(api, connect_org, monkeypatch):
    """THE bug this suite exists for: an MCC connect must not attach the whole
    roster to one client profile."""
    _patch_google_exchange(monkeypatch)
    _google_discovery(monkeypatch, ["2001", "2002", "2003"])
    state = create_state_token(
        "google_oauth", connect_org["org"], connect_org["client_one"]
    )
    resp = api.get(
        f"/api/connect/google/callback?code=x&state={state}",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307), resp.text
    assert "connected=google" in resp.headers["location"]
    assert "select_accounts=1" in resp.headers["location"]
    # Nothing attached — the Admin picks in the UI.
    assert _google_accounts(api, connect_org, connect_org["client_one"]) == []


def test_google_single_account_callback_auto_attaches(api, connect_org, monkeypatch):
    """The solo-advertiser case keeps its one-click flow."""
    _patch_google_exchange(monkeypatch)
    monkeypatch.setattr(
        google_ads_api, "list_accessible_customers", lambda rt: ["3001"]
    )
    monkeypatch.setattr(
        google_ads_api,
        "fetch_customer_details",
        lambda rt, cid: {
            "external_id": cid,
            "name": "Solo Account",
            "currency": "USD",
            "timezone": None,
            "status": "ENABLED",
            "is_manager": False,
        },
    )
    state = create_state_token(
        "google_oauth", connect_org["org"], connect_org["client_two"]
    )
    resp = api.get(
        f"/api/connect/google/callback?code=x&state={state}",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "select_accounts" not in resp.headers["location"]
    accounts = _google_accounts(api, connect_org, connect_org["client_two"])
    assert [a["external_id"] for a in accounts] == ["3001"]


def test_google_callback_user_cancel_is_not_an_error_page_500(api, connect_org):
    """Backing out of the consent screen used to 422 (missing `code`); now it
    lands on a clear page."""
    state = create_state_token(
        "google_oauth", connect_org["org"], connect_org["client_one"]
    )
    resp = api.get(f"/api/connect/google/callback?state={state}&error=access_denied")
    assert resp.status_code == 200
    assert "canceled" in resp.text
    assert "connection failed" in resp.text


def test_meta_callback_platform_error_surfaces_cleanly(api, connect_org, monkeypatch):
    def _boom(code):
        raise meta_api.MetaApiError("redirect_uri does not match app settings")

    monkeypatch.setattr(meta_api, "exchange_code_for_token", _boom)
    state = create_state_token(
        "meta_oauth", connect_org["org"], connect_org["client_one"]
    )
    resp = api.get(f"/api/connect/meta/callback?code=x&state={state}")
    assert resp.status_code == 200  # a page, not a 500
    assert "connection failed" in resp.text
    assert "redirect_uri does not match" in resp.text


def test_meta_multi_account_callback_needs_selection(api, connect_org, monkeypatch):
    monkeypatch.setattr(
        meta_api, "exchange_code_for_token", lambda code: {"access_token": "s"}
    )
    monkeypatch.setattr(
        meta_api,
        "exchange_for_long_lived_token",
        lambda t: {"access_token": "ll", "expires_in": 5184000},
    )
    monkeypatch.setattr(meta_api, "fetch_me", lambda t: {"id": "fbuser1"})
    monkeypatch.setattr(
        meta_api,
        "fetch_ad_accounts",
        lambda t: [
            {"id": "act_9001", "name": "BM Client A", "currency": "USD"},
            {"id": "act_9002", "name": "BM Client B", "currency": "USD"},
        ],
    )
    state = create_state_token(
        "meta_oauth", connect_org["org"], connect_org["client_one"]
    )
    resp = api.get(
        f"/api/connect/meta/callback?code=x&state={state}", follow_redirects=False
    )
    assert resp.status_code in (302, 307)
    assert "select_accounts=1" in resp.headers["location"]
    r = api.get(
        f"/api/ad-accounts?client_id={connect_org['client_one']}",
        headers=connect_org["headers"],
    )
    assert [a for a in r.json() if a["platform"] == "meta"] == []


def test_callback_bad_state_still_400(api):
    assert api.get("/api/connect/google/callback?code=x&state=garbage").status_code == 400
    assert api.get("/api/connect/meta/callback?code=x&state=garbage").status_code == 400


# --- account picker -----------------------------------------------------------


@pytest.fixture()
def meta_roster(monkeypatch):
    """What Connect Co's Meta login can see: one new account, one that the
    picker tests attach to Client One, and Atlas Reach's act_111 (held by
    another Organization)."""
    monkeypatch.setattr(
        meta_api,
        "fetch_ad_accounts",
        lambda t: [
            {"id": "act_5001", "name": "Picker One", "currency": "USD"},
            {"id": "act_5002", "name": "Picker Two", "currency": "USD"},
            {"id": "act_111", "name": "Someone Elses", "currency": "USD"},
        ],
    )


def test_picker_lists_and_annotates(api, connect_org, meta_roster):
    h = connect_org["headers"]
    # Attach one of the roster to Client One first, through the real endpoint.
    r = api.post(
        "/api/connect/meta/accounts",
        json={"client_id": connect_org["client_one"], "external_ids": ["act_5001"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"attached": 1, "skipped": []}

    # Client Two's picker: sees the same roster, correctly annotated.
    r = api.get(
        f"/api/connect/meta/accounts?client_id={connect_org['client_two']}", headers=h
    )
    assert r.status_code == 200, r.text
    by_ext = {a["external_id"]: a for a in r.json()}
    assert by_ext["act_5001"]["attached"]["client_id"] == connect_org["client_one"]
    assert by_ext["act_5001"]["attached"]["client_name"] == "Client One"
    assert by_ext["act_5002"]["available"] is True
    assert by_ext["act_5002"]["attached"] is None
    # Another Organization's account: unavailable, and never named.
    assert by_ext["act_111"]["available"] is False
    assert by_ext["act_111"]["attached"] is None


def test_attach_is_idempotent_and_never_steals(api, connect_org, meta_roster):
    h = connect_org["headers"]
    # Re-attaching act_5001 (already on Client One) from Client Two skips it —
    # moving is the explicit reassign, never a side effect of attach.
    r = api.post(
        "/api/connect/meta/accounts",
        json={
            "client_id": connect_org["client_two"],
            "external_ids": ["act_5001", "act_5002"],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"attached": 1, "skipped": ["act_5001"]}
    one = api.get(
        f"/api/ad-accounts?client_id={connect_org['client_one']}", headers=h
    ).json()
    assert "act_5001" in [a["external_id"] for a in one]


def test_attach_cross_org_account_409(api, connect_org, meta_roster):
    r = api.post(
        "/api/connect/meta/accounts",
        json={"client_id": connect_org["client_one"], "external_ids": ["act_111"]},
        headers=connect_org["headers"],
    )
    assert r.status_code == 409
    # The other tenant is never named.
    assert "Atlas" not in r.text and "Alpha" not in r.text


def test_attach_invisible_account_400(api, connect_org, meta_roster):
    r = api.post(
        "/api/connect/meta/accounts",
        json={"client_id": connect_org["client_one"], "external_ids": ["act_404"]},
        headers=connect_org["headers"],
    )
    assert r.status_code == 400


def test_picker_admin_and_tenant_gates(api, connect_org, member_headers, team_headers):
    # Member role: no connection management.
    r = api.get(
        f"/api/connect/meta/accounts?client_id={connect_org['client_one']}",
        headers=member_headers,
    )
    assert r.status_code == 403
    # Another org's Admin: the client id 404s before anything leaks.
    r = api.get(
        f"/api/connect/meta/accounts?client_id={connect_org['client_one']}",
        headers=team_headers,
    )
    assert r.status_code == 404
    # Unknown platform namespace.
    r = api.get(
        f"/api/connect/tiktok/accounts?client_id={connect_org['client_one']}",
        headers=connect_org["headers"],
    )
    assert r.status_code == 404


def test_picker_requires_active_connection(api, connect_org, monkeypatch):
    # Flip Client Two's meta connection to disconnected → 409, not a crash.
    db = SessionLocal()
    conn = db.get(
        PlatformConnection, connect_org["conns"][("Client Two", "meta")]
    )
    conn.status = "disconnected"
    db.commit()
    try:
        r = api.get(
            f"/api/connect/meta/accounts?client_id={connect_org['client_two']}",
            headers=connect_org["headers"],
        )
        assert r.status_code == 409
    finally:
        conn.status = "active"
        db.commit()
        db.close()


# --- reassignment -------------------------------------------------------------


def _seed_google_hierarchy(connect_org):
    """A google account on Client One with a cached campaign→ad-group→ad chain,
    insight history at every level, quality snapshots (keyword + asset group),
    and one pending change — everything reassign must carry."""
    db = SessionLocal()
    org = connect_org["org"]
    acct = AdAccount(
        organization_id=org,
        client_id=connect_org["client_one"],
        connection_id=connect_org["conns"][("Client One", "google")],
        platform="google",
        external_id="7001",
        name="Movable Account",
    )
    db.add(acct)
    db.flush()
    camp = Campaign(
        organization_id=org,
        client_id=connect_org["client_one"],
        ad_account_id=acct.id,
        platform="google",
        external_id="c_7001",
        name="Move Campaign",
    )
    db.add(camp)
    db.flush()
    ag = AdGroup(
        organization_id=org,
        client_id=connect_org["client_one"],
        campaign_id=camp.id,
        platform="google",
        external_id="ag_7001",
        name="Move Ad Group",
    )
    db.add(ag)
    db.flush()
    ad = Ad(
        organization_id=org,
        client_id=connect_org["client_one"],
        ad_group_id=ag.id,
        platform="google",
        external_id="ad_7001",
        name="Move Ad",
    )
    db.add(ad)
    today = dt.date.today()
    for ext, etype in [
        ("7001", "account"),
        ("c_7001", "campaign"),
        ("ag_7001", "ad_group"),
        ("ad_7001", "ad"),
    ]:
        db.add(
            InsightDaily(
                organization_id=org,
                client_id=connect_org["client_one"],
                platform="google",
                entity_type=etype,
                entity_external_id=ext,
                date=today,
                impressions=10,
                clicks=1,
                spend_micros=1_000_000,
                conversions=0,
            )
        )
    db.add_all(
        [
            QualitySnapshot(
                organization_id=org,
                client_id=connect_org["client_one"],
                platform="google",
                entity_type="keyword",
                entity_external_id="ag_7001~55",
                metric="quality_score",
                value=7,
                date=today,
            ),
            QualitySnapshot(
                organization_id=org,
                client_id=connect_org["client_one"],
                platform="google",
                entity_type="asset_group",
                entity_external_id="asset_7001",
                metric="ad_strength",
                value=3,
                value_label="GOOD",
                date=today,
            ),
        ]
    )
    db.flush()
    # One change still awaiting confirmation, one already executed (history).
    common = dict(
        organization_id=org,
        client_id=connect_org["client_one"],
        platform="google",
        ad_account_id=acct.id,
        entity_type="campaign",
        entity_id=camp.id,
        action="pause",
        payload={"status": "PAUSED"},
        diff=[{"field": "status", "before": "ENABLED", "after": "PAUSED"}],
        expires_at=utcnow() + dt.timedelta(minutes=30),
    )
    db_user_id = None
    from app.models.core import User

    db_user_id = (
        db.query(User).filter(User.email == "owner@connectco.com").one().id
    )
    db.add_all(
        [
            PendingChange(created_by_user_id=db_user_id, status="pending", **common),
            PendingChange(created_by_user_id=db_user_id, status="executed", **common),
        ]
    )
    db.commit()
    ids = {"acct": acct.id, "camp": camp.id, "ag": ag.id, "ad": ad.id}
    db.close()
    return ids


def test_reassign_moves_account_and_everything_under_it(
    api, connect_org, monkeypatch
):
    ids = _seed_google_hierarchy(connect_org)
    # Asset groups aren't cached locally; reassign resolves them live.
    monkeypatch.setattr(
        google_ads_api,
        "fetch_asset_groups",
        lambda rt, cust, camp_ext: [
            {"external_id": "asset_7001", "name": "AG", "status": "ENABLED",
             "ad_strength": "GOOD", "final_urls": []}
        ],
    )
    r = api.patch(
        f"/api/ad-accounts/{ids['acct']}",
        json={"client_id": connect_org["client_two"]},
        headers=connect_org["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["moved"] is True
    assert body["cascade"]["campaigns"] == 1
    assert body["cascade"]["insights"] == 4
    assert body["cascade"]["quality_snapshots"] == 2  # keyword + asset group

    db = SessionLocal()
    try:
        new_client = connect_org["client_two"]
        assert db.get(AdAccount, ids["acct"]).client_id == new_client
        assert db.get(Campaign, ids["camp"]).client_id == new_client
        assert db.get(AdGroup, ids["ag"]).client_id == new_client
        assert db.get(Ad, ids["ad"]).client_id == new_client
        insights = (
            db.query(InsightDaily)
            .filter(InsightDaily.organization_id == connect_org["org"])
            .all()
        )
        assert {i.client_id for i in insights} == {new_client}
        snaps = (
            db.query(QualitySnapshot)
            .filter(QualitySnapshot.organization_id == connect_org["org"])
            .all()
        )
        assert {s.client_id for s in snaps} == {new_client}
        pendings = (
            db.query(PendingChange)
            .filter(PendingChange.ad_account_id == ids["acct"])
            .all()
        )
        by_status = {p.status: p.client_id for p in pendings}
        assert by_status["pending"] == new_client  # follows the account
        assert by_status["executed"] == connect_org["client_one"]  # history stays
    finally:
        db.close()

    # The move is in the audit trail (guardrail 8).
    log = api.get(
        "/api/audit-log?entity_type=ad_account&action=reassign",
        headers=connect_org["headers"],
    )
    assert log.status_code == 200
    entries = log.json()
    assert entries and entries[0]["entity_external_id"] == "7001"


def test_reassign_cross_org_404s_both_directions(
    api, connect_org, team_headers, seeded
):
    ids = _google_ids = api.get(
        f"/api/ad-accounts?client_id={connect_org['client_two']}",
        headers=connect_org["headers"],
    ).json()
    movable = next(a for a in ids if a["external_id"] == "7001")
    # Another org's Admin can't even see the account.
    r = api.patch(
        f"/api/ad-accounts/{movable['id']}",
        json={"client_id": seeded["client_a"]},
        headers=team_headers,
    )
    assert r.status_code == 404
    # And the owner can't move an account onto another org's client.
    r = api.patch(
        f"/api/ad-accounts/{movable['id']}",
        json={"client_id": seeded["client_a"]},
        headers=connect_org["headers"],
    )
    assert r.status_code == 404


def test_reassign_same_client_is_a_noop(api, connect_org):
    accounts = api.get(
        f"/api/ad-accounts?client_id={connect_org['client_two']}",
        headers=connect_org["headers"],
    ).json()
    movable = next(a for a in accounts if a["external_id"] == "7001")
    r = api.patch(
        f"/api/ad-accounts/{movable['id']}",
        json={"client_id": connect_org["client_two"]},
        headers=connect_org["headers"],
    )
    assert r.status_code == 200
    assert r.json()["moved"] is False
