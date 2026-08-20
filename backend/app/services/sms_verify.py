"""BlueBubbles post-send verification + automatic retry.

The no-Private-API BlueBubbles path (AppleScript sends through the EC2 Mac's
Text Message Forwarding) reports success when the message is HANDED to
Messages.app; the real outcome lands asynchronously in the Mac's Messages DB
(`error` column), sometimes a minute or more later. When the paired iPhone is
asleep or off-network, every send silently dies with error 4 while our ledger
says "sent" — 17 fl hvac opener texts were lost exactly this way on
2026-07-24 (05:43–05:52 Phoenix; the phone woke at 05:52 and sends resumed).

This pass closes that hole: it reads each send's true state back from the
relay and
- error != 0  → the row becomes FAILED (honest ledger/stats) and the send is
  RETRIED: campaign sends rewind the enrollment to the failed step (engine
  guards — window, pacing, consent — re-apply on the resend); notification
  texts need nothing here, lead_notify.retry_failed already picks up failed
  rows on its own backoff.
- error == 0  → verified; upgraded to DELIVERED/READ when the Mac has the
  corresponding receipt (delivery receipts are rare for green-bubble SMS, so
  "sent" stays the terminal state for most successful sends on this channel).

WHY THIS RE-POLLS INSTEAD OF CHECKING ONCE
The two outcomes are NOT equally trustworthy at the same age. A failure is
trustworthy the instant it appears — the Mac only writes a nonzero `error`
once it really failed. A success is not: the Mac stamps failures
asynchronously, so a read at 1-2 minutes can report error=0 on a send that
later flips to failed (observed live). The old pass resolved that tension by
checking ONCE, late (4 minutes) — which made every failure wait out the
success-safety delay before the lead's text could be retried.

So the pass now polls EARLY and repeatedly, and terminalizes the two outcomes
on different clocks: a failure ends the row on first sight, while a success is
provisional (recorded, re-polled) until the row is old enough that a late
failure stamp would already have landed. verified_at means TERMINAL — set only
when the outcome is final; last_checked_at/check_attempts drive the re-poll.
Net effect: failed sends are caught and retried in about a minute instead of
four-plus, with a STRONGER success guarantee than before, since a success is
now confirmed by a later look rather than a single early guess.

Retries per (enrollment, step) are capped so a dead device can't machine-gun
the same lead forever. Best-effort: a relay outage leaves rows unverified for
the next tick, and nothing here ever raises out of the scheduler. Rows that
age out unconfirmed keep verified_at NULL on purpose — "we never got an
answer" is a real state that stats surface, not something to paper over by
stamping a verification that never happened.
"""

import datetime as dt
import logging
import random
from typing import Optional

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.sms_outreach import (
    SMS_ENROLL_ACTIVE,
    SMS_MSG_DELIVERED,
    SMS_MSG_FAILED,
    SMS_MSG_READ,
    SMS_MSG_SENT,
    SmsAccount,
    SmsEnrollment,
    SmsMessage,
    SmsStep,
    advance_status,
)
from ..security import decrypt_secret

log = logging.getLogger("salescale.sms_verify")

# How soon after a send the first read happens. A nonzero `error` is already
# meaningful here, so this is the real "how fast do we catch a dead send" dial.
FIRST_CHECK_AGE = dt.timedelta(seconds=45)
# Minimum gap between re-polls of the same row (relay budget — see MAX_CHECKS).
RECHECK_INTERVAL = dt.timedelta(seconds=60)
# A success is only TERMINAL once the row is this old: past this point a late
# failure stamp from Messages.app would already have landed. This is the
# safety constant — lowering it risks retiring a failed send as successful.
CONFIRM_SUCCESS_AGE = dt.timedelta(minutes=4)
# Stop polling a row that has never resolved. It keeps verified_at NULL and is
# reported as unconfirmed rather than silently counted as a success.
MAX_AGE = dt.timedelta(hours=24)
MAX_CHECKS = 8
BATCH_PER_ACCOUNT = 25
MAX_STEP_RETRIES = 3


class RelayUnavailable(Exception):
    """The relay could not answer (auth rejected, 5xx, transport error).

    Distinct from "the relay answered and does not know this guid": treating
    the two the same is how a rotated BlueBubbles password or a 502 used to
    fabricate verification for a whole batch — every row stamped verified with
    no outcome ever recorded, and no way to re-check them.
    """


def _fetch_state(relay_url: str, password: str, guid: str) -> Optional[dict]:
    """The message's state per the relay, or None when the relay answers but
    doesn't know this guid. Raises RelayUnavailable when the relay itself is
    the problem, so the caller can back off instead of recording a verdict."""
    resp = httpx.get(
        f"{relay_url}/api/v1/message/{guid}",
        params={"password": password},
        timeout=10,
    )
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise RelayUnavailable(f"relay HTTP {resp.status_code}")
    try:
        return resp.json().get("data") or {}
    except ValueError as e:  # non-JSON body = a proxy/error page, not an answer
        raise RelayUnavailable(f"relay returned non-JSON: {e}")


def _aware(value: dt.datetime) -> dt.datetime:
    """SQLite hands back naive datetimes even for DateTime(timezone=True), so
    any Python-side age comparison has to normalize first (same reason
    sms_campaigns._aware and sms_send._last_out_created_at exist)."""
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


# The Messages.app error codes we can explain. Everything else is recorded as
# the bare number — _failure_reasons groups on the text, so naming the common
# ones turns "4" into an actionable line in the campaign's failure breakdown.
_ERROR_DETAIL = {
    4: (
        "Send failed on the Mac (error 4) — usually the paired iPhone is "
        "asleep, offline, or Text Message Forwarding is off"
    ),
    22: "Recipient not reachable on iMessage (error 22)",
}


def _error_detail(error: int) -> str:
    return _ERROR_DETAIL.get(error, f"Messages.app reported error {error}")


def _as_int(value) -> Optional[int]:
    """Coerce the relay's `error` to an int. It has been observed as both a
    number and a numeric string; a bare `== 0` on the string form reads a
    SUCCESSFUL send as failed and re-sends it to the lead."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def run_due(db: Session) -> int:
    """One scheduler tick: verify aged, unverified BlueBubbles sends.
    Returns the number of rows verified this pass."""
    now = utcnow()
    accounts = (
        db.execute(
            select(SmsAccount).where(
                SmsAccount.provider == "bluebubbles",
                SmsAccount.status == "active",
            )
        )
        .scalars()
        .all()
    )
    verified = 0
    for acct in accounts:
        if not acct.relay_url:
            continue
        try:
            password = decrypt_secret(acct.auth_token_encrypted)
        except Exception:
            log.warning("sms verify: cannot decrypt relay password for %s", acct.id)
            continue
        rows = (
            db.execute(
                select(SmsMessage)
                .where(
                    SmsMessage.account_id == acct.id,
                    SmsMessage.direction == "out",
                    # 'delivered' is re-checked too: a receipt can land before
                    # the Mac stamps a failure, and the old status==sent filter
                    # meant such a row was never verified at all.
                    SmsMessage.status.in_((SMS_MSG_SENT, SMS_MSG_DELIVERED)),
                    SmsMessage.provider_sid.isnot(None),
                    SmsMessage.provider_sid != "",
                    SmsMessage.verified_at.is_(None),
                    SmsMessage.check_attempts < MAX_CHECKS,
                    SmsMessage.created_at <= now - FIRST_CHECK_AGE,
                    SmsMessage.created_at >= now - MAX_AGE,
                    or_(
                        SmsMessage.last_checked_at.is_(None),
                        SmsMessage.last_checked_at <= now - RECHECK_INTERVAL,
                    ),
                )
                .order_by(SmsMessage.created_at.asc())
                .limit(BATCH_PER_ACCOUNT)
            )
            .scalars()
            .all()
        )
        for msg in rows:
            try:
                state = _fetch_state(acct.relay_url, password, msg.provider_sid)
            except Exception:
                # Relay unreachable or refusing — leave every remaining row of
                # this account untouched (no attempt burned, no verdict
                # recorded) and try again next tick.
                break

            msg.last_checked_at = now
            msg.check_attempts = (msg.check_attempts or 0) + 1
            age = now - _aware(msg.created_at)

            if state is None:
                # The relay answered and has no such message. Right after a
                # send that can be a propagation lag, so only treat it as
                # final once the row is old enough to know better.
                if age >= CONFIRM_SUCCESS_AGE:
                    msg.verified_at = now
                continue

            error = _as_int(state.get("error"))
            if error is not None and error != 0:
                # The Mac says this send never left — make the ledger honest
                # and retry it. Trustworthy immediately: a nonzero error is
                # only ever written after a real failure. Notifications need
                # no help here, lead_notify.retry_failed re-attempts them.
                msg.status = advance_status(msg.status, SMS_MSG_FAILED)
                if msg.status == SMS_MSG_FAILED:
                    msg.failed_at = msg.failed_at or now
                    msg.error_code = str(error)[:20]
                    msg.error_detail = _error_detail(error)
                    _requeue_campaign_send(db, msg)
                msg.verified_at = now
                verified += 1
                continue

            # No failure recorded. Capture whatever positive receipts exist —
            # these are safe to record early, they only ever move forward.
            if state.get("dateRead"):
                msg.status = advance_status(msg.status, SMS_MSG_READ)
                msg.read_at = msg.read_at or now
            if state.get("dateDelivered"):
                msg.status = advance_status(msg.status, SMS_MSG_DELIVERED)
                msg.delivered_at = msg.delivered_at or now
            # The transport actually used — an iMessage-capable account
            # reporting SMS is the green-bubble downgrade signal, and the send
            # path never recorded one, so this is the only place an outbound
            # row can learn it.
            svc = state.get("service")
            if not msg.service and isinstance(svc, str) and svc:
                msg.service = svc[:20]

            if error is None and age < CONFIRM_SUCCESS_AGE:
                # State exists but carries no verdict yet — keep polling.
                continue
            if age < CONFIRM_SUCCESS_AGE:
                # error == 0, but too early to trust as final; a late failure
                # stamp can still land. Provisional — re-polled next pass.
                continue
            msg.verified_at = now
            verified += 1
        db.commit()
    return verified


def _requeue_campaign_send(db: Session, msg: SmsMessage) -> None:
    """Rewind the enrollment so the failed step sends again. All the engine's
    own guards (consent, suppression, send window, account caps, pacing)
    re-apply on the retry — this only reschedules, never sends directly."""
    if not msg.enrollment_id or not msg.step_id:
        return
    enrollment = db.get(SmsEnrollment, msg.enrollment_id)
    if enrollment is None or enrollment.status != SMS_ENROLL_ACTIVE:
        return
    step = db.get(SmsStep, msg.step_id)
    if step is None:
        return
    prior_failures = db.execute(
        select(func.count(SmsMessage.id)).where(
            SmsMessage.enrollment_id == msg.enrollment_id,
            SmsMessage.step_id == msg.step_id,
            SmsMessage.status == SMS_MSG_FAILED,
        )
    ).scalar_one()
    if prior_failures > MAX_STEP_RETRIES:
        log.warning(
            "sms verify: enrollment %s step %s failed %s times — giving up",
            enrollment.id,
            step.position,
            prior_failures,
        )
        return
    # Don't rewind if a LATER message already reached this lead. Verification
    # is asynchronous and reply steps can fire within minutes, so by now the
    # sequence may have moved on — and rewinding then re-sends an earlier
    # message they already received, while clearing awaiting_reply_since on a
    # conversation legitimately parked waiting for their answer.
    #
    # The test is "did a later send actually go out", not current_position:
    # the engine advances the position after every successful send, so the
    # normal failed-step case ALWAYS has current_position == step.position + 1.
    later_send = db.execute(
        select(SmsMessage.id).where(
            SmsMessage.enrollment_id == msg.enrollment_id,
            SmsMessage.id != msg.id,
            SmsMessage.step_id.isnot(None),
            SmsMessage.step_id != msg.step_id,
            SmsMessage.status.in_((SMS_MSG_SENT, SMS_MSG_DELIVERED, SMS_MSG_READ)),
            SmsMessage.created_at > msg.created_at,
        )
    ).first()
    if later_send:
        log.info(
            "sms verify: enrollment %s already sent a later step than %s — "
            "not rewinding",
            enrollment.id,
            step.position,
        )
        return
    enrollment.current_position = step.position
    enrollment.awaiting_reply_since = None
    # Jitter the retry. A sleeping phone fails a whole batch at once; without
    # this they all come due at the same instant and then contend for the
    # account's single spacing slot, burning a round trip each per tick.
    enrollment.next_run_at = utcnow() + dt.timedelta(seconds=random.uniform(0, 45))
