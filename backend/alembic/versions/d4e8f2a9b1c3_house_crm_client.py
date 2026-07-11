"""house CRM client flag

Revision ID: d4e8f2a9b1c3
Revises: c3d7e1f8a920
Create Date: 2026-07-11

Adds the is_house flag on clients — the agency's own prospect pipeline lives on
a single synthetic Client row per Organization. A partial unique index enforces
"at most one house client per org"; the partial-index WHERE clause works on both
Postgres (prod/Supabase) and SQLite (dev/test), so no dialect guard is needed —
each backend gets its own dialect kwarg and ignores the other.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d4e8f2a9b1c3"
down_revision = "c3d7e1f8a920"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column(
            "is_house", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_index(
        "uq_clients_house_per_org",
        "clients",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("is_house"),
        sqlite_where=sa.text("is_house"),
    )


def downgrade() -> None:
    op.drop_index("uq_clients_house_per_org", table_name="clients")
    op.drop_column("clients", "is_house")
