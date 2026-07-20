"""Organization + client default timezone

Adds organizations.timezone (the agency's app-wide default) and
clients.timezone (per-client). Both nullable — NULL means "inherit" (client
falls back to the org's, the org falls back to the outreach default). Used to
default new SMS/email campaign send-window/quiet-hours timezones so operators
stop typing IANA names per campaign. Additive + nullable — prod-safe.

Revision ID: d3b8f1a4c920
Revises: c7e1a9f3b2d8
Create Date: 2026-07-19
"""
from alembic import op
import sqlalchemy as sa

revision = "d3b8f1a4c920"
down_revision = "c7e1a9f3b2d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("timezone", sa.String(length=64), nullable=True))
    op.add_column("clients", sa.Column("timezone", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "timezone")
    op.drop_column("organizations", "timezone")
