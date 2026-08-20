"""Check which CRM contacts are reachable on iMessage.

Answers one question per contact: is this phone number registered with Apple?
That decides whether a BlueBubbles send lands as a blue iMessage or has to go
out as green-bubble SMS through the host Mac's Text Message Forwarding — which
is a materially different channel (iMessage gets read receipts and no carrier
filtering; SMS-via-forwarding needs a paired iPhone online and is what error 4
failures are).

Mechanism: the org's BlueBubbles relay exposes Apple's own IDS lookup at
/api/v1/handle/availability/imessage. That is a LOOKUP, not a message — it
sends nothing to the contact. services/sms_send._bluebubbles_resolve_service
already calls it per send; this module is the bulk, cached counterpart so an
audience can be segmented BEFORE a campaign rather than discovered one failed
send at a time.

Two deliberate differences from the per-send resolver:

- A failed lookup writes NOTHING (leaves imessage_capable None) instead of
  defaulting to iMessage. The send path defaults optimistically because
  refusing to send on a transient blip is worse than trying; a stored
  capability flag has the opposite trade-off — a wrong label silently
  misroutes a whole segment.
- Lookups are PACED. The relay runs on a real Mac against a real Apple ID;
  a few thousand back-to-back IDS lookups is exactly the pattern that gets
  an Apple ID rate-limited. One per second is slow (an hour for 3,600
  contacts) and deliberately so.
"""

import datetime as dt
import logging
import time
from typing import List, Optional

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.core import Organization
from ..models.crm import Contact, ContactListMember
from ..models.sms_outreach import SMS_ACCOUNT_ACTIVE, SmsAccount
from ..security import decrypt_secret
from . import sms_consent

log = logging.getLogger("salescale.imessage_check")

# Extra sleep between IDS lookups, ON TOP of the round trip itself.
#
# Measured against a live relay (2026-08-19): a single lookup takes ~320ms
# median, and throughput plateaus at ~4/sec no matter the concurrency —
# 4, 8 and 16 parallel workers all landed at ~4/sec, with 16 slightly WORSE
# than 4. So the ceiling is server-side (BlueBubbles/Apple serialize the IDS
# query per Apple ID), not something the client can spend its way out of.
# Parallelism was measured and deliberately NOT adopted: it buys ~35% for a
# real increase in burst profile on an Apple ID that already gets throttled
# for send volume.
#
# That leaves the round trip itself as the natural pacer at ~3/sec, which is
# nowhere near a rate a human couldn't produce by typing recipients. This
# constant is the small deliberate gap on top so the loop is never tight;
# raise it if Apple ever starts refusing lookups.
CHECK_SPACING_SECONDS = 0.1
# Hard ceiling on one run, so a runaway caller can't queue an all-night job.
MAX_PER_RUN = 3000
# A verdict older than this is re-checked (numbers do get ported onto and off
# of iMessage); a fresher one is reused so re-running over a list is cheap.
RECHECK_AFTER_DAYS = 30
# The relay is a Mac over a home/office tunnel — generous but bounded.
LOOKUP_TIMEOUT_SECONDS = 12


class NoRelayError(RuntimeError):
    """The org has no active BlueBubbles account to run lookups through."""


def resolve_account(db: Session, organization_id: str) -> Optional[SmsAccount]:
    """The org's BlueBubbles account to run lookups through. iMessage
    availability is an Apple-side question, so only a BlueBubbles relay can
    answer it — Twilio/Telnyx/Sendblue have no equivalent."""
    return (
        db.execute(
            select(SmsAccount)
            .where(
                SmsAccount.organization_id == organization_id,
                SmsAccount.provider == "bluebubbles",
                SmsAccount.status == SMS_ACCOUNT_ACTIVE,
            )
            .order_by(SmsAccount.created_at)
        )
        .scalars()
        .first()
    )


def check_number(base: str, password: str, e164: str) -> Optional[bool]:
    """True/False if Apple answered, None if the lookup itself failed.

    None is not "no" — see the module docstring. Callers must not persist it."""
    try:
        resp = httpx.get(
            f"{base}/api/v1/handle/availability/imessage",
            params={"password": password, "address": e164},
            timeout=LOOKUP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code // 100 != 2:
        return None
    try:
        data = resp.json().get("data") or {}
    except ValueError:
        return None
    available = data.get("available")
    return bool(available) if isinstance(available, bool) else None


def _due(contact: Contact, force: bool, cutoff: dt.datetime) -> bool:
    if force or contact.imessage_checked_at is None:
        return True
    checked = contact.imessage_checked_at
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=dt.timezone.utc)
    return checked < cutoff


def run_check(
    organization_id: str,
    contact_ids: Optional[List[str]] = None,
    *,
    client_id: Optional[str] = None,
    list_id: Optional[str] = None,
    force: bool = False,
) -> None:
    """Background pass: look up the given contacts and record iMessage
    capability. With no contact_ids it sweeps a whole population — the org,
    one client's CRM (client_id), or one contact list (list_id). Whichever
    scope the caller counted MUST be the scope passed here, or the UI
    promises "check 5" and checks 27.

    Own session, per-contact commit — a crash mid-run keeps every verdict
    already written, and the EnrichmentJob row drives the CRM's existing
    progress card."""
    from ..db import SessionLocal
    from ..models.lead_finder import EnrichmentJob

    db = SessionLocal()
    job = None
    try:
        org = db.get(Organization, organization_id)
        if org is None:
            return
        account = resolve_account(db, organization_id)
        if account is None:
            raise NoRelayError(
                "No active BlueBubbles account — connect one in SMS → Accounts"
            )
        base = (account.relay_url or "").rstrip("/")
        if not base:
            raise NoRelayError("The BlueBubbles account has no relay URL configured")
        try:
            password = decrypt_secret(account.auth_token_encrypted or "")
        except Exception as exc:  # InvalidToken stringifies to "" — be explicit
            raise NoRelayError(
                "The BlueBubbles account's stored password could not be read. "
                "Reconnect it in SMS \u2192 Accounts."
            ) from exc

        q = select(Contact).where(Contact.organization_id == organization_id)
        if contact_ids:
            q = q.where(Contact.id.in_(contact_ids))
        elif list_id:
            q = q.where(
                Contact.id.in_(
                    select(ContactListMember.contact_id).where(
                        ContactListMember.list_id == list_id
                    )
                )
            )
        elif client_id:
            q = q.where(Contact.client_id == client_id)
        contacts = list(db.execute(q).scalars().all())

        cutoff = utcnow() - dt.timedelta(days=RECHECK_AFTER_DAYS)
        # Only contacts with a usable number, deduped by number: the same
        # cell can sit on several CRM rows and Apple's answer is a property
        # of the NUMBER, not the row.
        todo: list[tuple[Contact, str]] = []
        for c in contacts:
            number = sms_consent.contact_sms_number(c)
            if not number or not _due(c, force, cutoff):
                continue
            todo.append((c, number))
        todo = todo[:MAX_PER_RUN]

        job = EnrichmentJob(
            organization_id=organization_id,
            status="running",
            phase="imessage",
            total=len(todo),
            processed=0,
        )
        db.add(job)
        db.commit()

        seen: dict[str, Optional[bool]] = {}
        for index, (contact, number) in enumerate(todo):
            # Cancellation, without a new column: the API flips this job's
            # status, and the loop notices between lookups. Cheap because the
            # row is already in this session and we commit every iteration
            # anyway. Everything checked so far is kept — the sweep is
            # resumable by simply running it again (fresh verdicts are
            # skipped by the RECHECK_AFTER_DAYS cache).
            db.refresh(job)
            if job.status != "running":
                log.info("imessage check %s cancelled at %s/%s", job.id, index, len(todo))
                job.finished_at = utcnow()
                db.commit()
                return
            if number in seen:
                verdict = seen[number]
            else:
                if index:
                    time.sleep(CHECK_SPACING_SECONDS)
                verdict = check_number(base, password, number)
                seen[number] = verdict
            if verdict is not None:
                contact.imessage_capable = verdict
                contact.imessage_checked_at = utcnow()
            job.processed = index + 1
            job.updated_at = utcnow()
            db.commit()

        job.status = "done"
        job.finished_at = utcnow()
        db.commit()
    except Exception as exc:  # noqa: BLE001 — background task, never raises out
        log.exception("imessage check failed for org %s", organization_id)
        if job is not None:
            try:
                db.rollback()
                job.status = "failed"
                # cryptography.InvalidToken and friends stringify to "" —
                # keep the class name so the UI shows something actionable.
                job.error = (str(exc) or type(exc).__name__)[:500]
                job.finished_at = utcnow()
                db.commit()
            except Exception:
                log.exception("could not record imessage job failure")
    finally:
        db.close()


def summary(
    db: Session,
    organization_id: str,
    client_id: Optional[str] = None,
    list_id: Optional[str] = None,
) -> dict:
    """Counts for a population — the whole org, one client's CRM, or one
    contact list. Drives the 'X of Y checked' readout, and must be able to
    count exactly the scope run_check will sweep."""
    q = select(Contact).where(Contact.organization_id == organization_id)
    if list_id:
        q = q.where(
            Contact.id.in_(
                select(ContactListMember.contact_id).where(
                    ContactListMember.list_id == list_id
                )
            )
        )
    elif client_id:
        q = q.where(Contact.client_id == client_id)
    rows = list(db.execute(q).scalars().all())
    with_number = [c for c in rows if sms_consent.contact_sms_number(c)]
    checked = [c for c in with_number if c.imessage_capable is not None]
    return {
        "total": len(rows),
        "with_number": len(with_number),
        "checked": len(checked),
        "imessage": sum(1 for c in checked if c.imessage_capable),
        "sms_only": sum(1 for c in checked if not c.imessage_capable),
        "unchecked": len(with_number) - len(checked),
    }


def lists_coverage(
    db: Session, organization_id: str, client_id: Optional[str] = None
) -> list[dict]:
    """Per-contact-list coverage for the checker view. One aggregate query
    rather than a summary() call per list — an org with 30 lists would
    otherwise fire 30 full contact scans to paint one table."""
    from ..models.crm import ContactList

    lq = select(ContactList).where(ContactList.organization_id == organization_id)
    if client_id:
        lq = lq.where(ContactList.client_id == client_id)
    lists = list(db.execute(lq.order_by(ContactList.name)).scalars().all())
    if not lists:
        return []

    # Members that have a usable number at all, bucketed by verdict. The
    # "has a number" test mirrors sms_consent.contact_sms_number (mobile
    # first, then phone) — anything without one is not checkable.
    rows = db.execute(
        select(
            ContactListMember.list_id,
            func.count(Contact.id),
            func.count(Contact.id).filter(Contact.imessage_capable.is_(True)),
            func.count(Contact.id).filter(Contact.imessage_capable.is_(False)),
        )
        .join(Contact, Contact.id == ContactListMember.contact_id)
        .where(
            ContactListMember.list_id.in_([lst.id for lst in lists]),
            Contact.organization_id == organization_id,
            (Contact.mobile_phone.isnot(None)) | (Contact.phone.isnot(None)),
        )
        .group_by(ContactListMember.list_id)
    ).all()
    by_list = {r[0]: r for r in rows}

    out = []
    for lst in lists:
        r = by_list.get(lst.id)
        with_number = r[1] if r else 0
        imessage = r[2] if r else 0
        sms_only = r[3] if r else 0
        out.append(
            {
                "id": lst.id,
                "name": lst.name,
                "client_id": lst.client_id,
                "with_number": with_number,
                "checked": imessage + sms_only,
                "imessage": imessage,
                "sms_only": sms_only,
                "unchecked": with_number - imessage - sms_only,
            }
        )
    return out
