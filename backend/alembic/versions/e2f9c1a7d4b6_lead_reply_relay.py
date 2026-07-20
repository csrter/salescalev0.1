"""Lead-reply relay to an operator phone (BlueBubbles)

Adds organizations.lead_relay_enabled + lead_relay_phone. When enabled, an
inbound lead reply on the org's BlueBubbles number is forwarded to
lead_relay_phone, and a message from that phone (tagged with a lead's reply
code) is relayed back to the lead through BlueBubbles. Additive + nullable —
prod-safe.

Revision ID: e2f9c1a7d4b6
Revises: d3b8f1a4c920
Create Date: 2026-07-20
"""
from alembic import op
import sqlalchemy as sa

revision = "e2f9c1a7d4b6"
down_revision = "d3b8f1a4c920"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "lead_relay_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "organizations", sa.Column("lead_relay_phone", sa.String(length=20), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("organizations", "lead_relay_phone")
    op.drop_column("organizations", "lead_relay_enabled")
