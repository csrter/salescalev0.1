"""Email mailbox auto-recovery bookkeeping

Revision ID: e6f2a8b4c9d1
Revises: d5a8c3f1b9e2
Create Date: 2026-07-27

Two additive columns on email_accounts, prod-safe:
- sync_failure_count — consecutive IMAP sync failures; the account now only
  flips to error at a threshold (3) instead of on the first transient blip,
  which used to strand sends AND sync until a human clicked Test.
- last_reprobe_at — paces reprobe_errored, the scheduler pass that
  automatically re-tests errored mailboxes and revives them (status +
  rearm_account) when the transport recovers.
"""

import sqlalchemy as sa
from alembic import op

revision = "e6f2a8b4c9d1"
down_revision = "d5a8c3f1b9e2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "email_accounts",
        sa.Column(
            "sync_failure_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "email_accounts",
        sa.Column("last_reprobe_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("email_accounts", "last_reprobe_at")
    op.drop_column("email_accounts", "sync_failure_count")
