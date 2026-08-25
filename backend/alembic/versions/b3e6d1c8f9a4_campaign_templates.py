"""Campaign templates for SMS + email outreach.

A campaign can be flagged as a reusable template (is_template=true): its
config + steps are saved for reuse but it never activates, never accepts
enrollments, and never sends. The "Templates" list is the same table
filtered on this flag rather than a new one — templates are just campaigns
that are inert by policy, enforced in the API layer (activate/enroll both
422 on is_template=true). Creating a real campaign FROM a template clones
config+steps into a new is_template=false row, mirroring the existing
duplicate-campaign endpoint.

Additive nullable-false-with-default column on both tables, safe on a live
table.

Revision ID: b3e6d1c8f9a4
Revises: f2c9a4e7d1b6
"""

import sqlalchemy as sa
from alembic import op

revision = "b3e6d1c8f9a4"
down_revision = "f2c9a4e7d1b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sms_campaigns",
        sa.Column(
            "is_template", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "email_campaigns",
        sa.Column(
            "is_template", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("email_campaigns", "is_template")
    op.drop_column("sms_campaigns", "is_template")
