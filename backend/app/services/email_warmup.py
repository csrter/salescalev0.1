"""Cold-email mailbox warmup.

Two effects, both reputation-building:

1. A ramping effective daily cap. An account with warmup_enabled ramps its
   allowed volume UP from a small floor toward warmup_target_daily over a fixed
   28-day (4-week) schedule, linearly by day, and never above the account's own
   daily_send_cap. `effective_daily_cap` is the ONE function every cap check
   consults (the send gateway calls it instead of the raw column), so a young
   mailbox can't be blasted on day one.

     floor  = max(5, round(0.2 * target))         # ~20% of target, min 5/day
     cap(d) = floor + (target - floor) * min(d, 28) / 28   # linear over 4 weeks
     cap    = min(cap(d), account.daily_send_cap)

2. Peer exchange. Warmup-enabled active mailboxes in the SAME Organization send
   each other short synthetic emails (kind="warmup", threadless, X-Salescale-
   Warmup header) so real inbox-engagement signals accrue. Cross-tenant warmup
   is never done — that would breach tenant isolation. `run_warmup_tick`
   iterates every ordered (sender -> peer) same-org pair, so both mailboxes
   accrue sent AND received volume without a reply cascade (no ping-pong: the
   inbound hook only records receipt, it never auto-replies). An org with < 2
   warmup mailboxes still ramps its cap (useful) but has no peer to exchange
   with — handled as a no-op.
"""

import datetime as dt
import logging
from typing import List

from email.message import Message
from email.utils import parseaddr

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.email_outreach import (
    ACCOUNT_ACTIVE,
    KIND_WARMUP,
    EmailAccount,
    EmailWarmupPeer,
)
from . import email_outreach_send as gateway

log = logging.getLogger("salescale.email_outreach")

RAMP_DAYS = 28  # 4 weeks from warmup_started_at to full target
_MIN_FLOOR = 5  # never start a ramp below 5/day

# Short innocuous warmup bodies, rotated by day so a mailbox pair isn't sending
# a byte-identical message every time.
_WARMUP_BODIES = [
    "Quick note to keep this thread warm — hope your week is going well.",
    "Just checking in, nothing urgent. Talk soon.",
    "Following up so we stay in touch. Have a good one.",
    "Hello! Sending a short note to keep our conversation going.",
    "Hope all is well on your end — touching base briefly.",
]
_WARMUP_SUBJECT = "Touching base"


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def warmup_floor(target: int) -> int:
    return min(target, max(_MIN_FLOOR, round(target * 0.2)))


def effective_daily_cap(account: EmailAccount) -> int:
    """The account's real sending cap right now, factoring the warmup ramp.
    Equal to daily_send_cap when warmup is off or not started."""
    if not account.warmup_enabled or account.warmup_started_at is None:
        return account.daily_send_cap
    target = account.warmup_target_daily or account.daily_send_cap
    floor = warmup_floor(target)
    days = (utcnow() - _aware(account.warmup_started_at)).days
    if days < 0:
        days = 0
    if days >= RAMP_DAYS:
        ramped = target
    else:
        ramped = floor + round((target - floor) * days / RAMP_DAYS)
    return max(0, min(ramped, account.daily_send_cap))


def warmup_stage(account: EmailAccount) -> str | None:
    """Human label for the ramp position, or None when not warming up."""
    if not account.warmup_enabled or account.warmup_started_at is None:
        return None
    days = (utcnow() - _aware(account.warmup_started_at)).days
    if days < 0:
        days = 0
    if days >= RAMP_DAYS:
        return "target reached"
    return f"week {days // 7 + 1} of 4"


def _warmup_accounts(db: Session, org_id: str) -> List[EmailAccount]:
    return list(
        db.execute(
            select(EmailAccount)
            .where(
                EmailAccount.organization_id == org_id,
                EmailAccount.status == ACCOUNT_ACTIVE,
                EmailAccount.warmup_enabled.is_(True),
            )
            .order_by(EmailAccount.created_at)
        ).scalars()
    )


def _pair(db: Session, sender: EmailAccount, peer: EmailAccount) -> EmailWarmupPeer:
    row = db.execute(
        select(EmailWarmupPeer).where(
            EmailWarmupPeer.account_id == sender.id,
            EmailWarmupPeer.peer_account_id == peer.id,
        )
    ).scalar_one_or_none()
    if row is None:
        row = EmailWarmupPeer(
            organization_id=sender.organization_id,
            account_id=sender.id,
            peer_account_id=peer.id,
        )
        db.add(row)
        db.flush()
    return row


def _send_gap(sender: EmailAccount) -> dt.timedelta:
    """How long between warmups from one mailbox to a given peer: spread the
    account's effective daily allowance across 24h so warmups drip rather than
    burst."""
    cap = max(1, effective_daily_cap(sender))
    return dt.timedelta(seconds=86400 / cap)


def run_warmup_tick(db: Session, org_id: str) -> dict:
    """One warmup pass for one org: for each ordered same-org pair, drip a
    warmup email if this pair is past its send gap. Returns a small summary.
    Caller owns the commit."""
    accounts = _warmup_accounts(db, org_id)
    if len(accounts) < 2:
        return {"organization_id": org_id, "accounts": len(accounts), "sent": 0}
    now = utcnow()
    sent = 0
    body_idx = now.timetuple().tm_yday % len(_WARMUP_BODIES)
    for sender in accounts:
        for peer in accounts:
            if peer.id == sender.id:
                continue
            pair = _pair(db, sender, peer)
            if pair.last_sent_at is not None and now - _aware(
                pair.last_sent_at
            ) < _send_gap(sender):
                continue
            code, _msg = gateway.send(
                db,
                sender,
                to_email=peer.from_email,
                subject=_WARMUP_SUBJECT,
                body_text=_WARMUP_BODIES[body_idx],
                kind=KIND_WARMUP,
            )
            if code == gateway.SENT:
                pair.last_sent_at = now
                sent += 1
            # Only one peer per sender per tick — keeps warmup a gentle drip.
            break
    return {"organization_id": org_id, "accounts": len(accounts), "sent": sent}


def run_due(db: Session) -> int:
    """Run a warmup tick for every org that has any warmup-enabled active
    mailbox. Per-org isolation so one org's failure never stalls the rest.
    Commits per org."""
    org_ids = [
        r[0]
        for r in db.execute(
            select(EmailAccount.organization_id)
            .where(
                EmailAccount.status == ACCOUNT_ACTIVE,
                EmailAccount.warmup_enabled.is_(True),
            )
            .distinct()
        )
    ]
    ran = 0
    for org_id in org_ids:
        try:
            run_warmup_tick(db, org_id)
            db.commit()
            ran += 1
        except Exception:
            log.exception("warmup tick failed for org %s", org_id)
            db.rollback()
    return ran


# --- inbound warmup handling (sync hook) ------------------------------------


def on_warmup_received(db: Session, account: EmailAccount, parsed: Message) -> None:
    """A warmup echo landed in `account`'s inbox. Record receipt on the peer
    pairing row (bookkeeping / realism) and keep it out of the human inbox. We
    deliberately do NOT auto-reply here — bidirectional volume already comes
    from run_warmup_tick iterating both directions, so there is no ping-pong."""
    from_addr = parseaddr(parsed.get("From") or "")[1]
    if not from_addr:
        return
    peer = db.execute(
        select(EmailAccount).where(
            EmailAccount.organization_id == account.organization_id,
            EmailAccount.from_email == from_addr.lower(),
        )
    ).scalar_one_or_none()
    if peer is None or peer.id == account.id:
        return
    pair = _pair(db, account, peer)
    pair.last_received_at = utcnow()
