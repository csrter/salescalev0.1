"""SMS personalization: sms_steps.ai_instructions + sms_enrollments.ai_snippets

Mirrors the email module's shapes exactly (see b1e7d4c9a025's
EmailStep.ai_instructions / EmailEnrollment.ai_snippets) so the two modules'
AI-personalization plumbing stays structurally identical.

Revision ID: b3f8a1d47c92
Revises: a1b7e3f9c2d6
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

revision = "b3f8a1d47c92"
down_revision = "a1b7e3f9c2d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sms_steps", sa.Column("ai_instructions", sa.Text(), nullable=True)
    )
    op.add_column(
        "sms_enrollments", sa.Column("ai_snippets", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sms_enrollments", "ai_snippets")
    op.drop_column("sms_steps", "ai_instructions")
