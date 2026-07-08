"""Phase 8 — Stripe subscription billing.

Checkout starts a subscription; the webhook is the source of truth that syncs
the org's plan/status (Checkout Sessions only start subscriptions — plan
changes and cancellations come back through webhooks / the customer portal).

Stripe is imported lazily so the package is only required where billing is
actually configured (e.g. the hosted deployment) — the desktop build doesn't
need it. When `STRIPE_SECRET_KEY` is unset every endpoint returns 503.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import require_owner, require_team
from ..models.core import Organization, User
from ..schemas import CheckoutRequest, CheckoutSessionOut, SubscriptionOut

router = APIRouter(prefix="/api/billing", tags=["billing"])
log = logging.getLogger("salescale.billing")


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


def apply_subscription_event(db: Session, event: dict) -> None:
    """Sync org billing state from a Stripe event. Pure/DB-only (no network),
    so it's unit-testable without live Stripe signatures."""
    etype = event.get("type")
    obj = event.get("data", {}).get("object", {})
    settings = get_settings()

    if etype == "checkout.session.completed":
        org_id = (obj.get("metadata") or {}).get("organization_id")
        plan = (obj.get("metadata") or {}).get("plan")
        org = db.get(Organization, org_id) if org_id else None
        if org:
            org.stripe_customer_id = obj.get("customer") or org.stripe_customer_id
            org.stripe_subscription_id = obj.get("subscription")
            if plan:
                org.plan = plan
            org.subscription_status = "active"
            db.commit()

    elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        customer_id = obj.get("customer")
        org = (
            db.query(Organization)
            .filter(Organization.stripe_customer_id == customer_id)
            .one_or_none()
            if customer_id
            else None
        )
        if org:
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
            db.commit()


@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
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
