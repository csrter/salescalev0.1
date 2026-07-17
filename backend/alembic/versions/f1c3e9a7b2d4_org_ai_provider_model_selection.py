"""Org-selectable AI provider + model

Owner-selectable AI provider/model override on organizations (NULL = operator
default). Additive + nullable — prod-safe.

Revision ID: f1c3e9a7b2d4
Revises: e7b4a9d2c6f1
Create Date: 2026-07-16
"""
from alembic import op
import sqlalchemy as sa

revision = "f1c3e9a7b2d4"
down_revision = "e7b4a9d2c6f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("ai_provider", sa.String(length=20), nullable=True))
    op.add_column("organizations", sa.Column("ai_model", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "ai_model")
    op.drop_column("organizations", "ai_provider")
