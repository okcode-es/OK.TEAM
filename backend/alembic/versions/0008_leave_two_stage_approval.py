"""Phase 5E: Two-stage leave approval workflow (Employee -> Store Manager -> HR).

Revision ID: 0008_leave_two_stage_approval
Revises: 0007_employee_profile_fields
Create Date: 2026-11-26 20:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_leave_two_stage_approval"
down_revision: str | Sequence[str] | None = "0007_employee_profile_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE leave_requests DROP CONSTRAINT IF EXISTS "
        "ck_leave_requests_ck_leave_requests_status_valid"
    )
    op.execute(
        "ALTER TABLE leave_requests DROP CONSTRAINT IF EXISTS ck_leave_requests_status_valid"
    )
    op.execute("UPDATE leave_requests SET status = 'pending_hr' WHERE status = 'pending'")
    op.create_check_constraint(
        "status_valid",
        "leave_requests",
        "status IN ('pending_manager', 'pending_hr', 'approved', 'rejected', 'cancelled')",
    )
    op.alter_column(
        "leave_requests",
        "status",
        server_default=sa.text("'pending_manager'"),
    )

    op.add_column(
        "leave_requests",
        sa.Column("manager_reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "leave_requests",
        sa.Column("manager_reviewed_at_utc", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "leave_requests",
        sa.Column(
            "manager_review_note",
            sa.String(length=300),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )
    op.add_column(
        "leave_requests",
        sa.Column("hr_reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "leave_requests",
        sa.Column("hr_reviewed_at_utc", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "leave_requests",
        sa.Column(
            "hr_review_note",
            sa.String(length=300),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("leave_requests", "hr_review_note")
    op.drop_column("leave_requests", "hr_reviewed_at_utc")
    op.drop_column("leave_requests", "hr_reviewed_by_user_id")
    op.drop_column("leave_requests", "manager_review_note")
    op.drop_column("leave_requests", "manager_reviewed_at_utc")
    op.drop_column("leave_requests", "manager_reviewed_by_user_id")

    op.execute(
        "ALTER TABLE leave_requests DROP CONSTRAINT IF EXISTS "
        "ck_leave_requests_ck_leave_requests_status_valid"
    )
    op.execute(
        "ALTER TABLE leave_requests DROP CONSTRAINT IF EXISTS ck_leave_requests_status_valid"
    )
    op.execute(
        "UPDATE leave_requests SET status = 'pending' "
        "WHERE status IN ('pending_manager', 'pending_hr')"
    )
    op.create_check_constraint(
        "status_valid",
        "leave_requests",
        "status IN ('pending', 'approved', 'rejected', 'cancelled')",
    )
    op.alter_column(
        "leave_requests",
        "status",
        server_default=sa.text("'pending'"),
    )
