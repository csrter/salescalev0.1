"""email campaign engine (Phase 2): warmup peer pairing + threadless warmup rows

Revision ID: c2f8e5a1b307
Revises: b1e7d4c9a025
Create Date: 2026-07-11 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c2f8e5a1b307'
down_revision = 'b1e7d4c9a025'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Warmup pairing state (one row per ordered same-org account->peer pair).
    op.create_table(
        'email_warmup_peers',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('organization_id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('peer_account_id', sa.String(length=36), nullable=False),
        sa.Column('last_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_received_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['email_accounts.id'], ),
        sa.ForeignKeyConstraint(['peer_account_id'], ['email_accounts.id'], ),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_id', 'peer_account_id', name='uq_email_warmup_pair'),
    )
    op.create_index(op.f('ix_email_warmup_peers_account_id'), 'email_warmup_peers', ['account_id'], unique=False)
    op.create_index(op.f('ix_email_warmup_peers_organization_id'), 'email_warmup_peers', ['organization_id'], unique=False)
    op.create_index(op.f('ix_email_warmup_peers_peer_account_id'), 'email_warmup_peers', ['peer_account_id'], unique=False)

    # Warmup messages are threadless audit rows (no Contact, kept out of the
    # human inbox), so thread_id must allow NULL. batch_alter_table keeps this
    # portable across SQLite (dev/test) and Postgres (prod).
    with op.batch_alter_table('email_messages') as batch_op:
        batch_op.alter_column('thread_id', existing_type=sa.String(length=36), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('email_messages') as batch_op:
        batch_op.alter_column('thread_id', existing_type=sa.String(length=36), nullable=False)
    op.drop_index(op.f('ix_email_warmup_peers_peer_account_id'), table_name='email_warmup_peers')
    op.drop_index(op.f('ix_email_warmup_peers_organization_id'), table_name='email_warmup_peers')
    op.drop_index(op.f('ix_email_warmup_peers_account_id'), table_name='email_warmup_peers')
    op.drop_table('email_warmup_peers')
