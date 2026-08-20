"""line-type lookup: can this number actually receive a text?

Three additive nullable columns on contacts, so safe against the live DB.
No DB-level FK/constraint work — SQLite has no ALTER-for-constraints and the
house convention for add_column is a bare typed column.

iMessage capability answers "blue bubble or green bubble". It does NOT answer
"can this number receive a text at all" — a landline is neither. That question
needs a carrier lookup, which is a different provider and a per-lookup cost,
hence its own columns and its own timestamp rather than overloading
imessage_checked_at.

Revision ID: e2b7d4f1a9c6
Revises: c1a4f7b9e206
"""

import sqlalchemy as sa
from alembic import op

revision = "e2b7d4f1a9c6"
down_revision = "c1a4f7b9e206"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # mobile | landline | voip | tollfree | unknown  (provider verdicts,
    # normalized in services/line_lookup.py)
    op.add_column("contacts", sa.Column("line_type", sa.String(length=20), nullable=True))
    # NULL = never looked up, which is deliberately distinct from False.
    op.add_column("contacts", sa.Column("sms_capable", sa.Boolean(), nullable=True))
    op.add_column("contacts", sa.Column("carrier_name", sa.String(length=120), nullable=True))
    op.add_column(
        "contacts", sa.Column("line_checked_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("contacts", "line_checked_at")
    op.drop_column("contacts", "carrier_name")
    op.drop_column("contacts", "sms_capable")
    op.drop_column("contacts", "line_type")
