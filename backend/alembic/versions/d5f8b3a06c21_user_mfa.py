"""user two-factor auth columns

Adds the per-user 2FA state: active method, encrypted TOTP secret, encrypted
SMS phone, hashed backup codes, and a pending email/SMS one-time code + expiry.

Revision ID: d5f8b3a06c21
Revises: c7e2a1b9d4f8
Create Date: 2026-07-09 09:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd5f8b3a06c21'
down_revision = 'c7e2a1b9d4f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('mfa_method', sa.String(length=10), nullable=True))
    op.add_column('users', sa.Column('totp_secret_encrypted', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('mfa_phone_encrypted', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('mfa_backup_codes', sa.JSON(), nullable=True))
    op.add_column('users', sa.Column('mfa_otp_hash', sa.String(length=200), nullable=True))
    op.add_column(
        'users',
        sa.Column('mfa_otp_expires_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'mfa_otp_expires_at')
    op.drop_column('users', 'mfa_otp_hash')
    op.drop_column('users', 'mfa_backup_codes')
    op.drop_column('users', 'mfa_phone_encrypted')
    op.drop_column('users', 'totp_secret_encrypted')
    op.drop_column('users', 'mfa_method')
