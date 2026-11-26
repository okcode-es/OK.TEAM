"""SQLAlchemy 2.0 ORM models for Phase 1 (public schema only, UTC TIMESTAMPTZ, soft delete)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base


class TenantModel(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        Index(
            "uq_tenants_cif_nif_active",
            func.upper(text("cif_nif")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trade_name: Mapped[str] = mapped_column(String(200), nullable=False)
    cif_nif: Mapped[str] = mapped_column(String(32), nullable=False)
    default_timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'Europe/Madrid'"),
    )
    default_locale: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        server_default=text("'zh-CN'"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StoreModel(Base):
    __tablename__ = "stores"
    __table_args__ = (
        Index("ix_stores_tenant_id", "tenant_id"),
        Index(
            "uq_stores_tenant_code_active",
            "tenant_id",
            func.upper(text("code")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    address: Mapped[str] = mapped_column(String(300), nullable=False, server_default=text("''"))
    city: Mapped[str] = mapped_column(String(100), nullable=False, server_default=text("''"))
    postal_code: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("''"))
    region_code: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("''"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MembershipModel(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'hr', 'store_manager', 'accountant', 'employee')",
            name="memberships_role_valid",
        ),
        CheckConstraint(
            "status IN ('active', 'invited', 'disabled')",
            name="memberships_status_valid",
        ),
        Index("ix_memberships_tenant_id", "tenant_id"),
        Index("ix_memberships_user_id", "user_id"),
        Index(
            "uq_memberships_tenant_user_active",
            "tenant_id",
            "user_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Logical reference to Supabase auth.users.id — NO cross-schema FK to auth schema.
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'active'"),
    )
    department_scope: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    store_links: Mapped[list[MembershipStoreModel]] = relationship(
        "MembershipStoreModel",
        back_populates="membership",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class MembershipStoreModel(Base):
    __tablename__ = "membership_stores"
    __table_args__ = (
        Index("ix_membership_stores_tenant_store", "tenant_id", "store_id"),
    )

    membership_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("memberships.id", ondelete="CASCADE"),
        primary_key=True,
    )
    store_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    membership: Mapped[MembershipModel] = relationship(
        "MembershipModel",
        back_populates="store_links",
    )


class EmployeeModel(Base):
    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint(
            "contract_type IN ('indefinido', 'temporal', 'fijo_discontinuo', 'practicas')",
            name="employees_contract_type_valid",
        ),
        CheckConstraint(
            "work_schedule_type IN ('full_time', 'part_time')",
            name="employees_schedule_type_valid",
        ),
        CheckConstraint(
            "contracted_weekly_hours > 0 AND contracted_weekly_hours <= 40.00",
            name="employees_weekly_hours_valid",
        ),
        CheckConstraint(
            "annual_vacation_days >= 30",
            name="employees_vacation_days_valid",
        ),
        CheckConstraint(
            "employment_status IN ('active', 'on_leave', 'terminated', 'legal_hold', 'anonymized')",
            name="employees_status_valid",
        ),
        CheckConstraint(
            "avatar_storage_backend IS NULL OR avatar_storage_backend IN ('local', 'supabase')",
            name="ck_employees_avatar_backend_valid",
        ),
        CheckConstraint(
            "default_break_minutes IS NULL OR "
            "(default_break_minutes >= 5 AND default_break_minutes <= 180)",
            name="ck_employees_default_break_valid",
        ),
        Index("ix_employees_tenant_store", "tenant_id", "primary_store_id"),
        Index("ix_employees_tenant_user", "tenant_id", "user_id"),
        Index(
            "uq_employees_tenant_code_active",
            "tenant_id",
            func.upper(text("employee_code")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_employees_tenant_nie_blind_active",
            "tenant_id",
            "nie_dni_blind_index",
            unique=True,
            postgresql_where=text("nie_dni_blind_index IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "uq_employees_tenant_user_active",
            "tenant_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    primary_store_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    employee_code: Mapped[str] = mapped_column(String(32), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    preferred_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        server_default=text("''"),
    )
    department: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        server_default=text("''"),
    )
    job_title: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        server_default=text("''"),
    )
    emergency_contact_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        server_default=text("''"),
    )
    emergency_contact_phone: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        server_default=text("''"),
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    phone: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("''"))
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    contract_type: Mapped[str] = mapped_column(String(32), nullable=False)
    work_schedule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    contracted_weekly_hours: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    annual_vacation_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("30"),
    )
    default_break_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hire_date: Mapped[date] = mapped_column(Date, nullable=False)
    termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # AES-256-GCM encrypted PII columns + safe masked display values + blind index (NO plaintext)
    nie_dni_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    nie_dni_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nie_dni_blind_index: Mapped[str | None] = mapped_column(String(64), nullable=True)
    iban_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    iban_masked: Mapped[str | None] = mapped_column(String(40), nullable=True)
    naf_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    naf_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)

    employment_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'active'"),
    )
    avatar_storage_backend: Mapped[str | None] = mapped_column(String(24), nullable=True)
    avatar_storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    avatar_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AttendanceEventModel(Base):
    """Strictly append-only attendance ledger (RDL 8/2019).

    Database trigger ``trg_attendance_events_immutable`` forbids UPDATE and DELETE.
    """

    __tablename__ = "attendance_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('clock_in', 'break_start', 'break_end', 'clock_out', 'correction')",
            name="attendance_events_event_type_valid",
        ),
        CheckConstraint(
            "source IN ('web', 'kiosk', 'manager_correction')",
            name="attendance_events_source_valid",
        ),
        CheckConstraint(
            "corrected_event_type IS NULL OR corrected_event_type IN "
            "('clock_in', 'break_start', 'break_end', 'clock_out', 'void')",
            name="attendance_events_corrected_type_valid",
        ),
        CheckConstraint(
            "work_mode IN ('onsite', 'remote')",
            name="ck_attendance_events_work_mode_valid",
        ),
        Index(
            "ix_attendance_events_tenant_emp_time",
            "tenant_id",
            "employee_id",
            "occurred_at_utc",
        ),
        Index(
            "ix_attendance_events_tenant_store_time",
            "tenant_id",
            "store_id",
            "occurred_at_utc",
        ),
        Index("ix_attendance_events_supersedes", "supersedes_event_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    store_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
    )
    employee_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    store_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    recorded_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    recorded_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    supersedes_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("attendance_events.id", ondelete="RESTRICT"),
        nullable=True,
    )
    corrected_event_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    correction_reason: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        server_default=text("''"),
    )
    work_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'onsite'"),
    )
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ShiftModel(Base):
    __tablename__ = "shifts"
    __table_args__ = (
        CheckConstraint("end_at_utc > start_at_utc", name="shifts_end_after_start"),
        CheckConstraint(
            "planned_break_minutes >= 0", name="shifts_planned_break_non_negative"
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'cancelled')",
            name="shifts_status_valid",
        ),
        Index("ix_shifts_tenant_store_start", "tenant_id", "store_id", "start_at_utc"),
        Index("ix_shifts_tenant_emp_start", "tenant_id", "employee_id", "start_at_utc"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    store_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False
    )
    employee_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    start_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_break_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'draft'")
    )
    notes: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"))
    created_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    published_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    published_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LeaveRequestModel(Base):
    __tablename__ = "leave_requests"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="leave_requests_end_gte_start"),
        CheckConstraint("natural_days > 0", name="leave_requests_natural_days_positive"),
        CheckConstraint(
            "leave_type IN ('vacation', 'sick_leave', 'medical_appointment', "
            "'day_off', 'paid_leave', 'unpaid_leave')",
            name="ck_leave_requests_type_valid",
        ),
        CheckConstraint(
            "status IN ('pending_manager', 'pending_hr', 'approved', 'rejected', 'cancelled')",
            name="leave_requests_status_valid",
        ),
        Index(
            "ix_leave_requests_tenant_emp_dates",
            "tenant_id",
            "employee_id",
            "start_date",
            "end_date",
        ),
        Index("ix_leave_requests_tenant_store_status", "tenant_id", "store_id", "status"),
        Index("ix_leave_requests_medical_doc", "medical_document_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    store_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False
    )
    employee_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    leave_type: Mapped[str] = mapped_column(String(24), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    natural_days: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'pending_manager'")
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"))
    requested_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    reviewed_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_note: Mapped[str] = mapped_column(
        String(500), nullable=False, server_default=text("''")
    )
    manager_reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    manager_reviewed_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    manager_review_note: Mapped[str] = mapped_column(
        String(300), nullable=False, server_default=text("''")
    )
    hr_reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    hr_reviewed_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hr_review_note: Mapped[str] = mapped_column(
        String(300), nullable=False, server_default=text("''")
    )
    medical_document_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "compliance_documents.id",
            name="fk_leave_requests_medical_doc",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    assigned_directly: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    assigned_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ComplianceDocumentModel(Base):
    __tablename__ = "compliance_documents"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="compliance_documents_size_positive"),
        CheckConstraint(
            "category IN ('contract', 'payslip', 'attendance_report', "
            "'medical_leave', 'id_document', 'other')",
            name="compliance_documents_category_valid",
        ),
        CheckConstraint(
            "storage_backend IN ('local', 'supabase')",
            name="compliance_documents_storage_backend_valid",
        ),
        UniqueConstraint("storage_key", name="uq_compliance_documents_storage_key"),
        Index("ix_compliance_documents_tenant_emp_cat", "tenant_id", "employee_id", "category"),
        Index(
            "ix_compliance_documents_tenant_store_created",
            "tenant_id",
            "store_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    store_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=True
    )
    employee_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"), nullable=True
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    period_month: Mapped[str | None] = mapped_column(String(7), nullable=True)
    storage_backend: Mapped[str] = mapped_column(String(24), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256_hex: Mapped[str] = mapped_column(String(64), nullable=False)
    requires_signature: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    uploaded_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentReceiptModel(Base):
    """Strictly append-only electronic document receipt ledger.

    Database triggers forbid UPDATE, DELETE, and TRUNCATE.
    """

    __tablename__ = "document_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "employee_id",
            name="uq_document_receipts_tenant_doc_emp",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("compliance_documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    employee_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    signed_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    document_sha256_hex: Mapped[str] = mapped_column(String(64), nullable=False)
    signed_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str] = mapped_column(String(512), nullable=False)
    receipt_statement: Mapped[str] = mapped_column(String(500), nullable=False)
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MonthlyClosingModel(Base):
    __tablename__ = "monthly_closings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('closed', 'reopened')",
            name="monthly_closings_status_valid",
        ),
        CheckConstraint("revision_number >= 1", name="monthly_closings_revision_positive"),
        Index(
            "uq_monthly_closings_tenant_store_month_active",
            "tenant_id",
            "store_id",
            "period_month",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_monthly_closings_tenant_month", "tenant_id", "period_month"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    store_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False
    )
    period_month: Mapped[str] = mapped_column(String(7), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    revision_number: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    closed_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    closed_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reopened_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    reopened_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reopen_reason: Mapped[str] = mapped_column(
        String(500), nullable=False, server_default=text("''")
    )
    notes: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"))
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MonthlyClosingItemModel(Base):
    """Strictly append-only monthly payroll closing snapshot (RDL 8/2019).

    Database triggers forbid UPDATE, DELETE, and TRUNCATE.
    """

    __tablename__ = "monthly_closing_items"
    __table_args__ = (
        CheckConstraint("revision_number >= 1", name="monthly_closing_items_rev_pos"),
        CheckConstraint("worked_days >= 0", name="monthly_closing_items_worked_days_nn"),
        CheckConstraint("total_net_hours >= 0", name="monthly_closing_items_net_hours_nn"),
        CheckConstraint("regular_hours >= 0", name="monthly_closing_items_reg_hours_nn"),
        CheckConstraint("overtime_hours >= 0", name="monthly_closing_items_ot_hours_nn"),
        CheckConstraint("annual_overtime_hours >= 0", name="monthly_closing_items_ann_ot_nn"),
        CheckConstraint("total_break_minutes >= 0", name="monthly_closing_items_break_min_nn"),
        CheckConstraint("vacation_days >= 0", name="monthly_closing_items_vac_days_nn"),
        CheckConstraint("sick_leave_days >= 0", name="monthly_closing_items_sick_days_nn"),
        CheckConstraint("paid_leave_days >= 0", name="monthly_closing_items_paid_days_nn"),
        CheckConstraint("unpaid_leave_days >= 0", name="monthly_closing_items_unpaid_days_nn"),
        CheckConstraint("warning_alerts_count >= 0", name="monthly_closing_items_warn_cnt_nn"),
        CheckConstraint("violation_alerts_count >= 0", name="monthly_closing_items_viol_cnt_nn"),
        UniqueConstraint(
            "tenant_id",
            "closing_id",
            "revision_number",
            "employee_id",
            name="uq_monthly_closing_items_rev_emp",
        ),
        Index(
            "ix_monthly_closing_items_closing_rev",
            "tenant_id",
            "closing_id",
            "revision_number",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    closing_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("monthly_closings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    store_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False
    )
    period_month: Mapped[str] = mapped_column(String(7), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    employee_code: Mapped[str] = mapped_column(String(32), nullable=False)
    employee_full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    nie_dni_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    contract_type: Mapped[str] = mapped_column(String(32), nullable=False)
    work_schedule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    contracted_weekly_hours: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    worked_days: Mapped[int] = mapped_column(Integer, nullable=False)
    total_net_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    regular_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    overtime_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    annual_overtime_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    total_break_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    vacation_days: Mapped[int] = mapped_column(Integer, nullable=False)
    sick_leave_days: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_leave_days: Mapped[int] = mapped_column(Integer, nullable=False)
    unpaid_leave_days: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_alerts_count: Mapped[int] = mapped_column(Integer, nullable=False)
    violation_alerts_count: Mapped[int] = mapped_column(Integer, nullable=False)
    attendance_report_document_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("compliance_documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnnouncementModel(Base):
    __tablename__ = "announcements"
    __table_args__ = (
        CheckConstraint(
            "category IN ('notice', 'policy')",
            name="ck_announcements_category_valid",
        ),
        Index("ix_announcements_tenant_pub", "tenant_id", "published_at_utc"),
        Index("ix_announcements_tenant_store", "tenant_id", "store_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    store_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=True
    )
    department_scope: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachment_document_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("compliance_documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    content_sha256_hex: Mapped[str] = mapped_column(String(64), nullable=False)
    requires_acknowledgement: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    published_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    published_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnnouncementAcknowledgementModel(Base):
    """Strictly append-only announcement acknowledgement ledger.

    Database triggers forbid UPDATE, DELETE, and TRUNCATE.
    """

    __tablename__ = "announcement_acknowledgements"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "announcement_id",
            "employee_id",
            name="uq_announcement_acks_tenant_ann_emp",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    announcement_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("announcements.id", ondelete="RESTRICT"),
        nullable=False,
    )
    employee_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    acknowledged_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    content_sha256_hex: Mapped[str] = mapped_column(String(64), nullable=False)
    acknowledged_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str] = mapped_column(String(512), nullable=False)
    statement: Mapped[str] = mapped_column(String(500), nullable=False)
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LeaveEntitlementModel(Base):
    __tablename__ = "leave_entitlements"
    __table_args__ = (
        CheckConstraint("year >= 2020 AND year <= 2100", name="ck_leave_entitlements_year"),
        CheckConstraint(
            "base_vacation_days >= 30 AND base_vacation_days <= 90",
            name="ck_leave_entitlements_base_days",
        ),
        CheckConstraint(
            "carryover_days >= -30 AND carryover_days <= 60",
            name="ck_leave_entitlements_carryover_days",
        ),
        CheckConstraint(
            "comp_off_days >= 0 AND comp_off_days <= 90",
            name="ck_leave_entitlements_comp_off_days",
        ),
        UniqueConstraint(
            "tenant_id",
            "employee_id",
            "year",
            name="uq_leave_entitlements_tenant_emp_year",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    employee_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    base_vacation_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("30")
    )
    carryover_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    comp_off_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    updated_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LeaveEntitlementAdjustmentModel(Base):
    """Strictly append-only leave entitlement adjustment audit ledger."""

    __tablename__ = "leave_entitlement_adjustments"
    __table_args__ = (
        CheckConstraint(
            "adjustment_type IN ('base_update', 'carryover', 'comp_off', 'bulk_init')",
            name="ck_leave_entitlement_adj_type_valid",
        ),
        Index(
            "ix_leave_entitlement_adj_tenant_emp_year",
            "tenant_id",
            "employee_id",
            "year",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    employee_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    adjustment_type: Mapped[str] = mapped_column(String(24), nullable=False)
    delta_days: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_total_days: Mapped[int] = mapped_column(Integer, nullable=False)
    new_total_days: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    adjusted_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BreakPolicyModel(Base):
    __tablename__ = "break_policies"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('tenant', 'store', 'employee')",
            name="ck_break_policies_scope_type",
        ),
        CheckConstraint(
            "(scope_type = 'tenant' AND store_id IS NULL AND employee_id IS NULL) OR "
            "(scope_type = 'store' AND store_id IS NOT NULL AND employee_id IS NULL) OR "
            "(scope_type = 'employee' AND store_id IS NULL AND employee_id IS NOT NULL)",
            name="ck_break_policies_scope_target",
        ),
        CheckConstraint(
            "break_mode IN ('fixed_duration', 'fixed_window')",
            name="ck_break_policies_mode",
        ),
        CheckConstraint(
            "duration_minutes IS NULL OR (duration_minutes >= 5 AND duration_minutes <= 240)",
            name="ck_break_policies_duration",
        ),
        CheckConstraint(
            "tracking_mode IN ('manual_punch', 'auto_deduct')",
            name="ck_break_policies_tracking_mode",
        ),
        CheckConstraint(
            "min_shift_minutes_for_auto_deduct >= 60 AND min_shift_minutes_for_auto_deduct <= 720",
            name="ck_break_policies_min_shift_auto_deduct",
        ),
        Index(
            "uq_break_policies_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("scope_type = 'tenant' AND deleted_at IS NULL"),
        ),
        Index(
            "uq_break_policies_store",
            "tenant_id",
            "store_id",
            unique=True,
            postgresql_where=text("scope_type = 'store' AND deleted_at IS NULL"),
        ),
        Index(
            "uq_break_policies_employee",
            "tenant_id",
            "employee_id",
            unique=True,
            postgresql_where=text("scope_type = 'employee' AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    store_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=True
    )
    employee_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"), nullable=True
    )
    break_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    windows_json: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    tracking_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'manual_punch'")
    )
    min_shift_minutes_for_auto_deduct: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("360")
    )
    auto_end_manual_break: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    updated_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
