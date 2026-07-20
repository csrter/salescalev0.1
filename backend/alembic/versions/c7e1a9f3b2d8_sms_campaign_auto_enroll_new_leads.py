"""SMS campaign auto-enroll new leads

Adds sms_campaigns.auto_enroll_new_leads — when true (and the campaign is
active + scoped to a client_id), a new lead created for that client is
automatically enrolled into the campaign at lead-creation time
(services/lead_autoenroll.py), mirroring how services/lead_notify.py fires the
team alert. Additive, non-null with a server_default of false so every
existing campaign keeps today's behavior (manual enroll only) unless
explicitly turned on.

Revision ID: c7e1a9f3b2d8
Revises: f1c3e9a7b2d4
Create Date: 2026-07-19
"""
from alembic import op
import sqlalchemy as sa

revision = "c7e1a9f3b2d8"
down_revision = "f1c3e9a7b2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sms_campaigns",
        sa.Column(
            "auto_enroll_new_leads",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("sms_campaigns", "auto_enroll_new_leads")
