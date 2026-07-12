"""SMS: read tracking — sms_messages.read_at

Dual-purpose by direction: outbound = when Sendblue reported the recipient
read the iMessage (status callback "read"); inbound = when our team marked
the conversation read. Twilio never reports read receipts, so this only
ever populates for Sendblue-outbound rows automatically; inbound rows across
both providers get it via the new mark-read endpoint.

Revision ID: d1e5f3b7a924
Revises: c4d9e6a2f815
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

revision = "d1e5f3b7a924"
down_revision = "c4d9e6a2f815"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sms_messages", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sms_messages", "read_at")
