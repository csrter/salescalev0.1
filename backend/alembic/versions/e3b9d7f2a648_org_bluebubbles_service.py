"""organizations.bluebubbles_service — org-level iMessage-only / SMS-only flip

Revision ID: e3b9d7f2a648
Revises: c8f1d3b6e720
Create Date: 2026-08-27

Which Apple service a BlueBubbles account sends on was a module-level constant
(services/sms_send.BLUEBUBBLES_SERVICE = "SMS"), pinned there on 2026-08-25
after iMessage-first routing sent a 95%-green-bubble audience onto a service
that could not carry it. Pinning was right; hardcoding it was not — the correct
leg depends on the host Mac, and on 2026-08-27 the SMS leg died on the sending
Mac (paired-iPhone Text Message Forwarding down: 53/53 SMS sends error 4) while
iMessage from the same host delivered 6/6. With the constant hardcoded there
was no way to move to the working leg without a code change and a deploy.

This makes it an Organization setting instead. Two values only, "SMS" and
"iMessage" — a deliberate flip an operator makes when they know which leg their
Mac can carry, NOT a per-send routing decision. The failure this replaced came
from deciding per send from a live availability probe that failed OPEN to
iMessage; one org-level constant per send window cannot fail that way.

Default "SMS" preserves today's behavior for every existing org.
Additive, non-null with a server_default — safe on a live table.
"""

import sqlalchemy as sa
from alembic import op

revision = "e3b9d7f2a648"
down_revision = "c8f1d3b6e720"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "bluebubbles_service",
            sa.String(length=10),
            nullable=False,
            server_default="SMS",
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "bluebubbles_service")
