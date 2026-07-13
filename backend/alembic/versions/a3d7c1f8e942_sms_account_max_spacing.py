"""SMS outreach: max_send_spacing_seconds on sms_accounts.

Turns min_send_spacing_seconds from a floor-plus-multiplier into a real
[min, max] uniform-random pacing range when both are set (a wider or
narrower randomized range than the old floor*1.0-1.8x jitter derives) — the
BlueBubbles anti-detection default becomes a literal 20-45s window instead
of a 60s floor. When max is left null, the old floor*jitter behavior applies
unchanged (backward compatible with any account that only set min).

Revision ID: a3d7c1f8e942
Revises: f9a3c7e1b6d4
Create Date: 2026-07-13
"""

import sqlalchemy as sa
from alembic import op

revision = "a3d7c1f8e942"
down_revision = "f9a3c7e1b6d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sms_accounts",
        sa.Column("max_send_spacing_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sms_accounts", "max_send_spacing_seconds")
