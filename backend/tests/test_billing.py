"""Subscription tier enforcement + Stripe webhook sync.

Tier limits are enforced server-side (not just hidden in the UI). Stripe
itself isn't configured in tests, so the webhook *handler* is exercised
directly and the money-path endpoints must fail closed (503).
"""
import pytest

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


def test_starter_client_limit_enforced(api, biz):
    h = biz["headers"]  # starter allows 5 clients; org starts with 0
    for i in range(5):
        assert api.post("/api/clients", headers=h, json={"name": f"C{i}"}).status_code == 201
    r = api.post("/api/clients", headers=h, json={"name": "C6"})
    assert r.status_code == 402
    assert "upgrade" in r.json()["detail"].lower()


def test_starter_seat_limit_enforced(api):
    h = {"Authorization": f"Bearer {_signup(api, 'Seat Co', 'seat@seatco.com')['access_token']}"}
    # owner is seat #1; starter allows 5 → 4 more members, then blocked
    for i in range(4):
        assert api.post(
            "/api/orgs/me/members",
            headers=h,
            json={"email": f"m{i}@seatco.com", "password": "member-pass-1", "full_name": f"M{i}", "role": "member"},
        ).status_code == 201
    r = api.post(
        "/api/orgs/me/members",
        headers=h,
        json={"email": "m5@seatco.com", "password": "member-pass-1", "full_name": "M5", "role": "member"},
    )
    assert r.status_code == 402


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
        "clients", "seats", "custom_fields", "research_fields",
        "lead_finder_searches", "email_verifications", "email_sends",
        "sms_sends",
    }
    for m in body["meters"]:
        assert isinstance(m["used"], int)
        assert m["limit"] is None or isinstance(m["limit"], int)
    clients = next(m for m in body["meters"] if m["key"] == "clients")
    assert clients["used"] >= 1  # biz created a client in its fixture
