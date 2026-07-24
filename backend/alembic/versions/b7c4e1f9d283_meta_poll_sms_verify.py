"""Meta lead polling cursor + BlueBubbles send-verification stamp

Revision ID: b7c4e1f9d283
Revises: f4b7e2a9c815
Create Date: 2026-07-24

Two additive nullable columns, prod-safe:
- lead_form_configs.last_polled_at — the Meta Instant Form POLLING fallback's
  per-page cursor (services/meta_lead_poll). Webhook delivery depends on the
  Meta app being published + the callback registered; polling makes lead
  arrival independent of that external state.
- sms_messages.verified_at — the BlueBubbles post-send verification stamp
  (services/sms_verify). The no-Private-API AppleScript path reports success
  at hand-off and only records the real outcome asynchronously in the Mac's
  Messages DB; this marks rows whose true outcome has been read back, so
  each is polled exactly once.
"""

from alembic import op
import sqlalchemy as sa

revision = "b7c4e1f9d283"
down_revision = "f4b7e2a9c815"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "lead_form_configs",
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sms_messages",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("sms_messages", "verified_at")
    op.drop_column("lead_form_configs", "last_polled_at")
