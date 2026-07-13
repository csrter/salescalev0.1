"""SMS outreach: iMessage via BlueBubbles (third provider) — relay config
+ min send spacing on sms_accounts, service capture on sms_messages.

BlueBubbles is a self-hosted (dev/prototype) iMessage provider, alongside the
existing Twilio and Sendblue providers on the same sms_accounts/sms_messages
tables — no new tables, no engine changes. relay_url is the org's own
BlueBubbles VPS relay base URL; min_send_spacing_seconds is a provider-
agnostic pacing guard enforced in the gateway (services/sms_send.send), null
means off. sms_messages.service records the transport actually used
(iMessage/SMS/RCS) — the green-bubble downgrade signal for channel health,
populated by status webhooks, not at send time.

Revision ID: f9a3c7e1b6d4
Revises: e4e04c133222
Create Date: 2026-07-13
"""

import sqlalchemy as sa
from alembic import op

revision = "f9a3c7e1b6d4"
down_revision = "e4e04c133222"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sms_accounts", sa.Column("relay_url", sa.String(500), nullable=True)
    )
    op.add_column(
        "sms_accounts",
        sa.Column("min_send_spacing_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sms_messages", sa.Column("service", sa.String(20), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sms_messages", "service")
    op.drop_column("sms_accounts", "min_send_spacing_seconds")
    op.drop_column("sms_accounts", "relay_url")
