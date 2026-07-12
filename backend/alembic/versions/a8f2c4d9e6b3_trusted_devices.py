"""2FA remember-this-device: trusted_devices table + org policy column

Revision ID: a8f2c4d9e6b3
Revises: c2f8e5a1b307
Create Date: 2026-07-12 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'a8f2c4d9e6b3'
down_revision = 'c2f8e5a1b307'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'organizations',
        sa.Column(
            'allow_remember_device',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('true'),
        ),
    )
    op.create_table(
        'trusted_devices',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('user_agent', sa.String(length=400), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            'revoked',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_trusted_devices_token_hash'),
    )
    op.create_index(
        op.f('ix_trusted_devices_user_id'), 'trusted_devices', ['user_id'], unique=False
    )
    op.create_index(
        op.f('ix_trusted_devices_token_hash'), 'trusted_devices', ['token_hash'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_trusted_devices_token_hash'), table_name='trusted_devices')
    op.drop_index(op.f('ix_trusted_devices_user_id'), table_name='trusted_devices')
    op.drop_table('trusted_devices')
    op.drop_column('organizations', 'allow_remember_device')
