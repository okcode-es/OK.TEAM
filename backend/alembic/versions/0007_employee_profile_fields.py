"""Phase 5C: Employee profile fields (job_title, emergency_contact_name, emergency_contact_phone).

Revision ID: 0007_employee_profile_fields
Revises: 0006_phase5a_workspace_ops
Create Date: 2026-11-26 18:20:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_employee_profile_fields"
down_revision: str | Sequence[str] | None = "0006_phase5a_workspace_ops"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column(
            "job_title",
            sa.String(length=120),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )
    op.add_column(
        "employees",
        sa.Column(
            "emergency_contact_name",
            sa.String(length=120),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )
    op.add_column(
        "employees",
        sa.Column(
            "emergency_contact_phone",
            sa.String(length=40),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("employees", "emergency_contact_phone")
    op.drop_column("employees", "emergency_contact_name")
    op.drop_column("employees", "job_title")
