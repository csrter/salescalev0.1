"""Lead SMS notifications: organizations.notify_new_leads +
lead_notification_phones.

Text-the-team alerts on new leads, reusing the existing SMS Outreach module's
connected account/provider transport (services/sms_send.py) rather than new
send infrastructure. lead_notification_phones is a JSON list of E.164
numbers (the agency's own ops numbers, not CRM contacts) belonging to the
org, not a client — one org-wide setting, no client-level table needed.

Revision ID: b8e2f4a916c7
Revises: a3d7c1f8e942
Create Date: 2026-07-13
"""

import sqlalchemy as sa
from alembic import op

revision = "b8e2f4a916c7"
down_revision = "a3d7c1f8e942"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "notify_new_leads",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "organizations",
        sa.Column("lead_notification_phones", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "lead_notification_phones")
    op.drop_column("organizations", "notify_new_leads")
