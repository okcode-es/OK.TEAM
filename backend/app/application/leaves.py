"""Application use cases for Annual Vacation & Leave Management (Art. 38 ET)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.documents import DocumentCategory
from app.domain.employees import Employee, EmploymentStatus, FrozenRecordError
from app.domain.leaves import (
    LeaveRequest,
    LeaveStatus,
    LeaveType,
    VacationBalance,
    calculate_natural_days,
    check_leave_overlap,
    check_leave_published_shift_conflict,
    compute_leave_retention_until,
    compute_vacation_balance,
    validate_leave_transition,
    validate_vacation_balance_for_request,
)
from app.domain.organization import (
    EntityNotFoundError,
    ValidationDomainError,
    validate_non_empty_text,
)
from app.domain.repositories import (
    DocumentRepository,
    EmployeeRepository,
    LeaveRepository,
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
            blocked_ids = {
                e.id
                for e in emps
                if e.employment_status
                in (EmploymentStatus.LEGAL_HOLD, EmploymentStatus.ANONYMIZED)
            }
            return [lv for lv in leaves if lv.employee_id not in blocked_ids]

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
    ) -> None:
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo

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
                principal.require_store_access(principal.tenant_id, emp.primary_store_id)
                if emp.employment_status in (
                    EmploymentStatus.LEGAL_HOLD,
                    EmploymentStatus.ANONYMIZED,
                ):
                    raise ForbiddenScopeError(
                        "Store managers cannot access legal_hold or anonymized employees"
                    )

        requests = await self._leave_repo.list_by_employee(principal.tenant_id, emp.id)
        return compute_vacation_balance(emp, requests, year)


class CreateLeaveRequestUseCase:
    def __init__(
        self,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
        document_repo: DocumentRepository,
    ) -> None:
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo
        self._document_repo = document_repo

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
                principal.require_store_access(principal.tenant_id, emp.primary_store_id)
                if emp.employment_status in (
                    EmploymentStatus.LEGAL_HOLD,
                    EmploymentStatus.ANONYMIZED,
                ):
                    raise ForbiddenScopeError(
                        "Store managers cannot access legal_hold or anonymized employees"
                    )

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
        existing = await self._leave_repo.list_by_employee(principal.tenant_id, emp.id)
        check_leave_overlap(
            start_date=cmd.start_date,
            end_date=cmd.end_date,
            existing_requests=existing,
        )
        if cmd.leave_type == LeaveType.VACATION:
            validate_vacation_balance_for_request(
                employee=emp,
                start_date=cmd.start_date,
                end_date=cmd.end_date,
                existing_requests=existing,
            )

        reason = (
            validate_non_empty_text(cmd.reason, field="reason", max_length=500)
            if cmd.reason.strip()
            else ""
        )
        now = now_utc.astimezone(UTC)
        leave = LeaveRequest(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            store_id=emp.primary_store_id,
            employee_id=emp.id,
            leave_type=cmd.leave_type,
            start_date=cmd.start_date,
            end_date=cmd.end_date,
            natural_days=natural_days,
            status=LeaveStatus.PENDING,
            reason=reason,
            requested_by_user_id=principal.user_id,
            reviewed_by_user_id=None,
            reviewed_at_utc=None,
            review_note="",
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
    ) -> None:
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo
        self._shift_repo = shift_repo
        self._store_repo = store_repo

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
        if (
            principal.role == UserRole.STORE_MANAGER
            and emp.employment_status
            in (EmploymentStatus.LEGAL_HOLD, EmploymentStatus.ANONYMIZED)
        ):
            raise ForbiddenScopeError("Store managers cannot access legal_hold employees")

        target_status = LeaveStatus.APPROVED if cmd.approve else LeaveStatus.REJECTED
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
                validate_vacation_balance_for_request(
                    employee=emp,
                    start_date=leave.start_date,
                    end_date=leave.end_date,
                    existing_requests=[r for r in existing if r.id != leave.id],
                )

        review_note = (
            validate_non_empty_text(cmd.review_note, field="review_note", max_length=500)
            if cmd.review_note.strip()
            else ""
        )
        now = now_utc.astimezone(UTC)
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
            medical_document_id=leave.medical_document_id,
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
    ) -> None:
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo

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
        elif principal.role == UserRole.STORE_MANAGER:
            principal.require_store_access(principal.tenant_id, leave.store_id)

        emp = await self._employee_repo.get_by_id(principal.tenant_id, leave.employee_id)
        if emp is not None and emp.employment_status in (
            EmploymentStatus.LEGAL_HOLD,
            EmploymentStatus.ANONYMIZED,
        ):
            if principal.role == UserRole.STORE_MANAGER:
                raise ForbiddenScopeError(
                    "Store managers cannot access or cancel leaves for legal_hold employees"
                )
            raise FrozenRecordError(
                f"Cannot cancel leave for frozen/anonymized employee {emp.employee_code}"
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
            medical_document_id=leave.medical_document_id,
            retention_until=leave.retention_until,
            created_at=leave.created_at,
            updated_at=now,
        )
        saved = await self._leave_repo.update(cancelled)
        if saved is None:
            raise EntityNotFoundError(f"Leave request {leave_id} not found")
        return saved
