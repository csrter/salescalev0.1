"""client portal access: client invites + client-owned lead status

Two additive, nullable columns — safe against the live DB.

No DB-level FK on the new client_id, matching the house convention for
add_column (see e7b4a9d2c6f1 email_enrollments.account_id): SQLite has no
ALTER-for-constraints, so a create_foreign_key here breaks every dev/test run.
The model keeps its ForeignKey() for ORM joins.

organization_invites.client_id: a client-portal invite has to carry WHICH
client the new user is pinned to. NULL keeps the row a team invite, so every
existing invite is unchanged.

contacts.client_status / client_status_at: the lead-quality feedback loop a
client user owns. Deliberately separate from `qualification`/`qualified_at`,
which feed the guarantee tracker and LQA-CPL — a client must never be able to
move the agency's guarantee math by working its own leads.

Revision ID: c1a4f7b9e206
Revises: e8c5a2d71f43
"""

import sqlalchemy as sa
from alembic import op

revision = "c1a4f7b9e206"
down_revision = "e8c5a2d71f43"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organization_invites",
        sa.Column("client_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "contacts", sa.Column("client_status", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "contacts",
        sa.Column("client_status_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contacts", "client_status_at")
    op.drop_column("contacts", "client_status")
    op.drop_column("organization_invites", "client_id")
