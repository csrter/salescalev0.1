"""email outreach module (cold-email: accounts, campaigns, threads, messages, suppression)

Revision ID: b1e7d4c9a025
Revises: a4e1c9d3f27b
Create Date: 2026-07-11 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b1e7d4c9a025'
down_revision = 'a4e1c9d3f27b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All new tables; non-null columns carry server_default so the migration is
    # safe on a live DB (the standing rule) even though these tables start empty.
    op.create_table(
        'email_accounts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('organization_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('from_name', sa.String(length=200), nullable=False),
        sa.Column('from_email', sa.String(length=320), nullable=False),
        sa.Column('smtp_host', sa.String(length=255), nullable=False),
        sa.Column('smtp_port', sa.Integer(), nullable=False),
        sa.Column('smtp_security', sa.String(length=20), nullable=False, server_default='ssl'),
        sa.Column('imap_host', sa.String(length=255), nullable=False),
        sa.Column('imap_port', sa.Integer(), nullable=False),
        sa.Column('imap_security', sa.String(length=20), nullable=False, server_default='ssl'),
        sa.Column('smtp_username', sa.String(length=320), nullable=False),
        sa.Column('smtp_password_encrypted', sa.Text(), nullable=True),
        sa.Column('imap_username', sa.String(length=320), nullable=False),
        sa.Column('imap_password_encrypted', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('error_detail', sa.Text(), nullable=True),
        sa.Column('daily_send_cap', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('warmup_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('warmup_started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('warmup_target_daily', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('signature', sa.Text(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_imap_uid', sa.Integer(), nullable=True),
        sa.Column('last_sync_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'from_email', name='uq_email_account_from'),
    )
    op.create_index(op.f('ix_email_accounts_organization_id'), 'email_accounts', ['organization_id'], unique=False)

    op.create_table(
        'email_campaigns',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('organization_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='draft'),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('timezone', sa.String(length=64), nullable=False, server_default='UTC'),
        sa.Column('send_window_start', sa.Integer(), nullable=False, server_default='8'),
        sa.Column('send_window_end', sa.Integer(), nullable=False, server_default='17'),
        sa.Column('send_days', sa.JSON(), nullable=True),
        sa.Column('daily_cap', sa.Integer(), nullable=False, server_default='50'),
        sa.Column('open_tracking', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('exit_on_reply', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('settings', sa.JSON(), nullable=True),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['email_accounts.id'], ),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_email_campaigns_account_id'), 'email_campaigns', ['account_id'], unique=False)
    op.create_index(op.f('ix_email_campaigns_organization_id'), 'email_campaigns', ['organization_id'], unique=False)

    op.create_table(
        'email_steps',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('organization_id', sa.String(length=36), nullable=False),
        sa.Column('campaign_id', sa.String(length=36), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('wait_days', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('subject_template', sa.Text(), nullable=True),
        sa.Column('body_template', sa.Text(), nullable=True),
        sa.Column('ai_instructions', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['campaign_id'], ['email_campaigns.id'], ),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('campaign_id', 'position', name='uq_email_step_position'),
    )
    op.create_index(op.f('ix_email_steps_campaign_id'), 'email_steps', ['campaign_id'], unique=False)
    op.create_index(op.f('ix_email_steps_organization_id'), 'email_steps', ['organization_id'], unique=False)

    op.create_table(
        'email_threads',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('organization_id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('contact_id', sa.String(length=36), nullable=False),
        sa.Column('subject', sa.String(length=500), nullable=True),
        sa.Column('snippet', sa.Text(), nullable=True),
        sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_inbound_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('unread', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('message_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['email_accounts.id'], ),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], ),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_id', 'contact_id', name='uq_email_thread_account_contact'),
    )
    op.create_index(op.f('ix_email_threads_account_id'), 'email_threads', ['account_id'], unique=False)
    op.create_index(op.f('ix_email_threads_contact_id'), 'email_threads', ['contact_id'], unique=False)
    op.create_index(op.f('ix_email_threads_last_message_at'), 'email_threads', ['last_message_at'], unique=False)
    op.create_index(op.f('ix_email_threads_organization_id'), 'email_threads', ['organization_id'], unique=False)

    op.create_table(
        'email_enrollments',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('organization_id', sa.String(length=36), nullable=False),
        sa.Column('campaign_id', sa.String(length=36), nullable=False),
        sa.Column('contact_id', sa.String(length=36), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('exit_reason', sa.String(length=30), nullable=True),
        sa.Column('current_position', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('thread_id', sa.String(length=36), nullable=True),
        sa.Column('ai_snippets', sa.JSON(), nullable=True),
        sa.Column('replied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('enrolled_by', sa.String(length=36), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['campaign_id'], ['email_campaigns.id'], ),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], ),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.ForeignKeyConstraint(['thread_id'], ['email_threads.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('campaign_id', 'contact_id', name='uq_email_enroll_campaign_contact'),
    )
    op.create_index(op.f('ix_email_enrollments_campaign_id'), 'email_enrollments', ['campaign_id'], unique=False)
    op.create_index(op.f('ix_email_enrollments_contact_id'), 'email_enrollments', ['contact_id'], unique=False)
    op.create_index(op.f('ix_email_enrollments_next_run_at'), 'email_enrollments', ['next_run_at'], unique=False)
    op.create_index(op.f('ix_email_enrollments_organization_id'), 'email_enrollments', ['organization_id'], unique=False)

    op.create_table(
        'email_messages',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('organization_id', sa.String(length=36), nullable=False),
        sa.Column('thread_id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('contact_id', sa.String(length=36), nullable=True),
        sa.Column('direction', sa.String(length=5), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=True),
        sa.Column('campaign_id', sa.String(length=36), nullable=True),
        sa.Column('step_id', sa.String(length=36), nullable=True),
        sa.Column('enrollment_id', sa.String(length=36), nullable=True),
        sa.Column('subject', sa.String(length=500), nullable=True),
        sa.Column('body_text', sa.Text(), nullable=True),
        sa.Column('message_id_header', sa.String(length=998), nullable=True),
        sa.Column('in_reply_to', sa.String(length=998), nullable=True),
        sa.Column('open_token', sa.String(length=64), nullable=True),
        sa.Column('unsubscribe_token', sa.String(length=64), nullable=True),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('open_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('bounced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('smtp_response', sa.Text(), nullable=True),
        sa.Column('error_detail', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['email_accounts.id'], ),
        sa.ForeignKeyConstraint(['campaign_id'], ['email_campaigns.id'], ),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], ),
        sa.ForeignKeyConstraint(['enrollment_id'], ['email_enrollments.id'], ),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.ForeignKeyConstraint(['step_id'], ['email_steps.id'], ),
        sa.ForeignKeyConstraint(['thread_id'], ['email_threads.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_id', 'message_id_header', name='uq_email_msg_account_msgid'),
    )
    op.create_index(op.f('ix_email_messages_account_id'), 'email_messages', ['account_id'], unique=False)
    op.create_index(op.f('ix_email_messages_contact_id'), 'email_messages', ['contact_id'], unique=False)
    op.create_index(op.f('ix_email_messages_organization_id'), 'email_messages', ['organization_id'], unique=False)
    op.create_index(op.f('ix_email_messages_sent_at'), 'email_messages', ['sent_at'], unique=False)
    op.create_index(op.f('ix_email_messages_thread_id'), 'email_messages', ['thread_id'], unique=False)
    op.create_index('uq_email_msg_open_token', 'email_messages', ['open_token'], unique=True)
    op.create_index('uq_email_msg_unsub_token', 'email_messages', ['unsubscribe_token'], unique=True)

    op.create_table(
        'email_suppressions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('organization_id', sa.String(length=36), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('reason', sa.String(length=20), nullable=False),
        sa.Column('contact_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], ),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'email', name='uq_email_suppression_org_email'),
    )
    op.create_index(op.f('ix_email_suppressions_organization_id'), 'email_suppressions', ['organization_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_email_suppressions_organization_id'), table_name='email_suppressions')
    op.drop_table('email_suppressions')
    op.drop_index('uq_email_msg_unsub_token', table_name='email_messages')
    op.drop_index('uq_email_msg_open_token', table_name='email_messages')
    op.drop_index(op.f('ix_email_messages_thread_id'), table_name='email_messages')
    op.drop_index(op.f('ix_email_messages_sent_at'), table_name='email_messages')
    op.drop_index(op.f('ix_email_messages_organization_id'), table_name='email_messages')
    op.drop_index(op.f('ix_email_messages_contact_id'), table_name='email_messages')
    op.drop_index(op.f('ix_email_messages_account_id'), table_name='email_messages')
    op.drop_table('email_messages')
    op.drop_index(op.f('ix_email_enrollments_organization_id'), table_name='email_enrollments')
    op.drop_index(op.f('ix_email_enrollments_next_run_at'), table_name='email_enrollments')
    op.drop_index(op.f('ix_email_enrollments_contact_id'), table_name='email_enrollments')
    op.drop_index(op.f('ix_email_enrollments_campaign_id'), table_name='email_enrollments')
    op.drop_table('email_enrollments')
    op.drop_index(op.f('ix_email_threads_organization_id'), table_name='email_threads')
    op.drop_index(op.f('ix_email_threads_last_message_at'), table_name='email_threads')
    op.drop_index(op.f('ix_email_threads_contact_id'), table_name='email_threads')
    op.drop_index(op.f('ix_email_threads_account_id'), table_name='email_threads')
    op.drop_table('email_threads')
    op.drop_index(op.f('ix_email_steps_organization_id'), table_name='email_steps')
    op.drop_index(op.f('ix_email_steps_campaign_id'), table_name='email_steps')
    op.drop_table('email_steps')
    op.drop_index(op.f('ix_email_campaigns_organization_id'), table_name='email_campaigns')
    op.drop_index(op.f('ix_email_campaigns_account_id'), table_name='email_campaigns')
    op.drop_table('email_campaigns')
    op.drop_index(op.f('ix_email_accounts_organization_id'), table_name='email_accounts')
    op.drop_table('email_accounts')
