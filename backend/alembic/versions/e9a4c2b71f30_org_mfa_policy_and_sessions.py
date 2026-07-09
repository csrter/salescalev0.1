"""org require_mfa policy + user_sessions table

- organizations.require_mfa: when set, team members must enable 2FA.
- user_sessions: one row per active login (device), carried as `sid` in the
  access token, enabling session viewing + per-device / everywhere logout.

Revision ID: e9a4c2b71f30
Revises: d5f8b3a06c21
Create Date: 2026-07-09 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e9a4c2b71f30'
down_revision = 'd5f8b3a06c21'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'organizations',
        sa.Column('require_mfa', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        'user_sessions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('user_agent', sa.String(length=400), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_user_sessions_user_id'), 'user_sessions', ['user_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_user_sessions_user_id'), table_name='user_sessions')
    op.drop_table('user_sessions')
    op.drop_column('organizations', 'require_mfa')
