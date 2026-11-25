"""Phase 5: monthly_closings, immutable monthly_closing_items, and employees user_id unique index.

Revision ID: 0005_phase5_payroll_closing
Revises: 0004_phase4_sched_leaves_docs
Create Date: 2026-11-25 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_phase5_payroll_closing"
down_revision: str | Sequence[str] | None = "0004_phase4_sched_leaves_docs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monthly_closings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_month", sa.String(length=7), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "revision_number",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("closed_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("closed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reopened_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reopened_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reopen_reason",
            sa.String(length=500),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "notes",
            sa.String(length=500),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('closed', 'reopened')",
            name="ck_monthly_closings_status_valid",
        ),
        sa.CheckConstraint(
            "revision_number >= 1",
            name="ck_monthly_closings_revision_positive",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_monthly_closings_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name="fk_monthly_closings_store_id_stores",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_monthly_closings"),
    )
    op.create_index(
        "uq_monthly_closings_tenant_store_month_active",
        "monthly_closings",
        ["tenant_id", "store_id", "period_month"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_monthly_closings_tenant_month",
        "monthly_closings",
        ["tenant_id", "period_month"],
        unique=False,
    )

    op.create_table(
        "monthly_closing_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("closing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_month", sa.String(length=7), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_code", sa.String(length=32), nullable=False),
        sa.Column("employee_full_name", sa.String(length=160), nullable=False),
        sa.Column("nie_dni_masked", sa.String(length=32), nullable=True),
        sa.Column("contract_type", sa.String(length=32), nullable=False),
        sa.Column("work_schedule_type", sa.String(length=20), nullable=False),
        sa.Column("contracted_weekly_hours", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("worked_days", sa.Integer(), nullable=False),
        sa.Column("total_net_hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("regular_hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("overtime_hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("annual_overtime_hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("total_break_minutes", sa.Integer(), nullable=False),
        sa.Column("vacation_days", sa.Integer(), nullable=False),
        sa.Column("sick_leave_days", sa.Integer(), nullable=False),
        sa.Column("paid_leave_days", sa.Integer(), nullable=False),
        sa.Column("unpaid_leave_days", sa.Integer(), nullable=False),
        sa.Column("warning_alerts_count", sa.Integer(), nullable=False),
        sa.Column("violation_alerts_count", sa.Integer(), nullable=False),
        sa.Column("attendance_report_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision_number >= 1", name="ck_monthly_closing_items_rev_pos"),
        sa.CheckConstraint("worked_days >= 0", name="ck_monthly_closing_items_worked_days_nn"),
        sa.CheckConstraint("total_net_hours >= 0", name="ck_monthly_closing_items_net_hours_nn"),
        sa.CheckConstraint("regular_hours >= 0", name="ck_monthly_closing_items_reg_hours_nn"),
        sa.CheckConstraint("overtime_hours >= 0", name="ck_monthly_closing_items_ot_hours_nn"),
        sa.CheckConstraint(
            "annual_overtime_hours >= 0", name="ck_monthly_closing_items_ann_ot_nn"
        ),
        sa.CheckConstraint(
            "total_break_minutes >= 0", name="ck_monthly_closing_items_break_min_nn"
        ),
        sa.CheckConstraint("vacation_days >= 0", name="ck_monthly_closing_items_vac_days_nn"),
        sa.CheckConstraint("sick_leave_days >= 0", name="ck_monthly_closing_items_sick_days_nn"),
        sa.CheckConstraint("paid_leave_days >= 0", name="ck_monthly_closing_items_paid_days_nn"),
        sa.CheckConstraint(
            "unpaid_leave_days >= 0", name="ck_monthly_closing_items_unpaid_days_nn"
        ),
        sa.CheckConstraint(
            "warning_alerts_count >= 0", name="ck_monthly_closing_items_warn_cnt_nn"
        ),
        sa.CheckConstraint(
            "violation_alerts_count >= 0", name="ck_monthly_closing_items_viol_cnt_nn"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_monthly_closing_items_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["closing_id"],
            ["monthly_closings.id"],
            name="fk_monthly_closing_items_closing_id_monthly_closings",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name="fk_monthly_closing_items_store_id_stores",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_monthly_closing_items_employee_id_employees",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attendance_report_document_id"],
            ["compliance_documents.id"],
            name="fk_monthly_closing_items_doc_id_compliance_documents",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_monthly_closing_items"),
        sa.UniqueConstraint(
            "tenant_id",
            "closing_id",
            "revision_number",
            "employee_id",
            name="uq_monthly_closing_items_rev_emp",
        ),
    )
    op.create_index(
        "ix_monthly_closing_items_closing_rev",
        "monthly_closing_items",
        ["tenant_id", "closing_id", "revision_number"],
        unique=False,
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_monthly_closing_items_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'monthly_closing_items is strictly append-only (RDL 8/2019)';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_monthly_closing_items_truncate()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'TRUNCATE is forbidden on monthly_closing_items (RDL 8/2019)';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_monthly_closing_items_immutable
        BEFORE UPDATE OR DELETE ON public.monthly_closing_items
        FOR EACH ROW
        EXECUTE FUNCTION prevent_monthly_closing_items_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_monthly_closing_items_no_truncate
        BEFORE TRUNCATE ON public.monthly_closing_items
        FOR EACH STATEMENT
        EXECUTE FUNCTION prevent_monthly_closing_items_truncate();
        """
    )

    # P2 Backlog: Partial unique index on employees(tenant_id, user_id)
    op.create_index(
        "uq_employees_tenant_user_active",
        "employees",
        ["tenant_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_employees_tenant_user_active", table_name="employees")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_monthly_closing_items_no_truncate "
        "ON public.monthly_closing_items;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_monthly_closing_items_immutable "
        "ON public.monthly_closing_items;"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_monthly_closing_items_truncate();")
    op.execute("DROP FUNCTION IF EXISTS prevent_monthly_closing_items_mutation();")

    op.drop_index("ix_monthly_closing_items_closing_rev", table_name="monthly_closing_items")
    op.drop_table("monthly_closing_items")

    op.drop_index("ix_monthly_closings_tenant_month", table_name="monthly_closings")
    op.drop_index("uq_monthly_closings_tenant_store_month_active", table_name="monthly_closings")
    op.drop_table("monthly_closings")
