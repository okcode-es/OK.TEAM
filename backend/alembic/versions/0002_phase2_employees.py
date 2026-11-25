"""Phase 2: employees table with Spanish contract constraints, encrypted PII, and 4y legal hold.

Revision ID: 0002_phase2_employees
Revises: 0001_phase1_org_rbac
Create Date: 2026-11-25 09:40:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_phase2_employees"
down_revision: str | Sequence[str] | None = "0001_phase1_org_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("primary_store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("employee_code", sa.String(length=32), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column(
            "preferred_name",
            sa.String(length=120),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=255), server_default=sa.text("''"), nullable=False),
        sa.Column("phone", sa.String(length=40), server_default=sa.text("''"), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("contract_type", sa.String(length=32), nullable=False),
        sa.Column("work_schedule_type", sa.String(length=20), nullable=False),
        sa.Column("contracted_weekly_hours", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column(
            "annual_vacation_days",
            sa.Integer(),
            server_default=sa.text("30"),
            nullable=False,
        ),
        sa.Column("hire_date", sa.Date(), nullable=False),
        sa.Column("termination_date", sa.Date(), nullable=True),
        sa.Column("nie_dni_enc", sa.Text(), nullable=True),
        sa.Column("nie_dni_masked", sa.String(length=32), nullable=True),
        sa.Column("nie_dni_blind_index", sa.String(length=64), nullable=True),
        sa.Column("iban_enc", sa.Text(), nullable=True),
        sa.Column("iban_masked", sa.String(length=40), nullable=True),
        sa.Column("naf_enc", sa.Text(), nullable=True),
        sa.Column("naf_masked", sa.String(length=32), nullable=True),
        sa.Column(
            "employment_status",
            sa.String(length=20),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "contract_type IN ('indefinido', 'temporal', 'fijo_discontinuo', 'practicas')",
            name="ck_employees_employees_contract_type_valid",
        ),
        sa.CheckConstraint(
            "work_schedule_type IN ('full_time', 'part_time')",
            name="ck_employees_employees_schedule_type_valid",
        ),
        sa.CheckConstraint(
            "contracted_weekly_hours > 0 AND contracted_weekly_hours <= 40.00",
            name="ck_employees_employees_weekly_hours_valid",
        ),
        sa.CheckConstraint(
            "annual_vacation_days >= 30",
            name="ck_employees_employees_vacation_days_valid",
        ),
        sa.CheckConstraint(
            "employment_status IN ('active', 'on_leave', 'terminated', 'legal_hold', 'anonymized')",
            name="ck_employees_employees_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_employees_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["primary_store_id"],
            ["stores.id"],
            name="fk_employees_primary_store_id_stores",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_employees"),
    )
    op.create_index(
        "ix_employees_tenant_store",
        "employees",
        ["tenant_id", "primary_store_id"],
        unique=False,
    )
    op.create_index(
        "ix_employees_tenant_user",
        "employees",
        ["tenant_id", "user_id"],
        unique=False,
    )
    op.create_index(
        "uq_employees_tenant_code_active",
        "employees",
        ["tenant_id", sa.text("upper(employee_code)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_employees_tenant_nie_blind_active",
        "employees",
        ["tenant_id", "nie_dni_blind_index"],
        unique=True,
        postgresql_where=sa.text("nie_dni_blind_index IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_employees_tenant_nie_blind_active", table_name="employees")
    op.drop_index("uq_employees_tenant_code_active", table_name="employees")
    op.drop_index("ix_employees_tenant_user", table_name="employees")
    op.drop_index("ix_employees_tenant_store", table_name="employees")
    op.drop_table("employees")
