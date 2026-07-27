"""Background insights-sync cursor

Revision ID: f8b3d6c2a9e4
Revises: e6f2a8b4c9d1
Create Date: 2026-07-27

One additive nullable column: platform_connections.last_insights_sync_at —
the per-connection cursor for the new automatic insights poll
(insights_sync.run_due; the dashboard previously only refreshed on the
manual Sync button) and the dashboard's data-freshness cue.
"""

import sqlalchemy as sa
from alembic import op

revision = "f8b3d6c2a9e4"
down_revision = "e6f2a8b4c9d1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "platform_connections",
        sa.Column("last_insights_sync_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("platform_connections", "last_insights_sync_at")
