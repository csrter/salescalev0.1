"""One-off: mark existing Atlas Reach CRM contacts as SMS-opted-in.

Agency attestation: every contact already in the CRM was captured through a
funnel where SMS consent was collected (per the org owner, 2026-07-12) — this
is not a re-collection of consent, it's recording an existing one. Source is
kept verbatim on each contact for the compliance audit trail.

Run with --dry-run first (default) to see the count before writing anything.
"""
import argparse

from app.db import SessionLocal
from app.models.core import Organization
from app.models.crm import Contact
from app.services.sms_consent import contact_sms_number, record_opt_in

SOURCE = "agency_attested:pre_existing_crm_consent"


def main(dry_run: bool) -> None:
    db = SessionLocal()
    try:
        org = (
            db.query(Organization)
            .filter(Organization.name.ilike("%atlas reach%"))
            .first()
        )
        if not org:
            print("Atlas Reach organization not found.")
            return

        contacts = db.query(Contact).filter(Contact.organization_id == org.id).all()
        eligible = [c for c in contacts if contact_sms_number(c) and not c.sms_opt_in]
        already = [c for c in contacts if c.sms_opt_in]
        no_number = [c for c in contacts if not contact_sms_number(c)]

        print(f"org: {org.name} ({org.id})")
        print(f"total contacts: {len(contacts)}")
        print(f"already opted in: {len(already)}")
        print(f"no phone number (skipped): {len(no_number)}")
        print(f"would mark opted-in now: {len(eligible)}")

        if dry_run:
            print("\nDry run only — no changes written. Re-run with --write to apply.")
            return

        for c in eligible:
            record_opt_in(c, source=SOURCE)
        db.commit()
        print(f"\nMarked {len(eligible)} contacts as SMS opted-in.")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Actually write changes (default is dry-run).")
    args = parser.parse_args()
    main(dry_run=not args.write)
