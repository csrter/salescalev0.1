"""Email campaigns: multi-mailbox sending pool.

email_campaign_accounts joins a campaign to N mailboxes; enrollments gain a
sticky account_id (the mailbox a contact's whole sequence sends from — thread
continuity). Backfill: every existing campaign gets one pool row for its
legacy account_id, and every existing enrollment is pinned to its campaign's
account (they've already been sending from it).

Revision ID: e7b4a9d2c6f1
Revises: c4e8f1a6b9d3
Create Date: 2026-07-16
"""

import sqlalchemy as sa
from alembic import op

revision = "e7b4a9d2c6f1"
down_revision = "c4e8f1a6b9d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_campaign_accounts",
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
            sa.ForeignKey("email_campaigns.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "account_id",
            sa.String(length=36),
            sa.ForeignKey("email_accounts.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "campaign_id", "account_id", name="uq_email_campaign_account"
        ),
    )
    op.add_column(
        "email_enrollments",
        sa.Column("account_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_email_enrollments_account_id", "email_enrollments", ["account_id"]
    )

    # Backfill — portable SQL (Postgres prod / SQLite dev). Random ids via the
    # dialects' own functions would diverge; do it row-by-row instead.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, organization_id, account_id FROM email_campaigns")
    ).fetchall()
    import uuid

    for cid, org_id, acct_id in rows:
        conn.execute(
            sa.text(
                "INSERT INTO email_campaign_accounts"
                " (id, organization_id, campaign_id, account_id, position,"
                "  created_at)"
                " VALUES (:id, :org, :camp, :acct, 0, CURRENT_TIMESTAMP)"
            ),
            {"id": str(uuid.uuid4()), "org": org_id, "camp": cid, "acct": acct_id},
        )
    # Pin only enrollments that already STARTED (they have a thread — their
    # conversation lives on that mailbox). Never-sent enrollments stay NULL so
    # they participate in rotation the moment a pool gains a second mailbox.
    conn.execute(
        sa.text(
            "UPDATE email_enrollments SET account_id ="
            " (SELECT account_id FROM email_campaigns"
            "  WHERE email_campaigns.id = email_enrollments.campaign_id)"
            " WHERE account_id IS NULL AND thread_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_email_enrollments_account_id", table_name="email_enrollments")
    op.drop_column("email_enrollments", "account_id")
    op.drop_table("email_campaign_accounts")
