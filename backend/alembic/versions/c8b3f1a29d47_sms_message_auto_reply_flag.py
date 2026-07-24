"""sms_messages.is_auto_reply — mark automated out-of-office inbound replies

Revision ID: c8b3f1a29d47
Revises: b4e9d2f7a1c8
Create Date: 2026-07-24

An inbound reply that is an automated out-of-office / auto-responder is logged
and attributed like any other, but must NOT count toward real human reply
engagement. Flag it at ingest so campaign stats can split human `replies` from
`auto_replies` in SQL rather than re-classifying bodies on every read.
"""

from alembic import op
import sqlalchemy as sa

revision = "c8b3f1a29d47"
down_revision = "b4e9d2f7a1c8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sms_messages",
        sa.Column(
            "is_auto_reply",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade():
    op.drop_column("sms_messages", "is_auto_reply")
