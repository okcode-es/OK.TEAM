"""Repository abstract interfaces (Dependency Inversion Principle).

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.domain.announcements import Announcement, AnnouncementAcknowledgement, AnnouncementCategory
from app.domain.attendance import AttendanceEvent
from app.domain.break_policies import BreakPolicy, BreakScopeType
from app.domain.documents import ComplianceDocument, DocumentCategory, DocumentReceipt
from app.domain.employees import (
    ContractType,
    Employee,
    EmploymentStatus,
    WorkScheduleType,
)
from app.domain.leaves import (
    LeaveEntitlement,
    LeaveEntitlementAdjustment,
    LeaveRequest,
    LeaveStatus,
)
from app.domain.organization import Membership, Store, Tenant, TenantMembershipSummary
from app.domain.payroll import MonthlyClosing, MonthlyClosingItem, MonthlyClosingStatus
from app.domain.scheduling import Shift
from app.domain.security import MembershipStatus, UserRole


class TenantRepository(Protocol):
    async def get_by_id(self, tenant_id: UUID) -> Tenant | None: ...

    async def exists_active_cif(
        self,
        cif_nif: str,
        *,
        exclude_tenant_id: UUID | None = None,
    ) -> bool: ...

    async def create(self, tenant: Tenant) -> Tenant: ...

    async def update(
        self,
        tenant_id: UUID,
        *,
        legal_name: str,
        trade_name: str,
        cif_nif: str,
        default_timezone: str,
        default_locale: str,
        updated_at: datetime,
    ) -> Tenant | None: ...


class StoreRepository(Protocol):
    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        include_inactive: bool = False,
    ) -> list[Store]: ...

    async def get_by_id(self, tenant_id: UUID, store_id: UUID) -> Store | None: ...

    async def exists_active_code(
        self,
        tenant_id: UUID,
        code: str,
        *,
        exclude_store_id: UUID | None = None,
    ) -> bool: ...

    async def count_existing_in_tenant(
        self,
        tenant_id: UUID,
        store_ids: frozenset[UUID],
    ) -> int: ...

    async def create(self, store: Store) -> Store: ...

    async def update(
        self,
        tenant_id: UUID,
        store_id: UUID,
        *,
        name: str,
        code: str,
        timezone: str,
        address: str,
        city: str,
        postal_code: str,
        region_code: str,
        is_active: bool,
        updated_at: datetime,
    ) -> Store | None: ...

    async def soft_delete(
        self,
        tenant_id: UUID,
        store_id: UUID,
        *,
        deleted_at: datetime,
    ) -> bool: ...


class MembershipRepository(Protocol):
    async def lock_tenant_for_membership_changes(self, tenant_id: UUID) -> None:
        """Serialize membership writes for this tenant until the transaction ends."""
        ...

    async def list_summaries_for_user(self, user_id: UUID) -> list[TenantMembershipSummary]: ...

    async def get_active_for_user_in_tenant(
        self,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Membership | None: ...

    async def get_by_id(self, tenant_id: UUID, membership_id: UUID) -> Membership | None: ...

    async def exists_active_user_in_tenant(self, tenant_id: UUID, user_id: UUID) -> bool: ...

    async def count_active_owners(self, tenant_id: UUID) -> int: ...

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        filter_store_ids: frozenset[UUID] | None = None,
        only_user_id: UUID | None = None,
    ) -> list[Membership]: ...

    async def create(self, membership: Membership) -> Membership: ...

    async def update(
        self,
        tenant_id: UUID,
        membership_id: UUID,
        *,
        display_name: str,
        role: UserRole,
        status: MembershipStatus,
        store_ids: frozenset[UUID],
        department_scope: str | None,
        updated_at: datetime,
    ) -> Membership | None: ...

    async def soft_delete(
        self,
        tenant_id: UUID,
        membership_id: UUID,
        *,
        deleted_at: datetime,
    ) -> bool: ...


class EmployeeRepository(Protocol):
    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        allowed_statuses: frozenset[EmploymentStatus] | None = None,
        only_user_id: UUID | None = None,
        user_ids: frozenset[UUID] | None = None,
        store_id: UUID | None = None,
        status: EmploymentStatus | None = None,
    ) -> list[Employee]: ...

    async def get_by_id(self, tenant_id: UUID, employee_id: UUID) -> Employee | None: ...

    async def exists_active_code(
        self,
        tenant_id: UUID,
        employee_code: str,
        *,
        exclude_employee_id: UUID | None = None,
    ) -> bool: ...

    async def exists_active_nie_blind_index(
        self,
        tenant_id: UUID,
        nie_dni_blind_index: str,
        *,
        exclude_employee_id: UUID | None = None,
    ) -> bool: ...

    async def create(self, employee: Employee) -> Employee: ...

    async def update(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        primary_store_id: UUID,
        user_id: UUID | None,
        employee_code: str,
        full_name: str,
        preferred_name: str,
        department: str,
        email: str,
        phone: str,
        birth_date: date | None,
        contract_type: ContractType,
        work_schedule_type: WorkScheduleType,
        contracted_weekly_hours: Decimal,
        annual_vacation_days: int,
        hire_date: date,
        employment_status: EmploymentStatus,
        nie_dni_enc: str | None,
        nie_dni_masked: str | None,
        nie_dni_blind_index: str | None,
        iban_enc: str | None,
        iban_masked: str | None,
        naf_enc: str | None,
        naf_masked: str | None,
        updated_at: datetime,
        job_title: str = "",
        emergency_contact_name: str = "",
        emergency_contact_phone: str = "",
        default_break_minutes: int | None = None,
    ) -> Employee | None: ...

    async def update_avatar(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        avatar_storage_backend: str | None,
        avatar_storage_key: str | None,
        avatar_mime_type: str | None,
        avatar_updated_at: datetime | None,
        updated_at: datetime,
    ) -> Employee | None: ...

    async def terminate_and_hold(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        termination_date: date,
        retention_until: datetime,
        updated_at: datetime,
    ) -> Employee | None: ...

    async def anonymize(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        anonymized_full_name: str,
        anonymized_at: datetime,
    ) -> Employee | None: ...


class AttendanceRepository(Protocol):
    """Strictly append-only & read-only repository for attendance_events (RDL 8/2019)."""

    async def append(self, event: AttendanceEvent) -> AttendanceEvent: ...

    async def get_by_id(self, tenant_id: UUID, event_id: UUID) -> AttendanceEvent | None: ...

    async def list_by_employee(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        occurred_from_utc: datetime | None = None,
        occurred_to_utc: datetime | None = None,
    ) -> list[AttendanceEvent]: ...

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        store_id: UUID | None = None,
        employee_id: UUID | None = None,
        occurred_from_utc: datetime | None = None,
        occurred_to_utc: datetime | None = None,
    ) -> list[AttendanceEvent]: ...


class ShiftRepository(Protocol):
    async def get_by_id(self, tenant_id: UUID, shift_id: UUID) -> Shift | None: ...

    async def list_by_store_window(
        self,
        tenant_id: UUID,
        store_id: UUID,
        *,
        window_start_utc: datetime,
        window_end_utc: datetime,
    ) -> list[Shift]: ...

    async def list_by_employee_window(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        window_start_utc: datetime,
        window_end_utc: datetime,
    ) -> list[Shift]: ...

    async def create(self, shift: Shift) -> Shift: ...

    async def update(self, shift: Shift) -> Shift | None: ...


class LeaveRepository(Protocol):
    async def get_by_id(self, tenant_id: UUID, leave_id: UUID) -> LeaveRequest | None: ...

    async def list_by_employee(
        self,
        tenant_id: UUID,
        employee_id: UUID,
    ) -> list[LeaveRequest]: ...

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        store_id: UUID | None = None,
        employee_id: UUID | None = None,
        status: LeaveStatus | None = None,
        year: int | None = None,
    ) -> list[LeaveRequest]: ...

    async def create(self, leave: LeaveRequest) -> LeaveRequest: ...

    async def update(self, leave: LeaveRequest) -> LeaveRequest | None: ...


class DocumentRepository(Protocol):
    async def get_by_id(
        self,
        tenant_id: UUID,
        document_id: UUID,
    ) -> ComplianceDocument | None: ...

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        only_employee_id: UUID | None = None,
        include_tenant_wide_for_employee: bool = False,
        employee_store_id: UUID | None = None,
        category: DocumentCategory | None = None,
        employee_id: UUID | None = None,
        store_id: UUID | None = None,
        period_month: str | None = None,
        requires_signature: bool | None = None,
        exclude_categories: frozenset[DocumentCategory] | None = None,
    ) -> list[ComplianceDocument]: ...

    async def create(self, document: ComplianceDocument) -> ComplianceDocument: ...

    async def soft_delete(
        self,
        tenant_id: UUID,
        document_id: UUID,
        *,
        deleted_at: datetime,
    ) -> bool: ...

    async def list_receipts_for_document(
        self,
        tenant_id: UUID,
        document_id: UUID,
    ) -> list[DocumentReceipt]: ...

    async def list_receipts_for_documents(
        self,
        tenant_id: UUID,
        document_ids: Sequence[UUID],
    ) -> list[DocumentReceipt]: ...

    async def get_receipt(
        self,
        tenant_id: UUID,
        document_id: UUID,
        employee_id: UUID,
    ) -> DocumentReceipt | None: ...

    async def append_receipt(self, receipt: DocumentReceipt) -> DocumentReceipt: ...


class PayrollClosingRepository(Protocol):
    async def is_store_month_closed(
        self,
        tenant_id: UUID,
        store_id: UUID,
        period_month: str,
    ) -> bool: ...

    async def list_closed_months_for_store(
        self,
        tenant_id: UUID,
        store_id: UUID,
        period_months: Sequence[str],
    ) -> set[str]: ...

    async def get_by_id(
        self,
        tenant_id: UUID,
        closing_id: UUID,
    ) -> MonthlyClosing | None: ...

    async def get_by_store_month(
        self,
        tenant_id: UUID,
        store_id: UUID,
        period_month: str,
    ) -> MonthlyClosing | None: ...

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        store_id: UUID | None = None,
        period_month: str | None = None,
    ) -> list[MonthlyClosing]: ...

    async def create_closing(self, closing: MonthlyClosing) -> MonthlyClosing: ...

    async def update_closing(
        self,
        closing: MonthlyClosing,
        *,
        expected_status: MonthlyClosingStatus | None = None,
        expected_revision: int | None = None,
    ) -> MonthlyClosing | None: ...

    async def append_items(
        self,
        items: Sequence[MonthlyClosingItem],
    ) -> list[MonthlyClosingItem]: ...

    async def list_items(
        self,
        tenant_id: UUID,
        closing_id: UUID,
        *,
        revision_number: int | None = None,
    ) -> list[MonthlyClosingItem]: ...

    async def list_items_for_closings(
        self,
        tenant_id: UUID,
        closing_ids: Sequence[UUID],
    ) -> list[MonthlyClosingItem]: ...


class AnnouncementRepository(Protocol):
    async def get_by_id(
        self,
        tenant_id: UUID,
        announcement_id: UUID,
    ) -> Announcement | None: ...

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        category: AnnouncementCategory | None = None,
    ) -> list[Announcement]: ...

    async def create(self, announcement: Announcement) -> Announcement: ...

    async def list_acknowledgements_for_announcement(
        self,
        tenant_id: UUID,
        announcement_id: UUID,
    ) -> list[AnnouncementAcknowledgement]: ...

    async def list_acknowledgements_for_announcements(
        self,
        tenant_id: UUID,
        announcement_ids: Sequence[UUID],
    ) -> list[AnnouncementAcknowledgement]: ...

    async def get_acknowledgement(
        self,
        tenant_id: UUID,
        announcement_id: UUID,
        employee_id: UUID,
    ) -> AnnouncementAcknowledgement | None: ...

    async def append_acknowledgement(
        self,
        ack: AnnouncementAcknowledgement,
    ) -> AnnouncementAcknowledgement: ...


class LeaveEntitlementRepository(Protocol):
    async def get_by_employee_year(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        year: int,
    ) -> LeaveEntitlement | None: ...

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        year: int | None = None,
        employee_id: UUID | None = None,
    ) -> list[LeaveEntitlement]: ...

    async def upsert(self, entitlement: LeaveEntitlement) -> LeaveEntitlement: ...

    async def list_adjustments(
        self,
        tenant_id: UUID,
        *,
        year: int | None = None,
        employee_id: UUID | None = None,
    ) -> list[LeaveEntitlementAdjustment]: ...

    async def append_adjustment(
        self,
        adjustment: LeaveEntitlementAdjustment,
    ) -> LeaveEntitlementAdjustment: ...


class BreakPolicyRepository(Protocol):
    async def get_by_id(self, tenant_id: UUID, policy_id: UUID) -> BreakPolicy | None: ...

    async def get_by_scope(
        self,
        tenant_id: UUID,
        *,
        scope_type: BreakScopeType,
        store_id: UUID | None = None,
        employee_id: UUID | None = None,
    ) -> BreakPolicy | None: ...

    async def list_by_tenant(self, tenant_id: UUID) -> list[BreakPolicy]: ...

    async def upsert(self, policy: BreakPolicy) -> BreakPolicy: ...

    async def soft_delete(
        self,
        tenant_id: UUID,
        policy_id: UUID,
        *,
        deleted_at: datetime,
        updated_by_user_id: UUID,
    ) -> BreakPolicy | None: ...
