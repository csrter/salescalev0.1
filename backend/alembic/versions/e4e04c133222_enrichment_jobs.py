"""Enrichment job progress tracking: one row per enrich_and_verify run so
the CRM can show whether enrichment is processing, live progress, and a
pace-based ETA (previously fire-and-forget with no visible state).

Revision ID: e4e04c133222
Revises: 924b1e025dc1
Create Date: 2026-07-13
"""
import sqlalchemy as sa

from alembic import op

revision = "e4e04c133222"
down_revision = "924b1e025dc1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "enrichment_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("phase", sa.String(20), nullable=False, server_default="enriching"),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("enrichment_jobs")
