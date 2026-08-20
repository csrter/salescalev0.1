"""contacts: iMessage capability flag

Revision ID: d6b4c9e2a17f
Revises: c3f7a1e58d94
Create Date: 2026-08-19

Additive + nullable: NULL means "never checked", which is the correct
starting state for every existing row. Nothing reads these columns unless
an org runs the check, so this is safe to apply ahead of the feature.
"""

import sqlalchemy as sa
from alembic import op

revision = "d6b4c9e2a17f"
down_revision = "c3f7a1e58d94"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("imessage_capable", sa.Boolean(), nullable=True))
    op.add_column(
        "contacts",
        sa.Column("imessage_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contacts", "imessage_checked_at")
    op.drop_column("contacts", "imessage_capable")
