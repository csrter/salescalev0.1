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
import logging
from typing import List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.core import Organization
from ..models.crm import Contact
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
from . import email_personalize  # reused for token rendering (regex + casing/tidy)
from . import sms_consent
from . import sms_send as gateway

log = logging.getLogger("salescale.sms_outreach")

# The only tokens an SMS template may use — no ai_snippet (no AI in SMS
# personalization) and no unsubscribe_url (STOP is the SMS opt-out
# mechanism, not a link). A typo'd or email-only token would otherwise
# silently render as "" in every sent text; the steps API rejects it at save
# time instead (see unknown_tokens below).
SMS_KNOWN_TOKENS = frozenset({"first_name", "last_name", "company", "city", "state"})


def unknown_tokens(template: Optional[str]) -> list:
    """Tokens in `template` that aren't in SMS_KNOWN_TOKENS. Reuses email
    personalize's token regex (same {{name}} / {{name|fallback}} grammar) so
    the two modules never drift on tokenizing syntax; the allowed-token set
    is SMS's own, deliberately narrower than email's."""
    out: list = []
    for m in email_personalize._TOKEN_RE.finditer(template or ""):
        name = m.group(1)
        if name in SMS_KNOWN_TOKENS:
            continue
        if name not in out:
            out.append(name)
    return out


def render_body(db: Session, contact: Contact, step: SmsStep) -> str:
    """Render one step's body for one contact. Reuses email_personalize's
    template substitution (casing normalization + the emptied-token tidy
    pass) so `{{first_name|there}}` etc. behave identically to email. `extra`
    is empty — SMS has no ai_snippet/unsubscribe_url tokens to inject."""
    company_name = email_personalize._company_name(db, contact)
    return email_personalize._render_template(
        step.body_template or "", contact, company_name, {}
    )


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
    body = render_body(db, contact, current)

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
