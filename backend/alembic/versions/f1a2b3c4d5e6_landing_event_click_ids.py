"""landing_event click_ids map

Generic per-platform click-ID store on landing_events so additional ad
platforms (msclkid, ttclid, li_fat_id, sccid, rdt_cid, epik, …) capture their
click IDs without a dedicated column each. fbclid/gclid keep their columns.

Revision ID: f1a2b3c4d5e6
Revises: b9b625ffb744
Create Date: 2026-07-08 16:20:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'f1a2b3c4d5e6'
down_revision = 'b9b625ffb744'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'landing_events',
        sa.Column('click_ids', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('landing_events', 'click_ids')
