"""Subscription tier enforcement + Stripe webhook sync.

Tier limits are enforced server-side (not just hidden in the UI). Stripe
itself isn't configured in tests, so the webhook *handler* is exercised
directly and the money-path endpoints must fail closed (503).
"""
import pytest
from fastapi import HTTPException

from app.config import get_settings

from app.api.billing import apply_subscription_event
from app.db import SessionLocal
from app.models.core import Organization

PW = "billing-pass-123"


def _signup(api, org, email):
    r = api.post(
        "/api/orgs/signup",
        json={"organization_name": org, "email": email, "password": PW, "full_name": "B"},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def biz(api):
    b = _signup(api, "Billing Co", "billing@billingco.com")
    return {"org_id": b["organization_id"], "headers": {"Authorization": f"Bearer {b['access_token']}"}}


def _set_plan(org_id, plan):
    """Tier-limit tests declare their tier explicitly — signup lands on
    DEFAULT_SIGNUP_PLAN (agency in tests), not starter."""
    db = SessionLocal()
    db.get(Organization, org_id).plan = plan
    db.commit()
    db.close()


def test_starter_client_limit_enforced(api, biz):
    _set_plan(biz["org_id"], "starter")
    h = biz["headers"]  # starter allows 5 clients; org starts with 0
    for i in range(5):
        assert api.post("/api/clients", headers=h, json={"name": f"C{i}"}).status_code == 201
    r = api.post("/api/clients", headers=h, json={"name": "C6"})
    assert r.status_code == 402
    assert "upgrade" in r.json()["detail"].lower()


def test_starter_seat_limit_enforced(api):
    seat = _signup(api, 'Seat Co', 'seat@seatco.com')
    h = {"Authorization": f"Bearer {seat['access_token']}"}
    _set_plan(seat["organization_id"], "starter")
    # Starter is a SINGLE-USER plan (Stripe copy): the owner is the one seat,
    # so the very first added member is blocked.
    r = api.post(
        "/api/orgs/me/members",
        headers=h,
        json={"email": "m0@seatco.com", "password": "member-pass-1", "full_name": "M0", "role": "member"},
    )
    assert r.status_code == 402

    # Pro lifts it.
    _set_plan(seat["organization_id"], "pro")
    assert api.post(
        "/api/orgs/me/members",
        headers=h,
        json={"email": "m0@seatco.com", "password": "member-pass-1", "full_name": "M0", "role": "member"},
    ).status_code == 201


def test_billing_endpoints_fail_closed_without_stripe(api, biz):
    # STRIPE_SECRET_KEY is unset in tests → money-path endpoints 503.
    assert api.post("/api/billing/checkout", headers=biz["headers"], json={"plan": "pro"}).status_code == 503
    assert api.post("/api/billing/portal", headers=biz["headers"]).status_code == 503
    # read-only status still works and reports billing disabled
    sub = api.get("/api/billing/subscription", headers=biz["headers"])
    assert sub.status_code == 200 and sub.json()["billing_enabled"] is False


def test_agency_plan_lifts_client_limit(api, biz):
    db = SessionLocal()
    db.get(Organization, biz["org_id"]).plan = "agency"
    db.commit()
    db.close()
    # the previously-blocked 6th client now succeeds (unlimited)
    assert api.post("/api/clients", headers=biz["headers"], json={"name": "C6-agency"}).status_code == 201


def test_webhook_checkout_completed_activates_plan(api):
    org_id = _signup(api, "Hook Co", "hook@hookco.com")["organization_id"]
    db = SessionLocal()
    apply_subscription_event(
        db,
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "metadata": {"organization_id": org_id, "plan": "pro"},
                    "customer": "cus_test123",
                    "subscription": "sub_test123",
                }
            },
        },
    )
    db.close()
    db2 = SessionLocal()
    org = db2.get(Organization, org_id)
    assert org.plan == "pro"
    assert org.subscription_status == "active"
    assert org.stripe_customer_id == "cus_test123"
    db2.close()


def _sub_event(eid, created, status):
    return {
        "id": eid,
        "created": created,
        "type": "customer.subscription.updated",
        "data": {"object": {"customer": "cus_idem1", "status": status}},
    }


def test_webhook_is_idempotent_and_ordered(api):
    org_id = _signup(api, "Idem Co", "idem@idemco.com")["organization_id"]
    db = SessionLocal()
    org = db.get(Organization, org_id)
    org.stripe_customer_id = "cus_idem1"
    org.plan = "pro"
    org.subscription_status = "active"
    db.commit()
    db.close()

    # newer event (t=200) cancels -> starter
    db = SessionLocal()
    apply_subscription_event(db, _sub_event("evt_2", 200, "canceled"))
    db.close()
    db = SessionLocal()
    assert db.get(Organization, org_id).plan == "starter"
    db.close()

    # a STALE event (t=100) arriving late must NOT regress the plan
    db = SessionLocal()
    apply_subscription_event(db, _sub_event("evt_1", 100, "active"))
    db.close()
    db = SessionLocal()
    assert db.get(Organization, org_id).subscription_status == "canceled"
    db.close()

    # replaying evt_2 (same id) is a no-op even after a later manual change
    db = SessionLocal()
    db.get(Organization, org_id).plan = "pro"
    db.commit()
    db.close()
    db = SessionLocal()
    apply_subscription_event(db, _sub_event("evt_2", 200, "canceled"))
    db.close()
    db = SessionLocal()
    assert db.get(Organization, org_id).plan == "pro"  # dedup skipped re-cancel
    db.close()


def test_webhook_subscription_deleted_downgrades_to_starter(api):
    org_id = _signup(api, "Cancel Co", "cancel@cancelco.com")["organization_id"]
    db = SessionLocal()
    org = db.get(Organization, org_id)
    org.plan = "pro"
    org.stripe_customer_id = "cus_cancel1"
    org.subscription_status = "active"
    db.commit()
    db.close()

    db = SessionLocal()
    apply_subscription_event(
        db,
        {
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_cancel1", "status": "canceled"}},
        },
    )
    db.close()

    db2 = SessionLocal()
    org = db2.get(Organization, org_id)
    assert org.plan == "starter"
    assert org.subscription_status == "canceled"
    db2.close()


def test_default_signup_plan_lever(api, monkeypatch):
    """DEFAULT_SIGNUP_PLAN (the beta lever) sets a new org's plan; unknown
    values fall back to starter instead of minting an unbilled tier."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "default_signup_plan", "agency")
    org_id = _signup(api, "Beta Plan Co", "owner@betaplanco.com")["organization_id"]
    db = SessionLocal()
    assert db.get(Organization, org_id).plan == "agency"
    db.close()

    monkeypatch.setattr(get_settings(), "default_signup_plan", "vip-nonsense")
    org_id = _signup(api, "Typo Plan Co", "owner@typoplanco.com")["organization_id"]
    db = SessionLocal()
    assert db.get(Organization, org_id).plan == "starter"
    db.close()


def test_usage_endpoint_reports_all_meters(api, biz):
    r = api.get("/api/billing/usage", headers=biz["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    keys = {m["key"] for m in body["meters"]}
    assert keys == {
        "clients", "seats", "meta_ad_accounts", "google_ad_accounts",
        "custom_fields", "research_fields",
        "lead_finder_searches", "email_verifications", "email_sends",
        "sms_sends",
    }
    for m in body["meters"]:
        assert isinstance(m["used"], int)
        assert m["limit"] is None or isinstance(m["limit"], int)
    clients = next(m for m in body["meters"] if m["key"] == "clients")
    assert clients["used"] >= 1  # biz created a client in its fixture


# --- tier gating (the entitlement flip) -------------------------------------


def test_white_label_and_ai_are_pro_and_agency_only(api, biz):
    """Stripe's pricing page markets white-labeling from Pro up; AI is gated
    the same way by operator decision. Starter gets neither."""
    from app.services import entitlements

    db = SessionLocal()
    org = db.get(Organization, biz["org_id"])
    for plan, allowed in (("starter", False), ("pro", True), ("agency", True)):
        org.plan = plan
        assert entitlements.can_use_white_labeling(org) is allowed, plan
        assert entitlements.can_use_ai_insights(org) is allowed, plan
    org.plan = "starter"
    db.commit()
    db.close()

    # The gate is real over HTTP: a branding change 403s on starter…
    r = api.put(
        "/api/orgs/me/branding",
        json={"product_name": "Starter Brand"},
        headers=biz["headers"],
    )
    assert r.status_code == 403
    # …but the CAN-SPAM mailing address stays writable on every tier.
    r = api.put(
        "/api/orgs/me/branding",
        json={"mailing_address": "1 Compliance Rd, Phoenix AZ"},
        headers=biz["headers"],
    )
    assert r.status_code == 200
    _set_plan(biz["org_id"], "agency")  # restore for any later test


def test_ad_accounts_per_platform_cap(api):
    """The pricing page's headline limit: 1 / 5 / unlimited accounts per
    platform, enforced where AdAccount rows are actually created."""
    from app.models.core import Client, PlatformConnection
    from app.services import ad_accounts, entitlements

    signed = _signup(api, "Cap Co", "owner@capco.com")
    org_id = signed["organization_id"]
    _set_plan(org_id, "starter")

    db = SessionLocal()
    client = Client(organization_id=org_id, name="Cap Client")
    db.add(client)
    db.flush()
    conn = PlatformConnection(
        organization_id=org_id, client_id=client.id, platform="meta", status="active"
    )
    db.add(conn)
    db.flush()

    def _attach(ext, c=None):
        return ad_accounts.attach(
            db, org_id, client.id, c or conn, {"external_id": ext, "name": ext}
        )

    assert _attach("act_cap_1") is not None  # starter: the one allowed account
    db.commit()
    with pytest.raises(HTTPException) as exc:
        _attach("act_cap_2")
    assert exc.value.status_code == 402
    assert "starter plan allows 1 meta ad account" in exc.value.detail

    # Google is a SEPARATE bucket — the cap is per platform, not total.
    gconn = PlatformConnection(
        organization_id=org_id, client_id=client.id, platform="google", status="active"
    )
    db.add(gconn)
    db.flush()
    assert _attach("goo_1", gconn) is not None
    db.commit()

    # Upgrading lifts it immediately; usage reporting agrees.
    db.get(Organization, org_id).plan = "pro"
    db.commit()
    org = db.get(Organization, org_id)
    assert entitlements.ad_account_usage(db, org, "meta") == {"used": 1, "limit": 5}
    assert _attach("act_cap_2") is not None
    db.commit()

    # Re-attaching an existing account is a no-op, never a 402 at the cap.
    db.get(Organization, org_id).plan = "starter"
    db.commit()
    assert _attach("act_cap_1") is None
    db.close()


def test_price_ids_map_both_intervals_to_one_plan(monkeypatch):
    """Monthly and annual prices resolve to the SAME plan, so an org
    switching interval keeps its tier when the webhook lands."""
    s = get_settings()
    monkeypatch.setattr(s, "stripe_price_pro", "price_pro_m")
    monkeypatch.setattr(s, "stripe_price_pro_yearly", "price_pro_y")
    monkeypatch.setattr(s, "stripe_price_starter", "price_starter_m")

    assert s.stripe_price_for_plan("pro", "month") == "price_pro_m"
    assert s.stripe_price_for_plan("pro", "year") == "price_pro_y"
    assert s.plan_for_stripe_price("price_pro_m") == "pro"
    assert s.plan_for_stripe_price("price_pro_y") == "pro"
    assert s.plan_for_stripe_price("price_starter_m") == "starter"
    assert s.plan_for_stripe_price("price_unknown") is None


def _fake_stripe(modified, sessions_created, current_price):
    class _FakeStripe:
        class Subscription:
            @staticmethod
            def retrieve(sid):
                return {
                    "items": {"data": [{"id": "si_1", "price": {"id": current_price}}]}
                }

            @staticmethod
            def modify(sid, **kw):
                modified.append((sid, kw))

        class checkout:
            class Session:
                @staticmethod
                def create(**kw):
                    sessions_created.append(kw)
                    return type("S", (), {"url": "https://checkout.example"})

        class billing_portal:
            class Session:
                @staticmethod
                def create(**kw):
                    # Stripe returns an object, not a dict — the code reads
                    # .url, so the double must too.
                    return type("S", (), {"url": "https://portal.example"})

        class Customer:
            @staticmethod
            def create(**kw):
                return type("C", (), {"id": "cus_new_fake"})

    return _FakeStripe


def test_existing_subscriber_changes_plan_instead_of_buying_a_second(api, monkeypatch):
    """A subscribed org hitting /checkout must MODIFY its subscription — a
    second Checkout session would double-bill it."""
    from app.api import billing as billing_api

    signed = _signup(api, "Switch Co", "owner@switchco.com")
    org_id = signed["organization_id"]
    db = SessionLocal()
    org = db.get(Organization, org_id)
    org.plan = "starter"
    org.stripe_customer_id = "cus_switch1"
    org.stripe_subscription_id = "sub_switch1"
    org.subscription_status = "active"
    db.commit()
    db.close()

    s = get_settings()
    monkeypatch.setattr(s, "stripe_price_starter", "price_starter_m")
    monkeypatch.setattr(s, "stripe_price_agency", "price_agency_m")
    h = {"Authorization": f"Bearer {signed['access_token']}"}

    modified, sessions_created = [], []
    monkeypatch.setattr(
        billing_api,
        "_stripe",
        lambda: _fake_stripe(modified, sessions_created, "price_starter_m"),
    )
    r = api.post(
        "/api/billing/checkout", json={"plan": "agency", "interval": "month"}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "https://portal.example"
    assert not sessions_created, "must not create a second subscription"
    assert len(modified) == 1
    sid, kw = modified[0]
    assert sid == "sub_switch1"
    assert kw["items"] == [{"id": "si_1", "price": "price_agency_m"}]
    # starter -> agency is an upgrade: bill now, prorated.
    assert kw["proration_behavior"] == "always_invoice"

    # The reverse is a downgrade — no proration, applies at period end.
    modified.clear()
    db = SessionLocal()
    db.get(Organization, org_id).plan = "agency"
    db.commit()
    db.close()
    monkeypatch.setattr(
        billing_api,
        "_stripe",
        lambda: _fake_stripe(modified, sessions_created, "price_agency_m"),
    )
    r = api.post(
        "/api/billing/checkout", json={"plan": "starter", "interval": "month"}, headers=h
    )
    assert r.status_code == 200, r.text
    assert modified[0][1]["proration_behavior"] == "none"
    assert not sessions_created


def test_unsubscribed_org_still_gets_a_checkout_session(api, monkeypatch):
    """The first purchase must still go through Checkout (nothing to modify)."""
    from app.api import billing as billing_api

    signed = _signup(api, "First Buy Co", "owner@firstbuy.com")
    s = get_settings()
    monkeypatch.setattr(s, "stripe_price_pro_yearly", "price_pro_y")
    modified, sessions_created = [], []
    monkeypatch.setattr(
        billing_api, "_stripe", lambda: _fake_stripe(modified, sessions_created, "")
    )
    r = api.post(
        "/api/billing/checkout",
        json={"plan": "pro", "interval": "year"},
        headers={"Authorization": f"Bearer {signed['access_token']}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "https://checkout.example"
    assert not modified
    assert sessions_created[0]["line_items"] == [{"price": "price_pro_y", "quantity": 1}]
