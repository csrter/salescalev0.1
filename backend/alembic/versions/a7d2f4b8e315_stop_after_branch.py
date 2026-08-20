"""Stop auto-replying once a branch response has been sent.

A reply step's branches carry the substantive answers — the pitch, the parting
message. Before this, an enrollment could re-open from a COMPLETED state on
every later keyword match, so a lead who kept texting could be pitched twice,
or pitched and then sent the parting message on top of it. Once the real
answer has gone out, the conversation belongs to a human.

Two additive nullable/defaulted columns, safe on a live table:
- sms_enrollments.branch_sent_at — when a matched BRANCH response was sent
  (the step's generic default body deliberately does NOT count; it is a
  placeholder that leaves the door open for the branch to fire later).
- sms_campaigns.stop_after_branch — the policy switch, default true. A
  deliberately multi-turn campaign can turn it off.

Revision ID: a7d2f4b8e315
Revises: e2b7d4f1a9c6
"""

import sqlalchemy as sa
from alembic import op

revision = "a7d2f4b8e315"
down_revision = "e2b7d4f1a9c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sms_enrollments",
        sa.Column("branch_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sms_campaigns",
        sa.Column(
            "stop_after_branch",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("sms_campaigns", "stop_after_branch")
    op.drop_column("sms_enrollments", "branch_sent_at")
