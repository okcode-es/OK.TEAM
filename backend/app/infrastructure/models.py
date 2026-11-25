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
    func,
    text,
)
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
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
