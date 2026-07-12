"""SMS: per-campaign compliance-footer toggle

sms_campaigns.include_compliance_footer (default true) lets an org turn off
the CTIA sender-id + "Reply STOP to opt out" text on the first message for
campaigns targeting known, already-consenting contacts. STOP handling itself
is unaffected either way.

Revision ID: c4d9e6a2f815
Revises: b3f8a1d47c92
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

revision = "c4d9e6a2f815"
down_revision = "b3f8a1d47c92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sms_campaigns",
        sa.Column(
            "include_compliance_footer",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("sms_campaigns", "include_compliance_footer")
