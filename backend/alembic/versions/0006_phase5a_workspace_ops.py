"""Phase 5A: Workspace Ops — store region, dept scope, avatars, work_mode, leaves, announcements.

Revision ID: 0006_phase5a_workspace_ops
Revises: 0005_phase5_payroll_closing
Create Date: 2026-11-25 14:30:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_phase5a_workspace_ops"
down_revision: str | Sequence[str] | None = "0005_phase5_payroll_closing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. public.stores: region_code
    op.add_column(
        "stores",
        sa.Column(
            "region_code",
            sa.String(length=16),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )

    # 2. public.memberships: department_scope
    op.add_column(
        "memberships",
        sa.Column("department_scope", sa.String(length=100), nullable=True),
    )

    # 3. public.employees: department + avatar columns
    op.add_column(
        "employees",
        sa.Column(
            "department",
            sa.String(length=100),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )
    op.add_column(
        "employees",
        sa.Column("avatar_storage_backend", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "employees",
        sa.Column("avatar_storage_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "employees",
        sa.Column("avatar_mime_type", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "employees",
        sa.Column("avatar_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_employees_avatar_backend_valid",
        "employees",
        "avatar_storage_backend IS NULL OR avatar_storage_backend IN ('local', 'supabase')",
    )

    # 4. public.attendance_events: work_mode
    op.add_column(
        "attendance_events",
        sa.Column(
            "work_mode",
            sa.String(length=16),
            server_default=sa.text("'onsite'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_attendance_events_work_mode_valid",
        "attendance_events",
        "work_mode IN ('onsite', 'remote')",
    )

    # 5. public.leave_requests: extend leave_type CHECK + FK & index on medical_document_id
    op.drop_constraint("ck_leave_requests_type_valid", "leave_requests", type_="check")
    op.create_check_constraint(
        "ck_leave_requests_type_valid",
        "leave_requests",
        "leave_type IN ('vacation', 'sick_leave', 'medical_appointment', "
        "'day_off', 'paid_leave', 'unpaid_leave')",
    )
    op.create_foreign_key(
        "fk_leave_requests_medical_doc",
        "leave_requests",
        "compliance_documents",
        ["medical_document_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_leave_requests_medical_doc",
        "leave_requests",
        ["medical_document_id"],
        unique=False,
    )

    # 6. public.announcements
    op.create_table(
        "announcements",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("department_scope", sa.String(length=100), nullable=True),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("attachment_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content_sha256_hex", sa.String(length=64), nullable=False),
        sa.Column(
            "requires_acknowledgement",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_pinned",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("published_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "category IN ('notice', 'policy')",
            name="ck_announcements_category_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_announcements_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name="fk_announcements_store_id_stores",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attachment_document_id"],
            ["compliance_documents.id"],
            name="fk_announcements_attachment_doc_id_compliance_documents",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_announcements"),
    )
    op.create_index(
        "ix_announcements_tenant_pub",
        "announcements",
        ["tenant_id", "published_at_utc"],
        unique=False,
    )
    op.create_index(
        "ix_announcements_tenant_store",
        "announcements",
        ["tenant_id", "store_id"],
        unique=False,
    )

    # 7. public.announcement_acknowledgements (strictly append-only)
    op.create_table(
        "announcement_acknowledgements",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("announcement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("acknowledged_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_sha256_hex", sa.String(length=64), nullable=False),
        sa.Column("acknowledged_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=False),
        sa.Column("statement", sa.String(length=500), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_announcement_acks_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["announcement_id"],
            ["announcements.id"],
            name="fk_announcement_acks_ann_id_announcements",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_announcement_acks_emp_id_employees",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_announcement_acknowledgements"),
        sa.UniqueConstraint(
            "tenant_id",
            "announcement_id",
            "employee_id",
            name="uq_announcement_acks_tenant_ann_emp",
        ),
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_announcement_acks_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'announcement_acknowledgements is strictly append-only: UPDATE/DELETE forbidden';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_announcement_acks_truncate()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'TRUNCATE is forbidden on announcement_acknowledgements';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_announcement_acks_immutable
        BEFORE UPDATE OR DELETE ON public.announcement_acknowledgements
        FOR EACH ROW
        EXECUTE FUNCTION prevent_announcement_acks_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_announcement_acks_no_truncate
        BEFORE TRUNCATE ON public.announcement_acknowledgements
        FOR EACH STATEMENT
        EXECUTE FUNCTION prevent_announcement_acks_truncate();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_announcement_acks_no_truncate "
        "ON public.announcement_acknowledgements;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_announcement_acks_immutable "
        "ON public.announcement_acknowledgements;"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_announcement_acks_truncate();")
    op.execute("DROP FUNCTION IF EXISTS prevent_announcement_acks_mutation();")

    op.drop_table("announcement_acknowledgements")

    op.drop_index("ix_announcements_tenant_store", table_name="announcements")
    op.drop_index("ix_announcements_tenant_pub", table_name="announcements")
    op.drop_table("announcements")

    op.drop_index("ix_leave_requests_medical_doc", table_name="leave_requests")
    op.drop_constraint("fk_leave_requests_medical_doc", "leave_requests", type_="foreignkey")
    op.drop_constraint("ck_leave_requests_type_valid", "leave_requests", type_="check")
    op.create_check_constraint(
        "ck_leave_requests_type_valid",
        "leave_requests",
        "leave_type IN ('vacation', 'sick_leave', 'paid_leave', 'unpaid_leave')",
    )

    op.drop_constraint("ck_attendance_events_work_mode_valid", "attendance_events", type_="check")
    op.drop_column("attendance_events", "work_mode")

    op.drop_constraint("ck_employees_avatar_backend_valid", "employees", type_="check")
    op.drop_column("employees", "avatar_updated_at")
    op.drop_column("employees", "avatar_mime_type")
    op.drop_column("employees", "avatar_storage_key")
    op.drop_column("employees", "avatar_storage_backend")
    op.drop_column("employees", "department")

    op.drop_column("memberships", "department_scope")

    op.drop_column("stores", "region_code")
