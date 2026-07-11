"""contact city and state

Revision ID: a4e1c9d3f27b
Revises: f7a2c8d4e9b1
Create Date: 2026-07-11 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'a4e1c9d3f27b'
down_revision = 'f7a2c8d4e9b1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('contacts', sa.Column('city', sa.String(length=120), nullable=True))
    op.add_column('contacts', sa.Column('state', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('contacts', 'state')
    op.drop_column('contacts', 'city')
