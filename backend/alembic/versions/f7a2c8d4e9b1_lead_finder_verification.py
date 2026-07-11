"""Phase 12 — Lead Finder & email verification

Revision ID: f7a2c8d4e9b1
Revises: d4e8f2a9b1c3
Create Date: 2026-07-11

Adds:
- contacts.verification_status / verified_at / candidate_emails — the email
  verification verdict (unverified|valid|risky|invalid|unknown) plus the
  enrichment candidate-email list;
- lead_finder_searches — one row per Google Places search (monthly quota
  counter + import attribution anchor; stores query text only, never Places
  result payloads, per Google's caching policy);
- email_verifications — one row per address sent to the verification
  provider (monthly quota counter + verdict history).
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f7a2c8d4e9b1"
down_revision = "d4e8f2a9b1c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column(
            "verification_status",
            sa.String(length=20),
            nullable=False,
            server_default="unverified",
        ),
    )
    op.add_column(
        "contacts", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("contacts", sa.Column("candidate_emails", sa.JSON(), nullable=True))

    op.create_table(
        "lead_finder_searches",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id")),
        sa.Column("query", sa.String(length=300), nullable=False),
        sa.Column("location", sa.String(length=300)),
        sa.Column("results_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_lead_finder_searches_organization_id",
        "lead_finder_searches",
        ["organization_id"],
    )

    op.create_table(
        "email_verifications",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id")),
        sa.Column("contact_id", sa.String(length=36), sa.ForeignKey("contacts.id")),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_email_verifications_organization_id",
        "email_verifications",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_verifications_organization_id", table_name="email_verifications"
    )
    op.drop_table("email_verifications")
    op.drop_index(
        "ix_lead_finder_searches_organization_id", table_name="lead_finder_searches"
    )
    op.drop_table("lead_finder_searches")
    op.drop_column("contacts", "candidate_emails")
    op.drop_column("contacts", "verified_at")
    op.drop_column("contacts", "verification_status")
