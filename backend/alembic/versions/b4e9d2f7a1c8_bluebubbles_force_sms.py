"""bluebubbles force_sms per-account flag

Revision ID: b4e9d2f7a1c8
Revises: a9c3f6e1d8b2
Create Date: 2026-07-23

Adds sms_accounts.bluebubbles_force_sms — pins a BlueBubbles account to the
green-bubble SMS service (skipping the iMessage availability probe) for hosts
where iMessage sending doesn't work but Text Message Forwarding does (AWS EC2
Macs: no Private API + Apple blocks datacenter iMessage; an iMessage send there
returns a guid but silently never delivers). Additive, non-null with a false
server_default — prod-safe.
"""

import sqlalchemy as sa
from alembic import op

revision = "b4e9d2f7a1c8"
down_revision = "a9c3f6e1d8b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sms_accounts",
        sa.Column(
            "bluebubbles_force_sms",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sms_accounts", "bluebubbles_force_sms")
