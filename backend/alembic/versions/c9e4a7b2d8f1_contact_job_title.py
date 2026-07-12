"""contact job_title: the pitch target's role at the company

Revision ID: c9e4a7b2d8f1
Revises: b6d1f3a8c5e2
Create Date: 2026-07-12 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c9e4a7b2d8f1'
down_revision = 'b6d1f3a8c5e2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'contacts', sa.Column('job_title', sa.String(length=150), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('contacts', 'job_title')
