"""Phase 5H: Attendance day reviews table for employee submit / manager approve workflow.

Revision ID: 0011_attendance_day_reviews
Revises: 0010_hr_leave_break_policies
Create Date: 2026-11-27 09:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_attendance_day_reviews"
down_revision: str | Sequence[str] | None = "0010_hr_leave_break_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attendance_day_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "note",
            sa.String(length=300),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('submitted', 'approved')",
            name="ck_attendance_day_reviews_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_attendance_day_reviews_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_attendance_day_reviews_employee",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name="fk_attendance_day_reviews_store",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_attendance_day_reviews"),
        sa.UniqueConstraint(
            "tenant_id",
            "employee_id",
            "local_date",
            name="uq_attendance_day_reviews_tenant_emp_date",
        ),
    )


def downgrade() -> None:
    op.drop_table("attendance_day_reviews")
