"""Lead-form ingestion health

Revision ID: c3f7a1e58d94
Revises: f8b3d6c2a9e4
Create Date: 2026-08-09

Two additive nullable columns on lead_form_configs:

- last_poll_error: why the Meta polling fallback last failed. Polling is
  best-effort, so failures only ever reached the container log — which is how
  Meta lead ingestion sat broken for three weeks in production (the app-level
  "Cannot call API for app ... on behalf of user ..." refusal) with nothing in
  the product saying so.
- last_lead_at: when a lead last actually ARRIVED through this route, by any
  path. A poll can succeed all day against a page with no new leads, so
  "polled recently" is not the same as "working" — this is the honest signal.
"""

import sqlalchemy as sa
from alembic import op

revision = "c3f7a1e58d94"
down_revision = "f8b3d6c2a9e4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "lead_form_configs",
        sa.Column("last_poll_error", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "lead_form_configs",
        sa.Column("last_lead_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("lead_form_configs", "last_lead_at")
    op.drop_column("lead_form_configs", "last_poll_error")
