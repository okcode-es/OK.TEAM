"""Application use cases for Store Shift Scheduling & Spanish Labor Law compliance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.employees import Employee, EmploymentStatus, FrozenRecordError
from app.domain.leaves import LeaveRequest, LeaveStatus, check_shift_leave_conflict
from app.domain.organization import (
    EntityNotFoundError,
    ValidationDomainError,
    validate_non_empty_text,
)
from app.domain.repositories import (
    EmployeeRepository,
    LeaveRepository,
    ShiftRepository,
    StoreRepository,
)
from app.domain.scheduling import (
    ScheduleAlert,
    Shift,
    ShiftStatus,
    check_shift_overlap,
    compute_shift_retention_until,
    enforce_hard_schedule_blocks,
    ensure_employee_schedulable,
    evaluate_employee_week_schedule,
    normalize_week_start,
    validate_shift_bounds,
    week_utc_window,
)
from app.domain.security import AuthenticatedPrincipal, UserRole

_SCHEDULE_WRITE_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.OWNER, UserRole.HR, UserRole.STORE_MANAGER}
)


@dataclass(frozen=True, slots=True)
class EmployeeWeeklyScheduleRow:
    employee: Employee
    shifts: list[Shift]
    approved_leaves: list[LeaveRequest]
    weekly_net_hours: Decimal
    alerts: list[ScheduleAlert]


@dataclass(frozen=True, slots=True)
class WeeklyScheduleResult:
    store_id: UUID
    store_code: str
    store_name: str
    store_timezone: str
    week_start: date
    week_end: date
    rows: list[EmployeeWeeklyScheduleRow]


@dataclass(frozen=True, slots=True)
class CreateShiftCommand:
    employee_id: UUID
    start_at_utc: datetime
    end_at_utc: datetime
    planned_break_minutes: int = 0
    status: ShiftStatus = ShiftStatus.DRAFT
    notes: str = ""


@dataclass(frozen=True, slots=True)
class UpdateShiftCommand:
    shift_id: UUID
    start_at_utc: datetime | None = None
    end_at_utc: datetime | None = None
    planned_break_minutes: int | None = None
    status: ShiftStatus | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ShiftMutationResult:
    shift: Shift
    alerts: list[ScheduleAlert]


async def _resolve_employee_for_user(
    employee_repo: EmployeeRepository,
    tenant_id: UUID,
    user_id: UUID,
) -> Employee:
    matches = await employee_repo.list_by_tenant(tenant_id, only_user_id=user_id)
    if not matches:
        raise EntityNotFoundError("No employee record linked to current user")
    return matches[0]


def _require_schedule_write_role(principal: AuthenticatedPrincipal) -> None:
    if principal.role not in _SCHEDULE_WRITE_ROLES:
        from app.domain.security import ForbiddenScopeError

        raise ForbiddenScopeError("Only owners, HR, or store managers may modify shift schedules")


class GetWeeklyScheduleUseCase:
    def __init__(
        self,
        shift_repo: ShiftRepository,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
    ) -> None:
        self._shift_repo = shift_repo
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo
        self._store_repo = store_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        store_id: UUID | None,
        week_start: date,
    ) -> WeeklyScheduleResult:
        monday = normalize_week_start(week_start)
        sunday = monday + timedelta(days=6)

        if principal.role == UserRole.EMPLOYEE:
            self_emp = await _resolve_employee_for_user(
                self._employee_repo, principal.tenant_id, principal.user_id
            )
            target_store_id = store_id or self_emp.primary_store_id
            if (
                target_store_id != self_emp.primary_store_id
                and target_store_id not in principal.store_ids
            ):
                from app.domain.security import ForbiddenScopeError

                raise ForbiddenScopeError("Employees may only view their own store schedule")
            employees = [self_emp]
        else:
            if store_id is None:
                raise ValidationDomainError("store_id query parameter is required")
            target_store_id = store_id
            principal.require_store_access(principal.tenant_id, target_store_id)
            if principal.role == UserRole.STORE_MANAGER:
                allowed_statuses = frozenset({EmploymentStatus.ACTIVE, EmploymentStatus.ON_LEAVE})
            else:
                allowed_statuses = frozenset(
                    {
                        EmploymentStatus.ACTIVE,
                        EmploymentStatus.ON_LEAVE,
                        EmploymentStatus.LEGAL_HOLD,
                    }
                )
            employees = await self._employee_repo.list_by_tenant(
                principal.tenant_id,
                store_id=target_store_id,
                allowed_statuses=allowed_statuses,
            )
            if principal.role == UserRole.STORE_MANAGER:
                employees = [e for e in employees if principal.can_manage_employee(e)]

        store = await self._store_repo.get_by_id(principal.tenant_id, target_store_id)
        if store is None:
            raise EntityNotFoundError(f"Store {target_store_id} not found")

        win_start_utc, win_end_utc = week_utc_window(monday, store.timezone)
        padded_start = win_start_utc - timedelta(hours=24)
        padded_end = win_end_utc + timedelta(hours=24)

        all_store_shifts = await self._shift_repo.list_by_store_window(
            principal.tenant_id,
            store.id,
            window_start_utc=padded_start,
            window_end_utc=padded_end,
        )
        approved_leaves = await self._leave_repo.list_by_tenant(
            principal.tenant_id,
            store_id=store.id,
            status=LeaveStatus.APPROVED,
        )

        shifts_by_emp: dict[UUID, list[Shift]] = {}
        for s in all_store_shifts:
            if not s.is_active:
                continue
            if principal.role == UserRole.EMPLOYEE and s.status != ShiftStatus.PUBLISHED:
                continue
            shifts_by_emp.setdefault(s.employee_id, []).append(s)

        leaves_by_emp: dict[UUID, list[LeaveRequest]] = {}
        for lv in approved_leaves:
            if lv.start_date <= sunday and lv.end_date >= monday:
                leaves_by_emp.setdefault(lv.employee_id, []).append(lv)

        rows: list[EmployeeWeeklyScheduleRow] = []
        for emp in employees:
            emp_shifts = shifts_by_emp.get(emp.id, [])
            week_shifts = [
                s
                for s in emp_shifts
                if s.start_at_utc < win_end_utc and s.end_at_utc > win_start_utc
            ]
            adjacent_shifts = [s for s in emp_shifts if s not in week_shifts]
            weekly_hours, alerts = evaluate_employee_week_schedule(
                employee=emp,
                week_start=monday,
                store_timezone=store.timezone,
                shifts=week_shifts,
                adjacent_shifts=adjacent_shifts,
            )
            rows.append(
                EmployeeWeeklyScheduleRow(
                    employee=emp,
                    shifts=week_shifts,
                    approved_leaves=leaves_by_emp.get(emp.id, []),
                    weekly_net_hours=weekly_hours,
                    alerts=alerts,
                )
            )

        return WeeklyScheduleResult(
            store_id=store.id,
            store_code=store.code,
            store_name=store.name,
            store_timezone=store.timezone,
            week_start=monday,
            week_end=sunday,
            rows=rows,
        )


class CreateShiftUseCase:
    def __init__(
        self,
        shift_repo: ShiftRepository,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
    ) -> None:
        self._shift_repo = shift_repo
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo
        self._store_repo = store_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        cmd: CreateShiftCommand,
        *,
        now_utc: datetime,
    ) -> ShiftMutationResult:
        _require_schedule_write_role(principal)
        if cmd.status == ShiftStatus.CANCELLED:
            raise ValidationDomainError("Cannot create a shift directly in cancelled status")

        employee = await self._employee_repo.get_by_id(principal.tenant_id, cmd.employee_id)
        if employee is None:
            raise EntityNotFoundError(f"Employee {cmd.employee_id} not found")

        principal.require_store_access(principal.tenant_id, employee.primary_store_id)
        if principal.role == UserRole.STORE_MANAGER:
            principal.require_employee_in_scope(employee)

        ensure_employee_schedulable(employee)

        store = await self._store_repo.get_by_id(principal.tenant_id, employee.primary_store_id)
        if store is None:
            raise EntityNotFoundError(f"Store {employee.primary_store_id} not found")

        start_utc, end_utc, break_mins = validate_shift_bounds(
            cmd.start_at_utc, cmd.end_at_utc, cmd.planned_break_minutes
        )
        notes = (
            validate_non_empty_text(cmd.notes, field="notes", max_length=500)
            if cmd.notes.strip()
            else ""
        )

        win_start, win_end = week_utc_window(start_utc.date(), store.timezone)
        existing_shifts = await self._shift_repo.list_by_employee_window(
            principal.tenant_id,
            employee.id,
            window_start_utc=win_start - timedelta(days=7),
            window_end_utc=win_end + timedelta(days=7),
        )
        check_shift_overlap(
            candidate_id=None,
            start_at_utc=start_utc,
            end_at_utc=end_utc,
            existing_shifts=existing_shifts,
        )

        emp_leaves = await self._leave_repo.list_by_employee(principal.tenant_id, employee.id)
        check_shift_leave_conflict(
            start_at_utc=start_utc,
            end_at_utc=end_utc,
            store_timezone=store.timezone,
            approved_leaves=emp_leaves,
        )

        now = now_utc.astimezone(UTC)
        candidate = Shift(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            store_id=store.id,
            employee_id=employee.id,
            start_at_utc=start_utc,
            end_at_utc=end_utc,
            planned_break_minutes=break_mins,
            status=cmd.status,
            notes=notes,
            created_by_user_id=principal.user_id,
            published_by_user_id=principal.user_id if cmd.status == ShiftStatus.PUBLISHED else None,
            published_at_utc=now if cmd.status == ShiftStatus.PUBLISHED else None,
            retention_until=compute_shift_retention_until(end_utc),
            created_at=now,
            updated_at=now,
        )

        alerts = enforce_hard_schedule_blocks(
            employee=employee,
            candidate_shift=candidate,
            store_timezone=store.timezone,
            week_shifts=existing_shifts,
        )

        created = await self._shift_repo.create(candidate)
        return ShiftMutationResult(shift=created, alerts=alerts)


class UpdateShiftUseCase:
    def __init__(
        self,
        shift_repo: ShiftRepository,
        leave_repo: LeaveRepository,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
    ) -> None:
        self._shift_repo = shift_repo
        self._leave_repo = leave_repo
        self._employee_repo = employee_repo
        self._store_repo = store_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        cmd: UpdateShiftCommand,
        *,
        now_utc: datetime,
    ) -> ShiftMutationResult:
        _require_schedule_write_role(principal)

        existing = await self._shift_repo.get_by_id(principal.tenant_id, cmd.shift_id)
        if existing is None:
            raise EntityNotFoundError(f"Shift {cmd.shift_id} not found")
        principal.require_store_access(principal.tenant_id, existing.store_id)

        if existing.status == ShiftStatus.CANCELLED:
            raise ValidationDomainError("Cannot modify a cancelled shift")
        if cmd.status == ShiftStatus.CANCELLED:
            raise ValidationDomainError("Use DELETE /shifts/{shift_id} to cancel a shift")

        employee = await self._employee_repo.get_by_id(principal.tenant_id, existing.employee_id)
        if employee is None:
            raise EntityNotFoundError(f"Employee {existing.employee_id} not found")
        if principal.role == UserRole.STORE_MANAGER:
            principal.require_employee_in_scope(employee)
        ensure_employee_schedulable(employee)

        store = await self._store_repo.get_by_id(principal.tenant_id, existing.store_id)
        if store is None:
            raise EntityNotFoundError(f"Store {existing.store_id} not found")

        start_utc, end_utc, break_mins = validate_shift_bounds(
            cmd.start_at_utc or existing.start_at_utc,
            cmd.end_at_utc or existing.end_at_utc,
            (
                cmd.planned_break_minutes
                if cmd.planned_break_minutes is not None
                else existing.planned_break_minutes
            ),
        )
        new_status = cmd.status or existing.status
        new_notes = (
            (
                validate_non_empty_text(cmd.notes, field="notes", max_length=500)
                if cmd.notes.strip()
                else ""
            )
            if cmd.notes is not None
            else existing.notes
        )

        win_start, win_end = week_utc_window(start_utc.date(), store.timezone)
        existing_shifts = await self._shift_repo.list_by_employee_window(
            principal.tenant_id,
            employee.id,
            window_start_utc=win_start - timedelta(days=7),
            window_end_utc=win_end + timedelta(days=7),
        )
        check_shift_overlap(
            candidate_id=existing.id,
            start_at_utc=start_utc,
            end_at_utc=end_utc,
            existing_shifts=existing_shifts,
        )

        emp_leaves = await self._leave_repo.list_by_employee(principal.tenant_id, employee.id)
        check_shift_leave_conflict(
            start_at_utc=start_utc,
            end_at_utc=end_utc,
            store_timezone=store.timezone,
            approved_leaves=emp_leaves,
        )

        now = now_utc.astimezone(UTC)
        published_by = existing.published_by_user_id
        published_at = existing.published_at_utc
        if new_status == ShiftStatus.PUBLISHED and existing.status != ShiftStatus.PUBLISHED:
            published_by = principal.user_id
            published_at = now

        updated_candidate = Shift(
            id=existing.id,
            tenant_id=existing.tenant_id,
            store_id=existing.store_id,
            employee_id=existing.employee_id,
            start_at_utc=start_utc,
            end_at_utc=end_utc,
            planned_break_minutes=break_mins,
            status=new_status,
            notes=new_notes,
            created_by_user_id=existing.created_by_user_id,
            published_by_user_id=published_by,
            published_at_utc=published_at,
            retention_until=compute_shift_retention_until(end_utc),
            created_at=existing.created_at,
            updated_at=now,
        )

        alerts = enforce_hard_schedule_blocks(
            employee=employee,
            candidate_shift=updated_candidate,
            store_timezone=store.timezone,
            week_shifts=existing_shifts,
        )

        saved = await self._shift_repo.update(updated_candidate)
        if saved is None:
            raise EntityNotFoundError(f"Shift {cmd.shift_id} not found")
        return ShiftMutationResult(shift=saved, alerts=alerts)


class PublishWeeklyShiftsUseCase:
    def __init__(
        self,
        shift_repo: ShiftRepository,
        store_repo: StoreRepository,
        employee_repo: EmployeeRepository,
        leave_repo: LeaveRepository,
    ) -> None:
        self._shift_repo = shift_repo
        self._store_repo = store_repo
        self._employee_repo = employee_repo
        self._leave_repo = leave_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        store_id: UUID,
        week_start: date,
        now_utc: datetime,
    ) -> list[Shift]:
        _require_schedule_write_role(principal)
        principal.require_store_access(principal.tenant_id, store_id)

        store = await self._store_repo.get_by_id(principal.tenant_id, store_id)
        if store is None:
            raise EntityNotFoundError(f"Store {store_id} not found")

        win_start_utc, win_end_utc = week_utc_window(week_start, store.timezone)
        shifts = await self._shift_repo.list_by_store_window(
            principal.tenant_id,
            store_id,
            window_start_utc=win_start_utc,
            window_end_utc=win_end_utc,
        )
        draft_shifts = [
            s for s in shifts if s.status == ShiftStatus.DRAFT and s.deleted_at is None
        ]
        emp_cache: dict[UUID, Employee] = {}
        leaves_cache: dict[UUID, list[LeaveRequest]] = {}
        scoped_drafts: list[Shift] = []
        for shift in draft_shifts:
            emp = emp_cache.get(shift.employee_id)
            if emp is None:
                loaded_emp = await self._employee_repo.get_by_id(
                    principal.tenant_id, shift.employee_id
                )
                if loaded_emp is None:
                    raise EntityNotFoundError(f"Employee {shift.employee_id} not found")
                emp = loaded_emp
                emp_cache[shift.employee_id] = emp
            if principal.role == UserRole.STORE_MANAGER and not principal.can_manage_employee(emp):
                continue
            scoped_drafts.append(shift)
            if emp.employment_status in (EmploymentStatus.LEGAL_HOLD, EmploymentStatus.ANONYMIZED):
                raise FrozenRecordError(
                    f"Employee {emp.employee_code} is {emp.employment_status.value} "
                    "and cannot have shifts published"
                )
            ensure_employee_schedulable(emp)

            emp_leaves = leaves_cache.get(shift.employee_id)
            if emp_leaves is None:
                emp_leaves = await self._leave_repo.list_by_employee(
                    principal.tenant_id, shift.employee_id
                )
                leaves_cache[shift.employee_id] = emp_leaves
            check_shift_leave_conflict(
                start_at_utc=shift.start_at_utc,
                end_at_utc=shift.end_at_utc,
                store_timezone=store.timezone,
                approved_leaves=emp_leaves,
            )

        now = now_utc.astimezone(UTC)
        published: list[Shift] = []
        for shift in scoped_drafts:
            updated = Shift(
                id=shift.id,
                tenant_id=shift.tenant_id,
                store_id=shift.store_id,
                employee_id=shift.employee_id,
                start_at_utc=shift.start_at_utc,
                end_at_utc=shift.end_at_utc,
                planned_break_minutes=shift.planned_break_minutes,
                status=ShiftStatus.PUBLISHED,
                notes=shift.notes,
                created_by_user_id=shift.created_by_user_id,
                published_by_user_id=principal.user_id,
                published_at_utc=now,
                retention_until=shift.retention_until,
                created_at=shift.created_at,
                updated_at=now,
            )
            saved = await self._shift_repo.update(updated)
            if saved is not None:
                published.append(saved)
        return published


class CancelShiftUseCase:
    def __init__(
        self,
        shift_repo: ShiftRepository,
        employee_repo: EmployeeRepository,
    ) -> None:
        self._shift_repo = shift_repo
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        shift_id: UUID,
        now_utc: datetime,
    ) -> Shift:
        _require_schedule_write_role(principal)
        existing = await self._shift_repo.get_by_id(principal.tenant_id, shift_id)
        if existing is None:
            raise EntityNotFoundError(f"Shift {shift_id} not found")
        principal.require_store_access(principal.tenant_id, existing.store_id)

        emp = await self._employee_repo.get_by_id(principal.tenant_id, existing.employee_id)
        if emp is not None:
            if principal.role == UserRole.STORE_MANAGER:
                principal.require_employee_in_scope(emp)
            elif emp.employment_status in (
                EmploymentStatus.LEGAL_HOLD,
                EmploymentStatus.ANONYMIZED,
            ):
                raise FrozenRecordError(
                    f"Cannot cancel shift for frozen/anonymized employee {emp.employee_code}"
                )

        if existing.status == ShiftStatus.CANCELLED:
            return existing

        now = now_utc.astimezone(UTC)
        cancelled = Shift(
            id=existing.id,
            tenant_id=existing.tenant_id,
            store_id=existing.store_id,
            employee_id=existing.employee_id,
            start_at_utc=existing.start_at_utc,
            end_at_utc=existing.end_at_utc,
            planned_break_minutes=existing.planned_break_minutes,
            status=ShiftStatus.CANCELLED,
            notes=existing.notes,
            created_by_user_id=existing.created_by_user_id,
            published_by_user_id=existing.published_by_user_id,
            published_at_utc=existing.published_at_utc,
            retention_until=existing.retention_until,
            created_at=existing.created_at,
            updated_at=now,
            deleted_at=now,
        )
        saved = await self._shift_repo.update(cancelled)
        if saved is None:
            raise EntityNotFoundError(f"Shift {shift_id} not found")
        return saved
