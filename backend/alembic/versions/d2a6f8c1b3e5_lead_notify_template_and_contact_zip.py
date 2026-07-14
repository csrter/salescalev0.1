"""Editable lead-notification SMS template + contacts.zip.

organizations.lead_notification_template holds an admin-editable {{token}}
template for the text-the-team/text-the-client SMS alert (services/
lead_notify.py); None means the built-in default. contacts.zip is a new
first-class field (mirrors city/state) so the template's {{zip}} token has
somewhere to read from — populated fill-blanks-only from the JS landing
embed, the generic landing-page webhook, Google's POSTAL_CODE lead-form
column, CSV import, or manual entry.

Revision ID: d2a6f8c1b3e5
Revises: b8e2f4a916c7
Create Date: 2026-07-13
"""

import sqlalchemy as sa
from alembic import op

revision = "d2a6f8c1b3e5"
down_revision = "b8e2f4a916c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("lead_notification_template", sa.Text(), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("zip", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contacts", "zip")
    op.drop_column("organizations", "lead_notification_template")
