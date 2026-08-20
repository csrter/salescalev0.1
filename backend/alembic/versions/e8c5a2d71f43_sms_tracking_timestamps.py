"""SMS tracking: delivery/failure timestamps + re-pollable verification

Adds four nullable/defaulted columns to sms_messages (additive, prod-safe):

  delivered_at / failed_at  — WHEN the status transition happened. Only the
      status string was stored before, so time-to-delivery was unmeasurable
      and by_day charts had to bucket a delivery on its SEND day.
  last_checked_at / check_attempts — drive the re-pollable BlueBubbles
      verification loop. The old pass was one-shot (verified_at or nothing),
      which forced a conservative 4-minute delay before the single read: the
      relay can report error=0 on a send that later flips to failed, so
      reading early risked retiring a failed send as successful, forever.
      With a re-poll cursor the pass can read EARLY (a failure is trustworthy
      the moment it appears) and only terminalize a SUCCESS once the row is
      old enough to trust.

Revision ID: e8c5a2d71f43
Revises: d6b4c9e2a17f
"""

from alembic import op
import sqlalchemy as sa

revision = "e8c5a2d71f43"
down_revision = "d6b4c9e2a17f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sms_messages", sa.Column("delivered_at", sa.DateTime(timezone=True))
    )
    op.add_column("sms_messages", sa.Column("failed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "sms_messages", sa.Column("last_checked_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "sms_messages",
        sa.Column(
            "check_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    # The verification pass scans for unterminalized outbound rows due for a
    # re-poll; without this it is a full table scan on every tick.
    op.create_index(
        "ix_sms_messages_verify_scan",
        "sms_messages",
        ["account_id", "status", "verified_at", "last_checked_at"],
    )
    # Every campaign-scoped stat (sent/delivered/read/failed/unconfirmed, the
    # per-step funnel, the daily chart) filters on campaign_id, and the
    # column was never indexed — so the dashboard table-scanned the whole
    # ledger once per campaign per metric.
    op.create_index(
        "ix_sms_messages_campaign_id", "sms_messages", ["campaign_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_sms_messages_campaign_id", table_name="sms_messages")
    op.drop_index("ix_sms_messages_verify_scan", table_name="sms_messages")
    op.drop_column("sms_messages", "check_attempts")
    op.drop_column("sms_messages", "last_checked_at")
    op.drop_column("sms_messages", "failed_at")
    op.drop_column("sms_messages", "delivered_at")
