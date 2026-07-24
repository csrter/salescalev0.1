"""BlueBubbles post-send verification + automatic retry.

The no-Private-API BlueBubbles path (AppleScript sends through the EC2 Mac's
Text Message Forwarding) reports success when the message is HANDED to
Messages.app; the real outcome lands asynchronously in the Mac's Messages DB
(`error` column), sometimes a minute or more later. When the paired iPhone is
asleep or off-network, every send silently dies with error 4 while our ledger
says "sent" — 17 fl hvac opener texts were lost exactly this way on
2026-07-24 (05:43–05:52 Phoenix; the phone woke at 05:52 and sends resumed).

This pass closes that hole: a few minutes after each BlueBubbles send it
reads the message's true state back from the relay and
- error != 0  → the row becomes FAILED (honest ledger/stats) and the send is
  RETRIED: campaign sends rewind the enrollment to the failed step (engine
  guards — window, pacing, consent — re-apply on the resend); notification
  texts need nothing here, lead_notify.retry_failed already picks up failed
  rows on its own backoff.
- error == 0  → verified; upgraded to DELIVERED when the Mac has a delivery
  receipt (rare for green-bubble SMS, so "sent" stays the terminal state for
  most successful sends on this channel).

Each row is verified once (sms_messages.verified_at). Retries per
(enrollment, step) are capped so a dead device can't machine-gun the same
lead forever. Best-effort: a relay outage leaves rows unverified for the
next tick, and nothing here ever raises out of the scheduler.
"""

import datetime as dt
import logging

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.sms_outreach import (
    SMS_ENROLL_ACTIVE,
    SMS_MSG_DELIVERED,
    SMS_MSG_FAILED,
    SMS_MSG_SENT,
    SmsAccount,
    SmsEnrollment,
    SmsMessage,
    SmsStep,
)
from ..security import decrypt_secret

log = logging.getLogger("salescale.sms_verify")

# The Mac stamps failures asynchronously — a lookup at 1–2 minutes can still
# read error=0 on a send that later flips to failed (observed live), so wait
# before trusting the state.
MIN_AGE = dt.timedelta(minutes=4)
MAX_AGE = dt.timedelta(hours=24)
BATCH_PER_ACCOUNT = 25
MAX_STEP_RETRIES = 3


def _fetch_state(relay_url: str, password: str, guid: str) -> dict:
    resp = httpx.get(
        f"{relay_url}/api/v1/message/{guid}",
        params={"password": password},
        timeout=10,
    )
    if resp.status_code >= 400:
        # Unknown guid / relay refusal: report as unknowable, not retryable.
        return {}
    return resp.json().get("data") or {}


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
                    SmsMessage.status == SMS_MSG_SENT,
                    SmsMessage.provider_sid.isnot(None),
                    SmsMessage.verified_at.is_(None),
                    SmsMessage.created_at <= now - MIN_AGE,
                    SmsMessage.created_at >= now - MAX_AGE,
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
                # Relay unreachable — leave unverified; next tick retries.
                break
            error = state.get("error")
            if error is None:
                # Message unknown to the relay (e.g. pre-migration guid) —
                # stamp it so it's never polled again, change nothing else.
                msg.verified_at = now
                continue
            msg.verified_at = now
            if error == 0:
                if state.get("dateDelivered"):
                    msg.status = SMS_MSG_DELIVERED
                verified += 1
                continue
            # The Mac says this send never left — make the ledger honest and
            # retry it. Notifications need no help here: lead_notify.
            # retry_failed re-attempts failed notification rows on its own.
            msg.status = SMS_MSG_FAILED
            msg.error_code = str(error)[:20]
            _requeue_campaign_send(db, msg)
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
    enrollment.current_position = step.position
    enrollment.awaiting_reply_since = None
    enrollment.next_run_at = utcnow()
