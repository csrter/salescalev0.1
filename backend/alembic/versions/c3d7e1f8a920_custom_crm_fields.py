"""custom CRM fields (Phase 14)

Revision ID: c3d7e1f8a920
Revises: a1329e8b7c04
Create Date: 2026-07-10

Adds per-Organization custom field definitions and the JSONB value bag on
contacts, with a GIN index (jsonb_path_ops) created on day one so filtered
list views stay fast at 40k+ contacts. The JSONB column type and the GIN
index are Postgres-only; on SQLite (dev/test) the column is plain JSON and
the GIN index is skipped — the app works identically, just without the index.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c3d7e1f8a920"
down_revision = "a1329e8b7c04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    # JSONB where we have it, plain JSON on SQLite — same as the ORM's JsonB.
    json_type = postgresql.JSONB() if is_postgres else sa.JSON()

    op.create_table(
        "custom_field_definitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column(
            "entity_type",
            sa.String(length=20),
            nullable=False,
            server_default="contact",
        ),
        sa.Column("label", sa.String(length=150), nullable=False),
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("field_type", sa.String(length=20), nullable=False),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column(
            "required", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "visible_to_clients",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "entity_type",
            "key",
            name="uq_custom_field_org_entity_key",
        ),
    )
    op.create_index(
        op.f("ix_custom_field_definitions_organization_id"),
        "custom_field_definitions",
        ["organization_id"],
        unique=False,
    )

    op.add_column("contacts", sa.Column("custom_fields", json_type, nullable=True))

    # Per-(user, client) lead-list column choice — the Phase 4 customizable-UI
    # preference pattern reused, not a new preferences system.
    op.create_table(
        "crm_list_preferences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("columns", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "client_id", name="uq_crm_cols_user_client"),
    )
    op.create_index(
        op.f("ix_crm_list_preferences_organization_id"),
        "crm_list_preferences",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_crm_list_preferences_user_id"),
        "crm_list_preferences",
        ["user_id"],
        unique=False,
    )

    if is_postgres:
        # jsonb_path_ops GIN: the index the phase mandates on day one. Smaller
        # and faster than the default operator class for the @>/existence
        # queries the custom-field filter builder emits.
        op.create_index(
            "ix_contacts_custom_fields_gin",
            "contacts",
            ["custom_fields"],
            unique=False,
            postgresql_using="gin",
            postgresql_ops={"custom_fields": "jsonb_path_ops"},
        )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index(
        op.f("ix_crm_list_preferences_user_id"), table_name="crm_list_preferences"
    )
    op.drop_index(
        op.f("ix_crm_list_preferences_organization_id"),
        table_name="crm_list_preferences",
    )
    op.drop_table("crm_list_preferences")
    if bind.dialect.name == "postgresql":
        op.drop_index("ix_contacts_custom_fields_gin", table_name="contacts")
    op.drop_column("contacts", "custom_fields")
    op.drop_index(
        op.f("ix_custom_field_definitions_organization_id"),
        table_name="custom_field_definitions",
    )
    op.drop_table("custom_field_definitions")
