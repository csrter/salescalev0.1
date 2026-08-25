"""SMS branch-level "interested" flag + Messages tab filtering.

A response branch on a reply step can now be flagged `interested` (mirrors
the existing `notify` / `add_to_pipeline` flags — stored in the branch's own
JSON on sms_steps.branches, no schema change needed there). When a lead's
reply matches an interested-flagged branch, the outbound response message is
stamped sms_messages.is_interested=true at send time (same choke point that
already evaluates notify/add_to_pipeline) — persisted rather than
recomputed, so the tag survives later edits to the step's branches. The
Messages tab filters (client-side, over the same page of messages it already
loads) on this column.

Additive nullable-false-with-default column, safe on a live table.

Revision ID: c8f4a2e9d6b1
Revises: b3e6d1c8f9a4
"""

import sqlalchemy as sa
from alembic import op

revision = "c8f4a2e9d6b1"
down_revision = "b3e6d1c8f9a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sms_messages",
        sa.Column(
            "is_interested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("sms_messages", "is_interested")
