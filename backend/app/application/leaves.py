"""Application use cases for Annual Vacation & Leave Management (Art. 38 ET)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from app.application.employees import mint_avatar_url
from app.domain.documents import (
    ComplianceDocument,
    DocumentCategory,
    DocumentStoragePort,
    SignedUrlResult,
    build_storage_key,
    compute_document_retention_until,
    sanitize_filename,
    validate_document_upload,
    validate_signed_url_ttl,
)
from app.domain.employees import (
    STATUTORY_MIN_VACATION_DAYS,
    Employee,
    EmploymentStatus,
    FrozenRecordError,
)
from app.domain.holidays import Holiday, list_holidays_for_year, list_upcoming_holidays
from app.domain.leaves import (
    PENDING_LEAVE_STATUSES,
    EntitlementAdjustmentType,
    LeaveEntitlement,
    LeaveEntitlementAdjustment,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
    VacationBalance,
    calculate_natural_days,
    check_leave_overlap,
    check_leave_published_shift_conflict,
    compute_leave_retention_until,
    compute_vacation_balance,
    split_days_by_year,
    validate_entitlement_values,
    validate_leave_transition,
    validate_vacation_balance_for_request,
)
from app.domain.organization import (
    ConflictError,
    EntityNotFoundError,
    ValidationDomainError,
    validate_non_empty_text,
)
from app.domain.payroll import spanned_period_months
from app.domain.repositories import (
    DocumentRepository,
    EmployeeRepository,
    LeaveEntitlementRepository,
    LeaveRepository,
    PayrollClosingRepository,
    ShiftRepository,
    StoreRepository,
)
from app.domain.security import AuthenticatedPrincipal, ForbiddenScopeError, UserRole

_LEAVE_REVIEW_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.OWNER, UserRole.HR, UserRole.STORE_MANAGER}
)
_BLOCKED_EMPLOYEE_STATUSES: frozenset[EmploymentStatus] = frozenset(
    {
        EmploymentStatus.TERMINATED,
        EmploymentStatus.LEGAL_HOLD,
        EmploymentStatus.ANONYMIZED,
    }
)


@dataclass(frozen=True, slots=True)
class CreateLeaveCommand:
    employee_id: UUID | None
    leave_type: LeaveType
    start_date: date
    end_date: date
    reason: str = ""
    medical_document_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ReviewLeaveCommand:
    leave_id: UUID
    approve: bool
    review_note: str = ""


async def _resolve_self_employee(
    employee_repo: EmployeeRepository,
    tenant_id: UUID,
    user_id: UUID,
) -> Employee:
    matches = await employee_repo.list_by_tenant(tenant_id, only_user_id=user_id)
    if not matches:
        raise EntityNotFoundError("No employee record linked to current user")
    return matches[0]


class ListLeavesUseCase:
    def __init__(
        self,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
    ) -> None:
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        store_id: UUID | None = None,
        employee_id: UUID | None = None,
        status: LeaveStatus | None = None,
        year: int | None = None,
    ) -> list[LeaveRequest]:
        if principal.role == UserRole.EMPLOYEE:
            self_emp = await _resolve_self_employee(
                self._employee_repo, principal.tenant_id, principal.user_id
            )
            if employee_id is not None and employee_id != self_emp.id:
                raise ForbiddenScopeError("Employees may only view their own leave requests")
            return await self._leave_repo.list_by_tenant(
                principal.tenant_id,
                employee_id=self_emp.id,
                status=status,
                year=year,
            )

        if principal.role == UserRole.STORE_MANAGER:
            if store_id is not None:
                principal.require_store_access(principal.tenant_id, store_id)
            leaves = await self._leave_repo.list_by_tenant(
                principal.tenant_id,
                allowed_store_ids=principal.store_ids,
                store_id=store_id,
                employee_id=employee_id,
                status=status,
                year=year,
            )
            emps = await self._employee_repo.list_by_tenant(principal.tenant_id)
            allowed_emp_ids = {e.id for e in emps if principal.can_manage_employee(e)}
            return [lv for lv in leaves if lv.employee_id in allowed_emp_ids]

        return await self._leave_repo.list_by_tenant(
            principal.tenant_id,
            store_id=store_id,
            employee_id=employee_id,
            status=status,
            year=year,
        )


class GetVacationBalanceUseCase:
    def __init__(
        self,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
        entitlement_repo: LeaveEntitlementRepository | None = None,
    ) -> None:
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo
        self._entitlement_repo = entitlement_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        employee_id: UUID | None,
        year: int,
    ) -> VacationBalance:
        if principal.role == UserRole.EMPLOYEE:
            self_emp = await _resolve_self_employee(
                self._employee_repo, principal.tenant_id, principal.user_id
            )
            if employee_id is not None and employee_id != self_emp.id:
                raise ForbiddenScopeError("Employees may only view their own vacation balance")
            emp = self_emp
        else:
            if employee_id is None:
                raise ValidationDomainError("employee_id query parameter is required")
            loaded = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
            if loaded is None:
                raise EntityNotFoundError(f"Employee {employee_id} not found")
            emp = loaded
            if principal.role == UserRole.STORE_MANAGER:
                principal.require_employee_in_scope(emp)

        requests = await self._leave_repo.list_by_employee(principal.tenant_id, emp.id)
        entitlement = (
            await self._entitlement_repo.get_by_employee_year(principal.tenant_id, emp.id, year)
            if self._entitlement_repo is not None
            else None
        )
        return compute_vacation_balance(emp, requests, year, entitlement=entitlement)


async def _load_entitlements_by_year(
    entitlement_repo: LeaveEntitlementRepository | None,
    tenant_id: UUID,
    employee_id: UUID,
    start_date: date,
    end_date: date,
) -> dict[int, LeaveEntitlement]:
    if entitlement_repo is None:
        return {}
    years = split_days_by_year(start_date, end_date).keys()
    result: dict[int, LeaveEntitlement] = {}
    for yr in years:
        ent = await entitlement_repo.get_by_employee_year(tenant_id, employee_id, yr)
        if ent is not None:
            result[yr] = ent
    return result


class CreateLeaveRequestUseCase:
    def __init__(
        self,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
        document_repo: DocumentRepository,
        payroll_repo: PayrollClosingRepository | None = None,
        entitlement_repo: LeaveEntitlementRepository | None = None,
    ) -> None:
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo
        self._document_repo = document_repo
        self._payroll_repo = payroll_repo
        self._entitlement_repo = entitlement_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        cmd: CreateLeaveCommand,
        *,
        now_utc: datetime,
    ) -> LeaveRequest:
        if principal.role == UserRole.ACCOUNTANT:
            raise ForbiddenScopeError("Accountants have read-only access and cannot create leaves")

        if principal.role == UserRole.EMPLOYEE:
            self_emp = await _resolve_self_employee(
                self._employee_repo, principal.tenant_id, principal.user_id
            )
            if cmd.employee_id is not None and cmd.employee_id != self_emp.id:
                raise ForbiddenScopeError("Employees may only submit leave requests for themselves")
            emp = self_emp
        else:
            if cmd.employee_id is None:
                raise ValidationDomainError("employee_id is required")
            loaded = await self._employee_repo.get_by_id(principal.tenant_id, cmd.employee_id)
            if loaded is None:
                raise EntityNotFoundError(f"Employee {cmd.employee_id} not found")
            emp = loaded
            if principal.role == UserRole.STORE_MANAGER:
                principal.require_employee_in_scope(emp)

        if emp.deleted_at is not None or emp.employment_status in _BLOCKED_EMPLOYEE_STATUSES:
            raise ValidationDomainError(
                f"Cannot request leave for employee in '{emp.employment_status.value}' status"
            )

        if cmd.medical_document_id is not None:
            doc = await self._document_repo.get_by_id(
                principal.tenant_id, cmd.medical_document_id
            )
            if (
                doc is None
                or doc.employee_id != emp.id
                or doc.category != DocumentCategory.MEDICAL_LEAVE
            ):
                raise EntityNotFoundError(
                    f"Medical document {cmd.medical_document_id} not found"
                )
            if (
                principal.role == UserRole.STORE_MANAGER
                and doc.store_id is not None
                and doc.store_id not in principal.store_ids
            ):
                raise ForbiddenScopeError("Store manager lacks access to medical document")

        natural_days = calculate_natural_days(cmd.start_date, cmd.end_date)
        if self._payroll_repo is not None:
            closed_months = await self._payroll_repo.list_closed_months_for_store(
                principal.tenant_id,
                emp.primary_store_id,
                spanned_period_months(cmd.start_date, cmd.end_date),
            )
            if closed_months:
                raise ConflictError(
                    f"Cannot create leave request: payroll month(s) {sorted(closed_months)} "
                    "are closed for this store"
                )
        existing = await self._leave_repo.list_by_employee(principal.tenant_id, emp.id)
        check_leave_overlap(
            start_date=cmd.start_date,
            end_date=cmd.end_date,
            existing_requests=existing,
        )
        if cmd.leave_type == LeaveType.VACATION:
            entitlements_by_year = await _load_entitlements_by_year(
                self._entitlement_repo,
                principal.tenant_id,
                emp.id,
                cmd.start_date,
                cmd.end_date,
            )
            validate_vacation_balance_for_request(
                employee=emp,
                start_date=cmd.start_date,
                end_date=cmd.end_date,
                existing_requests=existing,
                entitlements_by_year=entitlements_by_year,
            )

        reason = (
            validate_non_empty_text(cmd.reason, field="reason", max_length=500)
            if cmd.reason.strip()
            else ""
        )
        now = now_utc.astimezone(UTC)
        if principal.role == UserRole.EMPLOYEE:
            initial_status = LeaveStatus.PENDING_MANAGER
            mgr_by: UUID | None = None
            mgr_at: datetime | None = None
        elif principal.role == UserRole.STORE_MANAGER:
            initial_status = LeaveStatus.PENDING_HR
            mgr_by = principal.user_id
            mgr_at = now
        else:
            # HR or Owner -> starts at PENDING_HR
            initial_status = LeaveStatus.PENDING_HR
            mgr_by = None
            mgr_at = None

        leave = LeaveRequest(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            store_id=emp.primary_store_id,
            employee_id=emp.id,
            leave_type=cmd.leave_type,
            start_date=cmd.start_date,
            end_date=cmd.end_date,
            natural_days=natural_days,
            status=initial_status,
            reason=reason,
            requested_by_user_id=principal.user_id,
            reviewed_by_user_id=mgr_by,
            reviewed_at_utc=mgr_at,
            review_note="",
            manager_reviewed_by_user_id=mgr_by,
            manager_reviewed_at_utc=mgr_at,
            manager_review_note="",
            hr_reviewed_by_user_id=None,
            hr_reviewed_at_utc=None,
            hr_review_note="",
            medical_document_id=cmd.medical_document_id,
            retention_until=compute_leave_retention_until(cmd.end_date),
            created_at=now,
            updated_at=now,
        )
        return await self._leave_repo.create(leave)


class ReviewLeaveRequestUseCase:
    def __init__(
        self,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
        shift_repo: ShiftRepository,
        store_repo: StoreRepository,
        payroll_repo: PayrollClosingRepository | None = None,
        entitlement_repo: LeaveEntitlementRepository | None = None,
    ) -> None:
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo
        self._shift_repo = shift_repo
        self._store_repo = store_repo
        self._payroll_repo = payroll_repo
        self._entitlement_repo = entitlement_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        cmd: ReviewLeaveCommand,
        *,
        now_utc: datetime,
    ) -> LeaveRequest:
        if principal.role not in _LEAVE_REVIEW_ROLES:
            raise ForbiddenScopeError(
                "Only owners, HR, or store managers may approve or reject leave requests"
            )

        leave = await self._leave_repo.get_by_id(principal.tenant_id, cmd.leave_id)
        if leave is None:
            raise EntityNotFoundError(f"Leave request {cmd.leave_id} not found")

        if principal.role == UserRole.STORE_MANAGER:
            principal.require_store_access(principal.tenant_id, leave.store_id)

        emp = await self._employee_repo.get_by_id(principal.tenant_id, leave.employee_id)
        if emp is None:
            raise EntityNotFoundError(f"Employee {leave.employee_id} not found")
        if principal.role == UserRole.STORE_MANAGER:
            principal.require_employee_in_scope(emp)

        if leave.status == LeaveStatus.PENDING_MANAGER:
            if principal.role == UserRole.HR:
                raise ValidationDomainError(
                    "Leave request requires Store Manager approval before HR final review"
                )
            target_status = LeaveStatus.PENDING_HR if cmd.approve else LeaveStatus.REJECTED
        elif leave.status == LeaveStatus.PENDING_HR:
            if principal.role == UserRole.STORE_MANAGER:
                raise ForbiddenScopeError(
                    "Only HR or Owner can perform final approval on manager-approved leave requests"
                )
            target_status = LeaveStatus.APPROVED if cmd.approve else LeaveStatus.REJECTED
        else:
            target_status = LeaveStatus.APPROVED if cmd.approve else LeaveStatus.REJECTED

        if self._payroll_repo is not None:
            closed_months = await self._payroll_repo.list_closed_months_for_store(
                principal.tenant_id,
                leave.store_id,
                spanned_period_months(leave.start_date, leave.end_date),
            )
            if closed_months:
                raise ConflictError(
                    f"Cannot review leave request: payroll month(s) {sorted(closed_months)} "
                    "are closed for this store"
                )

        validate_leave_transition(leave.status, target_status)

        if target_status == LeaveStatus.APPROVED:
            store = await self._store_repo.get_by_id(principal.tenant_id, leave.store_id)
            if store is None:
                raise EntityNotFoundError(f"Store {leave.store_id} not found")
            win_start = datetime(
                leave.start_date.year, leave.start_date.month, leave.start_date.day, tzinfo=UTC
            ) - timedelta(days=1)
            win_end = datetime(
                leave.end_date.year, leave.end_date.month, leave.end_date.day, tzinfo=UTC
            ) + timedelta(days=2)
            shifts = await self._shift_repo.list_by_employee_window(
                principal.tenant_id,
                leave.employee_id,
                window_start_utc=win_start,
                window_end_utc=win_end,
            )
            check_leave_published_shift_conflict(
                start_date=leave.start_date,
                end_date=leave.end_date,
                store_timezone=store.timezone,
                shifts=shifts,
            )
            if leave.leave_type == LeaveType.VACATION:
                existing = await self._leave_repo.list_by_employee(
                    principal.tenant_id, leave.employee_id
                )
                entitlements_by_year = await _load_entitlements_by_year(
                    self._entitlement_repo,
                    principal.tenant_id,
                    leave.employee_id,
                    leave.start_date,
                    leave.end_date,
                )
                validate_vacation_balance_for_request(
                    employee=emp,
                    start_date=leave.start_date,
                    end_date=leave.end_date,
                    existing_requests=[r for r in existing if r.id != leave.id],
                    entitlements_by_year=entitlements_by_year,
                )

        review_note = (
            validate_non_empty_text(cmd.review_note, field="review_note", max_length=300)
            if cmd.review_note.strip()
            else ""
        )
        now = now_utc.astimezone(UTC)
        mgr_by: UUID | None
        mgr_at: datetime | None
        hr_by: UUID | None
        hr_at: datetime | None
        if leave.status == LeaveStatus.PENDING_MANAGER:
            mgr_by = principal.user_id
            mgr_at = now
            mgr_note = review_note
            hr_by = leave.hr_reviewed_by_user_id
            hr_at = leave.hr_reviewed_at_utc
            hr_note = leave.hr_review_note
        else:
            mgr_by = leave.manager_reviewed_by_user_id
            mgr_at = leave.manager_reviewed_at_utc
            mgr_note = leave.manager_review_note
            hr_by = principal.user_id
            hr_at = now
            hr_note = review_note

        updated = LeaveRequest(
            id=leave.id,
            tenant_id=leave.tenant_id,
            store_id=leave.store_id,
            employee_id=leave.employee_id,
            leave_type=leave.leave_type,
            start_date=leave.start_date,
            end_date=leave.end_date,
            natural_days=leave.natural_days,
            status=target_status,
            reason=leave.reason,
            requested_by_user_id=leave.requested_by_user_id,
            reviewed_by_user_id=principal.user_id,
            reviewed_at_utc=now,
            review_note=review_note,
            manager_reviewed_by_user_id=mgr_by,
            manager_reviewed_at_utc=mgr_at,
            manager_review_note=mgr_note,
            hr_reviewed_by_user_id=hr_by,
            hr_reviewed_at_utc=hr_at,
            hr_review_note=hr_note,
            medical_document_id=leave.medical_document_id,
            assigned_directly=leave.assigned_directly,
            assigned_by_user_id=leave.assigned_by_user_id,
            retention_until=leave.retention_until,
            created_at=leave.created_at,
            updated_at=now,
        )
        saved = await self._leave_repo.update(updated)
        if saved is None:
            raise EntityNotFoundError(f"Leave request {cmd.leave_id} not found")
        return saved


class CancelLeaveRequestUseCase:
    def __init__(
        self,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
        payroll_repo: PayrollClosingRepository | None = None,
    ) -> None:
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo
        self._payroll_repo = payroll_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        leave_id: UUID,
        now_utc: datetime,
    ) -> LeaveRequest:
        if principal.role == UserRole.ACCOUNTANT:
            raise ForbiddenScopeError("Accountants cannot cancel leave requests")

        leave = await self._leave_repo.get_by_id(principal.tenant_id, leave_id)
        if leave is None:
            raise EntityNotFoundError(f"Leave request {leave_id} not found")

        if principal.role == UserRole.EMPLOYEE:
            self_emp = await _resolve_self_employee(
                self._employee_repo, principal.tenant_id, principal.user_id
            )
            if leave.employee_id != self_emp.id:
                raise ForbiddenScopeError("Employees may only cancel their own leave requests")
        if principal.role == UserRole.STORE_MANAGER:
            principal.require_store_access(principal.tenant_id, leave.store_id)

        emp = await self._employee_repo.get_by_id(principal.tenant_id, leave.employee_id)
        if emp is not None:
            if principal.role == UserRole.STORE_MANAGER:
                principal.require_employee_in_scope(emp)
            elif emp.employment_status in (
                EmploymentStatus.LEGAL_HOLD,
                EmploymentStatus.ANONYMIZED,
            ):
                raise FrozenRecordError(
                    f"Cannot cancel leave for frozen/anonymized employee {emp.employee_code}"
                )

        if self._payroll_repo is not None:
            closed_months = await self._payroll_repo.list_closed_months_for_store(
                principal.tenant_id,
                leave.store_id,
                spanned_period_months(leave.start_date, leave.end_date),
            )
            if closed_months:
                raise ConflictError(
                    f"Cannot cancel leave request: payroll month(s) {sorted(closed_months)} "
                    "are closed for this store"
                )

        validate_leave_transition(leave.status, LeaveStatus.CANCELLED)

        now = now_utc.astimezone(UTC)
        cancelled = LeaveRequest(
            id=leave.id,
            tenant_id=leave.tenant_id,
            store_id=leave.store_id,
            employee_id=leave.employee_id,
            leave_type=leave.leave_type,
            start_date=leave.start_date,
            end_date=leave.end_date,
            natural_days=leave.natural_days,
            status=LeaveStatus.CANCELLED,
            reason=leave.reason,
            requested_by_user_id=leave.requested_by_user_id,
            reviewed_by_user_id=leave.reviewed_by_user_id,
            reviewed_at_utc=leave.reviewed_at_utc,
            review_note=leave.review_note,
            manager_reviewed_by_user_id=leave.manager_reviewed_by_user_id,
            manager_reviewed_at_utc=leave.manager_reviewed_at_utc,
            manager_review_note=leave.manager_review_note,
            hr_reviewed_by_user_id=leave.hr_reviewed_by_user_id,
            hr_reviewed_at_utc=leave.hr_reviewed_at_utc,
            hr_review_note=leave.hr_review_note,
            medical_document_id=leave.medical_document_id,
            assigned_directly=leave.assigned_directly,
            assigned_by_user_id=leave.assigned_by_user_id,
            retention_until=leave.retention_until,
            created_at=leave.created_at,
            updated_at=now,
        )
        saved = await self._leave_repo.update(cancelled)
        if saved is None:
            raise EntityNotFoundError(f"Leave request {leave_id} not found")
        return saved


@dataclass(frozen=True, slots=True)
class LeaveCalendarResult:
    year: int
    employee_id: UUID
    region_code: str
    vacation_balance: VacationBalance
    type_counts: dict[LeaveType, int]
    holidays: list[Holiday]
    leaves: list[LeaveRequest]


class UploadLeaveJustificanteUseCase:
    def __init__(
        self,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
        doc_repo: DocumentRepository,
        storage: DocumentStoragePort,
        payroll_repo: PayrollClosingRepository | None = None,
    ) -> None:
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo
        self._doc_repo = doc_repo
        self._storage = storage
        self._payroll_repo = payroll_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        leave_id: UUID,
        filename: str,
        mime_type: str,
        content: bytes,
        now_utc: datetime,
    ) -> LeaveRequest:
        if principal.role == UserRole.ACCOUNTANT:
            raise ForbiddenScopeError("Accountants cannot upload medical justificantes")

        leave = await self._leave_repo.get_by_id(principal.tenant_id, leave_id)
        if leave is None:
            raise EntityNotFoundError(f"Leave request {leave_id} not found")

        emp = await self._employee_repo.get_by_id(principal.tenant_id, leave.employee_id)
        if emp is None:
            raise EntityNotFoundError(f"Employee {leave.employee_id} not found")

        if principal.role == UserRole.EMPLOYEE:
            self_emp = await _resolve_self_employee(
                self._employee_repo, principal.tenant_id, principal.user_id
            )
            if leave.employee_id != self_emp.id:
                raise ForbiddenScopeError(
                    "Employees may only upload justificantes for their own leaves"
                )
        elif principal.role == UserRole.STORE_MANAGER:
            principal.require_employee_in_scope(emp)

        if leave.leave_type not in (LeaveType.SICK_LEAVE, LeaveType.MEDICAL_APPOINTMENT):
            raise ValidationDomainError(
                "Justificante upload is only supported for sick_leave or medical_appointment"
            )
        if leave.status in (LeaveStatus.CANCELLED, LeaveStatus.REJECTED):
            raise ConflictError(
                f"Cannot upload justificante for leave in '{leave.status.value}' status"
            )

        if self._payroll_repo is not None:
            closed_months = await self._payroll_repo.list_closed_months_for_store(
                principal.tenant_id,
                leave.store_id,
                spanned_period_months(leave.start_date, leave.end_date),
            )
            if closed_months:
                raise ConflictError(
                    f"Cannot upload justificante: payroll month(s) {sorted(closed_months)} "
                    "are closed for this store"
                )

        safe_filename = sanitize_filename(filename)
        sha256_hex = validate_document_upload(mime_type, content)
        doc_id = uuid4()
        storage_key = build_storage_key(principal.tenant_id, doc_id)
        now = now_utc.astimezone(UTC)

        await self._storage.put_object(
            storage_key=storage_key,
            content=content,
            mime_type=mime_type,
        )
        doc = ComplianceDocument(
            id=doc_id,
            tenant_id=principal.tenant_id,
            store_id=leave.store_id,
            employee_id=leave.employee_id,
            category=DocumentCategory.MEDICAL_LEAVE,
            title=f"Justificante {leave.leave_type.value} ({leave.start_date.isoformat()})",
            period_month=leave.start_date.strftime("%Y-%m"),
            storage_backend=self._storage.backend_name,
            storage_key=storage_key,
            original_filename=safe_filename,
            mime_type=mime_type,
            size_bytes=len(content),
            sha256_hex=sha256_hex,
            requires_signature=False,
            uploaded_by_user_id=principal.user_id,
            retention_until=compute_document_retention_until(now),
            created_at=now,
            updated_at=now,
        )
        try:
            saved_doc = await self._doc_repo.create(doc)
        except Exception:
            await self._storage.delete_object(storage_key=storage_key)
            raise

        updated_leave = LeaveRequest(
            id=leave.id,
            tenant_id=leave.tenant_id,
            store_id=leave.store_id,
            employee_id=leave.employee_id,
            leave_type=leave.leave_type,
            start_date=leave.start_date,
            end_date=leave.end_date,
            natural_days=leave.natural_days,
            status=leave.status,
            reason=leave.reason,
            requested_by_user_id=leave.requested_by_user_id,
            reviewed_by_user_id=leave.reviewed_by_user_id,
            reviewed_at_utc=leave.reviewed_at_utc,
            review_note=leave.review_note,
            manager_reviewed_by_user_id=leave.manager_reviewed_by_user_id,
            manager_reviewed_at_utc=leave.manager_reviewed_at_utc,
            manager_review_note=leave.manager_review_note,
            hr_reviewed_by_user_id=leave.hr_reviewed_by_user_id,
            hr_reviewed_at_utc=leave.hr_reviewed_at_utc,
            hr_review_note=leave.hr_review_note,
            medical_document_id=saved_doc.id,
            assigned_directly=leave.assigned_directly,
            assigned_by_user_id=leave.assigned_by_user_id,
            retention_until=leave.retention_until,
            created_at=leave.created_at,
            updated_at=now,
        )
        saved_leave = await self._leave_repo.update(updated_leave)
        if saved_leave is None:
            raise EntityNotFoundError(f"Leave request {leave_id} not found")
        return saved_leave


class CreateJustificanteSignedUrlUseCase:
    def __init__(
        self,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
        doc_repo: DocumentRepository,
        storage: DocumentStoragePort,
    ) -> None:
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo
        self._doc_repo = doc_repo
        self._storage = storage

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        leave_id: UUID,
        ttl_seconds: int = 60,
    ) -> tuple[SignedUrlResult, str]:
        # SECURITY RED LINE: Accountant is strictly blocked from medical justificantes
        if principal.role == UserRole.ACCOUNTANT:
            raise ForbiddenScopeError(
                "Accountants are not permitted to access medical justificantes"
            )

        ttl = validate_signed_url_ttl(ttl_seconds)
        leave = await self._leave_repo.get_by_id(principal.tenant_id, leave_id)
        if leave is None:
            raise EntityNotFoundError(f"Leave request {leave_id} not found")

        emp = await self._employee_repo.get_by_id(principal.tenant_id, leave.employee_id)
        if emp is None:
            raise EntityNotFoundError(f"Employee {leave.employee_id} not found")

        if principal.role == UserRole.EMPLOYEE:
            self_emp = await _resolve_self_employee(
                self._employee_repo, principal.tenant_id, principal.user_id
            )
            if leave.employee_id != self_emp.id:
                raise ForbiddenScopeError(
                    "Employees may only access justificantes for their own leaves"
                )
        elif principal.role == UserRole.STORE_MANAGER:
            principal.require_employee_in_scope(emp)

        if leave.medical_document_id is None:
            raise EntityNotFoundError("No justificante attached to this leave request")

        doc = await self._doc_repo.get_by_id(principal.tenant_id, leave.medical_document_id)
        if doc is None:
            raise EntityNotFoundError("Justificante document not found")

        signed = await self._storage.create_signed_url(
            storage_key=doc.storage_key,
            ttl_seconds=ttl,
            download_filename=doc.original_filename,
            mime_type=doc.mime_type,
        )
        return signed, doc.sha256_hex


class GetLeaveCalendarUseCase:
    def __init__(
        self,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        entitlement_repo: LeaveEntitlementRepository | None = None,
    ) -> None:
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._entitlement_repo = entitlement_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        year: int,
        employee_id: UUID | None = None,
    ) -> LeaveCalendarResult:
        if principal.role == UserRole.EMPLOYEE:
            self_emp = await _resolve_self_employee(
                self._employee_repo, principal.tenant_id, principal.user_id
            )
            if employee_id is not None and employee_id != self_emp.id:
                raise ForbiddenScopeError("Employees may only view their own leave calendar")
            emp = self_emp
        else:
            if employee_id is None:
                self_matches = await self._employee_repo.list_by_tenant(
                    principal.tenant_id, only_user_id=principal.user_id
                )
                if not self_matches:
                    raise ValidationDomainError("employee_id query parameter is required")
                emp = self_matches[0]
            else:
                loaded = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
                if loaded is None:
                    raise EntityNotFoundError(f"Employee {employee_id} not found")
                emp = loaded
                if principal.role == UserRole.STORE_MANAGER:
                    principal.require_employee_in_scope(emp)

        store = await self._store_repo.get_by_id(principal.tenant_id, emp.primary_store_id)
        region_code = store.region_code if store is not None else ""
        requests = await self._leave_repo.list_by_employee(principal.tenant_id, emp.id)

        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)
        leaves_in_year = [
            lv
            for lv in requests
            if lv.deleted_at is None and lv.start_date <= year_end and lv.end_date >= year_start
        ]
        type_counts: dict[LeaveType, int] = {lt: 0 for lt in LeaveType}
        for lv in leaves_in_year:
            if lv.status == LeaveStatus.APPROVED or lv.status in PENDING_LEAVE_STATUSES:
                overlap_start = max(lv.start_date, year_start)
                overlap_end = min(lv.end_date, year_end)
                type_counts[lv.leave_type] += (overlap_end - overlap_start).days + 1

        entitlement = (
            await self._entitlement_repo.get_by_employee_year(principal.tenant_id, emp.id, year)
            if self._entitlement_repo is not None
            else None
        )
        return LeaveCalendarResult(
            year=year,
            employee_id=emp.id,
            region_code=region_code,
            vacation_balance=compute_vacation_balance(emp, requests, year, entitlement=entitlement),
            type_counts=type_counts,
            holidays=list_holidays_for_year(year, region_code),
            leaves=leaves_in_year,
        )


_TEAM_CALENDAR_MAX_SPAN_DAYS = 93


@dataclass(frozen=True, slots=True)
class TeamCalendarEntry:
    leave_id: UUID
    display_type: str
    status: LeaveStatus
    start_date: date
    end_date: date
    natural_days: int


@dataclass(frozen=True, slots=True)
class TeamCalendarMember:
    employee_id: UUID
    full_name: str
    preferred_name: str
    job_title: str
    department: str
    store_id: UUID
    store_name: str
    avatar_url: str | None
    entries: tuple[TeamCalendarEntry, ...]


@dataclass(frozen=True, slots=True)
class TeamCalendarResult:
    from_date: date
    to_date: date
    holidays: tuple[Holiday, ...]
    members: tuple[TeamCalendarMember, ...]


class GetTeamCalendarUseCase:
    """GDPR Art. 9 safe team leave calendar (Gantt) across scoped employees."""

    def __init__(
        self,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        storage: DocumentStoragePort,
    ) -> None:
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._storage = storage

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        from_date: date,
        to_date: date,
        store_id: UUID | None = None,
        department: str | None = None,
    ) -> TeamCalendarResult:
        if to_date < from_date:
            raise ValidationDomainError("to_date must be greater than or equal to from_date")
        span_days = (to_date - from_date).days
        if span_days > _TEAM_CALENDAR_MAX_SPAN_DAYS:
            raise ValidationDomainError(
                f"Team calendar date span cannot exceed {_TEAM_CALENDAR_MAX_SPAN_DAYS} days"
            )

        self_emp: Employee | None = None
        if principal.role == UserRole.EMPLOYEE:
            self_emp = await _resolve_self_employee(
                self._employee_repo, principal.tenant_id, principal.user_id
            )
            if self_emp.deleted_at is not None or self_emp.employment_status not in (
                EmploymentStatus.ACTIVE,
                EmploymentStatus.ON_LEAVE,
            ):
                raise ForbiddenScopeError(
                    "Inactive or terminated employees cannot access the team calendar"
                )
            if store_id is not None and store_id != self_emp.primary_store_id:
                raise ForbiddenScopeError(
                    "Employees may only view the team calendar for their own store"
                )
            self_dept_norm = self_emp.department.strip().casefold()
            if (
                department is not None
                and department.strip()
                and department.strip().casefold() != self_dept_norm
            ):
                raise ForbiddenScopeError(
                    "Requested department is outside your authorized department scope"
                )
        elif principal.role == UserRole.STORE_MANAGER:
            if store_id is not None:
                principal.require_store_access(principal.tenant_id, store_id)
            if (
                principal.department_scope
                and principal.department_scope.strip()
                and department
                and department.strip()
                and department.strip().casefold() != principal.department_scope.strip().casefold()
            ):
                raise ForbiddenScopeError(
                    "Requested department is outside your authorized department_scope"
                )
        else:
            # owner, hr, accountant: check if caller has an employee record (self unredaction)
            self_matches = await self._employee_repo.list_by_tenant(
                principal.tenant_id, only_user_id=principal.user_id
            )
            if self_matches:
                self_emp = self_matches[0]

        stores = await self._store_repo.list_by_tenant(principal.tenant_id, include_inactive=True)
        store_map = {s.id: s for s in stores}

        effective_store_id = (
            self_emp.primary_store_id
            if principal.role == UserRole.EMPLOYEE and self_emp is not None
            else store_id
        )
        all_emps = await self._employee_repo.list_by_tenant(
            principal.tenant_id,
            store_id=effective_store_id,
        )

        dept_filter = department.strip().casefold() if department and department.strip() else None
        self_dept = (
            self_emp.department.strip().casefold()
            if principal.role == UserRole.EMPLOYEE and self_emp is not None
            else None
        )

        scoped_emps: list[Employee] = []
        for emp in all_emps:
            if emp.deleted_at is not None:
                continue
            if emp.employment_status not in (EmploymentStatus.ACTIVE, EmploymentStatus.ON_LEAVE):
                continue
            if principal.role == UserRole.STORE_MANAGER and not principal.can_manage_employee(emp):
                continue
            if self_dept is not None and emp.department.strip().casefold() != self_dept:
                continue
            if dept_filter is not None and emp.department.strip().casefold() != dept_filter:
                continue
            scoped_emps.append(emp)

        scoped_emps.sort(key=lambda e: (e.full_name.casefold(), str(e.id)))

        leaves = await self._leave_repo.list_by_tenant(
            principal.tenant_id,
            allowed_store_ids=(
                principal.store_ids if principal.role == UserRole.STORE_MANAGER else None
            ),
            store_id=effective_store_id,
        )

        redact_others_health = principal.role in (UserRole.EMPLOYEE, UserRole.ACCOUNTANT)
        self_emp_id = self_emp.id if self_emp is not None else None
        scoped_emp_ids = {e.id for e in scoped_emps}

        entries_by_emp: dict[UUID, list[TeamCalendarEntry]] = defaultdict(list)
        for lv in leaves:
            if lv.deleted_at is not None:
                continue
            if lv.employee_id not in scoped_emp_ids:
                continue
            if lv.status != LeaveStatus.APPROVED and lv.status not in PENDING_LEAVE_STATUSES:
                continue
            if lv.start_date > to_date or lv.end_date < from_date:
                continue

            if (
                redact_others_health
                and lv.employee_id != self_emp_id
                and lv.leave_type != LeaveType.VACATION
            ):
                display_type = "absence_redacted"
            else:
                display_type = lv.leave_type.value

            entries_by_emp[lv.employee_id].append(
                TeamCalendarEntry(
                    leave_id=lv.id,
                    display_type=display_type,
                    status=lv.status,
                    start_date=lv.start_date,
                    end_date=lv.end_date,
                    natural_days=lv.natural_days,
                )
            )

        members: list[TeamCalendarMember] = []
        region_codes: set[str] = set()
        if (
            effective_store_id is not None
            and effective_store_id in store_map
            and store_map[effective_store_id].region_code
        ):
            region_codes.add(store_map[effective_store_id].region_code)

        for emp in scoped_emps:
            store = store_map.get(emp.primary_store_id)
            if store is not None and store.region_code:
                region_codes.add(store.region_code)
            emp_entries = sorted(
                entries_by_emp.get(emp.id, []),
                key=lambda x: (x.start_date, str(x.leave_id)),
            )
            members.append(
                TeamCalendarMember(
                    employee_id=emp.id,
                    full_name=emp.full_name,
                    preferred_name=emp.preferred_name,
                    job_title=emp.job_title,
                    department=emp.department,
                    store_id=emp.primary_store_id,
                    store_name=store.name if store is not None else "",
                    avatar_url=await mint_avatar_url(self._storage, emp),
                    entries=tuple(emp_entries),
                )
            )

        holidays = list_upcoming_holidays(from_date, span_days, sorted(region_codes))
        return TeamCalendarResult(
            from_date=from_date,
            to_date=to_date,
            holidays=tuple(holidays),
            members=tuple(members),
        )


@dataclass(frozen=True, slots=True)
class EmployeeEntitlementView:
    employee_id: UUID
    employee_code: str
    full_name: str
    preferred_name: str
    store_id: UUID
    department: str
    year: int
    base_vacation_days: int
    carryover_days: int
    comp_off_days: int
    entitled_days: int
    approved_days: int
    pending_days: int
    remaining_days: int
    adjustments: tuple[LeaveEntitlementAdjustment, ...]


@dataclass(frozen=True, slots=True)
class UpsertLeaveEntitlementCommand:
    year: int
    base_vacation_days: int
    carryover_days: int
    comp_off_days: int
    reason: str


@dataclass(frozen=True, slots=True)
class BulkUpsertLeaveEntitlementsCommand:
    year: int
    base_vacation_days: int
    carryover_days: int
    comp_off_days: int
    reason: str
    employee_ids: tuple[UUID, ...] | None = None
    store_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class DirectAssignLeaveCommand:
    employee_id: UUID
    leave_type: LeaveType
    start_date: date
    end_date: date
    reason: str = ""


class ListLeaveEntitlementsUseCase:
    def __init__(
        self,
        entitlement_repo: LeaveEntitlementRepository,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
    ) -> None:
        self._entitlement_repo = entitlement_repo
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        year: int,
        employee_id: UUID | None = None,
        store_id: UUID | None = None,
    ) -> list[EmployeeEntitlementView]:
        if principal.role == UserRole.EMPLOYEE:
            self_emp = await _resolve_self_employee(
                self._employee_repo, principal.tenant_id, principal.user_id
            )
            if employee_id is not None and employee_id != self_emp.id:
                raise ForbiddenScopeError("Employees may only view their own leave entitlements")
            scoped_emps = [self_emp]
        elif principal.role == UserRole.STORE_MANAGER:
            if store_id is not None:
                principal.require_store_access(principal.tenant_id, store_id)
            all_emps = await self._employee_repo.list_by_tenant(
                principal.tenant_id,
                store_id=store_id,
            )
            scoped_emps = [
                e
                for e in all_emps
                if e.deleted_at is None
                and e.employment_status not in _BLOCKED_EMPLOYEE_STATUSES
                and principal.can_manage_employee(e)
            ]
            if employee_id is not None:
                target = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
                if target is None or target.deleted_at is not None:
                    raise EntityNotFoundError(f"Employee {employee_id} not found")
                principal.require_employee_in_scope(target)
                scoped_emps = [e for e in scoped_emps if e.id == employee_id]
        else:
            all_emps = await self._employee_repo.list_by_tenant(
                principal.tenant_id,
                store_id=store_id,
            )
            scoped_emps = [
                e
                for e in all_emps
                if e.deleted_at is None
                and e.employment_status not in _BLOCKED_EMPLOYEE_STATUSES
                and (employee_id is None or e.id == employee_id)
            ]

        entitlements = await self._entitlement_repo.list_by_tenant(
            principal.tenant_id,
            year=year,
            employee_id=employee_id if principal.role == UserRole.EMPLOYEE else None,
        )
        ent_by_emp = {e.employee_id: e for e in entitlements}

        adjustments = await self._entitlement_repo.list_adjustments(
            principal.tenant_id,
            year=year,
            employee_id=employee_id if principal.role == UserRole.EMPLOYEE else None,
        )
        adj_by_emp: dict[UUID, list[LeaveEntitlementAdjustment]] = defaultdict(list)
        for adj in adjustments:
            adj_by_emp[adj.employee_id].append(adj)

        leaves = await self._leave_repo.list_by_tenant(
            principal.tenant_id,
            store_id=store_id,
            employee_id=employee_id,
            year=year,
        )
        leaves_by_emp: dict[UUID, list[LeaveRequest]] = defaultdict(list)
        for lv in leaves:
            leaves_by_emp[lv.employee_id].append(lv)

        views: list[EmployeeEntitlementView] = []
        for emp in scoped_emps:
            ent = ent_by_emp.get(emp.id)
            bal = compute_vacation_balance(
                emp, leaves_by_emp.get(emp.id, []), year, entitlement=ent
            )
            base_days = (
                ent.base_vacation_days
                if ent is not None
                else max(emp.annual_vacation_days, STATUTORY_MIN_VACATION_DAYS)
            )
            views.append(
                EmployeeEntitlementView(
                    employee_id=emp.id,
                    employee_code=emp.employee_code,
                    full_name=emp.full_name,
                    preferred_name=emp.preferred_name,
                    store_id=emp.primary_store_id,
                    department=emp.department,
                    year=year,
                    base_vacation_days=base_days,
                    carryover_days=ent.carryover_days if ent is not None else 0,
                    comp_off_days=ent.comp_off_days if ent is not None else 0,
                    entitled_days=bal.entitled_days,
                    approved_days=bal.approved_days,
                    pending_days=bal.pending_days,
                    remaining_days=bal.remaining_days,
                    adjustments=tuple(adj_by_emp.get(emp.id, [])),
                )
            )
        return views


def _determine_adjustment_type(
    existing: LeaveEntitlement | None,
    *,
    base_vacation_days: int,
    carryover_days: int,
    comp_off_days: int,
    is_bulk: bool = False,
) -> EntitlementAdjustmentType:
    if is_bulk and existing is None:
        return EntitlementAdjustmentType.BULK_INIT
    if existing is not None:
        if (
            carryover_days != existing.carryover_days
            and base_vacation_days == existing.base_vacation_days
            and comp_off_days == existing.comp_off_days
        ):
            return EntitlementAdjustmentType.CARRYOVER
        if (
            comp_off_days != existing.comp_off_days
            and base_vacation_days == existing.base_vacation_days
            and carryover_days == existing.carryover_days
        ):
            return EntitlementAdjustmentType.COMP_OFF
    return EntitlementAdjustmentType.BASE_UPDATE


class UpsertLeaveEntitlementUseCase:
    def __init__(
        self,
        entitlement_repo: LeaveEntitlementRepository,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
    ) -> None:
        self._entitlement_repo = entitlement_repo
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        employee_id: UUID,
        cmd: UpsertLeaveEntitlementCommand,
        *,
        now_utc: datetime,
    ) -> EmployeeEntitlementView:
        if principal.role not in _LEAVE_REVIEW_ROLES:
            raise ForbiddenScopeError(
                "Only owner, HR, or store_manager may manage leave entitlements"
            )

        emp = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
        if emp is None or emp.deleted_at is not None:
            raise EntityNotFoundError(f"Employee {employee_id} not found")
        if principal.role == UserRole.STORE_MANAGER:
            principal.require_employee_in_scope(emp)
        if emp.employment_status in _BLOCKED_EMPLOYEE_STATUSES:
            raise ValidationDomainError(
                f"Cannot update leave entitlement for employee in '{emp.employment_status.value}' "
                "status"
            )

        reason = validate_non_empty_text(cmd.reason, field="reason", max_length=300)
        new_total = validate_entitlement_values(
            year=cmd.year,
            base_vacation_days=cmd.base_vacation_days,
            carryover_days=cmd.carryover_days,
            comp_off_days=cmd.comp_off_days,
            employee_annual_vacation_days=emp.annual_vacation_days,
        )

        existing = await self._entitlement_repo.get_by_employee_year(
            principal.tenant_id, emp.id, cmd.year
        )
        prev_total = (
            max(
                max(
                    existing.base_vacation_days,
                    emp.annual_vacation_days,
                    STATUTORY_MIN_VACATION_DAYS,
                )
                + existing.carryover_days
                + existing.comp_off_days,
                STATUTORY_MIN_VACATION_DAYS,
            )
            if existing is not None
            else max(emp.annual_vacation_days, STATUTORY_MIN_VACATION_DAYS)
        )
        now = now_utc.astimezone(UTC)
        ent = LeaveEntitlement(
            id=existing.id if existing is not None else uuid4(),
            tenant_id=principal.tenant_id,
            employee_id=emp.id,
            year=cmd.year,
            base_vacation_days=cmd.base_vacation_days,
            carryover_days=cmd.carryover_days,
            comp_off_days=cmd.comp_off_days,
            updated_by_user_id=principal.user_id,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        saved_ent = await self._entitlement_repo.upsert(ent)

        adj_type = _determine_adjustment_type(
            existing,
            base_vacation_days=cmd.base_vacation_days,
            carryover_days=cmd.carryover_days,
            comp_off_days=cmd.comp_off_days,
        )
        await self._entitlement_repo.append_adjustment(
            LeaveEntitlementAdjustment(
                id=uuid4(),
                tenant_id=principal.tenant_id,
                employee_id=emp.id,
                year=cmd.year,
                adjustment_type=adj_type,
                delta_days=new_total - prev_total,
                previous_total_days=prev_total,
                new_total_days=new_total,
                reason=reason,
                adjusted_by_user_id=principal.user_id,
                created_at=now,
            )
        )

        requests = await self._leave_repo.list_by_employee(principal.tenant_id, emp.id)
        bal = compute_vacation_balance(emp, requests, cmd.year, entitlement=saved_ent)
        adjustments = await self._entitlement_repo.list_adjustments(
            principal.tenant_id, year=cmd.year, employee_id=emp.id
        )
        return EmployeeEntitlementView(
            employee_id=emp.id,
            employee_code=emp.employee_code,
            full_name=emp.full_name,
            preferred_name=emp.preferred_name,
            store_id=emp.primary_store_id,
            department=emp.department,
            year=cmd.year,
            base_vacation_days=saved_ent.base_vacation_days,
            carryover_days=saved_ent.carryover_days,
            comp_off_days=saved_ent.comp_off_days,
            entitled_days=bal.entitled_days,
            approved_days=bal.approved_days,
            pending_days=bal.pending_days,
            remaining_days=bal.remaining_days,
            adjustments=tuple(adjustments),
        )


class BulkUpsertLeaveEntitlementsUseCase:
    def __init__(
        self,
        entitlement_repo: LeaveEntitlementRepository,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
    ) -> None:
        self._entitlement_repo = entitlement_repo
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        cmd: BulkUpsertLeaveEntitlementsCommand,
        *,
        now_utc: datetime,
    ) -> list[EmployeeEntitlementView]:
        if principal.role not in {UserRole.OWNER, UserRole.HR}:
            raise ForbiddenScopeError(
                "Only owner or HR may bulk-assign leave entitlements"
            )

        reason = validate_non_empty_text(cmd.reason, field="reason", max_length=300)
        all_emps = await self._employee_repo.list_by_tenant(
            principal.tenant_id,
            store_id=cmd.store_id,
        )
        target_ids = set(cmd.employee_ids) if cmd.employee_ids is not None else None
        targets = [
            e
            for e in all_emps
            if e.deleted_at is None
            and e.employment_status not in _BLOCKED_EMPLOYEE_STATUSES
            and (target_ids is None or e.id in target_ids)
        ]

        now = now_utc.astimezone(UTC)
        views: list[EmployeeEntitlementView] = []
        for emp in targets:
            new_total = validate_entitlement_values(
                year=cmd.year,
                base_vacation_days=cmd.base_vacation_days,
                carryover_days=cmd.carryover_days,
                comp_off_days=cmd.comp_off_days,
                employee_annual_vacation_days=emp.annual_vacation_days,
            )
            existing = await self._entitlement_repo.get_by_employee_year(
                principal.tenant_id, emp.id, cmd.year
            )
            prev_total = (
                max(
                    max(
                        existing.base_vacation_days,
                        emp.annual_vacation_days,
                        STATUTORY_MIN_VACATION_DAYS,
                    )
                    + existing.carryover_days
                    + existing.comp_off_days,
                    STATUTORY_MIN_VACATION_DAYS,
                )
                if existing is not None
                else max(emp.annual_vacation_days, STATUTORY_MIN_VACATION_DAYS)
            )
            ent = LeaveEntitlement(
                id=existing.id if existing is not None else uuid4(),
                tenant_id=principal.tenant_id,
                employee_id=emp.id,
                year=cmd.year,
                base_vacation_days=cmd.base_vacation_days,
                carryover_days=cmd.carryover_days,
                comp_off_days=cmd.comp_off_days,
                updated_by_user_id=principal.user_id,
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
            )
            saved_ent = await self._entitlement_repo.upsert(ent)
            adj_type = _determine_adjustment_type(
                existing,
                base_vacation_days=cmd.base_vacation_days,
                carryover_days=cmd.carryover_days,
                comp_off_days=cmd.comp_off_days,
                is_bulk=True,
            )
            await self._entitlement_repo.append_adjustment(
                LeaveEntitlementAdjustment(
                    id=uuid4(),
                    tenant_id=principal.tenant_id,
                    employee_id=emp.id,
                    year=cmd.year,
                    adjustment_type=adj_type,
                    delta_days=new_total - prev_total,
                    previous_total_days=prev_total,
                    new_total_days=new_total,
                    reason=reason,
                    adjusted_by_user_id=principal.user_id,
                    created_at=now,
                )
            )
            requests = await self._leave_repo.list_by_employee(principal.tenant_id, emp.id)
            bal = compute_vacation_balance(emp, requests, cmd.year, entitlement=saved_ent)
            adjs = await self._entitlement_repo.list_adjustments(
                principal.tenant_id, year=cmd.year, employee_id=emp.id
            )
            views.append(
                EmployeeEntitlementView(
                    employee_id=emp.id,
                    employee_code=emp.employee_code,
                    full_name=emp.full_name,
                    preferred_name=emp.preferred_name,
                    store_id=emp.primary_store_id,
                    department=emp.department,
                    year=cmd.year,
                    base_vacation_days=saved_ent.base_vacation_days,
                    carryover_days=saved_ent.carryover_days,
                    comp_off_days=saved_ent.comp_off_days,
                    entitled_days=bal.entitled_days,
                    approved_days=bal.approved_days,
                    pending_days=bal.pending_days,
                    remaining_days=bal.remaining_days,
                    adjustments=tuple(adjs),
                )
            )
        return views


class DirectAssignLeaveUseCase:
    def __init__(
        self,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
        payroll_repo: PayrollClosingRepository | None = None,
        entitlement_repo: LeaveEntitlementRepository | None = None,
        shift_repo: ShiftRepository | None = None,
        store_repo: StoreRepository | None = None,
    ) -> None:
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo
        self._payroll_repo = payroll_repo
        self._entitlement_repo = entitlement_repo
        self._shift_repo = shift_repo
        self._store_repo = store_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        cmd: DirectAssignLeaveCommand,
        *,
        now_utc: datetime,
    ) -> LeaveRequest:
        if principal.role not in _LEAVE_REVIEW_ROLES:
            raise ForbiddenScopeError(
                "Only owner, HR, or store_manager may directly assign leave"
            )

        emp = await self._employee_repo.get_by_id(principal.tenant_id, cmd.employee_id)
        if emp is None or emp.deleted_at is not None:
            raise EntityNotFoundError(f"Employee {cmd.employee_id} not found")
        if principal.role == UserRole.STORE_MANAGER:
            principal.require_employee_in_scope(emp)

        if emp.employment_status in _BLOCKED_EMPLOYEE_STATUSES:
            raise ValidationDomainError(
                f"Cannot assign leave for employee in '{emp.employment_status.value}' status"
            )

        natural_days = calculate_natural_days(cmd.start_date, cmd.end_date)
        if self._payroll_repo is not None:
            closed_months = await self._payroll_repo.list_closed_months_for_store(
                principal.tenant_id,
                emp.primary_store_id,
                spanned_period_months(cmd.start_date, cmd.end_date),
            )
            if closed_months:
                raise ConflictError(
                    f"Cannot assign leave: payroll month(s) {sorted(closed_months)} "
                    "are closed for this store"
                )

        existing = await self._leave_repo.list_by_employee(principal.tenant_id, emp.id)
        check_leave_overlap(
            start_date=cmd.start_date,
            end_date=cmd.end_date,
            existing_requests=existing,
        )
        if self._shift_repo is not None and self._store_repo is not None:
            store = await self._store_repo.get_by_id(principal.tenant_id, emp.primary_store_id)
            if store is None:
                raise EntityNotFoundError(f"Store {emp.primary_store_id} not found")
            win_start = datetime(
                cmd.start_date.year, cmd.start_date.month, cmd.start_date.day, tzinfo=UTC
            ) - timedelta(days=1)
            win_end = datetime(
                cmd.end_date.year, cmd.end_date.month, cmd.end_date.day, tzinfo=UTC
            ) + timedelta(days=2)
            shifts = await self._shift_repo.list_by_employee_window(
                principal.tenant_id,
                emp.id,
                window_start_utc=win_start,
                window_end_utc=win_end,
            )
            check_leave_published_shift_conflict(
                start_date=cmd.start_date,
                end_date=cmd.end_date,
                store_timezone=store.timezone,
                shifts=shifts,
            )
        if cmd.leave_type == LeaveType.VACATION:
            entitlements_by_year = await _load_entitlements_by_year(
                self._entitlement_repo,
                principal.tenant_id,
                emp.id,
                cmd.start_date,
                cmd.end_date,
            )
            validate_vacation_balance_for_request(
                employee=emp,
                start_date=cmd.start_date,
                end_date=cmd.end_date,
                existing_requests=existing,
                entitlements_by_year=entitlements_by_year,
            )

        reason = (
            validate_non_empty_text(cmd.reason, field="reason", max_length=500)
            if cmd.reason.strip()
            else ""
        )
        now = now_utc.astimezone(UTC)
        is_mgr = principal.role == UserRole.STORE_MANAGER
        leave = LeaveRequest(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            store_id=emp.primary_store_id,
            employee_id=emp.id,
            leave_type=cmd.leave_type,
            start_date=cmd.start_date,
            end_date=cmd.end_date,
            natural_days=natural_days,
            status=LeaveStatus.APPROVED,
            reason=reason,
            requested_by_user_id=principal.user_id,
            reviewed_by_user_id=principal.user_id,
            reviewed_at_utc=now,
            review_note=reason[:300],
            manager_reviewed_by_user_id=principal.user_id if is_mgr else None,
            manager_reviewed_at_utc=now if is_mgr else None,
            manager_review_note=reason[:300] if is_mgr else "",
            hr_reviewed_by_user_id=None if is_mgr else principal.user_id,
            hr_reviewed_at_utc=None if is_mgr else now,
            hr_review_note="" if is_mgr else reason[:300],
            assigned_directly=True,
            assigned_by_user_id=principal.user_id,
            medical_document_id=None,
            retention_until=compute_leave_retention_until(cmd.end_date),
            created_at=now,
            updated_at=now,
        )
        return await self._leave_repo.create(leave)
