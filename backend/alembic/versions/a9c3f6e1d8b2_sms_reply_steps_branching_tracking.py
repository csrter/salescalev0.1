"""SMS reply-triggered steps, response branching, richer tracking state

Steps gain a trigger ("schedule" — today's drip — or "reply", which fires
wait_days/wait_minutes AFTER the lead replies), a wait_minutes column for
finer-than-day delays, response `branches` (label + keywords + body, matched
against the lead's reply at send time) and an ai_branching flag (grounded
fail-open AI classification when keywords miss).

Enrollments gain the awaiting-reply park state (awaiting_reply_since — set
with next_run_at NULL while the enrollment waits for the lead, so
rearm_parked never force-fires a reply step) and last_reply_at/
last_reply_body (the most recent inbound, the branch-matching input;
replied_at stays the FIRST reply).

All additive, nullable or server-defaulted — existing campaigns/enrollments
keep today's behavior exactly.

Revision ID: a9c3f6e1d8b2
Revises: e2f9c1a7d4b6
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa

revision = "a9c3f6e1d8b2"
down_revision = "e2f9c1a7d4b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sms_steps",
        sa.Column("wait_minutes", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sms_steps",
        sa.Column(
            "trigger", sa.String(20), nullable=False, server_default="schedule"
        ),
    )
    op.add_column("sms_steps", sa.Column("branches", sa.JSON(), nullable=True))
    op.add_column(
        "sms_steps",
        sa.Column(
            "ai_branching", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "sms_enrollments",
        sa.Column("awaiting_reply_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sms_enrollments",
        sa.Column("last_reply_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sms_enrollments", sa.Column("last_reply_body", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sms_enrollments", "last_reply_body")
    op.drop_column("sms_enrollments", "last_reply_at")
    op.drop_column("sms_enrollments", "awaiting_reply_since")
    op.drop_column("sms_steps", "ai_branching")
    op.drop_column("sms_steps", "branches")
    op.drop_column("sms_steps", "trigger")
    op.drop_column("sms_steps", "wait_minutes")
