"""sms_messages.account_id nullable — let an SMS account be deleted

Revision ID: c8f1d3b6e720
Revises: a5e9d2c7f483
Create Date: 2026-08-25

Deleting an SMS account was impossible for any account that had ever sent:
sms_messages.account_id was a NOT NULL FK, so DELETE /api/sms/accounts/{id}
died on a ForeignKeyViolation and surfaced as a generic 500 in the browser
(9,161 rows blocked one real account on prod). The handler already nulled the
campaigns pointing at the account; it had nothing it could do about the ledger.

Making the column nullable lets the delete DETACH the ledger instead, which is
the posture services/crm._cascade_contact_refs already takes for these same
rows on contact deletion: SmsMessage is the append-only send ledger — the audit
trail of what was really sent, and the source of the monthly send meter — so it
must outlive the account it was sent from. The meter counts by
organization_id, so detaching costs it nothing, and every per-account reader
(channel health, the verify poller, notification retry, read-capability)
filters by a concrete account id, so detached rows simply drop out of views
about a channel that no longer exists.

Nullable-ing a column is loosening-only: existing rows and their FK stay valid.
"""

import sqlalchemy as sa
from alembic import op

revision = "c8f1d3b6e720"
down_revision = "a5e9d2c7f483"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "sms_messages",
        "account_id",
        existing_type=sa.String(length=36),
        nullable=True,
    )


def downgrade() -> None:
    # Only reversible while no row has been detached; a NULL would violate the
    # restored constraint.
    op.alter_column(
        "sms_messages",
        "account_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )
