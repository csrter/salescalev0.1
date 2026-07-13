"""AI research fields, campaign QA/preview overrides, org outreach context

Adds: research_field_defs table; contacts.research JSON; email_enrollments
.overrides JSON + qa_status; email_campaigns.require_approval (server
default false) + ai_tone + ai_example; organizations.outreach_context JSON.

Revision ID: b2e6f1a9c4d7
Revises: d1e5f3b7a924
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

revision = "b2e6f1a9c4d7"
down_revision = "d1e5f3b7a924"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_field_defs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("max_words", sa.Integer(), nullable=False, server_default="40"),
        sa.Column(
            "archived", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id", "key", name="uq_research_field_org_key"
        ),
    )

    op.add_column("contacts", sa.Column("research", sa.JSON(), nullable=True))

    with op.batch_alter_table("email_enrollments") as batch:
        batch.add_column(sa.Column("overrides", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("qa_status", sa.String(length=20), nullable=True))

    with op.batch_alter_table("email_campaigns") as batch:
        batch.add_column(
            sa.Column(
                "require_approval",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("ai_tone", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("ai_example", sa.Text(), nullable=True))

    op.add_column(
        "organizations", sa.Column("outreach_context", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("organizations", "outreach_context")

    with op.batch_alter_table("email_campaigns") as batch:
        batch.drop_column("ai_example")
        batch.drop_column("ai_tone")
        batch.drop_column("require_approval")

    with op.batch_alter_table("email_enrollments") as batch:
        batch.drop_column("qa_status")
        batch.drop_column("overrides")

    op.drop_column("contacts", "research")

    op.drop_table("research_field_defs")
