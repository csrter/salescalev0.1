"""sms_enrollments source attribution — where each enrollment came from

Revision ID: f4b7e2a9c815
Revises: c8b3f1a29d47
Create Date: 2026-07-24

Production tracking: an enrollment now records HOW it entered the campaign
(manual selection, a contact list, whole-client enroll, or the new-lead
auto-enroll trigger) plus a human-readable detail (the list name, or the
lead's own capture source for auto-enrolls). Plain strings, no FK — the
attribution must survive a list being renamed or deleted later. Additive
nullable columns, prod-safe; existing rows simply read as untracked.
"""

from alembic import op
import sqlalchemy as sa

revision = "f4b7e2a9c815"
down_revision = "c8b3f1a29d47"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sms_enrollments", sa.Column("source", sa.String(30), nullable=True))
    op.add_column(
        "sms_enrollments", sa.Column("source_detail", sa.String(120), nullable=True)
    )


def downgrade():
    op.drop_column("sms_enrollments", "source_detail")
    op.drop_column("sms_enrollments", "source")
