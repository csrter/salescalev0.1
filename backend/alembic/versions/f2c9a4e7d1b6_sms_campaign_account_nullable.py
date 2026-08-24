"""sms_campaigns.account_id becomes nullable.

Deleting an SMS account (Twilio, Sendblue, BlueBubbles, Telnyx) no longer
requires archiving every campaign that references it. Instead, deleting an
in-use account clears account_id to NULL on those campaigns and leaves them
exactly as they were otherwise (active/paused/whatever status they already
had) — process_enrollment already parks an active campaign's enrollments
when its account is missing ("reconnect flow re-arms" — see
services/sms_campaigns.py), so this reuses an existing, already-tested code
path rather than adding a new one. An admin restores sending by PATCHing a
new account_id onto the campaign; that PATCH re-arms parked/errored
enrollments (services/sms_campaigns.rearm_campaign), same as reconnecting an
account today.

Revision ID: f2c9a4e7d1b6
Revises: a7d2f4b8e315
Create Date: 2026-08-24
"""

import sqlalchemy as sa
from alembic import op

revision = "f2c9a4e7d1b6"
down_revision = "a7d2f4b8e315"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sms_campaigns") as batch:
        batch.alter_column(
            "account_id", existing_type=sa.String(length=36), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table("sms_campaigns") as batch:
        batch.alter_column(
            "account_id", existing_type=sa.String(length=36), nullable=False
        )
