"""AI research fields ("Claygent-lite") — org-defined research prompts
answered per-contact by the AI provider, grounded ONLY in:
(a) the contact/company's own CRM + enrichment facts (the same set
    email_personalize._company_facts/generate_ai_snippet use, plus the org's
    outreach_context when set), and
(b) plain text fetched from the contact's/company's OWN website
    (enrichment.fetch_site_text — the same polite-crawler posture as Lead
    Finder enrichment: robots.txt honored, honest USER_AGENT, the
    lead_finder_crawl_enabled kill switch, homepage + /about only).

Guardrail 6 holds: no Meta-surface scraping anywhere in this pipeline.

Each answer lands on Contact.research[def.key] = {"value", "confidence",
"source_url", "researched_at"}. A def already answered on a contact is
skipped unless force=True. One AI call per MISSING field; the contact's site
text is fetched once per run and reused across all of its fields. Every call
is metered (AiUsage feature="outreach_research") and gated on the org's
monthly AI allowance (ai_insights.check_allowance) — same rule as email/SMS
personalization. Fail-open per field: any exception (network, model, parse)
leaves that field empty and logs; it never raises out of run_for_contact /
run_for_contacts, so one bad contact/field never aborts a batch.
"""

import datetime as dt
import json
import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.ai import AiUsage
from ..models.core import Organization
from ..models.crm import Company, Contact, ResearchFieldDef
from . import ai_insights, ai_provider, enrichment

log = logging.getLogger("salescale.research")

# Hard ceiling on active definitions per org, independent of tier — mirrors
# custom_fields.MAX_ACTIVE_DEFINITIONS.
MAX_ACTIVE_RESEARCH_FIELDS = 20

_SYSTEM_PROMPT = (
    "You answer ONE specific research question about a business contact, "
    "using ONLY the facts in GROUNDED_DATA (CRM data and text from the "
    "contact's own website). Never invent facts, never search outside what "
    "you were given. If GROUNDED_DATA does not answer the question, the "
    'answer must be the empty string "". Reply with STRICT JSON only, no '
    "prose, no markdown fences, in exactly this shape: "
    '{"answer": "...", "confidence": "high"|"medium"|"low", '
    '"source_url": "..."|null}. confidence reflects how directly '
    "GROUNDED_DATA supports the answer; source_url is the specific URL the "
    "answer came from (the contact's own site), or null when the answer "
    "came from CRM data instead."
)


def _call_model(system: str, user_content: str, max_tokens: int = 300):
    """Isolated monkeypatch seam (mirrors email_personalize._call_model, kept
    separate per module so tests can patch research calls independently of
    email/SMS ones)."""
    return ai_provider.complete(system, user_content, max_tokens)


def _record_usage(
    db: Session, org: Organization, model: str, input_tokens: int, output_tokens: int
) -> None:
    in_price, out_price = ai_provider.price(model)
    db.add(
        AiUsage(
            organization_id=org.id,
            client_id=None,
            user_id=None,
            feature="outreach_research",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_micro_usd=int(input_tokens * in_price + output_tokens * out_price),
        )
    )
    db.flush()


def _grounded_facts(db: Session, org: Organization, contact: Contact) -> dict:
    company = db.get(Company, contact.company_id) if contact.company_id else None
    facts = {
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "job_title": contact.job_title,
        "city": contact.city,
        "state": contact.state,
        "company_name": company.name if company else None,
        "company_description": company.description if company else None,
        "company_revenue": company.estimated_revenue if company else None,
        "company_employees": company.employee_count if company else None,
        "custom_fields": contact.custom_fields or {},
    }
    if org.outreach_context:
        facts["org_context"] = org.outreach_context
    return facts


def _website_for(db: Session, contact: Contact) -> Optional[str]:
    if not contact.company_id:
        return None
    company = db.get(Company, contact.company_id)
    return company.domain if company else None


def _parse_answer(raw: str, max_words: int) -> Optional[dict]:
    """Defensive parse of the model's JSON reply. Discards (returns None) on
    parse failure, an answer over `max_words`, or an answer that still
    contains a template artifact ("{{") — the model echoing prompt/template
    soup back rather than actually answering."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    answer = str(data.get("answer") or "").strip()
    confidence = data.get("confidence")
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
    source_url = data.get("source_url") or None
    if not isinstance(source_url, str):
        source_url = None
    if not answer:
        return {"value": "", "confidence": confidence, "source_url": source_url}
    if "{{" in answer:
        return None
    if len(answer.split()) > max_words:
        return None
    return {"value": answer, "confidence": confidence, "source_url": source_url}


def active_keys(db: Session, org_id: str) -> set:
    """Active (non-archived) research field keys for an org — the validation
    set for {{research.<key>}} tokens at step-save time (mirrors
    custom_fields.definitions_by_key's role for {{custom.<key>}})."""
    return set(
        db.execute(
            select(ResearchFieldDef.key).where(
                ResearchFieldDef.organization_id == org_id,
                ResearchFieldDef.archived.is_(False),
            )
        ).scalars()
    )


def run_for_contact(
    db: Session,
    org: Organization,
    contact: Contact,
    defs: List[ResearchFieldDef],
    force: bool = False,
) -> dict:
    """Answer every def in `defs` for one contact. Returns {"filled",
    "skipped_cached", "failed"} counts. The contact's site text is fetched
    once, lazily, the first time some def actually needs a model call —
    never fetched at all if every def is already cached."""
    current = dict(contact.research or {})
    result = {"filled": 0, "skipped_cached": 0, "failed": 0}
    site_text: Optional[str] = None
    site_fetched = False
    facts: Optional[dict] = None

    for d in defs:
        if not force and d.key in current:
            result["skipped_cached"] += 1
            continue
        try:
            if not site_fetched:
                website = _website_for(db, contact)
                site_text = enrichment.fetch_site_text(website) if website else None
                site_fetched = True
            if facts is None:
                facts = _grounded_facts(db, org, contact)
            grounding = {**facts, "site_text": site_text}
            ai_insights.check_allowance(db, org)  # entitlement + monthly cap
            # Cheaper outreach model (Haiku on Anthropic) — a short grounded
            # research answer, same class as the {{ai_snippet}} tasks.
            res = ai_provider.resolve_outreach(db, org)
            user_content = (
                f"GROUNDED_DATA:\n{json.dumps(grounding, sort_keys=True, default=str)}"
                f"\n\nQUESTION:\n{d.prompt}"
            )
            with ai_provider.using(res):
                text, input_tokens, output_tokens = _call_model(
                    _SYSTEM_PROMPT, user_content
                )
            _record_usage(db, org, res.model, input_tokens, output_tokens)
            parsed = _parse_answer(text, d.max_words)
            if parsed is None:
                result["failed"] += 1
                continue
            parsed["researched_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            current[d.key] = parsed
            result["filled"] += 1
        except Exception as e:  # never let a research failure raise out
            log.info(
                "research field %s skipped for contact %s: %s", d.key, contact.id, e
            )
            result["failed"] += 1

    contact.research = current or None  # reassign so SQLAlchemy tracks the JSON
    return result


def run_for_contacts(
    db: Session,
    org: Organization,
    contact_ids: List[str],
    field_keys: Optional[List[str]] = None,
    force: bool = False,
) -> dict:
    """Batch entry point: at most 200 ids; cross-org ids are silently
    skipped. Commits per contact so one contact's failure can't roll back
    another's progress (mirrors the campaign scheduler's per-row
    commit/rollback isolation). Returns {"processed", "filled",
    "skipped_cached", "failed"}."""
    ids = contact_ids[:200]
    defs = list(
        db.execute(
            select(ResearchFieldDef).where(
                ResearchFieldDef.organization_id == org.id,
                ResearchFieldDef.archived.is_(False),
            )
        ).scalars()
    )
    if field_keys:
        wanted = set(field_keys)
        defs = [d for d in defs if d.key in wanted]

    totals = {"processed": 0, "filled": 0, "skipped_cached": 0, "failed": 0}
    if not defs:
        return totals
    for cid in ids:
        contact = db.get(Contact, cid)
        if contact is None or contact.organization_id != org.id:
            continue
        try:
            r = run_for_contact(db, org, contact, defs, force=force)
            db.commit()
        except Exception:
            log.exception("research run failed for contact %s", cid)
            db.rollback()
            continue
        totals["processed"] += 1
        totals["filled"] += r["filled"]
        totals["skipped_cached"] += r["skipped_cached"]
        totals["failed"] += r["failed"]
    return totals
