"""warmup peer counters: sent/received/junk per ordered pair (health inputs)

Revision ID: d7f3b9c1e4a6
Revises: c9e4a7b2d8f1
Create Date: 2026-07-12 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd7f3b9c1e4a6'
down_revision = 'c9e4a7b2d8f1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in ('sent_count', 'received_count', 'junk_count'):
        op.add_column(
            'email_warmup_peers',
            sa.Column(col, sa.Integer(), nullable=False, server_default='0'),
        )


def downgrade() -> None:
    for col in ('junk_count', 'received_count', 'sent_count'):
        op.drop_column('email_warmup_peers', col)
