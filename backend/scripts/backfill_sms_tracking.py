"""Backfill SMS tracking state for rows that predate the tracking upgrade.

Two jobs, both idempotent and safe to re-run:

1. TIMESTAMPS. delivered_at/failed_at didn't exist, so historical rows carry a
   terminal status with no moment attached. Their created_at is the send
   instant and is the only evidence available, so it's copied across — which
   makes the new latency metrics report ~0 for old rows rather than dropping
   them. Only rows whose status already says delivered/read/failed are
   touched, and only when the timestamp is still NULL.

2. STUCK 'sent' ROWS. The old verification pass was one-shot with a hard 24h
   window: if the relay was unreachable while a row aged out, it exited the
   query forever, still reading as a successful send. Prod has ~475 of these.
   The relay can no longer answer for them (BlueBubbles prunes, and the guids
   are old), so they are marked as what they actually are — never confirmed —
   by stamping check_attempts to the cap. They keep verified_at NULL, which
   is exactly what the new `unconfirmed` metric counts, so they stop being
   silently reported as confirmed successes without inventing an outcome.

Usage (inside the backend container):
    python scripts/backfill_sms_tracking.py            # dry run
    python scripts/backfill_sms_tracking.py --write
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import and_, or_, select, update  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models.base import utcnow  # noqa: E402
from app.models.sms_outreach import (  # noqa: E402
    SMS_MSG_DELIVERED,
    SMS_MSG_FAILED,
    SMS_MSG_READ,
    SMS_MSG_SENT,
    SmsMessage,
)
from app.services.sms_verify import MAX_AGE, MAX_CHECKS  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="apply (default: dry run)")
    args = ap.parse_args()
    db = SessionLocal()
    cutoff = utcnow() - MAX_AGE

    def count(*where) -> int:
        return len(db.execute(select(SmsMessage.id).where(*where)).all())

    delivered_missing = count(
        SmsMessage.status.in_((SMS_MSG_DELIVERED, SMS_MSG_READ)),
        SmsMessage.delivered_at.is_(None),
    )
    failed_missing = count(
        SmsMessage.status == SMS_MSG_FAILED, SmsMessage.failed_at.is_(None)
    )
    stuck = count(
        SmsMessage.direction == "out",
        SmsMessage.status == SMS_MSG_SENT,
        SmsMessage.verified_at.is_(None),
        SmsMessage.created_at < cutoff,
        SmsMessage.check_attempts < MAX_CHECKS,
    )

    print(f"delivered/read rows missing delivered_at : {delivered_missing}")
    print(f"failed rows missing failed_at            : {failed_missing}")
    print(f"aged-out unconfirmed 'sent' rows         : {stuck}")

    if not args.write:
        print("\nDry run — re-run with --write to apply.")
        return 0

    db.execute(
        update(SmsMessage)
        .where(
            SmsMessage.status.in_((SMS_MSG_DELIVERED, SMS_MSG_READ)),
            SmsMessage.delivered_at.is_(None),
        )
        .values(delivered_at=SmsMessage.created_at)
    )
    db.execute(
        update(SmsMessage)
        .where(SmsMessage.status == SMS_MSG_FAILED, SmsMessage.failed_at.is_(None))
        .values(failed_at=SmsMessage.created_at)
    )
    # Stop re-polling rows the relay can no longer answer for. verified_at
    # stays NULL on purpose: "we never got an answer" is the honest state.
    db.execute(
        update(SmsMessage)
        .where(
            SmsMessage.direction == "out",
            SmsMessage.status == SMS_MSG_SENT,
            SmsMessage.verified_at.is_(None),
            SmsMessage.created_at < cutoff,
            SmsMessage.check_attempts < MAX_CHECKS,
        )
        .values(check_attempts=MAX_CHECKS, last_checked_at=utcnow())
    )
    db.commit()
    print("\nApplied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
