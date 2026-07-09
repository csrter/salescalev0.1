"""stripe webhook idempotency + ordering

- organizations.subscription_event_at: `created` time of the last applied
  subscription event, so an out-of-order/replayed webhook can't regress plan.
- processed_stripe_events: dedup ledger of handled Stripe event ids.

Revision ID: f2b6d90c4a17
Revises: e9a4c2b71f30
Create Date: 2026-07-09 11:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'f2b6d90c4a17'
down_revision = 'e9a4c2b71f30'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'organizations',
        sa.Column('subscription_event_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        'processed_stripe_events',
        sa.Column('id', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('processed_stripe_events')
    op.drop_column('organizations', 'subscription_event_at')
