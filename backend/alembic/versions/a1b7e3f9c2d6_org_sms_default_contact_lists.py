"""Org sms_opt_in_default policy column + CRM contact_lists tables

Revision ID: a1b7e3f9c2d6
Revises: f3c7d9e2a1b5
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

revision = "a1b7e3f9c2d6"
down_revision = "f3c7d9e2a1b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Org policy: some agencies collect SMS consent upstream (their own site
    # funnel) before a lead ever reaches Salescale — this stamps every new
    # contact opted-in at creation. server_default false — off until an
    # Owner opts in.
    op.add_column(
        "organizations",
        sa.Column(
            "sms_opt_in_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "contact_lists",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "client_id",
            sa.String(length=36),
            sa.ForeignKey("clients.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "client_id", "name", name="uq_contact_list_client_name"
        ),
    )
    op.create_table(
        "contact_list_members",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "list_id",
            sa.String(length=36),
            sa.ForeignKey("contact_lists.id"),
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
        sa.UniqueConstraint("list_id", "contact_id", name="uq_contact_list_member"),
    )


def downgrade() -> None:
    op.drop_table("contact_list_members")
    op.drop_table("contact_lists")
    op.drop_column("organizations", "sms_opt_in_default")
