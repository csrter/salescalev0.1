"""Backfill SMS tracking state for rows that predate the tracking upgrade.

WHAT THIS DELIBERATELY DOES NOT DO: backfill delivered_at/failed_at on
historical rows. Those columns are new, so pre-upgrade rows carry a terminal
status with no moment attached, and the only timestamp available is
created_at — the SEND instant. Copying it across would assert that every
historical message was delivered in zero seconds, which would poison exactly
the latency metrics the column was added to make trustworthy. Leaving them
NULL keeps those rows out of the latency sample (the metrics simply start
accumulating from the upgrade forward, and by_day already falls back to the
send day for legacy rows), which is the honest answer to "how fast was this
delivered?" when the truth is "we never recorded it".

One job, idempotent and safe to re-run:

   STUCK 'sent' ROWS. The old verification pass was one-shot with a hard 24h
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

from sqlalchemy import select, update  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models.base import utcnow  # noqa: E402
from app.models.sms_outreach import SMS_MSG_SENT, SmsMessage  # noqa: E402
from app.services.sms_verify import MAX_AGE, MAX_CHECKS  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="apply (default: dry run)")
    args = ap.parse_args()
    db = SessionLocal()
    cutoff = utcnow() - MAX_AGE

    def count(*where) -> int:
        return len(db.execute(select(SmsMessage.id).where(*where)).all())

    stuck = count(
        SmsMessage.direction == "out",
        SmsMessage.status == SMS_MSG_SENT,
        SmsMessage.verified_at.is_(None),
        SmsMessage.created_at < cutoff,
        SmsMessage.check_attempts < MAX_CHECKS,
    )

    print(f"aged-out unconfirmed 'sent' rows to retire: {stuck}")
    print("(delivered_at/failed_at are deliberately NOT backfilled — see the "
          "module docstring)")

    if not args.write:
        print("\nDry run — re-run with --write to apply.")
        return 0

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
