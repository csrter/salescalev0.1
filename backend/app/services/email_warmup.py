"""Cold-email mailbox warmup.

Strategy encoded from the 2025–2026 vendor consensus (Smartlead, Instantly,
Lemwarm, MailReach — see the STATUS notes in CLAUDE.md):

1. RAMPED WARMUP VOLUME — synthetic mailbox-to-mailbox sends start at ~5/day
   and grow linearly to a hard ceiling of 40/day over 28 days, weekdays only,
   spread across a 08:00–18:00 UTC window with per-send jitter (bursts look
   robotic). After day 28 warmup NEVER stops: it holds at ~20% of the cold
   sending cap (floor 10/day) as reputation maintenance.

2. RAMPED COLD CAP — `effective_daily_cap` gates REAL sends from a small
   floor up to warmup_target_daily over the same 28 days (the send gateway
   consults it instead of the raw column), and halves itself while the
   account's 7-day bounce rate is over 2% (the vendor-standard auto-throttle).

3. ENGAGEMENT SIGNALS, ranked by impact: ~35% of received warmup mail gets a
   threaded auto-reply (replies are the single strongest placement signal;
   depth-capped so two mailboxes can't ping-pong forever); warmup mail found
   in a spam folder is rescued to INBOX (email_outreach_sync +
   email_transport.warmup_inbox_hygiene) and charged to the sender's junk
   counter; inbox warmup mail is marked read.

4. TWO NUMBERS, per industry convention: `warmup_progress` is deterministic
   ramp maturity (0–100, days into the 28-day schedule); `warmup_health` is
   measured reputation (bounces, junk placement, peer delivery ratio).

Peer exchange stays strictly same-org (cross-tenant warmup would breach
tenant isolation, guardrail #1). A 1–2 mailbox org still gets the cap ramp,
rescue, and read signals; exchange volume needs ≥2 warmup-enabled mailboxes.
All randomness is hash-derived (deterministic for tests and reproducibility).
"""

import datetime as dt
import hashlib
import logging
from typing import List, Optional

from email.message import Message
from email.utils import parseaddr

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.email_outreach import (
    ACCOUNT_ACTIVE,
    DIR_OUT,
    KIND_WARMUP,
    MSG_BOUNCED,
    MSG_SENT,
    EmailAccount,
    EmailMessage,
    EmailWarmupPeer,
)
from . import email_outreach_send as gateway

log = logging.getLogger("salescale.email_outreach")

RAMP_DAYS = 28  # 4 weeks from warmup_started_at to full target
_MIN_FLOOR = 5  # never start a ramp below 5/day

# Warmup exchange volume (synthetic sends), NOT the cold cap: start low, grow
# linearly, and never exceed 40/day even for a big target — vendor hard cap.
WARMUP_START = 5
WARMUP_CEILING = 40
# Maintenance after fully warmed: ~20% of the cold cap, floor 10 — "keep
# warmup running at 20% of your daily send volume" (Lemwarm).
MAINTENANCE_RATIO = 0.20
MAINTENANCE_FLOOR = 10
# Sending window: weekdays 08:00–18:00 UTC, spread with ±25% jitter.
WINDOW_START_HOUR = 8
WINDOW_END_HOUR = 18
# ~35% of received warmup mail gets a threaded reply; never reply past this
# thread depth (two auto-repliers with no cap = infinite loop).
REPLY_RATE = 35
MAX_REPLY_DEPTH = 2
# Auto-throttle: halve the cold cap while 7-day bounces exceed this.
BOUNCE_THROTTLE_PCT = 2.0

_WARMUP_SUBJECTS = [
    "Touching base",
    "Quick note",
    "Checking in",
    "This week",
    "Quick update",
    "Hello again",
    "Catching up",
    "One more thing",
    "Before the weekend",
    "Monday thoughts",
    "Short note",
    "Following up",
    "Re-connecting",
    "A quick hello",
    "Staying in touch",
]

_WARMUP_BODIES = [
    "Quick note to keep this thread warm — hope your week is going well.",
    "Just checking in, nothing urgent. Talk soon.",
    "Following up so we stay in touch. Have a good one.",
    "Hello! Sending a short note to keep our conversation going.",
    "Hope all is well on your end — touching base briefly.",
    "Been a busy week here. How are things on your side?",
    "No action needed, just keeping our thread alive. Cheers.",
    "Saw something today that reminded me of our last chat. Hope you're well.",
    "Short one from me — have a great rest of the week.",
    "All good here. Let's catch up properly soon.",
    "Passing through my inbox and thought I'd say hello.",
    "Hope the week is treating you well. More soon.",
    "Nothing urgent — just keeping in touch as promised.",
    "Quick hello before the day gets away from me.",
    "Things are moving along here. Hope same for you.",
]

_WARMUP_REPLIES = [
    "Thanks for the note — all good here too.",
    "Appreciate you checking in. Talk soon.",
    "Good to hear from you! Same on this end.",
    "Thanks — likewise, have a good week.",
    "All well here, thanks for the message.",
    "Got it, thanks. Let's catch up soon.",
    "Nice to hear from you — more soon.",
    "Thanks! Busy week but going well.",
]


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def _hash_pick(n: int, *parts) -> int:
    """Deterministic 0..n-1 from arbitrary parts — warmup must be jittery to
    a mail filter but reproducible to a test."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:4], "big") % n


def warmup_floor(target: int) -> int:
    return min(target, max(_MIN_FLOOR, round(target * 0.2)))


def bounce_rate_7d(db: Session, account: EmailAccount) -> float:
    """Percent of this mailbox's REAL outbound (non-warmup) sends in the last
    7 days that bounced. 0.0 with no volume."""
    since = utcnow() - dt.timedelta(days=7)
    base = select(func.count(EmailMessage.id)).where(
        EmailMessage.account_id == account.id,
        EmailMessage.direction == DIR_OUT,
        EmailMessage.kind != KIND_WARMUP,
        EmailMessage.created_at >= since,
        EmailMessage.status.in_((MSG_SENT, MSG_BOUNCED)),
    )
    total = db.execute(base).scalar_one()
    if not total:
        return 0.0
    bounced = db.execute(base.where(EmailMessage.bounced_at.is_not(None))).scalar_one()
    return 100.0 * bounced / total


def effective_daily_cap(
    account: EmailAccount, db: Optional[Session] = None
) -> int:
    """The account's real sending cap right now, factoring the warmup ramp.
    Equal to daily_send_cap when warmup is off or not started. With a db
    session, also applies the bounce auto-throttle: while the 7-day bounce
    rate is above 2%, the cap halves — the standard vendor response to a
    reputation spike (cut volume 30–50% and let it recover)."""
    if not account.warmup_enabled or account.warmup_started_at is None:
        cap = account.daily_send_cap
    else:
        target = account.warmup_target_daily or account.daily_send_cap
        floor = warmup_floor(target)
        days = (utcnow() - _aware(account.warmup_started_at)).days
        if days < 0:
            days = 0
        if days >= RAMP_DAYS:
            ramped = target
        else:
            ramped = floor + round((target - floor) * days / RAMP_DAYS)
        cap = max(0, min(ramped, account.daily_send_cap))
    if db is not None and cap > 1 and bounce_rate_7d(db, account) > BOUNCE_THROTTLE_PCT:
        cap = max(1, cap // 2)
    return cap


def warmup_volume_today(account: EmailAccount, now: Optional[dt.datetime] = None) -> int:
    """How many synthetic warmup emails this mailbox should send today.
    Weekdays only; 5 → min(40, target) linearly over 28 days, then a
    maintenance trickle of ~20% of the cold cap (floor 10) forever."""
    if not account.warmup_enabled or account.warmup_started_at is None:
        return 0
    now = now or utcnow()
    if now.weekday() >= 5:  # Sat/Sun — B2B warmup sends weekdays only
        return 0
    ceiling = min(WARMUP_CEILING, max(WARMUP_START, account.warmup_target_daily or WARMUP_CEILING))
    days = (now - _aware(account.warmup_started_at)).days
    if days < 0:
        days = 0
    if days < RAMP_DAYS:
        return round(WARMUP_START + (ceiling - WARMUP_START) * days / RAMP_DAYS)
    # Fully warmed: the cold cap equals the (clamped) target, so maintenance
    # is 20% of that — computed directly so an injected `now` stays coherent.
    full_cap = min(
        account.warmup_target_daily or account.daily_send_cap,
        account.daily_send_cap,
    )
    maintenance = max(MAINTENANCE_FLOOR, round(MAINTENANCE_RATIO * full_cap))
    return min(ceiling, maintenance)


def warmup_progress(account: EmailAccount, now: Optional[dt.datetime] = None) -> int:
    """Deterministic ramp maturity, 0–100. 100 = fully warmed (day 28+,
    maintenance mode). 0 when warmup is off."""
    if not account.warmup_enabled or account.warmup_started_at is None:
        return 0
    now = now or utcnow()
    days = (now - _aware(account.warmup_started_at)).days
    if days < 0:
        days = 0
    return min(100, round(days / RAMP_DAYS * 100))


def warmup_health(db: Session, account: EmailAccount) -> Optional[int]:
    """Measured reputation 0–100, or None until there's enough data (warmup
    off, or fewer than 5 warmup sends). Weights follow the vendor consensus:
    spam placement dominant, bounces punished hard, peer-delivery shortfall a
    flat penalty."""
    if not account.warmup_enabled or account.warmup_started_at is None:
        return None
    sent_pairs = list(
        db.execute(
            select(EmailWarmupPeer).where(EmailWarmupPeer.account_id == account.id)
        ).scalars()
    )
    total_sent = sum(p.sent_count for p in sent_pairs)
    if total_sent < 5:
        return None
    total_junk = sum(p.junk_count for p in sent_pairs)
    # Peers record receipt on THEIR (receiver → this sender) row.
    confirmed = db.execute(
        select(func.coalesce(func.sum(EmailWarmupPeer.received_count), 0)).where(
            EmailWarmupPeer.peer_account_id == account.id
        )
    ).scalar_one()
    health = 100.0
    health -= min(50.0, bounce_rate_7d(db, account) * 10)
    junk_share = 100.0 * total_junk / total_sent
    health -= min(30.0, junk_share * 5)
    if total_sent >= 10 and confirmed / total_sent < 0.6:
        health -= 15
    return max(0, min(100, round(health)))


def warmup_stage(account: EmailAccount) -> str | None:
    """Human label for the ramp position, or None when not warming up."""
    if not account.warmup_enabled or account.warmup_started_at is None:
        return None
    days = (utcnow() - _aware(account.warmup_started_at)).days
    if days < 0:
        days = 0
    if days >= RAMP_DAYS:
        return "fully warmed"
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


def warmup_sends_today(db: Session, account: EmailAccount) -> int:
    """Warmup sends (incl. auto-replies) from this mailbox since UTC midnight
    — the counter run_warmup_tick paces against."""
    midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.execute(
        select(func.count(EmailMessage.id)).where(
            EmailMessage.account_id == account.id,
            EmailMessage.direction == DIR_OUT,
            EmailMessage.kind == KIND_WARMUP,
            EmailMessage.status == MSG_SENT,
            EmailMessage.created_at >= midnight,
        )
    ).scalar_one()


def _send_gap_seconds(budget: int, account_id: str, now: dt.datetime, count: int) -> float:
    """Seconds to wait between one account's warmup sends: the day's budget
    spread across the 10h window, with deterministic ±25% jitter so the
    cadence never looks metronomic."""
    window = (WINDOW_END_HOUR - WINDOW_START_HOUR) * 3600
    base = window / max(1, budget)
    jitter = 0.75 + 0.5 * (_hash_pick(1000, account_id, now.date(), count) / 1000)
    return base * jitter


def run_warmup_tick(
    db: Session, org_id: str, now: Optional[dt.datetime] = None
) -> dict:
    """One warmup pass for one org (called every ~60s by the scheduler): for
    each warmup mailbox inside the weekday send window, drip one email toward
    today's ramped budget when past the jittered gap, rotating which peer
    receives it. Caller owns the commit. `now` is injectable so tests can pin
    a weekday inside the window."""
    accounts = _warmup_accounts(db, org_id)
    if len(accounts) < 2:
        return {"organization_id": org_id, "accounts": len(accounts), "sent": 0}
    now = now or utcnow()
    if now.weekday() >= 5 or not (WINDOW_START_HOUR <= now.hour < WINDOW_END_HOUR):
        return {"organization_id": org_id, "accounts": len(accounts), "sent": 0}
    sent = 0
    for sender in accounts:
        budget = warmup_volume_today(sender, now)
        done = warmup_sends_today(db, sender)
        if done >= budget:
            continue
        pairs = [
            _pair(db, sender, peer) for peer in accounts if peer.id != sender.id
        ]
        last = max(
            (_aware(p.last_sent_at) for p in pairs if p.last_sent_at), default=None
        )
        gap = _send_gap_seconds(budget, sender.id, now, done)
        if last is not None and (now - last).total_seconds() < gap:
            continue
        # Rotate the receiving peer so a 3+ mailbox pool doesn't collapse into
        # one tight reciprocal pair (which itself looks artificial).
        pair = pairs[done % len(pairs)]
        peer = next(a for a in accounts if a.id == pair.peer_account_id)
        pick = _hash_pick(len(_WARMUP_BODIES), sender.id, now.date(), done)
        code, _msg = gateway.send(
            db,
            sender,
            to_email=peer.from_email,
            subject=_WARMUP_SUBJECTS[
                _hash_pick(len(_WARMUP_SUBJECTS), sender.id, now.date(), done, "s")
            ],
            body_text=_WARMUP_BODIES[pick],
            kind=KIND_WARMUP,
        )
        if code == gateway.SENT:
            pair.last_sent_at = now
            pair.sent_count += 1
            sent += 1
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


# --- inbound warmup handling (sync hooks) ------------------------------------


def on_warmup_received(db: Session, account: EmailAccount, parsed: Message) -> None:
    """A warmup mail landed in `account`'s inbox. Record receipt on the
    (receiver → sender) pairing row, then — replies being the strongest
    placement signal a filter sees — send a threaded reply ~35% of the time.
    Deterministic on the Message-ID; depth-capped via the
    X-Salescale-Warmup-Depth header so two auto-repliers can't loop."""
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
    pair.received_count += 1

    message_id = (parsed.get("Message-ID") or "").strip()
    try:
        depth = int(parsed.get("X-Salescale-Warmup-Depth") or 0)
    except ValueError:
        depth = 0
    if depth >= MAX_REPLY_DEPTH or not message_id:
        return
    if _hash_pick(100, message_id) >= REPLY_RATE:
        return
    subject = (parsed.get("Subject") or "").strip() or "your note"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    gateway.send(
        db,
        account,
        to_email=peer.from_email,
        subject=subject,
        body_text=_WARMUP_REPLIES[_hash_pick(len(_WARMUP_REPLIES), message_id)],
        kind=KIND_WARMUP,
        reply_to_header=message_id,
        warmup_depth=depth + 1,
    )


def on_warmup_junk(db: Session, account: EmailAccount, sender_addr: str) -> None:
    """A warmup mail FROM sender_addr was found in `account`'s spam folder and
    rescued to INBOX. Charge the junk placement to the SENDING mailbox's
    reputation ledger — its (sender → this receiver) pair row."""
    sender = db.execute(
        select(EmailAccount).where(
            EmailAccount.organization_id == account.organization_id,
            EmailAccount.from_email == (sender_addr or "").lower(),
        )
    ).scalar_one_or_none()
    if sender is None or sender.id == account.id:
        return
    pair = _pair(db, sender, account)
    pair.junk_count += 1
