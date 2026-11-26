"""Phase 5F: Employee fixed break duration (default_break_minutes).

Revision ID: 0009_employee_fixed_break
Revises: 0008_leave_two_stage_approval
Create Date: 2026-11-26 20:45:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_employee_fixed_break"
down_revision: str | Sequence[str] | None = "0008_leave_two_stage_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column("default_break_minutes", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_employees_default_break_valid",
        "employees",
        "default_break_minutes IS NULL OR "
        "(default_break_minutes >= 5 AND default_break_minutes <= 180)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_employees_default_break_valid", "employees", type_="check")
    op.drop_column("employees", "default_break_minutes")
