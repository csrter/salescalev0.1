"""Native lead-form webhooks — Meta Instant Forms and Google Lead Form ads
(Phase 6 task 1). Payload shapes verified against live docs 2026-07-06; see
services/meta_leadgen.py for the Meta spec notes and the Google notes below.

Trust model (these are public, unauthenticated-by-JWT endpoints):
- Meta: one app-level endpoint for every tenant. Authenticity = the
  X-Hub-Signature-256 HMAC (app secret); tenant routing = the payload's
  page_id matched against a LeadFormConfig row — an unknown page is
  acknowledged and dropped, never guessed into a client.
- Google: per-client URL (Google Ads lets you set URL + key per form), and
  the body's google_key must equal that client's configured key. Google
  retries on 5xx and treats 4xx as non-retryable; a wrong key is 403.
- Both platforms redeliver, so ingestion is idempotent: the platform lead
  id is the contact's source_external_id and a retry updates rather than
  duplicates (services/lead_ingest.py).
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models.attribution import LandingEvent
from ..models.base import utcnow
from ..models.core import CONN_ACTIVE, Client, PlatformConnection
from ..models.crm import LeadFormConfig
from ..services import connections as conn_svc
from ..services import lead_ingest, meta_leadgen
from ..services.external_sync import push_contact_update

router = APIRouter(prefix="/api/webhooks", tags=["lead-webhooks"])


# --- Meta Instant Forms ---


@router.get("/meta/leadgen")
def meta_verify(request: Request):
    """Meta's one-time subscription handshake: echo hub.challenge back as
    plain text iff hub.verify_token matches ours."""
    params = request.query_params
    settings = get_settings()
    if (
        params.get("hub.mode") == "subscribe"
        and settings.meta_webhook_verify_token
        and params.get("hub.verify_token") == settings.meta_webhook_verify_token
    ):
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "Verification failed")


@router.post("/meta/leadgen")
async def meta_leadgen_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    settings = get_settings()
    if not meta_leadgen.verify_signature(
        settings.meta_app_secret, raw, request.headers.get("X-Hub-Signature-256")
    ):
        raise HTTPException(403, "Invalid signature")

    body = await request.json()
    results = []
    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "leadgen":
                continue
            value = change.get("value") or {}
            results.append(_ingest_meta_lead(db, value))
    db.commit()
    # Always 200 once the signature checks out — Meta redelivers on non-2xx
    # and an unroutable page_id won't become routable by retrying.
    return {"received": len(results), "results": results}


def _ingest_meta_lead(db: Session, value: dict) -> dict:
    leadgen_id = str(value.get("leadgen_id") or "")
    page_id = str(value.get("page_id") or "")
    if not leadgen_id or not page_id:
        return {"status": "ignored", "reason": "missing leadgen_id/page_id"}

    config = db.execute(
        select(LeadFormConfig).where(
            LeadFormConfig.platform == "meta",
            LeadFormConfig.external_key == page_id,
            LeadFormConfig.enabled.is_(True),
        )
    ).scalar_one_or_none()
    if config is None:
        return {"status": "ignored", "reason": "no client configured for page"}
    client = db.get(Client, config.client_id)

    conn = db.execute(
        select(PlatformConnection).where(
            PlatformConnection.client_id == client.id,
            PlatformConnection.platform == "meta",
            PlatformConnection.status == CONN_ACTIVE,
        )
    ).scalar_one_or_none()
    if conn is None:
        return {"status": "failed", "reason": "no active meta connection"}

    try:
        lead = meta_leadgen.fetch_lead(conn_svc.get_access_token(conn), leadgen_id)
    except Exception as e:  # keep one bad lead from failing the batch
        return {"status": "failed", "reason": str(e)}

    fields = meta_leadgen.parse_field_data(lead)
    contact, created = lead_ingest.upsert_contact(
        db,
        client,
        email=fields["email"],
        phone=fields["phone"],
        first_name=fields["first_name"],
        last_name=fields["last_name"],
        source="meta_instant_form",
        source_external_id=leadgen_id,
        # The ad linkage Meta sent — attribution for metrics comes from
        # contact.source (FORM_SOURCE_PLATFORM); an Instant Form lead never
        # touched a landing page, so there is no UTM/click-id trail to claim.
        source_detail={
            k: str(v)
            for k, v in {
                "page_id": page_id,
                "form_id": value.get("form_id") or lead.get("form_id"),
                "ad_id": value.get("ad_id") or lead.get("ad_id"),
                "adset_id": lead.get("adset_id"),
                "campaign_id": lead.get("campaign_id"),
            }.items()
            if v
        },
    )
    if created:
        push_contact_update(db, client, contact, event="lead.created")
    return {"status": "created" if created else "updated", "contact_id": contact.id}


# --- Google Lead Form ads ---
# Live-docs payload (developers.google.com/google-ads/webhook/docs):
# {lead_id, api_version, form_id, campaign_id, adgroup_id, creative_id,
#  gcl_id, google_key, is_test, user_column_data: [{column_id,
#  string_value, column_name}]}. Standard column_ids below; respond 200 {}
# on success, 4xx non-retryable, 5xx retryable. Dedupe on lead_id.

_GOOGLE_COLUMNS = {
    "EMAIL": "email",
    "WORK_EMAIL": "email",
    "PHONE_NUMBER": "phone",
    "WORK_PHONE": "phone",
    "FIRST_NAME": "first_name",
    "LAST_NAME": "last_name",
    "FULL_NAME": "full_name",
}


@router.post("/google/lead-form/{client_id}")
def google_lead_form_webhook(
    client_id: str,
    body: dict,
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    config = (
        db.execute(
            select(LeadFormConfig).where(
                LeadFormConfig.platform == "google",
                LeadFormConfig.client_id == client_id,
                LeadFormConfig.enabled.is_(True),
            )
        ).scalar_one_or_none()
        if client is not None
        else None
    )
    # One failure shape for unknown client / not configured / wrong key —
    # a public endpoint shouldn't teach a prober which part was wrong.
    if config is None or body.get("google_key") != config.external_key:
        raise HTTPException(403, "Invalid key")

    lead_id = str(body.get("lead_id") or "")
    if not lead_id:
        raise HTTPException(400, "Missing lead_id")
    if body.get("is_test"):
        # Google Ads "send test data" — acknowledge so the console shows
        # success, but never put fake leads in a client's CRM.
        return {"status": "test acknowledged"}

    fields = {"email": None, "phone": None, "first_name": None, "last_name": None}
    full_name = None
    for col in body.get("user_column_data") or []:
        key = _GOOGLE_COLUMNS.get(col.get("column_id") or "")
        value = col.get("string_value")
        if not key or not value:
            continue
        if key == "full_name":
            full_name = value
        elif fields[key] is None:
            fields[key] = value
    if full_name and not fields["first_name"]:
        parts = full_name.split(" ", 1)
        fields["first_name"] = parts[0]
        if len(parts) > 1:
            fields["last_name"] = fields["last_name"] or parts[1]

    contact, created = lead_ingest.upsert_contact(
        db,
        client,
        **fields,
        source="google_lead_form",
        source_external_id=lead_id,
        source_detail={
            k: str(v)
            for k, v in {
                "form_id": body.get("form_id"),
                "campaign_id": body.get("campaign_id"),
                "adgroup_id": body.get("adgroup_id"),
                "creative_id": body.get("creative_id"),
            }.items()
            if v
        },
    )

    # gcl_id is a real click id → this lead gets a first-class attribution
    # row, same capture layer as landing-page leads (Phase 1 rule).
    gclid = body.get("gcl_id")
    if created and gclid:
        db.add(
            LandingEvent(
                organization_id=client.organization_id,
                client_id=client.id,
                session_key=f"google-lead-form-{lead_id}",
                gclid=str(gclid),
                occurred_at=utcnow(),
                contact_id=contact.id,
            )
        )
    db.commit()
    if created:
        push_contact_update(db, client, contact, event="lead.created")
    return {}
