"""enrichment profile fields: contact mobile phone + company firmographics

Revision ID: b6d1f3a8c5e2
Revises: a8f2c4d9e6b3
Create Date: 2026-07-12 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b6d1f3a8c5e2'
down_revision = 'a8f2c4d9e6b3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'contacts', sa.Column('mobile_phone', sa.String(length=50), nullable=True)
    )
    op.add_column('companies', sa.Column('description', sa.Text(), nullable=True))
    op.add_column(
        'companies',
        sa.Column('estimated_revenue', sa.String(length=60), nullable=True),
    )
    op.add_column(
        'companies', sa.Column('employee_count', sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('companies', 'employee_count')
    op.drop_column('companies', 'estimated_revenue')
    op.drop_column('companies', 'description')
    op.drop_column('contacts', 'mobile_phone')
