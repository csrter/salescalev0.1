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

import hmac
import json
import re
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from ..config import get_settings
from ..db import get_db
from ..models.attribution import LandingEvent
from ..models.base import utcnow
from ..models.core import CONN_ACTIVE, Client, PlatformConnection
from ..models.crm import Activity, LeadFormConfig
from ..services import connections as conn_svc
from ..services import crm as crm_svc
from ..services import custom_fields as custom_fields_svc
from ..ratelimit import rate_limit
from ..models.conversions import ConversionEvent
from ..services import integration_creds, lead_autoenroll, lead_ingest, lead_notify, meta_leadgen
from ..services.conversion_dispatch import dispatch_conversion
from ..services.external_sync import push_contact_update

router = APIRouter(prefix="/api/webhooks", tags=["lead-webhooks"])

# Public, signature/key-authenticated inbound — generous per-IP cap (real lead
# volume) that still bounds DoS/amplification.
_webhook_limit = rate_limit("lead_webhook", limit=120, window_seconds=60)
# Max page_ids an unsigned caller can make us resolve in the BYO-secret fallback.
_MAX_FALLBACK_PAGE_IDS = 20


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


def _verify_meta_signature(db: Session, raw: bytes, signature) -> bool:
    """Verify the leadgen webhook HMAC. Tries the operator's global app secret
    first, then — for tenants on a bring-your-own Meta app — the app secret of
    each org that owns a LeadFormConfig for a page_id named in the (still
    untrusted) payload. The body is only acted on once a signature passes."""
    settings = get_settings()
    if settings.meta_app_secret and meta_leadgen.verify_signature(
        settings.meta_app_secret, raw, signature
    ):
        return True
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        return False
    page_ids = {
        str((change.get("value") or {}).get("page_id") or "")
        for entry in (body.get("entry") or [])
        for change in (entry.get("changes") or [])
    }
    page_ids.discard("")
    if not page_ids:
        return False
    # Bound the work an unsigned caller can force: consider only a capped number
    # of page_ids, resolved in a single query instead of one-per-id.
    configs = (
        db.execute(
            select(LeadFormConfig).where(
                LeadFormConfig.platform == "meta",
                LeadFormConfig.external_key.in_(list(page_ids)[:_MAX_FALLBACK_PAGE_IDS]),
            )
        )
        .scalars()
        .all()
    )
    tried: set[str] = set()
    for config in configs:
        client = db.get(Client, config.client_id)
        if client is None:
            continue
        secret = integration_creds.resolve_meta(db, client.organization_id).app_secret
        if secret and secret not in tried:
            tried.add(secret)
            if meta_leadgen.verify_signature(secret, raw, signature):
                return True
    return False


@router.post("/meta/leadgen")
async def meta_leadgen_webhook(
    request: Request, db: Session = Depends(get_db), _: None = _webhook_limit
):
    raw = await request.body()
    if not _verify_meta_signature(db, raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "Invalid signature")

    body = await request.json()
    results = []
    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "leadgen":
                continue
            value = change.get("value") or {}
            # Per-lead isolation + commit: one malformed lead (or a transient
            # DB error on it) must neither 500 the whole batch — Meta would
            # redeliver everything — nor roll back the leads already ingested
            # before it.
            try:
                results.append(_ingest_meta_lead(db, value))
                db.commit()
            except Exception as e:
                db.rollback()
                results.append({"status": "failed", "reason": str(e)})
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
    if client is None:
        # Stale config: the client was deleted but its LeadFormConfig row
        # survived. Without this guard the whole webhook 500s and Meta
        # redelivers the batch forever.
        return {"status": "ignored", "reason": "stale config: client deleted"}

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
        lead_notify.notify_new_lead(db, client, contact)
        lead_autoenroll.auto_enroll_new_lead(db, client, contact)
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
# Not part of upsert_contact's core-identity kwargs, so kept out of `fields`
# (which gets **-spread into that call) — applied fill-blanks-only afterward,
# same as city/state/job_title on the other capture paths.
_GOOGLE_ZIP_COLUMNS = {"ZIP_CODE", "POSTAL_CODE"}


@router.post("/google/lead-form/{client_id}")
def google_lead_form_webhook(
    client_id: str,
    body: dict,
    db: Session = Depends(get_db),
    _: None = _webhook_limit,
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
    if config is None or not hmac.compare_digest(
        str(body.get("google_key") or ""), config.external_key
    ):
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
    zip_code = None
    for col in body.get("user_column_data") or []:
        column_id = col.get("column_id") or ""
        value = col.get("string_value")
        if not value:
            continue
        if column_id in _GOOGLE_ZIP_COLUMNS:
            zip_code = zip_code or value
            continue
        key = _GOOGLE_COLUMNS.get(column_id)
        if not key:
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
    if contact.zip is None and zip_code:
        contact.zip = zip_code

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
    if created:
        push_contact_update(db, client, contact, event="lead.created")
        lead_notify.notify_new_lead(db, client, contact)
        lead_autoenroll.auto_enroll_new_lead(db, client, contact)
    db.commit()
    return {}


# --- Generic landing-page form webhook ---
# For clients whose landing pages/form tools aren't Meta or Google's native
# lead ads (Webflow, WPForms, Elementor, Typeform, Zapier/Make catch-hooks, a
# plain HTML form posted via fetch/curl) — anything that can POST JSON or
# form-encoded data to a URL. There's no platform-run console to configure a
# shared secret in (unlike Google's google_key), so the secret is generated
# by Salescale and folded into the URL path (services/clients.py
# rotate_landing_page_webhook) — the one auth mechanism every such tool
# supports without needing custom headers.
#
# The client's landing page controls the field names it posts, so routing
# uses a normalized-synonym table (case/punctuation-insensitive) rather than
# a fixed schema — the same "meet the data where it is" posture as the CSV
# import's header auto-detect. Unrecognized fields are kept verbatim (capped)
# in source_detail for audit; a recognized "message" field is logged onto the
# contact's activity timeline as the visitor's own inquiry text.

_LANDING_FORM_SYNONYMS: dict = {}


def _lf_key(raw: str) -> str:
    return re.sub(r"[^a-z0-9]", "", raw.lower())


def _lf_register(target: str, *names: str) -> None:
    for name in names:
        _LANDING_FORM_SYNONYMS[_lf_key(name)] = target


_lf_register("email", "email", "e-mail", "email address", "work email")
_lf_register(
    "phone", "phone", "phone number", "telephone", "tel", "mobile", "cell", "cell phone"
)
_lf_register("first_name", "first name", "fname", "first", "given name")
_lf_register("last_name", "last name", "lname", "last", "surname", "family name")
_lf_register("full_name", "name", "full name", "your name", "contact name")
_lf_register("city", "city", "town")
_lf_register("state", "state", "province", "region")
_lf_register("zip", "zip", "zip code", "postal code", "postcode")
_lf_register(
    "company", "company", "company name", "business", "business name", "organization"
)
_lf_register("job_title", "job title", "title", "position", "role")
_lf_register(
    "message", "message", "comments", "comment", "notes", "details", "inquiry", "enquiry"
)
_lf_register(
    "landing_url", "landing url", "page url", "url", "source url", "referrer", "referer"
)
_lf_register("utm_source", "utm_source")
_lf_register("utm_medium", "utm_medium")
_lf_register("utm_campaign", "utm_campaign")
_lf_register("utm_content", "utm_content")
_lf_register("utm_term", "utm_term")
_lf_register("gclid", "gclid", "google click id")
_lf_register("fbclid", "fbclid", "facebook click id")
_lf_register("fbp", "fbp")

_LANDING_FORM_EXTRA_CAP = 25


def _map_landing_form_fields(raw: dict) -> dict:
    mapped: dict = {}
    extra: dict = {}
    for key, value in raw.items():
        if value is None or isinstance(value, (dict, list, UploadFile)):
            continue
        text = str(value).strip()
        if not text:
            continue
        target = _LANDING_FORM_SYNONYMS.get(_lf_key(str(key)))
        if target and target not in mapped:
            mapped[target] = text[:2000]
        elif len(extra) < _LANDING_FORM_EXTRA_CAP:
            extra[str(key)[:100]] = text[:500]
    if mapped.get("full_name") and not mapped.get("first_name"):
        parts = mapped["full_name"].split(None, 1)
        mapped["first_name"] = parts[0]
        if len(parts) > 1:
            mapped.setdefault("last_name", parts[1])
    mapped["extra"] = extra
    return mapped


def _resolve_custom_value_ci(definition, raw: str):
    """Case-insensitive option resolution for select/multi_select — a landing
    form sends whatever casing its own field uses ("glacier" vs "Glacier"),
    but coerce_value's own matching is exact-string. Other field types pass
    through unresolved for coerce_value to handle as usual."""
    if definition.field_type not in ("select", "multi_select"):
        return raw
    by_norm = {}
    for o in definition.options or []:
        by_norm[_lf_key(o["key"])] = o["key"]
        by_norm[_lf_key(o["label"])] = o["key"]
    if definition.field_type == "select":
        return by_norm.get(_lf_key(raw), raw)
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return [by_norm.get(_lf_key(p), p) for p in parts]


def _match_custom_fields(db: Session, organization_id: str, extra: dict) -> dict:
    """Any unmapped form field whose normalized name matches one of the org's
    own custom-field labels or keys (e.g. "Brand" -> a "Brand" select field)
    is ALSO captured there, in addition to landing in source_detail audit as
    usual (harmless duplication — source_detail is a raw-payload snapshot
    either way). Returns {def.key: raw_value} for services/custom_fields.
    validate_and_merge; an org that hasn't defined the field just gets none
    back, and a value that doesn't match one of the field's options fails
    open at the call site (never blocks lead capture)."""
    if not extra:
        return {}
    defs = custom_fields_svc.list_definitions(db, organization_id, "contact")
    by_norm = {}
    for d in defs:
        by_norm[_lf_key(d.label)] = d
        by_norm[_lf_key(d.key)] = d
    custom_incoming: dict = {}
    for raw_key, value in extra.items():
        definition = by_norm.get(_lf_key(raw_key))
        if definition is None:
            continue
        custom_incoming[definition.key] = _resolve_custom_value_ci(definition, value)
    return custom_incoming


def _dispatch_landing_form_conversion(event_id: str, lead: dict) -> None:
    """Background half of the landing-form webhook's conversion dispatch —
    its own session so the webhook response never waits on a platform API
    (form tools retry on slow webhooks, which would duplicate leads)."""
    from ..db import SessionLocal

    with SessionLocal() as bg_db:
        event = bg_db.get(ConversionEvent, event_id)
        if event is None:
            return
        try:
            dispatch_conversion(bg_db, event, lead)
            bg_db.commit()
        except Exception:  # per-platform errors are already isolated inside;
            bg_db.rollback()  # this guards the dispatch plumbing itself.


@router.post("/landing-form/{client_id}/{key}")
async def landing_form_webhook(
    client_id: str,
    key: str,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    _: None = _webhook_limit,
):
    client = db.get(Client, client_id)
    config = (
        db.execute(
            select(LeadFormConfig).where(
                LeadFormConfig.client_id == client_id,
                LeadFormConfig.platform == "landing_page",
                LeadFormConfig.enabled.is_(True),
            )
        ).scalar_one_or_none()
        if client is not None
        else None
    )
    # One failure shape whether the client id, key, or config is wrong — a
    # public URL-embedded-secret endpoint shouldn't reveal which part failed.
    if config is None or not hmac.compare_digest(key, config.external_key):
        raise HTTPException(404, "Not found")

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type or "x-www-form-urlencoded" in content_type:
        form = await request.form()
        raw = {k: v for k, v in form.multi_items()}
    else:
        try:
            raw = await request.json()
        except (ValueError, json.JSONDecodeError):
            raise HTTPException(400, "Send form fields as JSON or form-encoded data")
    if not isinstance(raw, dict):
        raise HTTPException(400, "Expected an object of form fields")

    fields = _map_landing_form_fields(raw)
    if not fields.get("email") and not fields.get("phone"):
        raise HTTPException(
            400, "No recognized email or phone field in the submitted data"
        )
    custom_incoming = _match_custom_fields(db, client.organization_id, fields["extra"])

    contact, created = lead_ingest.upsert_contact(
        db,
        client,
        email=fields.get("email"),
        phone=fields.get("phone"),
        first_name=fields.get("first_name"),
        last_name=fields.get("last_name"),
        source="landing_page_webhook",
        source_detail=fields["extra"] or None,
    )
    if custom_incoming:
        try:
            custom_fields_svc.validate_and_merge(
                db, client.organization_id, contact, custom_incoming, enforce_required=False
            )
        except custom_fields_svc.CustomFieldError:
            # A value that doesn't match the field's options (or any other
            # coercion failure) must never block lead capture — the raw text
            # is still preserved in source_detail regardless.
            pass
    for attr in ("city", "state", "job_title", "zip"):
        if getattr(contact, attr) is None and fields.get(attr):
            setattr(contact, attr, fields[attr])
    if fields.get("company") and contact.company_id is None:
        contact.company_id = crm_svc.get_or_create_company(
            db, client.organization_id, client.id, fields["company"]
        )

    # Attribution parity with the Google lead-form webhook: any click id/UTM
    # in the payload gets a first-class LandingEvent row, same capture layer
    # as JS-tracked landing pages.
    landing = None
    if created and any(
        fields.get(k)
        for k in ("utm_source", "utm_medium", "utm_campaign", "gclid", "fbclid")
    ):
        landing = LandingEvent(
            organization_id=client.organization_id,
            client_id=client.id,
            session_key=f"landing-webhook-{contact.id}",
            landing_url=fields.get("landing_url"),
            utm_source=fields.get("utm_source"),
            utm_medium=fields.get("utm_medium"),
            utm_campaign=fields.get("utm_campaign"),
            utm_content=fields.get("utm_content"),
            utm_term=fields.get("utm_term"),
            gclid=fields.get("gclid"),
            fbclid=fields.get("fbclid"),
            fbp=fields.get("fbp"),
            occurred_at=utcnow(),
            contact_id=contact.id,
        )
        db.add(landing)
        db.flush()
    if created and fields.get("message"):
        db.add(
            Activity(
                organization_id=client.organization_id,
                client_id=client.id,
                contact_id=contact.id,
                type="note",
                body=f"Landing-page form submission:\n\n{fields['message']}",
                occurred_at=utcnow(),
            )
        )
    if created:
        push_contact_update(db, client, contact, event="lead.created")
        lead_notify.notify_new_lead(db, client, contact)
        lead_autoenroll.auto_enroll_new_lead(db, client, contact)

    # Server-side conversion tracking (Phase 5): unlike the Meta/Google
    # NATIVE lead-form webhooks — where the platform already counts its own
    # form's conversion and a CAPI/upload would double-count — a third-party
    # landing-page form is invisible to the platforms, so this is exactly
    # the path server-side upload exists for. One ConversionEvent per
    # submission (parity with /api/track/lead), dispatched in the background
    # so a slow platform API can't stall the form tool's webhook into a
    # retry (which would duplicate the lead).
    event = ConversionEvent(
        organization_id=client.organization_id,
        client_id=client.id,
        contact_id=contact.id,
        landing_event_id=landing.id if landing is not None else None,
        event_name="Lead",
        event_id=str(uuid.uuid4()),
        event_source_url=fields.get("landing_url"),
        occurred_at=utcnow(),
    )
    db.add(event)
    db.commit()
    background.add_task(
        _dispatch_landing_form_conversion,
        event.id,
        {
            "email": fields.get("email"),
            "phone": fields.get("phone"),
            "first_name": fields.get("first_name"),
            "last_name": fields.get("last_name"),
            "city": fields.get("city"),
            "state": fields.get("state"),
            "zip": fields.get("zip"),
            "fbp": fields.get("fbp"),
            # Deliberately no client_ip/user_agent: this request comes from
            # the form tool's server, not the lead's browser.
        },
    )
    return {"status": "created" if created else "updated", "contact_id": contact.id}
