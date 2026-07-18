"""Fill contacts.state from the phone area code where state is blank.

Fill-blanks-only (never overwrites a human/enrichment value), human-correctable.
Usage:  python backfill_state_from_area_code.py <organization_id> [--write]
Dry-run by default; pass --write to persist.
"""
import sys
from sqlalchemy import select
from app.db import SessionLocal
from app.models.crm import Contact
from app.services import area_codes

org_id = sys.argv[1]
write = "--write" in sys.argv
db = SessionLocal()
rows = db.execute(select(Contact).where(Contact.organization_id == org_id)).scalars().all()
would = 0
by_state = {}
for c in rows:
    if (c.state or "").strip():
        continue
    st = area_codes.state_for_phone(c.phone or c.mobile_phone)
    if not st:
        continue
    would += 1
    by_state[st] = by_state.get(st, 0) + 1
    if write:
        c.state = st
if write:
    db.commit()
print(f"contacts scanned={len(rows)} | {'UPDATED' if write else 'would update'}={would}")
print("by state:", dict(sorted(by_state.items(), key=lambda kv: -kv[1])))
db.close()
