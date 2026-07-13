"""Per-mailbox warmup timezone: the 08:00-18:00 send window, weekend
reduction, and daily-budget midnight follow the mailbox's own zone instead
of UTC (a UTC window is 1am-11am for a Phoenix org - synthetic mail at 1am
reads as scripted, defeating warmup's purpose). NULL = UTC, the previous
behavior.

Revision ID: 924b1e025dc1
Revises: b2e6f1a9c4d7
Create Date: 2026-07-12
"""
import sqlalchemy as sa

from alembic import op

revision = "924b1e025dc1"
down_revision = "b2e6f1a9c4d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_accounts", sa.Column("warmup_timezone", sa.String(64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("email_accounts", "warmup_timezone")
