"""Phase 5G: HR Leave Entitlements, Direct Assign, and 3-Tier Custom Break Policies.

Revision ID: 0010_hr_leave_break_policies
Revises: 0009_employee_fixed_break
Create Date: 2026-11-26 21:10:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_hr_leave_break_policies"
down_revision: str | Sequence[str] | None = "0009_employee_fixed_break"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. public.leave_entitlements
    op.create_table(
        "leave_entitlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column(
            "base_vacation_days",
            sa.Integer(),
            server_default=sa.text("30"),
            nullable=False,
        ),
        sa.Column(
            "carryover_days",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "comp_off_days",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("year >= 2020 AND year <= 2100", name="ck_leave_entitlements_year"),
        sa.CheckConstraint(
            "base_vacation_days >= 30 AND base_vacation_days <= 90",
            name="ck_leave_entitlements_base_days",
        ),
        sa.CheckConstraint(
            "carryover_days >= -30 AND carryover_days <= 60",
            name="ck_leave_entitlements_carryover_days",
        ),
        sa.CheckConstraint(
            "comp_off_days >= 0 AND comp_off_days <= 90",
            name="ck_leave_entitlements_comp_off_days",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_leave_entitlements_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_leave_entitlements_employee",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_leave_entitlements"),
        sa.UniqueConstraint(
            "tenant_id",
            "employee_id",
            "year",
            name="uq_leave_entitlements_tenant_emp_year",
        ),
    )

    # 2. public.leave_entitlement_adjustments (STRICTLY APPEND-ONLY)
    op.create_table(
        "leave_entitlement_adjustments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("adjustment_type", sa.String(length=24), nullable=False),
        sa.Column("delta_days", sa.Integer(), nullable=False),
        sa.Column("previous_total_days", sa.Integer(), nullable=False),
        sa.Column("new_total_days", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("adjusted_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "adjustment_type IN ('base_update', 'carryover', 'comp_off', 'bulk_init')",
            name="ck_leave_entitlement_adj_type_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_leave_entitlement_adj_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_leave_entitlement_adj_employee",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_leave_entitlement_adjustments"),
    )
    op.create_index(
        "ix_leave_entitlement_adj_tenant_emp_year",
        "leave_entitlement_adjustments",
        ["tenant_id", "employee_id", "year"],
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_leave_entitlement_adj_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'leave_entitlement_adjustments is strictly append-only: UPDATE/DELETE forbidden';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_leave_entitlement_adj_truncate()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'TRUNCATE is forbidden on leave_entitlement_adjustments';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_leave_entitlement_adj_immutable
        BEFORE UPDATE OR DELETE ON public.leave_entitlement_adjustments
        FOR EACH ROW
        EXECUTE FUNCTION prevent_leave_entitlement_adj_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_leave_entitlement_adj_no_truncate
        BEFORE TRUNCATE ON public.leave_entitlement_adjustments
        FOR EACH STATEMENT
        EXECUTE FUNCTION prevent_leave_entitlement_adj_truncate();
        """
    )

    # 3. Extend public.leave_requests
    op.add_column(
        "leave_requests",
        sa.Column(
            "assigned_directly",
            sa.Boolean(),
            server_default=sa.text("FALSE"),
            nullable=False,
        ),
    )
    op.add_column(
        "leave_requests",
        sa.Column("assigned_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # 4. public.break_policies
    op.create_table(
        "break_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("break_mode", sa.String(length=24), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column(
            "windows_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "is_paid",
            sa.Boolean(),
            server_default=sa.text("FALSE"),
            nullable=False,
        ),
        sa.Column(
            "tracking_mode",
            sa.String(length=24),
            server_default=sa.text("'manual_punch'"),
            nullable=False,
        ),
        sa.Column(
            "min_shift_minutes_for_auto_deduct",
            sa.Integer(),
            server_default=sa.text("360"),
            nullable=False,
        ),
        sa.Column(
            "auto_end_manual_break",
            sa.Boolean(),
            server_default=sa.text("TRUE"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("TRUE"),
            nullable=False,
        ),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "scope_type IN ('tenant', 'store', 'employee')",
            name="ck_break_policies_scope_type",
        ),
        sa.CheckConstraint(
            "(scope_type = 'tenant' AND store_id IS NULL AND employee_id IS NULL) OR "
            "(scope_type = 'store' AND store_id IS NOT NULL AND employee_id IS NULL) OR "
            "(scope_type = 'employee' AND store_id IS NULL AND employee_id IS NOT NULL)",
            name="ck_break_policies_scope_target",
        ),
        sa.CheckConstraint(
            "break_mode IN ('fixed_duration', 'fixed_window')",
            name="ck_break_policies_mode",
        ),
        sa.CheckConstraint(
            "duration_minutes IS NULL OR (duration_minutes >= 5 AND duration_minutes <= 240)",
            name="ck_break_policies_duration",
        ),
        sa.CheckConstraint(
            "tracking_mode IN ('manual_punch', 'auto_deduct')",
            name="ck_break_policies_tracking_mode",
        ),
        sa.CheckConstraint(
            "min_shift_minutes_for_auto_deduct >= 60 AND min_shift_minutes_for_auto_deduct <= 720",
            name="ck_break_policies_min_shift_auto_deduct",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_break_policies_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name="fk_break_policies_store",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_break_policies_employee",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_break_policies"),
    )
    op.create_index(
        "uq_break_policies_tenant",
        "break_policies",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("scope_type = 'tenant' AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_break_policies_store",
        "break_policies",
        ["tenant_id", "store_id"],
        unique=True,
        postgresql_where=sa.text("scope_type = 'store' AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_break_policies_employee",
        "break_policies",
        ["tenant_id", "employee_id"],
        unique=True,
        postgresql_where=sa.text("scope_type = 'employee' AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_break_policies_employee", table_name="break_policies")
    op.drop_index("uq_break_policies_store", table_name="break_policies")
    op.drop_index("uq_break_policies_tenant", table_name="break_policies")
    op.drop_table("break_policies")

    op.drop_column("leave_requests", "assigned_by_user_id")
    op.drop_column("leave_requests", "assigned_directly")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_leave_entitlement_adj_no_truncate "
        "ON public.leave_entitlement_adjustments;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_leave_entitlement_adj_immutable "
        "ON public.leave_entitlement_adjustments;"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_leave_entitlement_adj_truncate();")
    op.execute("DROP FUNCTION IF EXISTS prevent_leave_entitlement_adj_mutation();")
    op.drop_index(
        "ix_leave_entitlement_adj_tenant_emp_year",
        table_name="leave_entitlement_adjustments",
    )
    op.drop_table("leave_entitlement_adjustments")

    op.drop_table("leave_entitlements")
