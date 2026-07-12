"""lead_finder_searches.pages_fetched — quota counts billed Places pages

Revision ID: e5a9c2f7b4d8
Revises: d7f3b9c1e4a6
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

revision = "e5a9c2f7b4d8"
down_revision = "d7f3b9c1e4a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lead_finder_searches",
        sa.Column(
            "pages_fetched", sa.Integer(), nullable=False, server_default="1"
        ),
    )


def downgrade() -> None:
    op.drop_column("lead_finder_searches", "pages_fetched")
