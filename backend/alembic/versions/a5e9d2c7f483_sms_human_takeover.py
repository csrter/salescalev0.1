"""SMS human takeover: a person texting a lead stops the drip

Adds:
  sms_campaigns.stop_on_human_reply — per-campaign opt-out (default ON), so a
    deliberately human-assisted sequence can keep running alongside a person.
  contacts.sms_handover_at — WHEN a human took this lead over. Contact-level
    on purpose: being handled by a person is a property of the LEAD, not of
    whichever sequence happened to be running, and it also has to survive the
    enrollment ending (a COMPLETED enrollment can otherwise re-open on a
    branch match and talk over the human).

Both additive; the boolean carries a server_default so existing rows get the
ON behaviour without a data migration.

Revision ID: a5e9d2c7f483
Revises: c8f4a2e9d6b1
"""

import sqlalchemy as sa
from alembic import op

revision = "a5e9d2c7f483"
down_revision = "c8f4a2e9d6b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sms_campaigns",
        sa.Column(
            "stop_on_human_reply",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "contacts",
        sa.Column("sms_handover_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contacts", "sms_handover_at")
    op.drop_column("sms_campaigns", "stop_on_human_reply")
