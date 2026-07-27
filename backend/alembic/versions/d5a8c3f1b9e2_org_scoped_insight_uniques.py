"""Org-scope the insights/quality unique constraints

Revision ID: d5a8c3f1b9e2
Revises: b7c4e1f9d283
Create Date: 2026-07-27

uq_insight_entity_day and uq_quality_snapshot keyed on
(platform, entity_type, entity_external_id, [metric,] date) WITHOUT
organization_id — but two Organizations can legitimately manage and sync
the SAME external ad account, so the second org's sync would find and
overwrite the first org's rows (cross-tenant data loss). Both constraints
gain organization_id as the leading column, matching the org filter now in
services/insights_sync's upsert lookups.

Loosening a unique constraint needs no data dedupe: every row that was
unique under the old key is unique under the new one. batch_alter_table so
the same migration runs on SQLite dev stacks (table recreate) and Postgres
(plain ALTER).
"""

from alembic import op

revision = "d5a8c3f1b9e2"
down_revision = "b7c4e1f9d283"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("insights_daily") as batch:
        batch.drop_constraint("uq_insight_entity_day", type_="unique")
        batch.create_unique_constraint(
            "uq_insight_entity_day",
            ["organization_id", "platform", "entity_type",
             "entity_external_id", "date"],
        )
    with op.batch_alter_table("quality_snapshots") as batch:
        batch.drop_constraint("uq_quality_snapshot", type_="unique")
        batch.create_unique_constraint(
            "uq_quality_snapshot",
            ["organization_id", "platform", "entity_type",
             "entity_external_id", "metric", "date"],
        )


def downgrade():
    # Downgrade can fail if two orgs have since synced the same external
    # account (rows that only the org-scoped key keeps distinct) — that is
    # the bug this migration exists to allow, so it would need a manual
    # dedupe first.
    with op.batch_alter_table("quality_snapshots") as batch:
        batch.drop_constraint("uq_quality_snapshot", type_="unique")
        batch.create_unique_constraint(
            "uq_quality_snapshot",
            ["platform", "entity_type", "entity_external_id", "metric",
             "date"],
        )
    with op.batch_alter_table("insights_daily") as batch:
        batch.drop_constraint("uq_insight_entity_day", type_="unique")
        batch.create_unique_constraint(
            "uq_insight_entity_day",
            ["platform", "entity_type", "entity_external_id", "date"],
        )
