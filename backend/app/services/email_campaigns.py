"""Cold-email campaign engine (Phase 2): enrollment + the step state machine.

Mirrors the IG outreach_sequences engine's shape (enroll → schedule → send →
advance → exit), adapted to email's model:

- Steps are all message steps; `wait_days` is a property of the step (the delay
  before it fires), so there are no separate wait/condition step kinds — one
  send per enrollment per tick, then next_run_at is set to the following step's
  wait, landed inside a valid send window.
- Every send routes through the ONE gateway (email_outreach_send.send), so
  suppression, the verified-email gate, the per-account (warmup-ramped) daily
  cap, and the audit trail all hold — this engine never touches SMTP.
- Compliance exits are automatic and driven by the IMAP sync hook registry:
  a reply exits (when exit_on_reply), a bounce exits, and an unsubscribe exits
  the contact from EVERY campaign (opt-out is global, CLAUDE.md #9).

Time & window rules (documented, internally consistent):
- The campaign's daily_cap counts messages transmitted since UTC midnight
  (sent_at, same UTC accounting as the per-account cap in phase 1).
- Send window/day gating is evaluated in the campaign's own timezone
  (zoneinfo; falls back to UTC if the tz string is invalid). Outside the
  window, the enrollment is parked with next_run_at set to the next window
  open — it is never sent early and never errors.
- CAP_REACHED (account or campaign) parks the enrollment for a 1h retry.
- A hard send FAILURE ends the enrollment in `error` (the mailbox itself flips
  to error and every other enrollment on it parks) rather than retrying a
  broken mailbox forever.
"""

import datetime as dt
import logging
from typing import List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.core import Organization
from ..models.crm import Contact
from ..models.email_outreach import (
    ACCOUNT_ACTIVE,
    CAMPAIGN_ACTIVE,
    DIR_OUT,
    ENROLL_ACTIVE,
    ENROLL_COMPLETED,
    ENROLL_ERROR,
    ENROLL_EXITED,
    EXIT_BOUNCED,
    EXIT_ERROR,
    EXIT_MANUAL,
    EXIT_RENDER_ERROR,
    EXIT_REPLIED,
    EXIT_UNSUBSCRIBED,
    KIND_CAMPAIGN,
    EmailAccount,
    EmailCampaign,
    EmailEnrollment,
    EmailMessage,
    EmailStep,
    EmailSuppression,
    EmailThread,
)
from . import email_outreach_send as gateway
from . import email_outreach_sync, email_personalize, email_verification, email_warmup

log = logging.getLogger("salescale.email_outreach")


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


# The one literal token the SEND GATEWAY resolves (per-message unsubscribe
# link) — never the render engine, so its survival past rendering is
# expected and must not trip the leftover-brace guard below.
_ALLOWED_LEFTOVER = "{{unsubscribe_url}}"


def _has_leftover_braces(text: Optional[str]) -> bool:
    if not text:
        return False
    return "{{" in text.replace(_ALLOWED_LEFTOVER, "")


# --- enrollment -------------------------------------------------------------


def enroll_contacts(
    db: Session,
    campaign: EmailCampaign,
    contact_ids: List[str],
    enrolled_by: Optional[str] = None,
) -> dict:
    """Enroll a set of CRM contacts into a campaign. Partitions the batch
    through the SAME shared gate every send feature uses
    (email_verification.sendable) so cross-tenant/verification behaviour is
    identical: verified-invalid excluded, risky enrolled-but-warned. Returns
    {enrolled, risky: [{contact_id, email}], skipped: [{contact_id, reason}]}.
    Reasons: not_found | invalid_email | no_email | suppressed |
    already_enrolled."""
    org_id = campaign.organization_id
    resolved: List[Contact] = []
    skipped: List[dict] = []
    for cid in contact_ids:
        c = db.get(Contact, cid)
        if c is None or c.organization_id != org_id:
            skipped.append({"contact_id": cid, "reason": "not_found"})
            continue
        resolved.append(c)

    ok, invalid, risky = email_verification.sendable(resolved)
    risky_ids = {c.id for c in risky}
    for c in invalid:
        skipped.append({"contact_id": c.id, "reason": "invalid_email"})

    now = utcnow()
    enrolled = 0
    risky_out: List[dict] = []
    for c in ok:
        if not c.email:
            skipped.append({"contact_id": c.id, "reason": "no_email"})
            continue
        if gateway.is_suppressed(db, org_id, c.email):
            skipped.append({"contact_id": c.id, "reason": "suppressed"})
            continue
        existing = db.execute(
            select(EmailEnrollment.id).where(
                EmailEnrollment.campaign_id == campaign.id,
                EmailEnrollment.contact_id == c.id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            skipped.append({"contact_id": c.id, "reason": "already_enrolled"})
            continue
        db.add(
            EmailEnrollment(
                organization_id=org_id,
                campaign_id=campaign.id,
                contact_id=c.id,
                status=ENROLL_ACTIVE,
                current_position=1,
                next_run_at=now,
                enrolled_by=enrolled_by,
            )
        )
        enrolled += 1
        if c.id in risky_ids:
            risky_out.append({"contact_id": c.id, "email": c.email})
    db.flush()
    return {"enrolled": enrolled, "risky": risky_out, "skipped": skipped}


# --- window / cap helpers ---------------------------------------------------


def _tz(campaign: EmailCampaign):
    try:
        return ZoneInfo(campaign.timezone or "UTC")
    except Exception:
        return dt.timezone.utc


def _next_valid_send_time(
    after: dt.datetime, campaign: EmailCampaign
) -> Optional[dt.datetime]:
    """Earliest UTC datetime >= `after` that falls inside the campaign's send
    window on an allowed weekday, evaluated in the campaign's timezone. Returns
    `after` (in UTC) when it is already inside a window, or None when the
    campaign has no valid window at all (empty send_days / start>=end)."""
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
                # `local`, not the original `after` — identical on the first
                # loop iteration, but once the loop has advanced past rejected
                # days, returning the stale `after` could schedule a send on a
                # disallowed day (manifests when send_window_start == 0, where
                # an advanced day's midnight passes the open_t check).
                return local.astimezone(dt.timezone.utc)
        local = (local + dt.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    return None


def _campaign_sends_today(db: Session, campaign: EmailCampaign) -> int:
    """Messages this campaign transmitted since UTC midnight (sent_at) — the
    campaign daily_cap accounting unit, same UTC basis as the account cap."""
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.execute(
            select(func.count(EmailMessage.id)).where(
                EmailMessage.campaign_id == campaign.id,
                EmailMessage.direction == DIR_OUT,
                EmailMessage.sent_at >= day_start,
            )
        ).scalar_one()
        or 0
    )


def _steps(db: Session, campaign_id: str) -> List[EmailStep]:
    return list(
        db.execute(
            select(EmailStep)
            .where(EmailStep.campaign_id == campaign_id)
            .order_by(EmailStep.position)
        ).scalars()
    )


def _last_thread_message(db: Session, thread_id: str) -> Optional[EmailMessage]:
    return db.execute(
        select(EmailMessage)
        .where(
            EmailMessage.thread_id == thread_id,
            EmailMessage.message_id_header.is_not(None),
        )
        .order_by(EmailMessage.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _end(
    enrollment: EmailEnrollment, status: str, reason: Optional[str] = None
) -> None:
    enrollment.status = status
    enrollment.exit_reason = reason
    enrollment.next_run_at = None
    enrollment.ended_at = utcnow()


# --- the step state machine -------------------------------------------------


def process_enrollment(db: Session, enrollment: EmailEnrollment) -> None:
    """Advance one enrollment: park it (window/cap), send its current step, or
    exit it. At most one send per call."""
    now = utcnow()
    campaign = db.get(EmailCampaign, enrollment.campaign_id)
    if campaign is None or campaign.status != CAMPAIGN_ACTIVE:
        enrollment.next_run_at = None  # paused/archived campaign parks its enrollments
        return
    account = db.get(EmailAccount, campaign.account_id)
    if account is None or account.status != ACCOUNT_ACTIVE:
        enrollment.next_run_at = None  # reconnect flow re-arms
        return

    steps = _steps(db, campaign.id)
    current = next(
        (s for s in steps if s.position >= enrollment.current_position), None
    )
    if current is None:
        _end(enrollment, ENROLL_COMPLETED)
        return
    enrollment.current_position = current.position

    # Window / day gating (campaign timezone).
    valid_at = _next_valid_send_time(now, campaign)
    if valid_at is None:
        enrollment.next_run_at = None  # misconfigured window — park
        return
    if valid_at > now:
        enrollment.next_run_at = valid_at
        return

    # Campaign daily cap (UTC).
    if _campaign_sends_today(db, campaign) >= campaign.daily_cap:
        enrollment.next_run_at = now + dt.timedelta(hours=1)
        return

    org = db.get(Organization, campaign.organization_id)
    contact = db.get(Contact, enrollment.contact_id)
    subject, body = email_personalize.render_full(
        db, org, enrollment, current, campaign, contact=contact
    )
    # Render guard: a blank body, or a leftover "{{" (an unclosed #if, or a
    # typo the save-time 422 somehow missed) is deterministic — retrying
    # won't fix it, so exit rather than defer. {{unsubscribe_url}} is the one
    # allowed literal token; the gateway resolves it, not this engine.
    if not (body or "").strip() or _has_leftover_braces(subject) or _has_leftover_braces(
        body
    ):
        log.warning(
            "email enrollment %s render guard tripped (blank body or leftover"
            " template braces); exiting",
            enrollment.id,
        )
        _end(enrollment, ENROLL_EXITED, EXIT_RENDER_ERROR)
        return

    in_reply = (
        _last_thread_message(db, enrollment.thread_id)
        if enrollment.thread_id
        else None
    )
    code, msg = gateway.send(
        db,
        account,
        to_contact=contact,
        subject=subject,
        body_text=body,
        kind=KIND_CAMPAIGN,
        campaign=campaign,
        step=current,
        enrollment=enrollment,
        in_reply_to_message=in_reply,
    )

    if code == gateway.SENT:
        if enrollment.thread_id is None and msg is not None:
            enrollment.thread_id = msg.thread_id
        nxt = next((s for s in steps if s.position > current.position), None)
        if nxt is None:
            _end(enrollment, ENROLL_COMPLETED)
            return
        enrollment.current_position = nxt.position
        base = now + dt.timedelta(days=max(0, nxt.wait_days or 0))
        enrollment.next_run_at = _next_valid_send_time(base, campaign) or base
        return
    if code == gateway.CAP_REACHED:
        enrollment.next_run_at = now + dt.timedelta(hours=1)
        return
    if code == gateway.SUPPRESSED:
        # Opt-out / do-not-contact — never retry (compliance).
        _end(enrollment, ENROLL_EXITED, EXIT_UNSUBSCRIBED)
        return
    if code == gateway.BLOCKED:
        # Verified-invalid address — undeliverable, exit like a bounce.
        _end(enrollment, ENROLL_EXITED, EXIT_BOUNCED)
        return
    # FAILED — the mailbox flipped to error; don't spin on a broken account.
    _end(enrollment, ENROLL_ERROR, EXIT_ERROR)


def run_due(db: Session, limit: int = 200) -> int:
    """One scheduler tick: process due enrollments, isolated per enrollment so
    one failure never stalls the rest (outreach_sequences pattern)."""
    now = utcnow()
    due = (
        db.execute(
            select(EmailEnrollment)
            .where(
                EmailEnrollment.status == ENROLL_ACTIVE,
                EmailEnrollment.next_run_at.is_not(None),
                EmailEnrollment.next_run_at <= now,
            )
            .order_by(EmailEnrollment.next_run_at)
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
            log.exception("email enrollment %s tick failed", enrollment.id)
            db.rollback()
    return processed


# --- compliance exits (driven by the IMAP sync hook registry) ---------------


def exit_on_reply(db: Session, thread: EmailThread) -> None:
    """A prospect replied on this thread: stamp replied_at on every active
    enrollment on the thread (for reply-rate analytics) and exit those whose
    campaign is configured exit_on_reply."""
    now = utcnow()
    enrollments = (
        db.execute(
            select(EmailEnrollment).where(
                EmailEnrollment.thread_id == thread.id,
                EmailEnrollment.status == ENROLL_ACTIVE,
            )
        )
        .scalars()
        .all()
    )
    for e in enrollments:
        if e.status != ENROLL_ACTIVE:
            continue  # already exited (e.g. by an unsubscribe earlier this tick)
        if e.replied_at is None:
            e.replied_at = now
        campaign = db.get(EmailCampaign, e.campaign_id)
        if campaign is not None and campaign.exit_on_reply:
            _end(e, ENROLL_EXITED, EXIT_REPLIED)


def exit_on_bounce(db: Session, original_message: EmailMessage) -> None:
    """A send hard-bounced: exit the enrollment that produced it."""
    if not original_message.enrollment_id:
        return
    e = db.get(EmailEnrollment, original_message.enrollment_id)
    if e is not None and e.status == ENROLL_ACTIVE:
        _end(e, ENROLL_EXITED, EXIT_BOUNCED)


def exit_on_unsubscribe(db: Session, suppression: EmailSuppression) -> None:
    """An opt-out was recorded: stop EVERY active enrollment for that contact,
    across ALL campaigns (opt-out is global — CLAUDE.md #9). Resolves affected
    contacts by the suppression's contact_id and by matching the suppressed
    address org-wide (a manual suppression may carry no contact_id)."""
    contact_ids = set()
    if suppression.contact_id:
        contact_ids.add(suppression.contact_id)
    rows = db.execute(
        select(Contact.id).where(
            Contact.organization_id == suppression.organization_id,
            func.lower(Contact.email) == (suppression.email or "").casefold(),
        )
    ).all()
    contact_ids.update(cid for (cid,) in rows)
    if not contact_ids:
        return
    enrollments = (
        db.execute(
            select(EmailEnrollment).where(
                EmailEnrollment.organization_id == suppression.organization_id,
                EmailEnrollment.contact_id.in_(contact_ids),
                EmailEnrollment.status == ENROLL_ACTIVE,
            )
        )
        .scalars()
        .all()
    )
    for e in enrollments:
        _end(e, ENROLL_EXITED, EXIT_UNSUBSCRIBED)
    # Push the exits so a later hook in the same inbound (exit_on_reply) sees
    # them as no-longer-active rather than re-exiting from a stale read (the
    # session runs autoflush-off).
    db.flush()


def exit_manual(db: Session, enrollment: EmailEnrollment) -> None:
    _end(enrollment, ENROLL_EXITED, EXIT_MANUAL)


# --- hook registration ------------------------------------------------------


def register_hooks() -> None:
    """Wire the campaign/warmup exits into the IMAP sync registry. Called at
    import so the app (and the test suite, which imports app.main) always has
    real hooks, not the no-op defaults."""
    email_outreach_sync.hooks["on_reply"] = lambda db, thread, message: exit_on_reply(
        db, thread
    )
    email_outreach_sync.hooks["on_bounce"] = (
        lambda db, original_message, contact: exit_on_bounce(db, original_message)
    )
    email_outreach_sync.hooks["on_unsubscribe"] = (
        lambda db, suppression: exit_on_unsubscribe(db, suppression)
    )
    email_outreach_sync.hooks["on_warmup_received"] = email_warmup.on_warmup_received
    email_outreach_sync.hooks["on_warmup_junk"] = email_warmup.on_warmup_junk


register_hooks()
