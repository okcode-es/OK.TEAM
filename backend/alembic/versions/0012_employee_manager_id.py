"""Phase 5I: Add nullable self-referential manager_id reporting line to employees.

Revision ID: 0012_employee_manager_id
Revises: 0011_attendance_day_reviews
Create Date: 2026-11-27 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_employee_manager_id"
down_revision: str | Sequence[str] | None = "0011_attendance_day_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column("manager_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_employees_manager_id",
        "employees",
        "employees",
        ["manager_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_employees_no_self_manager",
        "employees",
        "manager_id IS NULL OR manager_id <> id",
    )
    op.create_index(
        "ix_employees_tenant_manager",
        "employees",
        ["tenant_id", "manager_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_employees_tenant_manager", table_name="employees")
    op.drop_constraint("ck_employees_no_self_manager", "employees", type_="check")
    op.drop_constraint("fk_employees_manager_id", "employees", type_="foreignkey")
    op.drop_column("employees", "manager_id")
