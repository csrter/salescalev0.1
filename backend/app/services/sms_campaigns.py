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
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.core import Organization
from ..models.crm import Company, Contact
from ..models.sms_outreach import (
    SMS_ACCOUNT_ACTIVE,
    SMS_CAMPAIGN_ACTIVE,
    SMS_ENROLL_ACTIVE,
    SMS_ENROLL_COMPLETED,
    SMS_ENROLL_ERROR,
    SMS_ENROLL_EXITED,
    SMS_KIND_CAMPAIGN,
    SmsAccount,
    SmsCampaign,
    SmsEnrollment,
    SmsStep,
)
from . import ai_insights, ai_provider
from . import email_personalize  # reused for token rendering (regex + casing/tidy)
from . import sms_consent
from . import sms_send as gateway

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
        res = ai_provider.resolve(db, org)
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


def render_body(db: Session, contact: Contact, step: SmsStep, *, ai_snippet: str = "") -> str:
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
    one-line change to re-enable rather than a rebuild."""
    facts = email_personalize._company_facts(db, contact)
    extra = {"ai_snippet": ai_snippet}
    if not (contact.first_name or "").strip() and (facts.get("company") or "").strip():
        extra["first_name"] = business_case(facts["company"])
    return email_personalize._render_template(
        step.body_template or "", contact, facts, extra
    )


def _cached_or_generate(
    db: Session,
    org: Organization,
    contact: Contact,
    enrollment: Optional[SmsEnrollment],
    step: SmsStep,
) -> str:
    """Enrollment-cached snippet (ai_snippets: step_id -> text), mirroring
    email_personalize._cached_or_generate. Preview (enrollment=None)
    generates fresh without caching."""
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


def generate_ai_snippet(
    db: Session, org: Organization, contact: Contact, step: SmsStep
) -> str:
    """One short grounded sentence for this contact, or "" on any failure —
    unconfigured key, over the monthly AI cap, model/timeout error, or the
    output guard discarding an unsafe response. A send is never blocked on
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
        res = ai_provider.resolve(db, org)  # provider + model + BYO/operator key
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
        return cleaned
    except Exception as e:  # never let AI failure stop a send
        log.info("sms outreach AI snippet skipped for contact %s: %s", contact.id, e)
        return ""


def render_full(
    db: Session,
    org: Organization,
    enrollment: Optional[SmsEnrollment],
    step: SmsStep,
    contact: Optional[Contact] = None,
) -> str:
    """Full render incl. {{ai_snippet}} (from the enrollment cache,
    generating once if absent). `contact` may be passed directly (preview);
    otherwise it is loaded from the enrollment."""
    if contact is None:
        contact = db.get(Contact, enrollment.contact_id)
    snippet = _cached_or_generate(db, org, contact, enrollment, step)
    return render_body(db, contact, step, ai_snippet=snippet)


# --- enrollment -------------------------------------------------------------


def enroll_contacts(
    db: Session,
    campaign: SmsCampaign,
    contact_ids: List[str],
    enrolled_by: Optional[str] = None,
) -> dict:
    """Enroll a set of CRM contacts into a campaign. Every contact is routed
    through the SAME shared gate every SMS-send feature uses
    (sms_consent.sendable) so TCPA/isolation behaviour is identical wherever
    a send is attempted. Returns {enrolled, skipped: [{contact_id, reason}]}.
    Reasons: not_found | duplicate | no_number | no_consent | suppressed |
    already (already enrolled in this campaign)."""
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
            )
        )
        enrolled += 1
    db.flush()
    return {"enrolled": enrolled, "skipped": skipped}


# --- window / step helpers ---------------------------------------------------


def _tz(campaign: SmsCampaign):
    try:
        return ZoneInfo(campaign.timezone or "America/New_York")
    except Exception:
        return dt.timezone.utc


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
    the next valid window open, never immediately-past-quiet-hours."""
    now = utcnow()
    when = _next_valid_send_time(now, campaign) or now
    parked = (
        db.execute(
            select(SmsEnrollment).where(
                SmsEnrollment.campaign_id == campaign.id,
                SmsEnrollment.status == SMS_ENROLL_ACTIVE,
                SmsEnrollment.next_run_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for e in parked:
        e.next_run_at = when
    return len(parked)


def rearm_account(db: Session, account_id: str) -> int:
    """Account reconnected: re-arm parked enrollments across all of the
    account's ACTIVE campaigns (the \"reconnect flow re-arms\" contract in
    process_enrollment)."""
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
    return sum(rearm_parked(db, c) for c in campaigns)


def _end(enrollment: SmsEnrollment, status: str, reason: Optional[str] = None) -> None:
    enrollment.status = status
    enrollment.exit_reason = reason
    enrollment.next_run_at = None
    enrollment.ended_at = utcnow()


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

    # Window / day gating (campaign timezone) — the TCPA quiet-hours guard.
    valid_at = _next_valid_send_time(now, campaign)
    if valid_at is None:
        enrollment.next_run_at = None  # misconfigured window — park
        return
    if valid_at > now:
        enrollment.next_run_at = valid_at
        return

    # Campaign daily cap (UTC) — an early exit that avoids rendering/sending
    # only to have the gateway hand back CAP_REACHED anyway.
    if gateway.campaign_sends_today(db, campaign) >= campaign.daily_cap:
        enrollment.next_run_at = now + dt.timedelta(hours=1)
        return

    org = db.get(Organization, campaign.organization_id)
    contact = db.get(Contact, enrollment.contact_id)
    body = render_full(db, org, enrollment, current, contact=contact)

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
    if segment_count(body) > MAX_RENDERED_SEGMENTS:
        log.warning(
            "sms enrollment %s render guard: %d segments > cap %d; exiting",
            enrollment.id,
            segment_count(body),
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
        nxt = next((s for s in steps if s.position > current.position), None)
        if nxt is None:
            _end(enrollment, SMS_ENROLL_COMPLETED)
            return
        enrollment.current_position = nxt.position
        base = now + dt.timedelta(days=max(0, nxt.wait_days or 0))
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
    one failure never stalls the rest (email_campaigns.run_due pattern)."""
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
