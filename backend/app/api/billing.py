"""Phase 8 — Stripe subscription billing.

Checkout starts a subscription; the webhook is the source of truth that syncs
the org's plan/status (Checkout Sessions only start subscriptions — plan
changes and cancellations come back through webhooks / the customer portal).

Stripe is imported lazily so the package is only required where billing is
actually configured (e.g. the hosted deployment) — the desktop build doesn't
need it. When `STRIPE_SECRET_KEY` is unset every endpoint returns 503.
"""
import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import require_owner, require_team
from ..models.base import utcnow
from ..models.core import Organization, ProcessedStripeEvent, User
from ..ratelimit import rate_limit
from ..schemas import CheckoutRequest, CheckoutSessionOut, SubscriptionOut
from ..services import entitlements

router = APIRouter(prefix="/api/billing", tags=["billing"])
log = logging.getLogger("salescale.billing")

# Signature-protected, but still cap it per IP (DoS).
_webhook_limit = rate_limit("stripe_webhook", limit=120, window_seconds=60)


def _stripe():
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Billing is not configured")
    import stripe  # lazy — see module docstring

    stripe.api_key = settings.stripe_secret_key
    return stripe


@router.get("/subscription", response_model=SubscriptionOut)
def get_subscription(user: User = Depends(require_team), db: Session = Depends(get_db)):
    org = db.get(Organization, user.organization_id)
    return SubscriptionOut(
        plan=org.plan,
        status=org.subscription_status,
        billing_enabled=bool(get_settings().stripe_secret_key),
    )


@router.get("/usage")
def get_usage(user: User = Depends(require_team), db: Session = Depends(get_db)):
    """Every metered thing in one place — "X of Y used" self-service
    visibility (standing guardrail #5) so an org sees a cap coming instead
    of hitting a raw 402. limit=null means unlimited on this plan."""
    org = db.get(Organization, user.organization_id)
    meters = [
        ("clients", "Clients", entitlements.client_usage(db, org)),
        ("seats", "Team seats", entitlements.seat_usage(db, org)),
        ("custom_fields", "Custom CRM fields", entitlements.custom_field_usage(db, org)),
        ("research_fields", "AI research fields", entitlements.research_field_usage(db, org)),
        ("lead_finder_searches", "Lead Finder searches (month)", entitlements.lead_finder_usage(db, org)),
        ("email_verifications", "Email verifications (month)", entitlements.email_verification_usage(db, org)),
        ("email_sends", "Cold-email sends (month)", entitlements.email_outreach_usage(db, org)),
        ("sms_sends", "SMS sends (month)", entitlements.sms_outreach_usage(db, org)),
    ]
    return {
        "plan": org.plan,
        "meters": [
            {"key": key, "label": label, "used": u["used"], "limit": u["limit"]}
            for key, label, u in meters
        ],
    }


@router.post("/checkout", response_model=CheckoutSessionOut)
def create_checkout(
    body: CheckoutRequest,
    user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    stripe = _stripe()
    price_id = settings.stripe_price_for_plan(body.plan)
    if not price_id:
        raise HTTPException(400, f"No Stripe price configured for plan '{body.plan}'")
    org = db.get(Organization, user.organization_id)

    customer_id = org.stripe_customer_id
    if not customer_id:
        customer = stripe.Customer.create(
            email=user.email,
            name=org.name,
            metadata={"organization_id": org.id},
        )
        customer_id = customer.id
        org.stripe_customer_id = customer_id
        db.commit()

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{settings.app_base_url}/?billing=success",
        cancel_url=f"{settings.app_base_url}/?billing=cancelled",
        metadata={"organization_id": org.id, "plan": body.plan},
        subscription_data={"metadata": {"organization_id": org.id, "plan": body.plan}},
    )
    return CheckoutSessionOut(url=session.url)


@router.post("/portal", response_model=CheckoutSessionOut)
def create_portal(user: User = Depends(require_owner), db: Session = Depends(get_db)):
    settings = get_settings()
    stripe = _stripe()
    org = db.get(Organization, user.organization_id)
    if not org.stripe_customer_id:
        raise HTTPException(400, "No billing account yet — start a subscription first")
    session = stripe.billing_portal.Session.create(
        customer=org.stripe_customer_id,
        return_url=f"{settings.app_base_url}/?billing=portal_return",
    )
    return CheckoutSessionOut(url=session.url)


def _event_time(event: dict) -> dt.datetime:
    created = event.get("created")
    if isinstance(created, (int, float)):
        return dt.datetime.fromtimestamp(created, tz=dt.timezone.utc)
    return utcnow()


def _is_newer(org: Organization, when: dt.datetime) -> bool:
    """Is `when` newer than the last applied subscription event for this org?
    (Guards against an out-of-order/replayed event regressing state.)"""
    last = org.subscription_event_at
    if last is None:
        return True
    if last.tzinfo is None:  # SQLite returns naive
        last = last.replace(tzinfo=dt.timezone.utc)
    return when > last


def apply_subscription_event(db: Session, event: dict) -> None:
    """Sync org billing state from a Stripe event. Pure/DB-only (no network),
    so it's unit-testable without live Stripe signatures. Idempotent by event id
    and ordered by the event's created time."""
    event_id = event.get("id")
    if event_id and db.get(ProcessedStripeEvent, event_id) is not None:
        return  # already handled — retry/replay is a no-op
    etype = event.get("type")
    obj = event.get("data", {}).get("object", {})
    settings = get_settings()
    when = _event_time(event)

    if etype == "checkout.session.completed":
        # The linking event (carries org_id + plan) — always applied so the
        # customer↔org mapping is established regardless of event ordering.
        org_id = (obj.get("metadata") or {}).get("organization_id")
        plan = (obj.get("metadata") or {}).get("plan")
        org = db.get(Organization, org_id) if org_id else None
        if org:
            org.stripe_customer_id = obj.get("customer") or org.stripe_customer_id
            org.stripe_subscription_id = obj.get("subscription")
            if plan:
                org.plan = plan
            org.subscription_status = "active"
            org.subscription_event_at = when

    elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        customer_id = obj.get("customer")
        org = (
            db.query(Organization)
            .filter(Organization.stripe_customer_id == customer_id)
            .one_or_none()
            if customer_id
            else None
        )
        # Skip a subscription event older than the last one we applied.
        if org and _is_newer(org, when):
            org.subscription_status = obj.get("status")
            # Map the active price back to a plan; on cancel, drop to starter.
            if etype == "customer.subscription.deleted" or obj.get("status") in (
                "canceled",
                "unpaid",
            ):
                org.plan = "starter"
            else:
                items = (obj.get("items") or {}).get("data") or []
                price_id = items[0]["price"]["id"] if items else None
                mapped = settings.plan_for_stripe_price(price_id) if price_id else None
                if mapped:
                    org.plan = mapped
            org.subscription_event_at = when

    if event_id:
        db.add(ProcessedStripeEvent(id=event_id))
    db.commit()


@router.post("/webhook")
async def webhook(
    request: Request, db: Session = Depends(get_db), _: None = _webhook_limit
):
    settings = get_settings()
    stripe = _stripe()
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(
            payload, sig, settings.stripe_webhook_secret
        )
    except Exception as e:  # signature/parse failure
        raise HTTPException(400, f"Invalid webhook: {e}")
    apply_subscription_event(db, event)
    return {"received": True}
