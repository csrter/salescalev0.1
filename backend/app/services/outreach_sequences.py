"""Outbound sequence engine: fully automated step firing.

State machine per enrollment: steps run in position order — message steps
send through the outreach_send gateway (which owns window/cap/audit rules),
wait steps schedule next_run_at, condition steps branch on whether the peer
has replied since enrollment. A queued (window-closed) send parks the
enrollment (waiting_window=True, next_run_at=None); the ingest path re-arms
it the moment an inbound message reopens the window.

Exits, all automatic (spec: "no manual cleanup"): peer replied (when
exit_on_reply), CRM stage/deal change (crm.py calls exit_for_contact),
manual unenroll, account auth loss.

Per-account isolation in run_due mirrors insights_sync: one account's
failure or rate limit never stalls the others.
"""

import datetime as dt
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.outreach import (
    ENROLL_ACTIVE,
    ENROLL_COMPLETED,
    ENROLL_EXITED,
    EXIT_MANUAL,
    EXIT_REPLIED,
    EXIT_STAGE_CHANGE,
    KIND_SEQUENCE,
    MSG_SENT,
    SEQ_ACTIVE,
    STEP_CONDITION,
    STEP_MESSAGE,
    STEP_WAIT,
    InstagramAccount,
    OutreachConversation,
    OutreachEnrollment,
    OutreachMessage,
    OutreachSequence,
    OutreachStep,
)
from . import outreach_send

log = logging.getLogger("salescale.outreach")

# A/B promotion: once both variants have this many sends, promote the one
# with the higher reply rate (overridable per sequence via settings).
DEFAULT_PROMOTION_MIN_SENDS = 20


def enroll(
    db: Session,
    sequence: OutreachSequence,
    convo: OutreachConversation,
    *,
    contact_id: Optional[str] = None,
    prospect_id: Optional[str] = None,
    enrolled_by: str = "manual",
) -> Optional[OutreachEnrollment]:
    """Idempotent: an existing active enrollment for this (sequence, convo)
    is returned untouched; a previously exited one blocks re-enrollment only
    via the unique constraint — we reactivate it instead (fresh run)."""
    existing = db.execute(
        select(OutreachEnrollment).where(
            OutreachEnrollment.sequence_id == sequence.id,
            OutreachEnrollment.conversation_id == convo.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.status == ENROLL_ACTIVE:
            return existing
        existing.status = ENROLL_ACTIVE
        existing.exit_reason = None
        existing.current_position = 0
        existing.next_run_at = utcnow()
        existing.waiting_window = False
        existing.replied_at = None
        existing.ended_at = None
        return existing
    enrollment = OutreachEnrollment(
        organization_id=convo.organization_id,
        client_id=convo.client_id,
        sequence_id=sequence.id,
        conversation_id=convo.id,
        contact_id=contact_id or convo.contact_id,
        prospect_id=prospect_id,
        next_run_at=utcnow(),
        enrolled_by=enrolled_by,
    )
    db.add(enrollment)
    db.flush()
    return enrollment


def _end(enrollment: OutreachEnrollment, status: str, reason: Optional[str] = None):
    enrollment.status = status
    enrollment.exit_reason = reason
    enrollment.next_run_at = None
    enrollment.waiting_window = False
    enrollment.ended_at = utcnow()


def exit_for_contact(db: Session, contact_id: str, reason: str = EXIT_STAGE_CHANGE):
    """CRM sync hook: a stage/deal change on this contact exits every active
    enrollment automatically."""
    rows = (
        db.execute(
            select(OutreachEnrollment).where(
                OutreachEnrollment.contact_id == contact_id,
                OutreachEnrollment.status == ENROLL_ACTIVE,
            )
        )
        .scalars()
        .all()
    )
    for e in rows:
        _end(e, ENROLL_EXITED, reason)
    return len(rows)


def exit_manual(db: Session, enrollment: OutreachEnrollment):
    _end(enrollment, ENROLL_EXITED, EXIT_MANUAL)


def handle_reply(db: Session, convo: OutreachConversation):
    """Inbound message arrived: record the reply on active enrollments (and
    exit those configured to stop on reply), and credit reply attribution to
    the most recent outbound sequence/rule message for variant stats."""
    now = utcnow()
    last_out = db.execute(
        select(OutreachMessage)
        .where(
            OutreachMessage.conversation_id == convo.id,
            OutreachMessage.direction == "out",
            OutreachMessage.status == MSG_SENT,
        )
        .order_by(OutreachMessage.sent_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if last_out is not None and not last_out.replied_to:
        last_out.replied_to = True
        if last_out.step_id:
            _maybe_promote(db, db.get(OutreachStep, last_out.step_id))

    # Reply attribution covers every enrollment on the thread (a reply after
    # the sequence completed still counts in its funnel); only ACTIVE ones
    # can additionally exit-on-reply.
    enrollments = (
        db.execute(
            select(OutreachEnrollment).where(
                OutreachEnrollment.conversation_id == convo.id
            )
        )
        .scalars()
        .all()
    )
    for e in enrollments:
        if e.replied_at is None:
            e.replied_at = now
        if e.status != ENROLL_ACTIVE:
            continue
        seq = db.get(OutreachSequence, e.sequence_id)
        if seq is not None and seq.exit_on_reply:
            _end(e, ENROLL_EXITED, EXIT_REPLIED)


def rearm_waiting(db: Session, convo: OutreachConversation):
    """Window just reopened (inbound message): wake enrollments parked on it."""
    rows = (
        db.execute(
            select(OutreachEnrollment).where(
                OutreachEnrollment.conversation_id == convo.id,
                OutreachEnrollment.status == ENROLL_ACTIVE,
                OutreachEnrollment.waiting_window.is_(True),
            )
        )
        .scalars()
        .all()
    )
    for e in rows:
        e.waiting_window = False
        e.next_run_at = utcnow()


def _steps(db: Session, sequence_id: str) -> list[OutreachStep]:
    return list(
        db.execute(
            select(OutreachStep)
            .where(OutreachStep.sequence_id == sequence_id)
            .order_by(OutreachStep.position)
        ).scalars()
    )


def _pick_variant(enrollment: OutreachEnrollment, step: OutreachStep) -> Optional[str]:
    if step.kind != STEP_MESSAGE or not step.text_b:
        return None
    if step.promoted_variant in ("a", "b"):
        return step.promoted_variant
    assignments = dict(enrollment.variant_assignments or {})
    if step.id in assignments:
        return assignments[step.id]
    # Stable, roughly balanced split without randomness (test-determinism).
    variant = "a" if (hash(enrollment.id + step.id) & 1) == 0 else "b"
    assignments[step.id] = variant
    enrollment.variant_assignments = assignments
    return variant


def _maybe_promote(db: Session, step: Optional[OutreachStep]):
    """Reply-rate-based variant promotion once both variants have enough
    sends. Called on reply attribution — cheap counts, no scan job."""
    if step is None or step.kind != STEP_MESSAGE or not step.text_b:
        return
    if step.promoted_variant:
        return
    seq = db.get(OutreachSequence, step.sequence_id)
    min_sends = (seq.settings or {}).get(
        "promotion_min_sends", DEFAULT_PROMOTION_MIN_SENDS
    ) if seq else DEFAULT_PROMOTION_MIN_SENDS
    stats = {}
    for variant in ("a", "b"):
        rows = db.execute(
            select(OutreachMessage.replied_to).where(
                OutreachMessage.step_id == step.id,
                OutreachMessage.variant == variant,
                OutreachMessage.status == MSG_SENT,
            )
        ).all()
        sent = len(rows)
        replies = sum(1 for (r,) in rows if r)
        stats[variant] = (sent, replies)
    if stats["a"][0] < min_sends or stats["b"][0] < min_sends:
        return
    rate_a = stats["a"][1] / stats["a"][0]
    rate_b = stats["b"][1] / stats["b"][0]
    if rate_a != rate_b:
        step.promoted_variant = "a" if rate_a > rate_b else "b"
        log.info(
            "Promoted variant %s on step %s (a=%.2f b=%.2f)",
            step.promoted_variant, step.id, rate_a, rate_b,
        )


def _branch_target(action: Optional[str], steps: list[OutreachStep]) -> Optional[int]:
    """Resolve a condition branch action to the next position index.
    None = exit."""
    if action in (None, "", "exit"):
        return None
    if action == "continue":
        return -1  # sentinel: advance one
    if action.startswith("goto:"):
        try:
            pos = int(action.split(":", 1)[1])
        except ValueError:
            return None
        if any(s.position == pos for s in steps):
            return pos
    return None


def process_enrollment(db: Session, enrollment: OutreachEnrollment) -> None:
    """Advance one enrollment as far as it can go right now. Stops on: a wait
    schedule, a queued/capped send, review hold, completion, or exit."""
    now = utcnow()
    seq = db.get(OutreachSequence, enrollment.sequence_id)
    if seq is None or seq.status != SEQ_ACTIVE:
        enrollment.next_run_at = None  # paused sequence parks its enrollments
        return
    convo = db.get(OutreachConversation, enrollment.conversation_id)
    account = db.get(InstagramAccount, convo.account_id)
    if account is None or account.status != "active":
        enrollment.next_run_at = None  # reconnect flow re-arms (api/outreach)
        return
    steps = _steps(db, seq.id)

    guard = 0
    while guard < 50:  # cycle guard for goto loops
        guard += 1
        current = next(
            (s for s in steps if s.position >= enrollment.current_position), None
        )
        if current is None:
            _end(enrollment, ENROLL_COMPLETED)
            return
        enrollment.current_position = current.position

        if current.kind == STEP_WAIT:
            enrollment.current_position = current.position + 1
            enrollment.next_run_at = now + dt.timedelta(hours=current.wait_hours or 0)
            return

        if current.kind == STEP_CONDITION:
            replied = enrollment.replied_at is not None
            action = current.on_true if replied else current.on_false
            target = _branch_target(action, steps)
            if target is None:
                _end(enrollment, ENROLL_EXITED, EXIT_REPLIED if replied else None)
                return
            enrollment.current_position = (
                current.position + 1 if target == -1 else target
            )
            continue

        # message step
        variant = _pick_variant(enrollment, step=current)
        text = current.text_b if variant == "b" else current.text_a
        hold = bool(
            seq.review_first_day
            and seq.activated_at is not None
            and now - _aware(seq.activated_at) < dt.timedelta(hours=24)
        )
        code, _msg = outreach_send.send(
            db,
            account,
            convo,
            text or "",
            kind=KIND_SEQUENCE,
            enrollment_id=enrollment.id,
            step_id=current.id,
            variant=variant,
            hold_for_review=hold,
        )
        if code == outreach_send.SENT or code == outreach_send.PENDING_REVIEW:
            enrollment.current_position = current.position + 1
            continue  # immediately process a following wait/condition
        if code == outreach_send.QUEUED:
            # The queued row will send on window reopen; park until then.
            enrollment.current_position = current.position + 1
            enrollment.next_run_at = None
            enrollment.waiting_window = True
            return
        if code == outreach_send.CAP_REACHED:
            enrollment.next_run_at = now + dt.timedelta(hours=1)  # retry after cap
            return
        if code == outreach_send.AUTH_ERROR:
            enrollment.next_run_at = None  # reconnect re-arms
            return
        # FAILED: recorded in the audit trail; skip the step rather than
        # retrying a permanently-rejected message forever.
        enrollment.current_position = current.position + 1
        continue
    log.warning("Enrollment %s hit the cycle guard — check goto loops", enrollment.id)
    enrollment.next_run_at = now + dt.timedelta(hours=1)


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def run_due(db: Session, limit: int = 200) -> int:
    """One scheduler tick: process due enrollments, isolated per enrollment
    so one failure never stalls the rest (insights_sync pattern)."""
    now = utcnow()
    due = (
        db.execute(
            select(OutreachEnrollment)
            .where(
                OutreachEnrollment.status == ENROLL_ACTIVE,
                OutreachEnrollment.next_run_at.is_not(None),
                OutreachEnrollment.next_run_at <= now,
            )
            .order_by(OutreachEnrollment.next_run_at)
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
            log.exception("Enrollment %s tick failed", enrollment.id)
            db.rollback()
    return processed
