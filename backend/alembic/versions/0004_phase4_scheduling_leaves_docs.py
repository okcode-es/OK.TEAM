"""Phase 4: shifts, leave_requests, compliance_documents, and immutable document_receipts.

Revision ID: 0004_phase4_sched_leaves_docs
Revises: 0003_phase3_attendance
Create Date: 2026-11-25 11:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_phase4_sched_leaves_docs"
down_revision: str | Sequence[str] | None = "0003_phase3_attendance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shifts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("start_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "planned_break_minutes",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column(
            "notes",
            sa.String(length=500),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("published_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("end_at_utc > start_at_utc", name="ck_shifts_end_after_start"),
        sa.CheckConstraint(
            "planned_break_minutes >= 0", name="ck_shifts_planned_break_non_negative"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'cancelled')",
            name="ck_shifts_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_shifts_tenant_id_tenants", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.id"], name="fk_shifts_store_id_stores", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_shifts_employee_id_employees",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_shifts"),
    )
    op.create_index(
        "ix_shifts_tenant_store_start",
        "shifts",
        ["tenant_id", "store_id", "start_at_utc"],
        unique=False,
    )
    op.create_index(
        "ix_shifts_tenant_emp_start",
        "shifts",
        ["tenant_id", "employee_id", "start_at_utc"],
        unique=False,
    )

    op.create_table(
        "leave_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("leave_type", sa.String(length=24), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("natural_days", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "reason",
            sa.String(length=500),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "review_note",
            sa.String(length=500),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("medical_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("end_date >= start_date", name="ck_leave_requests_end_gte_start"),
        sa.CheckConstraint("natural_days > 0", name="ck_leave_requests_natural_days_positive"),
        sa.CheckConstraint(
            "leave_type IN ('vacation', 'sick_leave', 'paid_leave', 'unpaid_leave')",
            name="ck_leave_requests_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_leave_requests_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_leave_requests_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name="fk_leave_requests_store_id_stores",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_leave_requests_employee_id_employees",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_leave_requests"),
    )
    op.create_index(
        "ix_leave_requests_tenant_emp_dates",
        "leave_requests",
        ["tenant_id", "employee_id", "start_date", "end_date"],
        unique=False,
    )
    op.create_index(
        "ix_leave_requests_tenant_store_status",
        "leave_requests",
        ["tenant_id", "store_id", "status"],
        unique=False,
    )

    op.create_table(
        "compliance_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("period_month", sa.String(length=7), nullable=True),
        sa.Column("storage_backend", sa.String(length=24), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256_hex", sa.String(length=64), nullable=False),
        sa.Column(
            "requires_signature",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("uploaded_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("size_bytes > 0", name="ck_compliance_documents_size_positive"),
        sa.CheckConstraint(
            "category IN ('contract', 'payslip', 'attendance_report', "
            "'medical_leave', 'id_document', 'other')",
            name="ck_compliance_documents_category_valid",
        ),
        sa.CheckConstraint(
            "storage_backend IN ('local', 'supabase')",
            name="ck_compliance_documents_storage_backend_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_compliance_documents_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name="fk_compliance_documents_store_id_stores",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_compliance_documents_employee_id_employees",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_compliance_documents"),
        sa.UniqueConstraint("storage_key", name="uq_compliance_documents_storage_key"),
    )
    op.create_index(
        "ix_compliance_documents_tenant_emp_cat",
        "compliance_documents",
        ["tenant_id", "employee_id", "category"],
        unique=False,
    )
    op.create_index(
        "ix_compliance_documents_tenant_store_created",
        "compliance_documents",
        ["tenant_id", "store_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "document_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signed_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_sha256_hex", sa.String(length=64), nullable=False),
        sa.Column("signed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=False),
        sa.Column("receipt_statement", sa.String(length=500), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_document_receipts_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["compliance_documents.id"],
            name="fk_document_receipts_document_id_compliance_documents",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_document_receipts_employee_id_employees",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_receipts"),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "employee_id",
            name="uq_document_receipts_tenant_doc_emp",
        ),
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_document_receipts_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'document_receipts is strictly append-only: UPDATE/DELETE/TRUNCATE forbidden';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_document_receipts_immutable
        BEFORE UPDATE OR DELETE ON document_receipts
        FOR EACH ROW
        EXECUTE FUNCTION prevent_document_receipts_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_document_receipts_no_truncate
        BEFORE TRUNCATE ON document_receipts
        FOR EACH STATEMENT
        EXECUTE FUNCTION prevent_document_receipts_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_attendance_events_no_truncate
        BEFORE TRUNCATE ON attendance_events
        FOR EACH STATEMENT
        EXECUTE FUNCTION prevent_attendance_event_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_attendance_events_no_truncate ON attendance_events;")
    op.execute("DROP TRIGGER IF EXISTS trg_document_receipts_no_truncate ON document_receipts;")
    op.execute("DROP TRIGGER IF EXISTS trg_document_receipts_immutable ON document_receipts;")
    op.execute("DROP FUNCTION IF EXISTS prevent_document_receipts_mutation();")

    op.drop_table("document_receipts")

    op.drop_index(
        "ix_compliance_documents_tenant_store_created", table_name="compliance_documents"
    )
    op.drop_index("ix_compliance_documents_tenant_emp_cat", table_name="compliance_documents")
    op.drop_table("compliance_documents")

    op.drop_index("ix_leave_requests_tenant_store_status", table_name="leave_requests")
    op.drop_index("ix_leave_requests_tenant_emp_dates", table_name="leave_requests")
    op.drop_table("leave_requests")

    op.drop_index("ix_shifts_tenant_emp_start", table_name="shifts")
    op.drop_index("ix_shifts_tenant_store_start", table_name="shifts")
    op.drop_table("shifts")
