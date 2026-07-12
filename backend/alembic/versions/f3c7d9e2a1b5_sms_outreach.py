"""SMS outreach framework: 6 sms_* tables + contact consent columns

Revision ID: f3c7d9e2a1b5
Revises: e5a9c2f7b4d8
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

revision = "f3c7d9e2a1b5"
down_revision = "e5a9c2f7b4d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sms_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "provider", sa.String(length=20), nullable=False, server_default="twilio"
        ),
        sa.Column("account_sid", sa.String(length=64), nullable=False),
        sa.Column("auth_token_encrypted", sa.Text()),
        sa.Column("from_number", sa.String(length=20)),
        sa.Column("messaging_service_sid", sa.String(length=64)),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="active"
        ),
        sa.Column("error_detail", sa.Text()),
        sa.Column(
            "daily_send_cap", sa.Integer(), nullable=False, server_default="200"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "from_number", name="uq_sms_account_from"),
    )
    op.create_table(
        "sms_campaigns",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("client_id", sa.String(length=36), sa.ForeignKey("clients.id")),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="draft"
        ),
        sa.Column(
            "account_id",
            sa.String(length=36),
            sa.ForeignKey("sms_accounts.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default="America/New_York",
        ),
        sa.Column(
            "send_window_start", sa.Integer(), nullable=False, server_default="11"
        ),
        sa.Column(
            "send_window_end", sa.Integer(), nullable=False, server_default="20"
        ),
        sa.Column("send_days", sa.JSON()),
        sa.Column("daily_cap", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "exit_on_reply", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("settings", sa.JSON()),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sms_steps",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "campaign_id",
            sa.String(length=36),
            sa.ForeignKey("sms_campaigns.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("wait_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("body_template", sa.Text()),
        sa.UniqueConstraint("campaign_id", "position", name="uq_sms_step_position"),
    )
    op.create_table(
        "sms_enrollments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "campaign_id",
            sa.String(length=36),
            sa.ForeignKey("sms_campaigns.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "contact_id",
            sa.String(length=36),
            sa.ForeignKey("contacts.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="active"
        ),
        sa.Column("exit_reason", sa.String(length=30)),
        sa.Column(
            "current_position", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), index=True),
        sa.Column("replied_at", sa.DateTime(timezone=True)),
        sa.Column("enrolled_by", sa.String(length=36)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "campaign_id", "contact_id", name="uq_sms_enroll_campaign_contact"
        ),
    )
    op.create_table(
        "sms_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "account_id",
            sa.String(length=36),
            sa.ForeignKey("sms_accounts.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("campaign_id", sa.String(length=36), sa.ForeignKey("sms_campaigns.id")),
        sa.Column("step_id", sa.String(length=36), sa.ForeignKey("sms_steps.id")),
        sa.Column(
            "enrollment_id", sa.String(length=36), sa.ForeignKey("sms_enrollments.id")
        ),
        sa.Column(
            "contact_id",
            sa.String(length=36),
            sa.ForeignKey("contacts.id"),
            index=True,
        ),
        sa.Column("direction", sa.String(length=5), nullable=False),
        sa.Column(
            "kind", sa.String(length=20), nullable=False, server_default="campaign"
        ),
        sa.Column("to_number", sa.String(length=20), nullable=False),
        sa.Column("from_number", sa.String(length=20)),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="queued"
        ),
        sa.Column("provider_sid", sa.String(length=64), index=True),
        sa.Column("error_code", sa.String(length=20)),
        sa.Column("error_detail", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sms_suppressions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("phone_e164", sa.String(length=20), nullable=False),
        sa.Column(
            "reason", sa.String(length=20), nullable=False, server_default="stop"
        ),
        sa.Column("detail", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "phone_e164", name="uq_sms_suppress"),
    )
    # Contact consent record (TCPA). server_default false — existing contacts
    # have NO SMS consent until an explicit opt-in lands.
    op.add_column(
        "contacts",
        sa.Column(
            "sms_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("contacts", sa.Column("sms_opt_in_at", sa.DateTime(timezone=True)))
    op.add_column(
        "contacts", sa.Column("sms_opt_in_source", sa.String(length=100))
    )


def downgrade() -> None:
    op.drop_column("contacts", "sms_opt_in_source")
    op.drop_column("contacts", "sms_opt_in_at")
    op.drop_column("contacts", "sms_opt_in")
    op.drop_table("sms_suppressions")
    op.drop_table("sms_messages")
    op.drop_table("sms_enrollments")
    op.drop_table("sms_steps")
    op.drop_table("sms_campaigns")
    op.drop_table("sms_accounts")
