"""SMS campaign engine: enrollment + the step state machine.

Mirrors services/email_campaigns.py's shape (enroll → schedule → send →
advance → exit), adapted to SMS's simpler model:

- No threads, no AI snippet, no open tracking, no unsubscribe URL. Steps are
  plain text messages; `wait_days` is the delay before a step fires, so there
  is one send per enrollment per tick, then next_run_at is set to the
  following step's wait, landed inside a valid send window.
- Every send routes through the ONE gateway (services/sms_send.send), so the
  consent gate (services/sms_consent), suppression, quiet hours, and the
  per-account/per-campaign daily caps all hold — this engine never talks to
  Twilio directly.
- Reply/opt-out compliance exits are handled directly by the inbound Twilio
  webhook (api/sms_webhooks.py), not by a hook registry like email's IMAP
  sync — SMS has no polling loop to hang hooks off of; the webhook is
  already the single point where an inbound message lands. This engine only
  owns the outbound tick (run_due) and manual unenroll.

Time & window rules (documented, internally consistent, mirrors email):
- The campaign's daily_cap counts messages transmitted since UTC midnight,
  via services/sms_send.campaign_sends_today (same UTC accounting as the
  per-account cap).
- Send window/day gating (the TCPA quiet-hours guard) is evaluated in the
  campaign's own timezone (zoneinfo; falls back to UTC if the tz string is
  invalid). Outside the window, the enrollment is parked with next_run_at set
  to the next window open — it is never sent early and never errors. The
  gateway (sms_send.send) re-checks the window itself before every send, so
  this is belt-and-suspenders, not the only guard.
- CAP_REACHED (account or campaign) parks the enrollment for a 1h retry.
- A hard send FAILURE ends the enrollment in `error` status (exit_reason
  "failed") rather than retrying a broken account forever.
- SUPPRESSED/BLOCKED (opted out, or blocked by the consent gate at
  send-time — e.g. consent was revoked after enrollment) exit the enrollment
  immediately; TCPA compliance means these never retry.
"""

import datetime as dt
import json
import logging
import re
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.core import Organization
from ..models.crm import Company, Contact
from ..models.sms_outreach import (
    SMS_ACCOUNT_ACTIVE,
    SMS_CAMPAIGN_ACTIVE,
    SMS_DIR_IN,
    SMS_DIR_OUT,
    SMS_ENROLL_ACTIVE,
    SMS_ENROLL_COMPLETED,
    SMS_ENROLL_ERROR,
    SMS_ENROLL_EXITED,
    SMS_KIND_CAMPAIGN,
    SMS_TRIGGER_REPLY,
    SmsAccount,
    SmsCampaign,
    SmsEnrollment,
    SmsMessage,
    SmsStep,
)
from . import ai_insights, ai_provider
from . import email_personalize  # reused for token rendering (regex + casing/tidy)
from . import sms_consent
from . import sms_send as gateway
from . import timezones

log = logging.getLogger("salescale.sms_outreach")

# The tokens an SMS template may use. ai_snippet + job_title added alongside
# email's equivalents; deliberately narrower than email's KNOWN_TOKENS — no
# company_description/revenue/employees (too long for a text) and no
# unsubscribe_url (STOP is the SMS opt-out mechanism, not a link). A typo'd
# or email-only token would otherwise silently render as "" in every sent
# text; the steps API rejects it at save time instead (see unknown_tokens).
SMS_KNOWN_TOKENS = frozenset(
    {"first_name", "last_name", "company", "city", "state", "job_title", "ai_snippet"}
)

# Cost-blowout guard: a runaway custom field or AI snippet could otherwise
# balloon one text into many billed segments. GSM-7 single-segment cap is 160
# chars; multipart segments are 153 chars each (frontend SMS_SEGMENT_LEN
# mirrors the single-segment 160; this is the send-time enforcement).
_SMS_SEGMENT_LEN = 160
_SMS_MULTIPART_SEGMENT_LEN = 153
MAX_RENDERED_SEGMENTS = 3

_SMS_AI_SYSTEM_PROMPT = (
    "You write ONE short, natural sentence (max 15 words) to personalize a "
    "text message, using ONLY the grounded facts given. No links, no "
    "emojis, no greeting, no sign-off, no quotation marks."
)


def segment_count(text: str) -> int:
    """GSM-7 segment math: 1 segment up to 160 chars, otherwise multipart at
    153 chars/segment (the concatenation-header overhead)."""
    n = len(text or "")
    if n <= _SMS_SEGMENT_LEN:
        return 1
    return -(-n // _SMS_MULTIPART_SEGMENT_LEN)  # ceil division


def unknown_tokens(template: Optional[str], custom_keys=None, research_keys=None) -> list:
    """Tokens in `template` that aren't in SMS_KNOWN_TOKENS (or a real
    custom.<key>/research.<key> when `custom_keys`/`research_keys` is given),
    plus #if/spin structural errors. Delegates to email_personalize's shared
    implementation (same {{#if}}/{{spin:}}/{{token|fallback}} grammar)
    parameterized on SMS's own, narrower known-token set so the two modules
    never drift on syntax."""
    return email_personalize._unknown_tokens_against(
        template, SMS_KNOWN_TOKENS, custom_keys, research_keys
    )


# --- render failsafes ---------------------------------------------------------
# A lead with no usable first_name greets by business name instead, and a
# missing city is AI-inferred once from the lead's OWN facts (guardrail 7).
# Both rescue sends that would otherwise exit render_empty; both fail open.

# Business-name acronyms that word-casing would mangle ("DESERT AIR HVAC
# LLC" must not become "Desert Air Hvac Llc"). Only true acronyms — Inc/Co/
# Corp/Ltd are conventionally title-case and word-casing already gets them
# right. Generic + the trade acronyms common in local-business names.
_BIZ_ACRONYMS = frozenset(
    {"llc", "llp", "lp", "pc", "pllc", "dba", "usa", "hvac", "ac", "a/c"}
)

_CITY_TOKEN_RE = re.compile(r"\{\{\s*(?:#if\s+)?city\b")

_CITY_FAILSAFE_SYSTEM = (
    "You determine the city where a business is located, using ONLY the "
    "facts given (business name, website domain, phone numbers, state, and "
    "the search query that found the business). You may reason from a US "
    "phone area code or a city named inside the search query. Reply with "
    "the city name ALONE — no state, no punctuation, no explanation. If the "
    "facts do not clearly point to one city, reply exactly UNKNOWN."
)


def business_case(value: str) -> str:
    """Proper-case a business name for use in a greeting: word-casing via the
    shared _smart_case (mixed-case input passes through untouched), then
    known suffixes/acronyms restored to uppercase."""
    cased = email_personalize._smart_case("company", value.strip())
    return re.sub(
        r"[^\s\-]+",
        lambda m: m.group(0).upper() if m.group(0).lower() in _BIZ_ACRONYMS else m.group(0),
        cased,
    )


def _clean_city(text: Optional[str]) -> str:
    """Output guard for the city-inference answer: one short line of plain
    letters or nothing — a hedge, a sentence, or digits means the model
    didn't actually know."""
    v = (text or "").strip().strip("\"'").rstrip(".").strip()
    if (
        not v
        or len(v) > 40
        or "\n" in v
        or any(ch.isdigit() for ch in v)
        or v.upper() == "UNKNOWN"
        or not re.fullmatch(r"[A-Za-z][A-Za-z .'\-]*", v)
    ):
        return ""
    return email_personalize._smart_case("city", v)


def infer_city_failsafe(db: Session, org: Organization, contact: Contact) -> str:
    """One grounded AI call to determine the lead's city, or "" on any
    failure (no key, over the monthly cap, model error, output guard). Same
    fail-open posture as generate_ai_snippet — a send is never blocked on AI."""
    company = None
    if contact.company_id:
        company = db.get(Company, contact.company_id)
    grounding = {
        "business_name": (company.name if company else None) or contact.first_name,
        "website_domain": company.domain if company else None,
        "business_phone": company.phone if company else None,
        "contact_phone": contact.phone,
        "contact_mobile": contact.mobile_phone,
        "state": contact.state,
        "search_query": (contact.source_detail or {}).get("query")
        if isinstance(contact.source_detail, dict)
        else None,
    }
    try:
        ai_insights.check_allowance(db, org)
        res = ai_provider.resolve_outreach(db, org)  # cheap outreach model
        user_content = f"FACTS:\n{json.dumps(grounding, sort_keys=True, default=str)}"
        with ai_provider.using(res):
            text, input_tokens, output_tokens = email_personalize._call_model(
                _CITY_FAILSAFE_SYSTEM, user_content, max_tokens=16
            )
        email_personalize._record_usage(db, org, res.model, input_tokens, output_tokens)
        city = _clean_city(text)
        if not city:
            log.info("sms city failsafe: no confident city for contact %s", contact.id)
        return city
    except Exception as e:  # never let AI failure stop a send
        log.info("sms city failsafe skipped for contact %s: %s", contact.id, e)
        return ""


# A name reads as a business (not a person) when it carries a company suffix or
# an industry word — used only as a last resort for {{company}} when no Company
# is linked and there's no surname, so a single-name person is never mistaken
# for a company.
_BIZ_HINT_RE = re.compile(
    r"\b(llc|inc|co|corp|corporation|ltd|plc|pllc|lp|cpa|hvac|group|"
    r"services?|associates|company|heating|cooling|plumbing|air|mechanical|"
    r"electric(?:al)?|roofing|construction|solutions|enterprises|holdings|"
    r"systems|contracting|remodeling|landscaping|cleaning|&)\b",
    re.IGNORECASE,
)


def _company_from_name(contact: Contact) -> Optional[str]:
    """Best-effort business name from the contact's name field, for the
    {{company}} fallback. Fires only when there's no surname (a real person has
    one) and the first_name reads like a business — multi-word or a company/
    industry word — so 'Mike' is never taken as a company but 'Desert Air HVAC'
    is. Proper-cased via business_case (acronym-aware)."""
    if (contact.last_name or "").strip():
        return None
    fn = (contact.first_name or "").strip()
    if not fn:
        return None
    if " " in fn or _BIZ_HINT_RE.search(fn):
        return business_case(fn)
    return None


def render_body(
    db: Session,
    contact: Contact,
    step: SmsStep,
    *,
    ai_snippet: str = "",
    body_template: Optional[str] = None,
) -> str:
    """Render one step's body for one contact. Reuses email_personalize's
    template substitution (casing normalization, #if/spin, the emptied-token
    tidy pass) so `{{first_name|there}}` etc. behave identically to email.
    `ai_snippet` is the only extra token SMS injects, plus the business-name
    greeting failsafe: a blank first_name renders as the proper-cased
    business name (beats any |fallback — a named greeting is the point).

    {{city}} is a plain deterministic field lookup (contact.city), same as
    {{state}} — blank renders blank. The AI-inference-when-blank failsafe
    that used to live here is disabled for now (proved unreliable in
    practice — a retired Gemini model + inconsistent per-lead behavior);
    infer_city_failsafe/_clean_city are kept, just uncalled, so it's a
    one-line change to re-enable rather than a rebuild.

    `body_template` overrides step.body_template — the branch-selected
    response body on a reply-triggered step (select_branch)."""
    facts = email_personalize._company_facts(db, contact)
    # {{company}} fallback: a lead whose business/place name landed in the name
    # field with no linked Company (a Lead Finder placeholder, or an import that
    # mapped the business to a name column) should still fill {{company}} from
    # that name rather than render blank.
    if not (facts.get("company") or "").strip():
        from_name = _company_from_name(contact)
        if from_name:
            facts = {**facts, "company": from_name}
    extra = {"ai_snippet": ai_snippet}
    if not (contact.first_name or "").strip() and (facts.get("company") or "").strip():
        extra["first_name"] = business_case(facts["company"])
    template = body_template if body_template is not None else (step.body_template or "")
    return email_personalize._render_template(template, contact, facts, extra)


def _cached_or_generate(
    db: Session,
    org: Organization,
    contact: Contact,
    enrollment: Optional[SmsEnrollment],
    step: SmsStep,
) -> str:
    """Enrollment-cached snippet (ai_snippets: step_id -> text), mirroring
    email_personalize._cached_or_generate. Preview (enrollment=None)
    generates fresh without caching.

    Only a non-None result is cached: a transient AI failure (None) is NOT
    written, so a missing key / cap hit doesn't permanently kill this
    enrollment's personalization — it retries on the next render/tick."""
    if not (step.ai_instructions or "").strip():
        return ""
    if enrollment is not None:
        cache = dict(enrollment.ai_snippets or {})
        if step.id in cache:
            return cache[step.id] or ""
        snippet = generate_ai_snippet(db, org, contact, step)
        if snippet is not None:
            cache[step.id] = snippet
            enrollment.ai_snippets = cache  # reassign so SQLAlchemy tracks the JSON
        return snippet or ""
    return generate_ai_snippet(db, org, contact, step) or ""


def generate_ai_snippet(
    db: Session, org: Organization, contact: Contact, step: SmsStep
) -> Optional[str]:
    """One short grounded sentence for this contact. Returns the text on
    success, "" when the model RAN but produced nothing usable (empty output /
    output-guard discard — a real cacheable result), or None on a TRANSIENT
    failure (unconfigured key, over the monthly AI cap, model/timeout error) —
    which the caller must NOT cache so it retries. A send is never blocked on
    AI (mirrors email_personalize.generate_ai_snippet)."""
    if not (step.ai_instructions or "").strip():
        return ""
    grounding = {
        "contact": {
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "job_title": contact.job_title,
            "company_name": email_personalize._company_name(db, contact),
            "city": contact.city,
            "state": contact.state,
            "custom_fields": contact.custom_fields or {},
        },
        "org": {"name": org.name},
        "instructions": step.ai_instructions,
    }
    if org.outreach_context:
        # Org-level AI writing context (Feature C) — SMS gets the grounding
        # injection same as email, but never the per-campaign tone/example
        # (SMS campaigns don't carry those fields; a text is too short for a
        # few-shot example to matter).
        grounding["org_context"] = org.outreach_context
    try:
        ai_insights.check_allowance(db, org)  # entitlement + monthly cap
        # Cheaper outreach model (Haiku on Anthropic) — mirrors email's snippet.
        res = ai_provider.resolve_outreach(db, org)  # provider + model + BYO/operator key
        user_content = (
            f"GROUNDED_DATA:\n{json.dumps(grounding, sort_keys=True, default=str)}\n\n"
            f"INSTRUCTIONS:\n{step.ai_instructions}"
        )
        with ai_provider.using(res):
            text, input_tokens, output_tokens = email_personalize._call_model(
                _SMS_AI_SYSTEM_PROMPT, user_content
            )
        email_personalize._record_usage(db, org, res.model, input_tokens, output_tokens)
        cleaned = email_personalize.clean_ai_snippet(text, 25)
        if not cleaned:
            log.info("sms outreach AI snippet discarded by output guard for contact %s", contact.id)
        # The model ran (and billed) — "" here is a real, cacheable result.
        return cleaned
    except Exception as e:  # never let AI failure stop a send
        # Transient — the model never ran; None so the caller doesn't cache it.
        log.info("sms outreach AI snippet skipped for contact %s: %s", contact.id, e)
        return None


def render_full(
    db: Session,
    org: Organization,
    enrollment: Optional[SmsEnrollment],
    step: SmsStep,
    contact: Optional[Contact] = None,
    body_template: Optional[str] = None,
) -> str:
    """Full render incl. {{ai_snippet}} (from the enrollment cache,
    generating once if absent). `contact` may be passed directly (preview);
    otherwise it is loaded from the enrollment. `body_template` is the
    branch-selected body override for reply steps."""
    if contact is None:
        contact = db.get(Contact, enrollment.contact_id)
    snippet = _cached_or_generate(db, org, contact, enrollment, step)
    return render_body(
        db, contact, step, ai_snippet=snippet, body_template=body_template
    )


# --- reply-step branching -----------------------------------------------------
# A reply-triggered step may carry `branches`: [{"label", "keywords", "body"}].
# Matching is deterministic-first (word-boundary keyword search over the lead's
# reply, branch order = priority), then optionally ONE cheap grounded AI
# classification (step.ai_branching) — fail-open to the step's default
# body_template on any AI failure, mirroring every other AI seam in outreach.

_CLASSIFY_REPLY_SYSTEM = (
    "You classify an inbound SMS reply into one of the given categories. "
    "Use ONLY the reply text and the category descriptions given. Answer "
    "with the category label ALONE, exactly as written — no punctuation, no "
    "explanation. If none clearly fits, answer exactly NONE."
)


def _branch_options(step: SmsStep) -> list:
    """The step's branches as a sanitized list of dicts (defensive against
    hand-edited JSON: non-dict entries and blank labels are skipped)."""
    out = []
    for b in step.branches or []:
        if not isinstance(b, dict):
            continue
        label = str(b.get("label") or "").strip()
        if not label:
            continue
        keywords = [
            str(k).strip() for k in (b.get("keywords") or []) if str(k).strip()
        ]
        out.append({"label": label, "keywords": keywords, "body": b.get("body") or ""})
    return out


def match_branch_keywords(step: SmsStep, reply_text: str) -> Optional[dict]:
    """First branch (in order) with a keyword appearing in the reply as a
    whole word, case-insensitive. Word boundaries matter: a "no" branch must
    not fire on "know"."""
    text = (reply_text or "").lower()
    if not text:
        return None
    for branch in _branch_options(step):
        for kw in branch["keywords"]:
            if re.search(r"(?<!\w)" + re.escape(kw.lower()) + r"(?!\w)", text):
                return branch
    return None


def classify_reply(
    db: Session, org: Organization, step: SmsStep, reply_text: str
) -> Optional[str]:
    """One grounded AI call to pick a branch label for `reply_text`, or None
    on any failure / no confident fit (fail-open — the default body sends).
    Metered like every other outreach AI call."""
    options = _branch_options(step)
    if not options or not (reply_text or "").strip():
        return None
    labels = {b["label"].casefold(): b["label"] for b in options}
    grounding = {
        "reply": reply_text[:500],
        "categories": [
            {"label": b["label"], "example_keywords": b["keywords"]} for b in options
        ],
    }
    try:
        ai_insights.check_allowance(db, org)
        res = ai_provider.resolve_outreach(db, org)
        user_content = f"DATA:\n{json.dumps(grounding, sort_keys=True, default=str)}"
        with ai_provider.using(res):
            text, input_tokens, output_tokens = email_personalize._call_model(
                _CLASSIFY_REPLY_SYSTEM, user_content, max_tokens=16
            )
        email_personalize._record_usage(db, org, res.model, input_tokens, output_tokens)
        answer = (text or "").strip().strip("\"'").rstrip(".").strip()
        return labels.get(answer.casefold())
    except Exception as e:  # never let AI failure stop a send
        log.info("sms reply classification skipped for step %s: %s", step.id, e)
        return None


def select_branch(
    db: Session, org: Organization, step: SmsStep, reply_text: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """(body_template, branch_label) for a reply step given the lead's reply.
    (None, None) means no branch matched — send the step's default
    body_template. Keywords first (deterministic), then AI when enabled."""
    if not _branch_options(step):
        return None, None
    matched = match_branch_keywords(step, reply_text or "")
    if matched is None and step.ai_branching:
        label = classify_reply(db, org, step, reply_text or "")
        if label is not None:
            matched = next(
                (b for b in _branch_options(step) if b["label"] == label), None
            )
    if matched is None:
        return None, None
    return matched["body"] or None, matched["label"]


# --- enrollment -------------------------------------------------------------


def enroll_contacts(
    db: Session,
    campaign: SmsCampaign,
    contact_ids: List[str],
    enrolled_by: Optional[str] = None,
    source: Optional[str] = None,
    source_detail: Optional[str] = None,
) -> dict:
    """Enroll a set of CRM contacts into a campaign. Every contact is routed
    through the SAME shared gate every SMS-send feature uses
    (sms_consent.sendable) so TCPA/isolation behaviour is identical wherever
    a send is attempted. Returns {enrolled, skipped: [{contact_id, reason}]}.
    Reasons: not_found | duplicate | no_number | no_consent | suppressed |
    already (already enrolled in this campaign).

    source/source_detail record HOW the contact entered ("manual" | "list" |
    "client" | "auto_new_lead" + list name / lead capture source) — the
    audience-attribution trail surfaced in the campaign's Audience tab."""
    org_id = campaign.organization_id
    seen: set = set()
    resolved: List[Contact] = []
    skipped: List[dict] = []
    for cid in contact_ids:
        if cid in seen:
            skipped.append({"contact_id": cid, "reason": "duplicate"})
            continue
        seen.add(cid)
        c = db.get(Contact, cid)
        if c is None or c.organization_id != org_id:
            skipped.append({"contact_id": cid, "reason": "not_found"})
            continue
        resolved.append(c)

    now = utcnow()
    enrolled = 0
    for c in resolved:
        ok, reason = sms_consent.sendable(db, c)
        if not ok:
            skipped.append({"contact_id": c.id, "reason": reason})
            continue
        existing = db.execute(
            select(SmsEnrollment.id).where(
                SmsEnrollment.campaign_id == campaign.id,
                SmsEnrollment.contact_id == c.id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            skipped.append({"contact_id": c.id, "reason": "already"})
            continue
        db.add(
            SmsEnrollment(
                organization_id=org_id,
                campaign_id=campaign.id,
                contact_id=c.id,
                status=SMS_ENROLL_ACTIVE,
                current_position=1,
                next_run_at=now,
                enrolled_by=enrolled_by,
                source=source,
                source_detail=(source_detail or None) and source_detail[:120],
            )
        )
        enrolled += 1
    db.flush()
    return {"enrolled": enrolled, "skipped": skipped}


# --- window / step helpers ---------------------------------------------------


def _tz(campaign: SmsCampaign):
    # Legacy-abbreviation tolerant (see services/timezones). Critically for
    # SMS: quiet-hours (TCPA) are evaluated in this zone — a silent UTC
    # fallback could text outside 8am-9pm local.
    return timezones.resolve(campaign.timezone or "America/New_York")


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def _next_valid_send_time(
    after: dt.datetime, campaign: SmsCampaign
) -> Optional[dt.datetime]:
    """Earliest UTC datetime >= `after` that falls inside the campaign's send
    window on an allowed weekday, evaluated in the campaign's timezone (the
    TCPA quiet-hours guard). Returns `after` (in UTC) when it is already
    inside a window, or None when the campaign has no valid window at all
    (empty send_days / start>=end)."""
    tz = _tz(campaign)
    days = campaign.send_days if campaign.send_days is not None else [0, 1, 2, 3, 4]
    start, end = campaign.send_window_start, campaign.send_window_end
    if not days or start >= end:
        return None
    local = _aware(after).astimezone(tz)
    for _ in range(15):  # up to two weeks lookahead
        if local.weekday() in days:
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
            open_t = midnight + dt.timedelta(hours=start)
            # end==24 means "through end of day"; hour=24 is not a valid clock
            # hour, so express the close as midnight of the next day.
            close_t = midnight + dt.timedelta(hours=end)
            if local < open_t:
                return open_t.astimezone(dt.timezone.utc)
            if local < close_t:
                # `local` (not the original `after`) — on the first loop
                # iteration these are identical (no day has been advanced
                # yet, so this preserves the requested time-of-day when it's
                # already inside today's window); on a later iteration
                # `local` has been reset to that day's midnight, which is
                # the correct instant to return when send_window_start == 0
                # (open_t == midnight, so `local < open_t` above is False —
                # returning the stale `after` here would silently resurrect
                # a moment on a day the loop already rejected).
                return local.astimezone(dt.timezone.utc)
        local = (local + dt.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    return None


def _step_delay(step: SmsStep) -> dt.timedelta:
    """Total wait for a step: days + minutes. For a schedule step it's the
    delay after the previous step; for a reply step, after the lead's reply."""
    return dt.timedelta(
        days=max(0, step.wait_days or 0), minutes=max(0, getattr(step, "wait_minutes", 0) or 0)
    )


def _steps(db: Session, campaign_id: str) -> List[SmsStep]:
    return list(
        db.execute(
            select(SmsStep)
            .where(SmsStep.campaign_id == campaign_id)
            .order_by(SmsStep.position)
        ).scalars()
    )


def rearm_parked(db: Session, campaign: SmsCampaign) -> int:
    """Re-schedule this campaign's ACTIVE enrollments that a tick parked
    (next_run_at = NULL — campaign paused or account disconnected at the
    time). Without this, reactivating a campaign leaves its audience
    dormant forever: run_due only scans non-NULL next_run_at. Scheduled at
    the next valid window open, never immediately-past-quiet-hours.

    Enrollments parked AWAITING A REPLY (awaiting_reply_since set) are
    deliberately excluded — they're waiting on the lead, not on the campaign,
    and re-arming would force-fire a reply step nobody replied to."""
    now = utcnow()
    when = _next_valid_send_time(now, campaign) or now
    parked = (
        db.execute(
            select(SmsEnrollment).where(
                SmsEnrollment.campaign_id == campaign.id,
                SmsEnrollment.status == SMS_ENROLL_ACTIVE,
                SmsEnrollment.next_run_at.is_(None),
                SmsEnrollment.awaiting_reply_since.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for e in parked:
        e.next_run_at = when
    return len(parked)


def _revive_errored(db: Session, campaign: SmsCampaign) -> int:
    """A hard send FAILURE ends its enrollment in SMS_ENROLL_ERROR (broken
    account). Reconnecting the account must resume THOSE contacts too, not
    just the parked ones — otherwise whoever hit the broken account first is
    dropped forever. Returns them to ACTIVE at the campaign's next valid send
    window (mirrors email_campaigns._revive_errored)."""
    now = utcnow()
    when = _next_valid_send_time(now, campaign) or now
    errored = (
        db.execute(
            select(SmsEnrollment).where(
                SmsEnrollment.campaign_id == campaign.id,
                SmsEnrollment.status == SMS_ENROLL_ERROR,
            )
        )
        .scalars()
        .all()
    )
    for e in errored:
        e.status = SMS_ENROLL_ACTIVE
        e.exit_reason = None
        e.ended_at = None
        e.next_run_at = when
    return len(errored)


def rearm_account(db: Session, account_id: str) -> int:
    """Account reconnected: re-arm parked enrollments — and revive send-FAILURE
    errored ones — across all of the account's ACTIVE campaigns (the
    \"reconnect flow re-arms\" contract in process_enrollment)."""
    campaigns = (
        db.execute(
            select(SmsCampaign).where(
                SmsCampaign.account_id == account_id,
                SmsCampaign.status == SMS_CAMPAIGN_ACTIVE,
            )
        )
        .scalars()
        .all()
    )
    return sum(rearm_parked(db, c) + _revive_errored(db, c) for c in campaigns)


def _end(enrollment: SmsEnrollment, status: str, reason: Optional[str] = None) -> None:
    enrollment.status = status
    enrollment.exit_reason = reason
    enrollment.next_run_at = None
    enrollment.ended_at = utcnow()


# --- inbound reply routing ----------------------------------------------------


# --- Automated out-of-office / auto-responder detection ---
#
# Businesses reply to outreach with an unattended auto-responder ("Thank you
# for reaching out. You've reached us outside of normal office hours. For a
# service emergency call ... a technician is on call after hours."). That is
# NOT a human answer, so the sequence must not treat it as one — no branch
# fires, no step advances, the lead keeps waiting for a real person.
#
# STRONG phrases are distinctive enough to classify on their own; MEDIUM
# phrases are individually ambiguous (an interested lead might ask "do you
# work after hours?"), so two or more are required. Keeps a genuine one-liner
# ("yes", "how much?", "call me") from ever being mistaken for an auto-reply.
_AUTOREPLY_STRONG = (
    "out of office",
    "out-of-office",
    "outside of normal",
    "outside our normal",
    "outside of our normal",
    "outside of office hours",
    "outside our office",
    "outside of business hours",
    "reached us outside",
    "automated response",
    "automated message",
    "automatic reply",
    "auto-reply",
    "auto reply",
    "this is an automated",
    "normal business hours",
    "regular business hours",
    "normal office hours",
    "do not reply",
    "unmonitored",
)
_AUTOREPLY_MEDIUM = (
    "after hours",
    "after-hours",
    "on call",
    "on-call",
    "service emergency",
    "currently closed",
    "we are closed",
    "we're closed",
    "office hours",
    "business hours",
    "get back to you",
    "reaching out to us",
)


def is_auto_reply(text: str) -> bool:
    """True when a reply reads like an automated out-of-office / auto-responder
    rather than a human answer. Any STRONG phrase classifies; MEDIUM phrases
    need two, so a short genuine reply is never misread."""
    t = (text or "").lower()
    if not t:
        return False
    if any(p in t for p in _AUTOREPLY_STRONG):
        return True
    return sum(1 for p in _AUTOREPLY_MEDIUM if p in t) >= 2


def handle_reply(
    db: Session,
    contact: Contact,
    reply_body: str,
    now: Optional[dt.datetime] = None,
) -> Optional[dict]:
    """Route one genuine inbound reply (non-STOP, non-HELP — the webhook
    already filtered those) through every ACTIVE enrollment this contact has:

    - Record the reply on the enrollment (replied_at stays the FIRST reply;
      last_reply_at/last_reply_body track the latest — the branch input).
    - If a reply-triggered step exists at/after the current position, jump to
      it and schedule it wait_days/wait_minutes after the reply (landed inside
      the campaign's send window — quiet hours still hold). This intentionally
      skips any pending schedule steps: the reply handler IS the response.
    - Otherwise, exit_on_reply campaigns exit with reason "replied" (the
      pre-reply-step behavior, unchanged); campaigns with exit_on_reply off
      keep dripping as before.

    Returns {campaign_id, enrollment_id, step_id} for inbound-message
    attribution: the contact's most recent OUTBOUND campaign message is the
    text the lead is replying to, so its campaign/enrollment/step is the
    linkage — including when that enrollment already completed/exited (a lead
    replying after the drip finished is still a campaign reply, and gets
    replied_at recorded for the stats). None when the contact has never been
    sent a campaign message."""
    now = now or utcnow()

    # Attribution: the last campaign text this contact received.
    prompted = db.execute(
        select(SmsMessage)
        .where(
            SmsMessage.contact_id == contact.id,
            SmsMessage.organization_id == contact.organization_id,
            SmsMessage.direction == SMS_DIR_OUT,
            SmsMessage.campaign_id.is_not(None),
        )
        .order_by(SmsMessage.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    linkage: Optional[dict] = None
    if prompted is not None:
        linkage = {
            "campaign_id": prompted.campaign_id,
            "enrollment_id": prompted.enrollment_id,
            "step_id": prompted.step_id,
        }

    # Auto-reply guard: an automated out-of-office / auto-responder text is not
    # a human answer. Make NO enrollment changes — a lead awaiting a reply keeps
    # awaiting, a lead mid-drip keeps dripping — so the sequence waits for a REAL
    # person to respond and nothing is pitched at a bot. The inbound row is still
    # recorded + attributed by the caller; we only decline to act on it here.
    if is_auto_reply(reply_body):
        return linkage

    # Record the reply on the attributed enrollment even when it's no longer
    # active (tracking only — a completed/exited enrollment is never resurrected).
    if prompted is not None and prompted.enrollment_id is not None:
        attributed = db.get(SmsEnrollment, prompted.enrollment_id)
        if attributed is not None and attributed.status != SMS_ENROLL_ACTIVE:
            attributed.replied_at = attributed.replied_at or now
            attributed.last_reply_at = now

    enrollments = (
        db.execute(
            select(SmsEnrollment).where(
                SmsEnrollment.organization_id == contact.organization_id,
                SmsEnrollment.contact_id == contact.id,
                SmsEnrollment.status == SMS_ENROLL_ACTIVE,
            )
        )
        .scalars()
        .all()
    )
    for e in enrollments:
        campaign = db.get(SmsCampaign, e.campaign_id)
        if campaign is None:
            continue
        e.replied_at = e.replied_at or now
        e.last_reply_at = now
        e.last_reply_body = reply_body
        reply_step = next(
            (
                s
                for s in _steps(db, campaign.id)
                if s.position >= e.current_position
                and (s.trigger or "schedule") == SMS_TRIGGER_REPLY
            ),
            None,
        )
        if reply_step is not None:
            # Route to the reply handler. Scheduling happens even when the
            # campaign is paused/account down — the engine re-checks status at
            # send time and parks if needed (and rearm re-arms it later).
            e.current_position = reply_step.position
            e.awaiting_reply_since = None
            base = now + _step_delay(reply_step)
            e.next_run_at = _next_valid_send_time(base, campaign) or base
        elif campaign.exit_on_reply:
            _end(e, SMS_ENROLL_EXITED, "replied")

    # A COMPLETED enrollment is not a conversational dead end. If the lead
    # texts again and the campaign author wrote a response branch for THAT
    # kind of reply (deterministic keyword match ONLY — e.g. "Not interested"
    # → the parting message), re-open the enrollment and send it. No branch
    # match means no re-open: repeating the step's default body at a lead who
    # said something new would be a robotic re-pitch, and auto-responder
    # texts ("we're closed, we'll get back to you") deserve silence. Only
    # clean completions re-open — exited/opted_out/manual stay terminal.
    completed = (
        db.execute(
            select(SmsEnrollment).where(
                SmsEnrollment.organization_id == contact.organization_id,
                SmsEnrollment.contact_id == contact.id,
                SmsEnrollment.status == SMS_ENROLL_COMPLETED,
                SmsEnrollment.exit_reason.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for e in completed:
        campaign = db.get(SmsCampaign, e.campaign_id)
        if campaign is None:
            continue
        reply_step = next(
            (
                s
                for s in _steps(db, campaign.id)
                if s.position >= e.current_position
                and (s.trigger or "schedule") == SMS_TRIGGER_REPLY
            ),
            None,
        )
        if reply_step is None:
            continue
        if match_branch_keywords(reply_step, reply_body) is None:
            continue
        e.status = SMS_ENROLL_ACTIVE
        e.ended_at = None
        e.current_position = reply_step.position
        e.awaiting_reply_since = None
        e.replied_at = e.replied_at or now
        e.last_reply_at = now
        e.last_reply_body = reply_body
        base = now + _step_delay(reply_step)
        e.next_run_at = _next_valid_send_time(base, campaign) or base
    return linkage


def resume_completed(
    db: Session, campaign: SmsCampaign, *, dry_run: bool = False
) -> dict:
    """Resume COMPLETED enrollments through steps added AFTER they finished —
    the "send the new parting message to leads who already went through the
    sequence" action. A lead completes at the last step that existed at the
    time; steps appended later never reach them otherwise.

    Per resumed enrollment: re-activate at the first step past its position.
    A schedule step is timed wait_days/wait_minutes AFTER THE COMPLETION
    (already-elapsed waits send at the next window open — never before the
    step's own delay would have allowed). A reply step parks the enrollment
    "awaiting reply" — zero sends now, but the lead's next text gets
    answered instead of hitting a completed dead end.

    Safeties mirror catch_up_past_replies: only clean completions (never
    opted_out/manual/failed exits), consent/suppression re-checked per lead
    here and again at send time, naturally idempotent (resumed enrollments
    are active — a second run finds nothing), `dry_run` for the confirm-
    with-real-counts UI. Returns {queued, awaiting, skipped}; queued counts
    resumes that will SEND (schedule step next), awaiting counts parked-
    awaiting resumes. {"no_new_steps": True} when no step extends past any
    completed enrollment."""
    steps = _steps(db, campaign.id)
    if not steps:
        return {"queued": 0, "awaiting": 0, "skipped": [], "no_new_steps": True}
    max_position = max(s.position for s in steps)

    candidates = (
        db.execute(
            select(SmsEnrollment).where(
                SmsEnrollment.campaign_id == campaign.id,
                SmsEnrollment.status == SMS_ENROLL_COMPLETED,
                SmsEnrollment.exit_reason.is_(None),
                SmsEnrollment.current_position < max_position,
            )
        )
        .scalars()
        .all()
    )
    if not candidates:
        return {"queued": 0, "awaiting": 0, "skipped": [], "no_new_steps": True}

    now = utcnow()
    queued = 0
    awaiting = 0
    skipped: List[dict] = []
    for e in candidates:
        nxt = next((s for s in steps if s.position > e.current_position), None)
        if nxt is None:
            continue
        contact = db.get(Contact, e.contact_id)
        if contact is None:
            skipped.append({"contact_id": e.contact_id, "reason": "not_found"})
            continue
        ok, reason = sms_consent.sendable(db, contact)
        if not ok:
            skipped.append({"contact_id": e.contact_id, "reason": reason})
            continue
        is_reply = (nxt.trigger or "schedule") == SMS_TRIGGER_REPLY
        if is_reply:
            awaiting += 1
        else:
            queued += 1
        if dry_run:
            continue
        e.status = SMS_ENROLL_ACTIVE
        e.exit_reason = None
        ended = _aware(e.ended_at) if e.ended_at else now
        e.ended_at = None
        e.current_position = nxt.position
        if is_reply:
            e.awaiting_reply_since = now
            e.next_run_at = None
        else:
            e.awaiting_reply_since = None
            base = max(ended + _step_delay(nxt), now)
            e.next_run_at = _next_valid_send_time(base, campaign) or base
    return {"queued": queued, "awaiting": awaiting, "skipped": skipped}


def catch_up_past_replies(
    db: Session, campaign: SmsCampaign, *, dry_run: bool = False
) -> dict:
    """Queue the campaign's reply step for leads who replied BEFORE the
    campaign had reply handling. Those enrollments are terminal — exited
    "replied" (the old exit_on_reply behavior), or completed with a
    post-sequence reply recorded — so a reply step added later can never
    reach them through the webhook path. This re-activates each one at the
    first applicable reply step, primes last_reply_body with the lead's
    ACTUAL most recent inbound text (so branch matching answers what they
    really said), and schedules at the next valid send window.

    Safeties:
    - Idempotent: an enrollment that already received ANY reply-step send is
      skipped ("already_responded") — running this twice never double-texts.
    - The consent gate re-checks at queue time (opted-out/suppressed leads
      are skipped, and the gateway re-checks again at send time).
    - `dry_run` computes the receipt without touching anything — the API
      uses it so the admin confirms real counts before anything queues.

    Who counts as "replied": evidence-based, NOT replied_at-based — the old
    code only stamped replied_at on stop-on-reply exits, so an
    exit_on_reply=false campaign's repliers (who just kept dripping to
    completion) carry no marker at all. A replier here is any enrollment
    whose contact has an inbound message AFTER the enrollment's first
    outbound send. Enrollments never replied to are silently ignored (they
    aren't in the receipt); active enrollments count too — a mid-drip
    replier jumps to the reply handler, exactly what handle_reply would
    have done had the feature existed when they replied.

    Returns {queued, skipped: [{contact_id, reason}]}; reasons:
    already_responded | no_applicable_step | no_reply_text | no_consent |
    suppressed | no_number | not_found. {"no_reply_step": True} when the
    campaign has no reply-triggered step at all."""
    steps = _steps(db, campaign.id)
    reply_steps = [s for s in steps if (s.trigger or "schedule") == SMS_TRIGGER_REPLY]
    if not reply_steps:
        return {"queued": 0, "skipped": [], "no_reply_step": True}
    reply_step_ids = [s.id for s in reply_steps]
    reply_positions = {s.position for s in reply_steps}

    candidates = (
        db.execute(
            select(SmsEnrollment).where(
                SmsEnrollment.campaign_id == campaign.id,
                SmsEnrollment.status.in_(
                    [SMS_ENROLL_ACTIVE, SMS_ENROLL_EXITED, SMS_ENROLL_COMPLETED]
                ),
                # "replied" exits, clean completions, and live enrollments —
                # never resurrect opted_out/manual/failed exits.
                (SmsEnrollment.exit_reason.is_(None))
                | (SmsEnrollment.exit_reason == "replied"),
            )
        )
        .scalars()
        .all()
    )

    # Bulk prefetches so a large audience doesn't turn into per-row queries.
    responded_ids = set(
        db.execute(
            select(SmsMessage.enrollment_id)
            .where(
                SmsMessage.campaign_id == campaign.id,
                SmsMessage.direction == SMS_DIR_OUT,
                SmsMessage.step_id.in_(reply_step_ids),
                SmsMessage.enrollment_id.is_not(None),
            )
            .distinct()
        ).scalars()
    )
    first_out_by_enrollment = {
        eid: created
        for eid, created in db.execute(
            select(SmsMessage.enrollment_id, func.min(SmsMessage.created_at))
            .where(
                SmsMessage.campaign_id == campaign.id,
                SmsMessage.direction == SMS_DIR_OUT,
                SmsMessage.enrollment_id.is_not(None),
            )
            .group_by(SmsMessage.enrollment_id)
        ).all()
    }
    contact_ids = [e.contact_id for e in candidates]
    last_in_by_contact: dict = {}
    if contact_ids:
        for m in db.execute(
            select(SmsMessage)
            .where(
                SmsMessage.organization_id == campaign.organization_id,
                SmsMessage.contact_id.in_(contact_ids),
                SmsMessage.direction == SMS_DIR_IN,
            )
            .order_by(SmsMessage.created_at.desc())
        ).scalars():
            last_in_by_contact.setdefault(m.contact_id, m)

    now = utcnow()
    queued = 0
    skipped: List[dict] = []
    for e in candidates:
        first_out = first_out_by_enrollment.get(e.id)
        last_in = last_in_by_contact.get(e.contact_id)
        replied = (
            first_out is not None
            and last_in is not None
            and _aware(last_in.created_at) >= _aware(first_out)
        ) or e.replied_at is not None
        if not replied:
            continue  # never replied — not part of this at all
        if e.id in responded_ids:
            skipped.append({"contact_id": e.contact_id, "reason": "already_responded"})
            continue
        if (
            e.status == SMS_ENROLL_ACTIVE
            and e.current_position in reply_positions
            and e.next_run_at is not None
            and (e.last_reply_body or "").strip()
        ):
            # handle_reply already routed this one — its scheduled response
            # (with the reply-delay honored) must not be re-timed to now.
            continue
        reply_step = next(
            (s for s in reply_steps if s.position >= e.current_position), None
        )
        if reply_step is None:
            skipped.append({"contact_id": e.contact_id, "reason": "no_applicable_step"})
            continue
        contact = db.get(Contact, e.contact_id)
        if contact is None:
            skipped.append({"contact_id": e.contact_id, "reason": "not_found"})
            continue
        ok, reason = sms_consent.sendable(db, contact)
        if not ok:
            skipped.append({"contact_id": e.contact_id, "reason": reason})
            continue
        if last_in is None or not (last_in.body or "").strip():
            # replied_at without any recorded inbound text (edge, e.g. a
            # provider-reported opt-in state) — nothing to branch on, and a
            # blank last_reply_body would just re-park the enrollment.
            skipped.append({"contact_id": e.contact_id, "reason": "no_reply_text"})
            continue
        queued += 1
        if dry_run:
            continue
        e.status = SMS_ENROLL_ACTIVE
        e.exit_reason = None
        e.ended_at = None
        e.current_position = reply_step.position
        e.awaiting_reply_since = None
        e.last_reply_body = last_in.body
        e.last_reply_at = _aware(last_in.created_at)
        e.replied_at = e.replied_at or _aware(last_in.created_at)
        # Their reply is in the past, so the "wait after their reply" delay
        # has already elapsed — respond at the next valid window open.
        e.next_run_at = _next_valid_send_time(now, campaign) or now
    return {"queued": queued, "skipped": skipped}


# --- the step state machine -------------------------------------------------


def process_enrollment(db: Session, enrollment: SmsEnrollment) -> None:
    """Advance one enrollment: park it (window/cap), send its current step, or
    exit it. At most one send per call."""
    now = utcnow()
    campaign = db.get(SmsCampaign, enrollment.campaign_id)
    if campaign is None or campaign.status != SMS_CAMPAIGN_ACTIVE:
        enrollment.next_run_at = None  # paused/archived campaign parks its enrollments
        return
    account = db.get(SmsAccount, campaign.account_id)
    if account is None or account.status != SMS_ACCOUNT_ACTIVE:
        enrollment.next_run_at = None  # reconnect flow re-arms
        return

    steps = _steps(db, campaign.id)
    current = next(
        (s for s in steps if s.position >= enrollment.current_position), None
    )
    if current is None:
        _end(enrollment, SMS_ENROLL_COMPLETED)
        return
    enrollment.current_position = current.position

    # A reply-triggered step only fires once the lead has actually replied —
    # the webhook (handle_reply) is what schedules it. Reaching it here with
    # no reply recorded (e.g. a campaign whose FIRST step is a reply handler,
    # or a re-armed enrollment) parks the enrollment awaiting one.
    if (current.trigger or "schedule") == SMS_TRIGGER_REPLY and not (
        enrollment.last_reply_body or ""
    ).strip():
        enrollment.awaiting_reply_since = enrollment.awaiting_reply_since or now
        enrollment.next_run_at = None
        return

    # Window / day gating (campaign timezone) — the TCPA quiet-hours guard.
    valid_at = _next_valid_send_time(now, campaign)
    if valid_at is None:
        enrollment.next_run_at = None  # misconfigured window — park
        return
    if valid_at > now:
        enrollment.next_run_at = valid_at
        return

    # Campaign daily cap (UTC) — an early exit that avoids rendering/sending
    # only to have the gateway hand back CAP_REACHED anyway. Reply-step
    # responses are exempt (mirrors the gateway): they answer a lead's own
    # inbound message, and timeliness is the point — a saturated cold-drip
    # cap must not park a conversational response for an hour.
    if (current.trigger or "schedule") != SMS_TRIGGER_REPLY and (
        gateway.campaign_sends_today(db, campaign) >= campaign.daily_cap
    ):
        enrollment.next_run_at = now + dt.timedelta(hours=1)
        return

    org = db.get(Organization, campaign.organization_id)
    contact = db.get(Contact, enrollment.contact_id)
    # Reply steps pick a response branch from what the lead actually said;
    # no match (or no branches) sends the step's default body.
    branch_body: Optional[str] = None
    if (current.trigger or "schedule") == SMS_TRIGGER_REPLY:
        branch_body, _branch_label = select_branch(
            db, org, current, enrollment.last_reply_body
        )
    body = render_full(
        db, org, enrollment, current, contact=contact, body_template=branch_body
    )

    # Send-time failsafes — all deterministic, so exit rather than retry.
    if not body.strip():
        log.warning("sms enrollment %s render guard: blank body; exiting", enrollment.id)
        _end(enrollment, SMS_ENROLL_EXITED, "render_empty")
        return
    if "{{" in body:
        log.warning(
            "sms enrollment %s render guard: leftover template braces; exiting",
            enrollment.id,
        )
        _end(enrollment, SMS_ENROLL_EXITED, "render_error")
        return
    # Segment cap must be measured on what ACTUALLY ships: the gateway prepends
    # "OrgName: " and appends the STOP footer on step 1 (when the campaign's
    # compliance footer is on), so a body that fits at 3 segments bare can ship
    # over the cap once the footer is added. Check the footered form — reusing
    # the real gateway.apply_compliance_suffix so the two never drift on the
    # exact suffix text — with the same first_step/include_footer inputs the
    # gateway itself derives for this send.
    metered_body = gateway.apply_compliance_suffix(
        body,
        org.name if org else "",
        first_step=(current.position == 1),
        include_footer=campaign.include_compliance_footer,
    )
    if segment_count(metered_body) > MAX_RENDERED_SEGMENTS:
        log.warning(
            "sms enrollment %s render guard: %d segments > cap %d; exiting",
            enrollment.id,
            segment_count(metered_body),
            MAX_RENDERED_SEGMENTS,
        )
        _end(enrollment, SMS_ENROLL_EXITED, "too_long")
        return

    code, msg = gateway.send(
        db,
        account,
        contact,
        body,
        kind=SMS_KIND_CAMPAIGN,
        campaign=campaign,
        step=current,
        enrollment_id=enrollment.id,
        org_name=(org.name if org else ""),
        now=now,
    )
    del msg  # the ledger row itself isn't needed by the engine

    if code == gateway.SENT:
        # A consumed reply doesn't re-fire a later reply step — the NEXT reply
        # step waits for the lead's NEXT message.
        if (current.trigger or "schedule") == SMS_TRIGGER_REPLY:
            enrollment.last_reply_body = None
        nxt = next((s for s in steps if s.position > current.position), None)
        if nxt is None:
            _end(enrollment, SMS_ENROLL_COMPLETED)
            return
        enrollment.current_position = nxt.position
        if (nxt.trigger or "schedule") == SMS_TRIGGER_REPLY:
            # The next step waits for the lead — park until the webhook
            # schedules it (handle_reply).
            enrollment.awaiting_reply_since = now
            enrollment.next_run_at = None
            return
        base = now + _step_delay(nxt)
        enrollment.next_run_at = _next_valid_send_time(base, campaign) or base
        return
    if code == gateway.CAP_REACHED:
        enrollment.next_run_at = now + dt.timedelta(hours=1)
        return
    if code == gateway.SPACING:
        # Per-account min-spacing throttle (anti-detection pacing, BlueBubbles
        # especially) — retry the SAME step shortly, at a jittered interval, so
        # a burst of due enrollments drips out at a human cadence instead of
        # firing all at once.
        enrollment.next_run_at = gateway.next_spacing_time(db, account, now=now)
        return
    if code == gateway.OUTSIDE_WINDOW:
        # Our own window check above passed but the gateway's re-check
        # disagreed (clock skew right at a window edge, or the campaign's
        # window was edited mid-tick) — recompute and park rather than spin.
        enrollment.next_run_at = (
            _next_valid_send_time(now + dt.timedelta(minutes=1), campaign)
            or now + dt.timedelta(hours=1)
        )
        return
    if code == gateway.SUPPRESSED:
        # STOP / do-not-contact — never retry (TCPA compliance).
        _end(enrollment, SMS_ENROLL_EXITED, "opted_out")
        return
    if code == gateway.BLOCKED:
        # Consent gate refused at send-time (e.g. consent was revoked between
        # enroll and send without a suppression row) or the account flipped
        # inactive — no future retry can succeed compliantly.
        _end(enrollment, SMS_ENROLL_EXITED, "failed")
        return
    # FAILED — hard provider failure. Distinct status from EXITED so a broken
    # account's failures are visibly different from compliance exits.
    _end(enrollment, SMS_ENROLL_ERROR, "failed")


def run_due(db: Session, limit: int = 200) -> int:
    """One scheduler tick: process due enrollments, isolated per enrollment so
    one failure never stalls the rest (email_campaigns.run_due pattern).

    Reply-step responses are processed FIRST within the batch: the account
    min-spacing throttle effectively serializes an account to ~one send per
    tick, so plain FIFO would leave a time-sensitive "answer their reply in 3
    minutes" send queued behind an arbitrary backlog of cold drip sends."""
    now = utcnow()
    due = (
        db.execute(
            select(SmsEnrollment)
            .where(
                SmsEnrollment.status == SMS_ENROLL_ACTIVE,
                SmsEnrollment.next_run_at.is_not(None),
                SmsEnrollment.next_run_at <= now,
            )
            .order_by(SmsEnrollment.next_run_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    if due:
        steps_by_campaign = {
            cid: _steps(db, cid) for cid in {e.campaign_id for e in due}
        }

        def _priority(e: SmsEnrollment):
            current = next(
                (
                    s
                    for s in steps_by_campaign.get(e.campaign_id, [])
                    if s.position >= e.current_position
                ),
                None,
            )
            is_reply = current is not None and (
                (current.trigger or "schedule") == SMS_TRIGGER_REPLY
            )
            return (0 if is_reply else 1, _aware(e.next_run_at))

        due.sort(key=_priority)
    processed = 0
    for enrollment in due:
        try:
            process_enrollment(db, enrollment)
            db.commit()
            processed += 1
        except Exception:
            log.exception("sms enrollment %s tick failed", enrollment.id)
            db.rollback()
    return processed


def exit_manual(db: Session, enrollment: SmsEnrollment) -> None:
    _end(enrollment, SMS_ENROLL_EXITED, "manual")
