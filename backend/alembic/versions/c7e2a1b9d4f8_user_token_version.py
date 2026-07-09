"""user security columns: token_version + auth_provider

- token_version: carried in the access token and checked per request, so a
  password reset / logout-all invalidates outstanding JWTs before they expire.
- auth_provider: how the account was created (None = local password, else the
  social provider), so a social login won't attach to a foreign account.

Revision ID: c7e2a1b9d4f8
Revises: f1a2b3c4d5e6
Create Date: 2026-07-08 17:10:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c7e2a1b9d4f8'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'token_version',
            sa.Integer(),
            nullable=False,
            server_default='0',
        ),
    )
    op.add_column(
        'users',
        sa.Column('auth_provider', sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'auth_provider')
    op.drop_column('users', 'token_version')
