"""Cold-email personalization: token substitution + grounded AI snippets.

Two layers, both driven by real CRM data for the SENDING Organization only
(CLAUDE.md #7 — AI grounds in the org's own facts, never invents, never crosses
a tenant boundary):

1. Token substitution — `{{first_name}}`, `{{last_name}}`, `{{company}}`,
   `{{city}}`, `{{state}}`, `{{email}}`, `{{custom.<key>}}`, with a
   `{{token|fallback}}` form ("there" when the field is empty/missing).
   `{{unsubscribe_url}}` is deliberately left as a LITERAL token — the send
   gateway resolves it per-message (each send has its own unsubscribe link).

2. `{{ai_snippet}}` — one or two natural sentences the Claude API writes for
   this specific contact from grounded facts only, when a step supplies
   `ai_instructions`. Metered against the org's monthly AI cap and cached on
   the enrollment (ai_snippets JSON, step_id -> text) so re-processing an
   enrollment never re-bills. AI failure NEVER blocks a send — it yields "".
"""

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from ..models.ai import AiUsage
from ..models.core import Organization
from ..models.crm import Company, Contact
from ..models.email_outreach import EmailCampaign, EmailEnrollment, EmailStep
from . import ai_insights, ai_provider, entitlements

log = logging.getLogger("salescale.email_outreach")

# Tokens left untouched by the renderer (resolved elsewhere / later).
_LITERAL_TOKENS = {"unsubscribe_url"}
# Matches {{ name }} and {{ name | fallback }}.
_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*(?:\|\s*([^}]*?)\s*)?\}\}")

_AI_SYSTEM_PROMPT = (
    "You write one or two short, natural sentences for a cold outreach email, "
    "personalizing to one specific contact using ONLY the grounded facts you "
    "are given. Hard rules: do not invent facts (companies, locations, numbers, "
    "achievements) that are not in GROUNDED_DATA; if a fact is missing, simply "
    "don't reference it. Do not add a greeting, a sign-off, a subject line, or "
    "quotation marks — this text is inserted into the middle of a template. "
    "Keep it under 40 words, plain and human."
)


def _company_name(db: Session, contact: Contact) -> Optional[str]:
    if not contact.company_id:
        return None
    company = db.get(Company, contact.company_id)
    return company.name if company else None


def _resolve_token(
    name: str, contact: Contact, company_name: Optional[str], extra: Dict[str, Any]
) -> Optional[Any]:
    if name in extra:
        return extra[name]
    if name == "first_name":
        return contact.first_name
    if name == "last_name":
        return contact.last_name
    if name == "company":
        return company_name
    if name == "city":
        return contact.city
    if name == "state":
        return contact.state
    if name == "email":
        return contact.email
    if name.startswith("custom."):
        return (contact.custom_fields or {}).get(name[len("custom.") :])
    return None


def _render_template(
    template: Optional[str],
    contact: Contact,
    company_name: Optional[str],
    extra: Dict[str, Any],
) -> str:
    if not template:
        return ""

    def _repl(m: re.Match) -> str:
        name = m.group(1)
        fallback = m.group(2)  # None when no "|fallback" was given
        if name in _LITERAL_TOKENS:
            return m.group(0)  # leave the literal token for the gateway
        value = _resolve_token(name, contact, company_name, extra)
        if value is None or str(value).strip() == "":
            return fallback if fallback is not None else ""
        return str(value)

    return _TOKEN_RE.sub(_repl, template)


def render_step(
    db: Session,
    org: Organization,
    contact: Contact,
    step: EmailStep,
    campaign: EmailCampaign,
    *,
    ai_snippet: str = "",
) -> Tuple[Optional[str], str]:
    """(subject, body) with tokens substituted. A null/blank subject_template
    returns subject=None — the gateway threads it as a "Re:" reply. The
    {{ai_snippet}} token resolves to `ai_snippet` (empty by default)."""
    company_name = _company_name(db, contact)
    extra = {"ai_snippet": ai_snippet}
    raw_subject = (step.subject_template or "").strip()
    subject = (
        _render_template(step.subject_template, contact, company_name, extra)
        if raw_subject
        else None
    )
    body = _render_template(step.body_template or "", contact, company_name, extra)
    return subject, body


# --- grounded AI snippet ----------------------------------------------------


def _call_model(system: str, user_content: str, max_tokens: int = 300):
    """(text, input_tokens, output_tokens). Dispatches to the operator-selected
    AI provider (anthropic | openai | gemini) via services/ai_provider, using
    the resolution bound by generate_ai_snippet. Isolated as the monkeypatch
    seam for tests."""
    return ai_provider.complete(system, user_content, max_tokens)


def _record_usage(
    db: Session, org: Organization, model: str, input_tokens: int, output_tokens: int
) -> None:
    in_price, out_price = ai_provider.price(model)
    db.add(
        AiUsage(
            organization_id=org.id,
            client_id=None,  # outreach personalization is agency-level, no client
            user_id=None,  # background/scheduler origin
            feature="outreach_personalize",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_micro_usd=int(input_tokens * in_price + output_tokens * out_price),
        )
    )
    db.flush()


def generate_ai_snippet(
    db: Session, org: Organization, contact: Contact, step: EmailStep
) -> str:
    """One/two grounded sentences for this contact, or "" on any failure
    (unconfigured key, over the monthly AI cap, model/timeout error). A send is
    never blocked on AI — the template still goes out without the snippet."""
    if not (step.ai_instructions or "").strip():
        return ""
    grounding = {
        "contact": {
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "company_name": _company_name(db, contact),
            "city": contact.city,
            "state": contact.state,
            "custom_fields": contact.custom_fields or {},
        },
        "org": {"name": org.name},
        "instructions": step.ai_instructions,
    }
    try:
        ai_insights.check_allowance(db, org)  # entitlement + monthly cap
        res = ai_provider.resolve(db, org)  # provider + model + BYO/operator key
        user_content = (
            f"GROUNDED_DATA:\n{json.dumps(grounding, sort_keys=True, default=str)}\n\n"
            f"INSTRUCTIONS:\n{step.ai_instructions}"
        )
        with ai_provider.using(res):
            text, input_tokens, output_tokens = _call_model(_AI_SYSTEM_PROMPT, user_content)
        _record_usage(db, org, res.model, input_tokens, output_tokens)
        return (text or "").strip()
    except Exception as e:  # never let AI failure stop a send
        log.info("outreach AI snippet skipped for contact %s: %s", contact.id, e)
        return ""


def _cached_or_generate(
    db: Session,
    org: Organization,
    contact: Contact,
    enrollment: Optional[EmailEnrollment],
    step: EmailStep,
) -> str:
    """Enrollment-cached snippet (ai_snippets: step_id -> text). Idempotent: a
    second render of the same enrollment/step reuses the cache and never
    re-bills. Preview (enrollment=None) generates fresh without caching."""
    if not (step.ai_instructions or "").strip():
        return ""
    if enrollment is not None:
        cache = dict(enrollment.ai_snippets or {})
        if step.id in cache:
            return cache[step.id] or ""
        snippet = generate_ai_snippet(db, org, contact, step)
        cache[step.id] = snippet
        enrollment.ai_snippets = cache  # reassign so SQLAlchemy tracks the JSON
        return snippet
    return generate_ai_snippet(db, org, contact, step)


def render_full(
    db: Session,
    org: Organization,
    enrollment: Optional[EmailEnrollment],
    step: EmailStep,
    campaign: EmailCampaign,
    contact: Optional[Contact] = None,
) -> Tuple[Optional[str], str]:
    """(subject, body) fully rendered incl. the {{ai_snippet}} token (from the
    enrollment cache, generating once if absent). `contact` may be passed
    directly (preview); otherwise it is loaded from the enrollment."""
    if contact is None:
        contact = db.get(Contact, enrollment.contact_id)
    snippet = _cached_or_generate(db, org, contact, enrollment, step)
    return render_step(db, org, contact, step, campaign, ai_snippet=snippet)
