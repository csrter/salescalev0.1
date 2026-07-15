"""Dashboard timeframe/account-campaign filter + insight account linkage.

dashboard_layouts.filters persists the new per-(user, client) dashboard
timeframe (preset or custom since/until) and account/campaign selection —
same Phase 4 "one JSON blob, no cross-user reads" pattern as `widgets`;
NULL means the role default (last 30 days, all accounts). widgets is
relaxed to nullable so a filters-only save (before the user has ever
touched their widget layout) can create the row without fabricating an
empty-dashboard widgets value that would incorrectly override the role
default on the widgets side. insights_daily gained account_external_id
(nullable — existing rows stay NULL until their next sync) so a specific ad
account's spend can be isolated: today the table only carries client_id,
which under-determines the account when a client has more than one ad
account on the same platform.

Revision ID: c4e8f1a6b9d3
Revises: d2a6f8c1b3e5
Create Date: 2026-07-15
"""

import sqlalchemy as sa
from alembic import op

revision = "c4e8f1a6b9d3"
down_revision = "d2a6f8c1b3e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("dashboard_layouts") as batch:
        batch.add_column(sa.Column("filters", sa.JSON(), nullable=True))
        batch.alter_column("widgets", existing_type=sa.JSON(), nullable=True)
    with op.batch_alter_table("insights_daily") as batch:
        batch.add_column(
            sa.Column("account_external_id", sa.String(length=100), nullable=True)
        )
    op.create_index(
        "ix_insights_daily_account_external_id",
        "insights_daily",
        ["account_external_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_insights_daily_account_external_id", table_name="insights_daily")
    with op.batch_alter_table("insights_daily") as batch:
        batch.drop_column("account_external_id")
    with op.batch_alter_table("dashboard_layouts") as batch:
        batch.alter_column("widgets", existing_type=sa.JSON(), nullable=False)
        batch.drop_column("filters")
