"""Application use cases for Phase 3 Append-Only Attendance, Corrections, Ledger & ITSS CSV."""

from __future__ import annotations

import calendar
import csv
import io
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from app.application.break_policies import build_break_policy_lookup, resolve_policy_for_employee
from app.domain.attendance import (
    AlertCode,
    AlertSeverity,
    AttendanceDayReview,
    AttendanceDayReviewStatus,
    AttendanceEvent,
    AttendanceEventType,
    AttendanceLedgerReport,
    AttendanceSource,
    CorrectedEventType,
    WorkMode,
    WorkSession,
    check_manual_day_session_overlap,
    compute_attendance_retention_until,
    compute_auto_break_end_utc,
    evaluate_attendance_ledger,
    fold_attendance_events,
    latest_effective_punch_utc,
    resolve_timezone,
    validate_and_build_manual_day_timestamps,
)
from app.domain.break_policies import EffectiveBreakPolicy
from app.domain.employees import (
    Employee,
    EmploymentStatus,
    FrozenRecordError,
    normalize_employee_code,
    verify_kiosk_pin,
)
from app.domain.leaves import LeaveStatus, LeaveType
from app.domain.organization import (
    ConflictError,
    EntityNotFoundError,
    Store,
    ValidationDomainError,
    validate_non_empty_text,
)
from app.domain.payroll import (
    AttendancePdfDayRow,
    AttendancePdfDocumentInput,
    AttendancePdfRendererPort,
)
from app.domain.repositories import (
    AttendanceDayReviewRepository,
    AttendanceRepository,
    BreakPolicyRepository,
    EmployeeRepository,
    LeaveRepository,
    PayrollClosingRepository,
    ShiftRepository,
    StoreRepository,
    TenantRepository,
)
from app.domain.scheduling import ShiftStatus
from app.domain.security import (
    TENANT_WIDE_ROLES,
    AuthenticatedPrincipal,
    ForbiddenScopeError,
    UserRole,
)

CLOCKABLE_STATUSES: frozenset[EmploymentStatus] = frozenset(
    {EmploymentStatus.ACTIVE, EmploymentStatus.ON_LEAVE}
)
MANAGER_VISIBLE_STATUSES: frozenset[EmploymentStatus] = frozenset(
    {EmploymentStatus.ACTIVE, EmploymentStatus.ON_LEAVE}
)
RAW_PUNCH_TYPES: frozenset[AttendanceEventType] = frozenset(
    {
        AttendanceEventType.CLOCK_IN,
        AttendanceEventType.BREAK_START,
        AttendanceEventType.BREAK_END,
        AttendanceEventType.CLOCK_OUT,
    }
)


@dataclass(frozen=True, slots=True)
class ClockAttendanceCommand:
    event_type: AttendanceEventType
    source: AttendanceSource = AttendanceSource.WEB
    store_id: UUID | None = None
    employee_code: str | None = None
    work_mode: WorkMode = WorkMode.ONSITE
    occurred_at_utc: datetime | None = None
    kiosk_pin: str | None = field(default=None, repr=False)

@dataclass(frozen=True, slots=True)
class ClockAttendanceResult:
    event: AttendanceEvent
    employee: Employee
    store: Store


@dataclass(frozen=True, slots=True)
class RecordManualDayCommand:
    local_date: date
    clock_in_time: str
    employee_id: UUID | None = None
    break_start_time: str | None = None
    break_end_time: str | None = None
    clock_out_time: str | None = None
    work_mode: WorkMode = WorkMode.ONSITE
    note: str = ""


@dataclass(frozen=True, slots=True)
class RecordManualDayResult:
    events: tuple[AttendanceEvent, ...]
    employee: Employee
    store: Store


@dataclass(frozen=True, slots=True)
class AppendCorrectionCommand:
    employee_id: UUID
    corrected_event_type: CorrectedEventType
    occurred_at_utc: datetime
    correction_reason: str
    store_id: UUID | None = None
    supersedes_event_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class EmployeeAttendanceLedgerItem:
    employee: Employee
    store: Store
    report: AttendanceLedgerReport
    events: tuple[AttendanceEvent, ...]
    effective_break_policy: EffectiveBreakPolicy | None = None
    closed_months: tuple[str, ...] = ()
    day_reviews: tuple[AttendanceDayReview, ...] = ()


@dataclass(frozen=True, slots=True)
class BatchReviewAttendanceDaysCommand:
    employee_id: UUID
    dates: tuple[date, ...]
    action: Literal["submit", "approve"]
    note: str = ""


@dataclass(frozen=True, slots=True)
class AttendanceLedgerResult:
    items: tuple[EmployeeAttendanceLedgerItem, ...]
    has_mutations: bool = False


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValidationDomainError("Datetime must include timezone information (UTC)")
    return dt.astimezone(UTC)


def _session_sig(
    s: WorkSession,
) -> tuple[UUID, date, datetime, datetime | None, int, int, bool, bool]:
    return (
        s.store_id,
        s.local_date,
        s.clock_in_utc,
        s.clock_out_utc,
        s.break_minutes,
        s.net_minutes,
        s.is_open,
        s.has_open_break,
    )


async def _check_store_months_open(
    payroll_repo: PayrollClosingRepository | None,
    *,
    tenant_id: UUID,
    checks: set[tuple[UUID, str]],
) -> None:
    if payroll_repo is None:
        return
    for check_store_id, check_month in sorted(checks, key=lambda x: (str(x[0]), x[1])):
        if await payroll_repo.is_store_month_closed(tenant_id, check_store_id, check_month):
            raise ConflictError(
                f"Cannot modify attendance: payroll month {check_month} is closed "
                f"for store {check_store_id}"
            )


async def _enforce_closed_month_session_diff(
    payroll_repo: PayrollClosingRepository | None,
    *,
    tenant_id: UUID,
    primary_store_id: UUID,
    before_sessions: Sequence[WorkSession],
    after_sessions: Sequence[WorkSession],
    already_checked: set[tuple[UUID, str]],
) -> None:
    if payroll_repo is None:
        return

    before_counts = Counter(_session_sig(s) for s in before_sessions)
    after_counts = Counter(_session_sig(s) for s in after_sessions)

    checks: set[tuple[UUID, str]] = set()
    all_sigs = set(before_counts.keys()) | set(after_counts.keys())
    for sig in all_sigs:
        if before_counts[sig] != after_counts[sig]:
            sess_store_id, sess_date = sig[0], sig[1]
            sess_month = sess_date.strftime("%Y-%m")
            checks.add((sess_store_id, sess_month))
            checks.add((primary_store_id, sess_month))

    await _check_store_months_open(
        payroll_repo,
        tenant_id=tenant_id,
        checks=checks - already_checked,
    )


async def auto_complete_elapsed_break_if_due(
    *,
    tenant_id: UUID,
    employee: Employee,
    events: Sequence[AttendanceEvent],
    now_utc: datetime,
    attendance_repo: AttendanceRepository,
    store_repo: StoreRepository,
    payroll_repo: PayrollClosingRepository | None = None,
    actor_user_id: UUID | None = None,
    recorded_at_utc: datetime | None = None,
    effective_policy: EffectiveBreakPolicy | None = None,
) -> tuple[list[AttendanceEvent], bool]:
    """Server-authoritative idempotent materialization of elapsed fixed-duration breaks."""
    event_list = list(events)
    now_norm = _ensure_utc(now_utc)
    rec_norm = _ensure_utc(recorded_at_utc) if recorded_at_utc is not None else now_norm

    _, due_end_utc = compute_auto_break_end_utc(
        event_list,
        employee.id,
        employee.default_break_minutes,
        now_norm,
        effective_policy=effective_policy,
    )
    duration_label = (
        effective_policy.effective_duration_minutes
        if effective_policy is not None and effective_policy.effective_duration_minutes is not None
        else employee.default_break_minutes
    )
    if due_end_utc is None or duration_label is None:
        return (event_list, False)

    sessions = fold_attendance_events(employee.id, event_list)
    open_session = next((s for s in sessions if s.is_open and s.has_open_break), None)
    if open_session is None:
        return (event_list, False)

    store = await store_repo.get_by_id(tenant_id, open_session.store_id)
    if store is None or not store.is_active:
        return (event_list, False)

    if payroll_repo is not None:
        tz = resolve_timezone(open_session.store_timezone)
        local_month = due_end_utc.astimezone(tz).strftime("%Y-%m")
        if await payroll_repo.is_store_month_closed(tenant_id, open_session.store_id, local_month):
            return (event_list, False)
        if (
            employee.primary_store_id != open_session.store_id
            and await payroll_repo.is_store_month_closed(
                tenant_id, employee.primary_store_id, local_month
            )
        ):
            return (event_list, False)

    recorder_id = employee.user_id or actor_user_id
    if recorder_id is None:
        return (event_list, False)

    source_ids = set(open_session.source_event_ids)
    last_work_mode = WorkMode.ONSITE
    for ev in reversed(event_list):
        if ev.id in source_ids:
            last_work_mode = ev.work_mode
            break

    candidate = AttendanceEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=open_session.store_id,
        employee_id=employee.id,
        event_type=AttendanceEventType.BREAK_END,
        occurred_at_utc=due_end_utc,
        store_timezone=open_session.store_timezone,
        source=AttendanceSource.WEB,
        recorded_by_user_id=recorder_id,
        recorded_at_utc=rec_norm,
        work_mode=last_work_mode,
        correction_reason=f"auto_break_end:{duration_label}m",
        retention_until=compute_attendance_retention_until(due_end_utc),
    )
    created = await attendance_repo.append(candidate)
    return ([*event_list, created], True)


class ClockAttendanceUseCase:
    def __init__(
        self,
        attendance_repo: AttendanceRepository,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        *,
        utc_now: Callable[[], datetime] | None = None,
        payroll_repo: PayrollClosingRepository | None = None,
        policy_repo: BreakPolicyRepository | None = None,
    ) -> None:
        self._attendance_repo = attendance_repo
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._utc_now = utc_now or (lambda: datetime.now(UTC))
        self._payroll_repo = payroll_repo
        self._policy_repo = policy_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        command: ClockAttendanceCommand,
    ) -> ClockAttendanceResult:
        if command.event_type not in RAW_PUNCH_TYPES:
            raise ValidationDomainError(
                "Clock endpoint only accepts clock_in, break_start, break_end, or clock_out"
            )
        if principal.role == UserRole.ACCOUNTANT:
            raise ForbiddenScopeError("Accountant role is read-only and cannot clock attendance")

        if command.source == AttendanceSource.WEB:
            employees = await self._employee_repo.list_by_tenant(
                principal.tenant_id,
                only_user_id=principal.user_id,
            )
            if not employees:
                raise EntityNotFoundError("No employee profile is linked to the current user")
            employee = employees[0]
            target_store_id = command.store_id or employee.primary_store_id
            if principal.role == UserRole.STORE_MANAGER:
                principal.require_store_access(principal.tenant_id, target_store_id)
            elif (
                principal.role == UserRole.EMPLOYEE
                and target_store_id != employee.primary_store_id
            ):
                raise ForbiddenScopeError(
                    "Employees may only clock attendance at their assigned primary store"
                )
        elif command.source == AttendanceSource.KIOSK:
            if principal.role not in {UserRole.OWNER, UserRole.HR, UserRole.STORE_MANAGER}:
                raise ForbiddenScopeError(
                    "Only owner, HR, or authorized store_manager may operate store kiosk"
                )
            if command.store_id is None:
                raise ValidationDomainError("store_id is required for kiosk clock-in")
            if not command.employee_code or not command.employee_code.strip():
                raise ValidationDomainError("employee_code is required for kiosk clock-in")

            if principal.role == UserRole.STORE_MANAGER:
                principal.require_store_access(principal.tenant_id, command.store_id)

            # Cross-store kiosk: the tablet's store only decides where the punch is recorded;
            # the employee is matched by employee_code across the whole tenant.
            tablet_store = await self._store_repo.get_by_id(
                principal.tenant_id, command.store_id
            )
            if tablet_store is None or not tablet_store.is_active:
                raise EntityNotFoundError("Kiosk store not found or inactive")

            normalized_code = normalize_employee_code(command.employee_code)
            tenant_employees = await self._employee_repo.list_by_tenant(principal.tenant_id)
            matched = next(
                (
                    emp
                    for emp in tenant_employees
                    if emp.employee_code.upper() == normalized_code and emp.deleted_at is None
                ),
                None,
            )
            if matched is None:
                raise EntityNotFoundError(f"Employee '{normalized_code}' was not found")
            if matched.employment_status not in CLOCKABLE_STATUSES:
                raise FrozenRecordError(
                    f"Employee with status '{matched.employment_status.value}' "
                    "cannot clock attendance"
                )
            if matched.kiosk_pin_hash is not None and not verify_kiosk_pin(
                command.kiosk_pin, matched.kiosk_pin_hash
            ):
                # 403 (not 401) so the SPA does not treat a wrong PIN as an expired session.
                raise ForbiddenScopeError("Invalid or missing kiosk PIN")
            employee = matched
            target_store_id = command.store_id
        else:
            raise ValidationDomainError(
                "Clock endpoint only supports 'web' or 'kiosk' attendance sources"
            )

        if employee.employment_status not in CLOCKABLE_STATUSES or employee.deleted_at is not None:
            raise FrozenRecordError(
                f"Employee with status '{employee.employment_status.value}' cannot clock attendance"
            )

        store = await self._store_repo.get_by_id(principal.tenant_id, target_store_id)
        if store is None or not store.is_active:
            raise EntityNotFoundError("Target store not found or inactive")

        now_utc = _ensure_utc(self._utc_now())
        if command.occurred_at_utc is not None:
            occurred_at = _ensure_utc(command.occurred_at_utc)
            if occurred_at > now_utc + timedelta(minutes=5):
                raise ValidationDomainError("Cannot record attendance in the future")
            if occurred_at < now_utc - timedelta(days=31):
                raise ValidationDomainError(
                    "Invalid time range: cannot record attendance older than 31 days"
                )
        else:
            occurred_at = now_utc

        local_month = occurred_at.astimezone(resolve_timezone(store.timezone)).strftime("%Y-%m")
        direct_checks = {
            (store.id, local_month),
            (employee.primary_store_id, local_month),
        }
        await _check_store_months_open(
            self._payroll_repo,
            tenant_id=principal.tenant_id,
            checks=direct_checks,
        )

        existing_events = await self._attendance_repo.list_by_employee(
            principal.tenant_id,
            employee.id,
        )
        eff_policy = None
        if self._policy_repo is not None:
            lookup = build_break_policy_lookup(
                await self._policy_repo.list_by_tenant(principal.tenant_id)
            )
            eff_policy = resolve_policy_for_employee(employee, lookup)
        existing_events, _ = await auto_complete_elapsed_break_if_due(
            tenant_id=principal.tenant_id,
            employee=employee,
            events=existing_events,
            now_utc=occurred_at,
            recorded_at_utc=now_utc,
            attendance_repo=self._attendance_repo,
            store_repo=self._store_repo,
            payroll_repo=self._payroll_repo,
            actor_user_id=principal.user_id,
            effective_policy=eff_policy,
        )
        latest_ts = latest_effective_punch_utc(existing_events)
        if latest_ts is not None:
            if command.occurred_at_utc is not None and occurred_at <= latest_ts:
                raise ValidationDomainError(
                    "Chronology error: occurred_at_utc must be after previous effective event"
                )
            if occurred_at < latest_ts:
                raise ValidationDomainError(
                    "Chronology error: occurred_at_utc cannot be before previous effective event"
                )
        candidate = AttendanceEvent(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            store_id=store.id,
            employee_id=employee.id,
            event_type=command.event_type,
            occurred_at_utc=occurred_at,
            store_timezone=store.timezone,
            source=command.source,
            recorded_by_user_id=principal.user_id,
            recorded_at_utc=now_utc,
            work_mode=command.work_mode,
            retention_until=compute_attendance_retention_until(occurred_at),
        )
        before_sessions = fold_attendance_events(employee.id, existing_events)
        after_sessions = fold_attendance_events(employee.id, [*existing_events, candidate])
        await _enforce_closed_month_session_diff(
            self._payroll_repo,
            tenant_id=principal.tenant_id,
            primary_store_id=employee.primary_store_id,
            before_sessions=before_sessions,
            after_sessions=after_sessions,
            already_checked=direct_checks,
        )

        created = await self._attendance_repo.append(candidate)
        return ClockAttendanceResult(event=created, employee=employee, store=store)


class RecordManualDayUseCase:
    def __init__(
        self,
        attendance_repo: AttendanceRepository,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        *,
        utc_now: Callable[[], datetime] | None = None,
        payroll_repo: PayrollClosingRepository | None = None,
        policy_repo: BreakPolicyRepository | None = None,
    ) -> None:
        self._attendance_repo = attendance_repo
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._utc_now = utc_now or (lambda: datetime.now(UTC))
        self._payroll_repo = payroll_repo
        self._policy_repo = policy_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        command: RecordManualDayCommand,
    ) -> RecordManualDayResult:
        if principal.role == UserRole.ACCOUNTANT:
            raise ForbiddenScopeError(
                "Accountant role is read-only and cannot record attendance"
            )

        if principal.role == UserRole.EMPLOYEE:
            employees = await self._employee_repo.list_by_tenant(
                principal.tenant_id,
                only_user_id=principal.user_id,
            )
            if not employees:
                raise EntityNotFoundError("No employee profile is linked to the current user")
            employee = employees[0]
            if command.employee_id is not None and command.employee_id != employee.id:
                raise ForbiddenScopeError(
                    "Employees may only record manual attendance for themselves"
                )
        elif command.employee_id is None:
            employees = await self._employee_repo.list_by_tenant(
                principal.tenant_id,
                only_user_id=principal.user_id,
            )
            if not employees:
                raise EntityNotFoundError("No employee profile is linked to the current user")
            employee = employees[0]
        else:
            loaded = await self._employee_repo.get_by_id(
                principal.tenant_id, command.employee_id
            )
            if loaded is None:
                raise EntityNotFoundError("Employee not found")
            if principal.role == UserRole.STORE_MANAGER:
                principal.require_store_access(principal.tenant_id, loaded.primary_store_id)
                principal.require_employee_in_scope(loaded)
            employee = loaded

        if employee.employment_status not in CLOCKABLE_STATUSES or employee.deleted_at is not None:
            raise FrozenRecordError(
                f"Employee with status '{employee.employment_status.value}' "
                "cannot record attendance"
            )

        store = await self._store_repo.get_by_id(
            principal.tenant_id, employee.primary_store_id
        )
        if store is None or not store.is_active:
            raise EntityNotFoundError("Target store not found or inactive")

        now_utc = _ensure_utc(self._utc_now())
        sequence = validate_and_build_manual_day_timestamps(
            local_date=command.local_date,
            clock_in_time=command.clock_in_time,
            break_start_time=command.break_start_time,
            break_end_time=command.break_end_time,
            clock_out_time=command.clock_out_time,
            store_timezone=store.timezone,
            now_utc=now_utc,
        )

        # Overnight shifts may spill into the next month: lock-check every touched local month.
        store_tz = resolve_timezone(store.timezone)
        touched_months = {command.local_date.strftime("%Y-%m")} | {
            ts.astimezone(store_tz).strftime("%Y-%m") for _, ts in sequence
        }
        direct_checks = {(store.id, month) for month in touched_months}
        await _check_store_months_open(
            self._payroll_repo,
            tenant_id=principal.tenant_id,
            checks=direct_checks,
        )

        existing_events = await self._attendance_repo.list_by_employee(
            principal.tenant_id,
            employee.id,
        )
        eff_policy = None
        if self._policy_repo is not None:
            lookup = build_break_policy_lookup(
                await self._policy_repo.list_by_tenant(principal.tenant_id)
            )
            eff_policy = resolve_policy_for_employee(employee, lookup)
        existing_events, _ = await auto_complete_elapsed_break_if_due(
            tenant_id=principal.tenant_id,
            employee=employee,
            events=existing_events,
            now_utc=now_utc,
            attendance_repo=self._attendance_repo,
            store_repo=self._store_repo,
            payroll_repo=self._payroll_repo,
            actor_user_id=principal.user_id,
            effective_policy=eff_policy,
        )
        before_sessions = fold_attendance_events(employee.id, existing_events)
        new_start_utc = sequence[0][1]
        new_end_utc = (
            sequence[-1][1]
            if sequence[-1][0] == AttendanceEventType.CLOCK_OUT
            else None
        )
        check_manual_day_session_overlap(before_sessions, new_start_utc, new_end_utc)

        note_text = command.note.strip()[:300]
        new_events = [
            AttendanceEvent(
                id=uuid4(),
                tenant_id=principal.tenant_id,
                store_id=store.id,
                employee_id=employee.id,
                event_type=ev_type,
                occurred_at_utc=ts_utc,
                store_timezone=store.timezone,
                source=AttendanceSource.WEB,
                recorded_by_user_id=principal.user_id,
                recorded_at_utc=now_utc,
                retention_until=compute_attendance_retention_until(ts_utc),
                correction_reason=note_text,
                work_mode=command.work_mode,
            )
            for ev_type, ts_utc in sequence
        ]

        after_sessions = fold_attendance_events(
            employee.id, [*existing_events, *new_events]
        )
        await _enforce_closed_month_session_diff(
            self._payroll_repo,
            tenant_id=principal.tenant_id,
            primary_store_id=employee.primary_store_id,
            before_sessions=before_sessions,
            after_sessions=after_sessions,
            already_checked=direct_checks,
        )

        created_events: list[AttendanceEvent] = []
        for candidate in new_events:
            created_events.append(await self._attendance_repo.append(candidate))

        return RecordManualDayResult(
            events=tuple(created_events),
            employee=employee,
            store=store,
        )


class AppendCorrectionUseCase:
    def __init__(
        self,
        attendance_repo: AttendanceRepository,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        *,
        payroll_repo: PayrollClosingRepository | None = None,
    ) -> None:
        self._attendance_repo = attendance_repo
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._payroll_repo = payroll_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        command: AppendCorrectionCommand,
    ) -> AttendanceEvent:
        if principal.role not in {UserRole.OWNER, UserRole.HR, UserRole.STORE_MANAGER}:
            raise ForbiddenScopeError(
                "Only owner, HR, or authorized store_manager may append attendance corrections"
            )

        reason = validate_non_empty_text(
            command.correction_reason,
            field="correction_reason",
            max_length=300,
        )
        occurred_at = _ensure_utc(command.occurred_at_utc)

        employee = await self._employee_repo.get_by_id(principal.tenant_id, command.employee_id)
        if employee is None:
            raise EntityNotFoundError("Employee not found")

        target_store_id = command.store_id or employee.primary_store_id
        if principal.role == UserRole.STORE_MANAGER:
            principal.require_store_access(principal.tenant_id, employee.primary_store_id)
            principal.require_store_access(principal.tenant_id, target_store_id)
            if (
                employee.employment_status not in MANAGER_VISIBLE_STATUSES
                or employee.deleted_at is not None
            ):
                raise ForbiddenScopeError(
                    "Frozen or anonymized employee records are blocked from store manager access "
                    "(Art. 32 LOPDGDD)"
                )
            principal.require_employee_in_scope(employee)

        if employee.employment_status not in CLOCKABLE_STATUSES or employee.deleted_at is not None:
            raise FrozenRecordError(
                f"Employee with status '{employee.employment_status.value}' cannot be corrected "
                "(Art. 32 LOPDGDD)"
            )

        store = await self._store_repo.get_by_id(principal.tenant_id, target_store_id)
        if store is None:
            raise EntityNotFoundError("Store not found")

        extra_store_months: list[tuple[UUID, str]] = []
        if command.supersedes_event_id is not None:
            superseded = await self._attendance_repo.get_by_id(
                principal.tenant_id,
                command.supersedes_event_id,
            )
            if superseded is None or superseded.employee_id != employee.id:
                raise EntityNotFoundError(
                    "Superseded attendance event not found for this employee"
                )
            if principal.role == UserRole.STORE_MANAGER:
                principal.require_store_access(principal.tenant_id, superseded.store_id)
            superseded_month = superseded.occurred_at_utc.astimezone(
                resolve_timezone(superseded.store_timezone)
            ).strftime("%Y-%m")
            extra_store_months.append((superseded.store_id, superseded_month))
            extra_store_months.append((employee.primary_store_id, superseded_month))

        occurred_month = occurred_at.astimezone(resolve_timezone(store.timezone)).strftime("%Y-%m")
        direct_checks = {
            (store.id, occurred_month),
            (employee.primary_store_id, occurred_month),
            *extra_store_months,
        }
        await _check_store_months_open(
            self._payroll_repo,
            tenant_id=principal.tenant_id,
            checks=direct_checks,
        )

        existing_events = await self._attendance_repo.list_by_employee(
            principal.tenant_id,
            employee.id,
        )
        now_utc = datetime.now(UTC)
        candidate = AttendanceEvent(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            store_id=store.id,
            employee_id=employee.id,
            event_type=AttendanceEventType.CORRECTION,
            occurred_at_utc=occurred_at,
            store_timezone=store.timezone,
            source=AttendanceSource.MANAGER_CORRECTION,
            recorded_by_user_id=principal.user_id,
            recorded_at_utc=now_utc,
            retention_until=compute_attendance_retention_until(occurred_at),
            supersedes_event_id=command.supersedes_event_id,
            corrected_event_type=command.corrected_event_type,
            correction_reason=reason,
        )
        before_sessions = fold_attendance_events(employee.id, existing_events)
        after_sessions = fold_attendance_events(employee.id, [*existing_events, candidate])
        await _enforce_closed_month_session_diff(
            self._payroll_repo,
            tenant_id=principal.tenant_id,
            primary_store_id=employee.primary_store_id,
            before_sessions=before_sessions,
            after_sessions=after_sessions,
            already_checked=direct_checks,
        )
        return await self._attendance_repo.append(candidate)


class GetAttendanceLedgerUseCase:
    def __init__(
        self,
        attendance_repo: AttendanceRepository,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        *,
        utc_now: Callable[[], datetime] | None = None,
        payroll_repo: PayrollClosingRepository | None = None,
        policy_repo: BreakPolicyRepository | None = None,
        shift_repo: ShiftRepository | None = None,
        day_review_repo: AttendanceDayReviewRepository | None = None,
    ) -> None:
        self._attendance_repo = attendance_repo
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._utc_now = utc_now or (lambda: datetime.now(UTC))
        self._payroll_repo = payroll_repo
        self._policy_repo = policy_repo
        self._shift_repo = shift_repo
        self._day_review_repo = day_review_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        store_id: UUID | None = None,
        employee_id: UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> AttendanceLedgerResult:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise ValidationDomainError("date_from cannot be after date_to")

        employees: list[Employee]
        if principal.role in TENANT_WIDE_ROLES:
            if employee_id is not None:
                emp = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
                if emp is None:
                    raise EntityNotFoundError("Employee not found")
                if store_id is not None and emp.primary_store_id != store_id:
                    employees = []
                else:
                    employees = [emp]
            else:
                employees = await self._employee_repo.list_by_tenant(
                    principal.tenant_id,
                    store_id=store_id,
                )
        elif principal.role == UserRole.STORE_MANAGER:
            if store_id is not None:
                principal.require_store_access(principal.tenant_id, store_id)
            if employee_id is not None:
                emp = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
                if emp is None:
                    raise EntityNotFoundError("Employee not found")
                principal.require_store_access(principal.tenant_id, emp.primary_store_id)
                if (
                    emp.employment_status not in MANAGER_VISIBLE_STATUSES
                    or emp.deleted_at is not None
                ):
                    raise ForbiddenScopeError(
                        "Frozen or anonymized employee records are blocked from operational access "
                        "(Art. 32 LOPDGDD)"
                    )
                principal.require_employee_in_scope(emp)
                if store_id is not None and emp.primary_store_id != store_id:
                    employees = []
                else:
                    employees = [emp]
            else:
                all_emps = await self._employee_repo.list_by_tenant(
                    principal.tenant_id,
                    allowed_store_ids=principal.store_ids,
                    allowed_statuses=MANAGER_VISIBLE_STATUSES,
                    store_id=store_id,
                )
                employees = [e for e in all_emps if principal.can_manage_employee(e)]
        else:
            # UserRole.EMPLOYEE: self only
            own_employees = await self._employee_repo.list_by_tenant(
                principal.tenant_id,
                only_user_id=principal.user_id,
            )
            if employee_id is not None:
                if not any(e.id == employee_id for e in own_employees):
                    raise ForbiddenScopeError("Employees may only view their own attendance ledger")
                employees = [e for e in own_employees if e.id == employee_id]
            else:
                employees = own_employees
            if store_id is not None:
                employees = [e for e in employees if e.primary_store_id == store_id]

        stores = await self._store_repo.list_by_tenant(
            principal.tenant_id,
            include_inactive=True,
        )
        store_map = {s.id: s for s in stores}

        target_event_employee_id = employee_id
        if principal.role == UserRole.EMPLOYEE and target_event_employee_id is None:
            if not employees:
                return AttendanceLedgerResult(items=())
            if len(employees) == 1:
                target_event_employee_id = employees[0].id

        allowed_emp_ids = {e.id for e in employees}
        all_events = (
            await self._attendance_repo.list_by_tenant(
                principal.tenant_id,
                employee_id=target_event_employee_id,
            )
            if allowed_emp_ids
            else []
        )
        events_by_emp: dict[UUID, list[AttendanceEvent]] = defaultdict(list)
        for ev in all_events:
            if ev.employee_id in allowed_emp_ids:
                events_by_emp[ev.employee_id].append(ev)

        now_utc = _ensure_utc(self._utc_now())
        policy_lookup = (
            build_break_policy_lookup(
                await self._policy_repo.list_by_tenant(principal.tenant_id)
            )
            if self._policy_repo is not None
            else None
        )
        has_mutations = False
        reviews_by_emp: dict[UUID, list[AttendanceDayReview]] = defaultdict(list)
        review_by_emp_date: dict[tuple[UUID, date], AttendanceDayReview] = {}
        if self._day_review_repo is not None and employees:
            all_reviews = await self._day_review_repo.list_by_tenant(
                principal.tenant_id,
                employee_ids=[e.id for e in employees],
                date_from=date_from,
                date_to=date_to,
            )
            for rev in all_reviews:
                reviews_by_emp[rev.employee_id].append(rev)
                review_by_emp_date[(rev.employee_id, rev.local_date)] = rev

        range_months: set[str] = set()
        if date_from is not None and date_to is not None:
            m_cursor = date_from.replace(day=1)
            end_m = date_to.replace(day=1)
            while m_cursor <= end_m:
                range_months.add(m_cursor.strftime("%Y-%m"))
                if m_cursor.month == 12:
                    m_cursor = date(m_cursor.year + 1, 1, 1)
                else:
                    m_cursor = date(m_cursor.year, m_cursor.month + 1, 1)

        items: list[EmployeeAttendanceLedgerItem] = []
        for emp in employees:
            store = store_map.get(emp.primary_store_id)
            if store is None:
                continue
            emp_events = events_by_emp.get(emp.id, [])
            eff_policy = (
                resolve_policy_for_employee(emp, policy_lookup)
                if policy_lookup is not None
                else None
            )
            emp_events, did_mutate = await auto_complete_elapsed_break_if_due(
                tenant_id=principal.tenant_id,
                employee=emp,
                events=emp_events,
                now_utc=now_utc,
                attendance_repo=self._attendance_repo,
                store_repo=self._store_repo,
                payroll_repo=self._payroll_repo,
                actor_user_id=principal.user_id,
                effective_policy=eff_policy,
            )
            if did_mutate:
                has_mutations = True
            report = evaluate_attendance_ledger(
                employee_id=emp.id,
                events=emp_events,
                birth_date=emp.birth_date,
                work_schedule_type=emp.work_schedule_type,
                contracted_weekly_hours=emp.contracted_weekly_hours,
                date_from=date_from,
                date_to=date_to,
                break_policy=eff_policy,
                now_utc=now_utc,
            )

            spanned_months = set(range_months)
            if not spanned_months:
                spanned_months = {s.local_date.strftime("%Y-%m") for s in report.daily_summaries}
                if date_from is not None:
                    spanned_months.add(date_from.strftime("%Y-%m"))
                if date_to is not None:
                    spanned_months.add(date_to.strftime("%Y-%m"))

            closed_by_store: dict[UUID, set[str]] = {}
            if self._payroll_repo is not None and spanned_months:
                month_list = sorted(spanned_months)
                store_ids_to_check = {emp.primary_store_id} | {s.store_id for s in report.sessions}
                for check_store_id in store_ids_to_check:
                    closed_by_store[check_store_id] = (
                        await self._payroll_repo.list_closed_months_for_store(
                            principal.tenant_id, check_store_id, month_list
                        )
                    )
            closed_months = tuple(sorted(closed_by_store.get(emp.primary_store_id, set())))

            shifts_by_date: dict[date, list[int]] = defaultdict(list)
            if self._shift_repo is not None and report.daily_summaries:
                min_dt = report.daily_summaries[0].local_date
                max_dt = report.daily_summaries[-1].local_date
                win_start = datetime.combine(min_dt - timedelta(days=1), time.min, tzinfo=UTC)
                win_end = datetime.combine(max_dt + timedelta(days=2), time.min, tzinfo=UTC)
                emp_shifts = await self._shift_repo.list_by_employee_window(
                    principal.tenant_id,
                    emp.id,
                    window_start_utc=win_start,
                    window_end_utc=win_end,
                )
                for sh in emp_shifts:
                    if not sh.is_active:
                        continue
                    if principal.role == UserRole.EMPLOYEE and sh.status != ShiftStatus.PUBLISHED:
                        continue
                    if (
                        principal.role == UserRole.STORE_MANAGER
                        and sh.store_id not in principal.store_ids
                    ):
                        continue
                    sh_store = store_map.get(sh.store_id) or store
                    sh_local_date = sh.start_at_utc.astimezone(
                        resolve_timezone(sh_store.timezone)
                    ).date()
                    shifts_by_date[sh_local_date].append(sh.net_minutes)

            contract_daily_mins = int(
                (emp.contracted_weekly_hours * Decimal(60) / Decimal(5)).to_integral_value()
            )
            updated_summaries = []
            for summary in report.daily_summaries:
                if summary.local_date in shifts_by_date:
                    planned_mins = sum(shifts_by_date[summary.local_date])
                else:
                    planned_mins = contract_daily_mins if summary.local_date.weekday() < 5 else 0
                day_rev = review_by_emp_date.get((emp.id, summary.local_date))
                summary_month = summary.local_date.strftime("%Y-%m")
                day_locked = summary_month in closed_by_store.get(
                    emp.primary_store_id, set()
                ) or any(
                    summary_month in closed_by_store.get(s.store_id, set())
                    for s in summary.sessions
                )
                updated_summaries.append(
                    replace(
                        summary,
                        planned_minutes=planned_mins,
                        is_locked=day_locked,
                        review_status=(day_rev.status if day_rev is not None else None),
                    )
                )
            report = replace(report, daily_summaries=tuple(updated_summaries))

            filtered_events = tuple(
                ev
                for ev in emp_events
                if (
                    date_from is None
                    or ev.occurred_at_utc.astimezone(
                        resolve_timezone(ev.store_timezone)
                    ).date() >= date_from
                )
                and (
                    date_to is None
                    or ev.occurred_at_utc.astimezone(
                        resolve_timezone(ev.store_timezone)
                    ).date() <= date_to
                )
            )
            items.append(
                EmployeeAttendanceLedgerItem(
                    employee=emp,
                    store=store,
                    report=report,
                    events=filtered_events,
                    effective_break_policy=eff_policy,
                    closed_months=closed_months,
                    day_reviews=tuple(reviews_by_emp.get(emp.id, ())),
                )
            )

        return AttendanceLedgerResult(items=tuple(items), has_mutations=has_mutations)


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")

_ALERT_LABELS_ES: dict[AlertCode, str] = {
    AlertCode.DAILY_MAX_EXCEEDED: "Exceso de jornada diaria (Art. 34.3 ET)",
    AlertCode.INTER_SHIFT_REST_VIOLATION: "Descanso entre jornadas < 12h (Art. 34.3 ET)",
    AlertCode.MISSING_CONTINUOUS_BREAK: "Falta pausa en jornada continuada (Art. 34.4 ET)",
    AlertCode.WEEKLY_OVERTIME: "Horas extra semanales (Art. 34.1 ET)",
    AlertCode.PART_TIME_OVERTIME_PROHIBITED: (
        "Horas extra prohibidas en tiempo parcial (Art. 12.4.c ET)"
    ),
    AlertCode.ANNUAL_OVERTIME_WARNING: "Aviso tope anual de horas extra (Art. 35.2 ET)",
    AlertCode.ANNUAL_OVERTIME_EXCEEDED: "Exceso tope anual de 80h extra (Art. 35.2 ET)",
}


def _sanitize_csv_cell(value: str) -> str:
    """Neutralize spreadsheet formula injection (CWE-1236) with a leading single quote."""
    stripped = value.lstrip(" \t\r\n")
    if value.startswith(_CSV_FORMULA_PREFIXES) or stripped.startswith(_CSV_FORMULA_PREFIXES):
        return f"'{value}"
    return value


class ExportAttendanceCsvUseCase:
    def __init__(
        self,
        ledger_use_case: GetAttendanceLedgerUseCase,
        tenant_repo: TenantRepository,
    ) -> None:
        self._ledger_use_case = ledger_use_case
        self._tenant_repo = tenant_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        store_id: UUID | None = None,
        employee_id: UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> str:
        tenant = await self._tenant_repo.get_by_id(principal.tenant_id)
        if tenant is None:
            raise EntityNotFoundError("Tenant not found")

        ledger = await self._ledger_use_case.execute(
            principal,
            store_id=store_id,
            employee_id=employee_id,
            date_from=date_from,
            date_to=date_to,
        )

        buffer = io.StringIO()
        # UTF-8 BOM for Spanish Excel compatibility
        buffer.write("\ufeff")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(
            [
                "Razón Social",
                "CIF",
                "Código Centro",
                "Nombre Centro",
                "Zona Horaria IANA",
                "Código Empleado",
                "Nombre Empleado",
                "Fecha Local",
                "Entrada Local",
                "Entrada UTC",
                "Salida Local",
                "Salida UTC",
                "Pausa (min)",
                "Horas Netas",
                "Con Correcciones",
                "Alertas Legales",
            ]
        )

        for item in ledger.items:
            alerts_by_date: dict[date, list[str]] = defaultdict(list)
            for alert in item.report.alerts:
                label_es = _ALERT_LABELS_ES.get(alert.code, alert.code.value)
                alerts_by_date[alert.local_date].append(f"[{alert.code.value}] {label_es}")

            for session in item.report.sessions:
                tz = resolve_timezone(session.store_timezone)
                in_local = session.clock_in_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
                in_utc = session.clock_in_utc.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
                if session.clock_out_utc is not None:
                    out_local = session.clock_out_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
                    out_utc = session.clock_out_utc.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
                else:
                    out_local = ""
                    out_utc = ""

                day_alerts = "; ".join(alerts_by_date.get(session.local_date, []))
                writer.writerow(
                    [
                        _sanitize_csv_cell(tenant.legal_name),
                        _sanitize_csv_cell(tenant.cif_nif),
                        _sanitize_csv_cell(item.store.code),
                        _sanitize_csv_cell(item.store.name),
                        _sanitize_csv_cell(session.store_timezone),
                        _sanitize_csv_cell(item.employee.employee_code),
                        _sanitize_csv_cell(item.employee.full_name),
                        _sanitize_csv_cell(session.local_date.isoformat()),
                        _sanitize_csv_cell(in_local),
                        _sanitize_csv_cell(in_utc),
                        _sanitize_csv_cell(out_local),
                        _sanitize_csv_cell(out_utc),
                        str(session.break_minutes),
                        f"{session.net_hours:.2f}",
                        "Sí" if session.has_corrections else "No",
                        _sanitize_csv_cell(day_alerts),
                    ]
                )

        return buffer.getvalue()


class BatchReviewAttendanceDaysUseCase:
    def __init__(
        self,
        attendance_repo: AttendanceRepository,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        day_review_repo: AttendanceDayReviewRepository,
        *,
        utc_now: Callable[[], datetime] | None = None,
        payroll_repo: PayrollClosingRepository | None = None,
        policy_repo: BreakPolicyRepository | None = None,
    ) -> None:
        self._attendance_repo = attendance_repo
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._day_review_repo = day_review_repo
        self._utc_now = utc_now or (lambda: datetime.now(UTC))
        self._payroll_repo = payroll_repo
        self._policy_repo = policy_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        command: BatchReviewAttendanceDaysCommand,
    ) -> list[AttendanceDayReview]:
        if principal.role == UserRole.ACCOUNTANT:
            raise ForbiddenScopeError(
                "Accountant role is read-only and cannot review attendance days"
            )

        emp = await self._employee_repo.get_by_id(principal.tenant_id, command.employee_id)
        if emp is None:
            raise EntityNotFoundError("Employee not found")

        if principal.role == UserRole.EMPLOYEE:
            if command.action != "submit" or emp.user_id != principal.user_id:
                raise ForbiddenScopeError(
                    "Employees may only submit their own attendance days for review"
                )
        elif principal.role == UserRole.STORE_MANAGER:
            if emp.user_id == principal.user_id and command.action == "approve":
                raise ForbiddenScopeError(
                    "Store managers cannot self-approve their own attendance days"
                )
            if not (emp.user_id == principal.user_id and command.action == "submit"):
                principal.require_store_access(principal.tenant_id, emp.primary_store_id)
                if (
                    emp.employment_status not in MANAGER_VISIBLE_STATUSES
                    or emp.deleted_at is not None
                ):
                    raise ForbiddenScopeError(
                        "Frozen or anonymized employee records are blocked from store manager "
                        "access (Art. 32 LOPDGDD)"
                    )
                principal.require_employee_in_scope(emp)
        elif principal.role not in {UserRole.OWNER, UserRole.HR}:
            raise ForbiddenScopeError("Insufficient permissions to review attendance days")

        if emp.employment_status not in CLOCKABLE_STATUSES or emp.deleted_at is not None:
            raise FrozenRecordError(
                f"Employee with status '{emp.employment_status.value}' cannot be reviewed"
            )

        unique_dates = tuple(dict.fromkeys(command.dates))
        if not (1 <= len(unique_dates) <= 31):
            raise ValidationDomainError("dates must contain between 1 and 31 unique dates")

        store = await self._store_repo.get_by_id(principal.tenant_id, emp.primary_store_id)
        if store is None:
            raise EntityNotFoundError("Store not found")

        now_utc = _ensure_utc(self._utc_now())
        store_local_today = now_utc.astimezone(resolve_timezone(store.timezone)).date()
        for dt in unique_dates:
            if dt > store_local_today:
                raise ValidationDomainError("Cannot review future attendance dates")

        emp_events = await self._attendance_repo.list_by_employee(principal.tenant_id, emp.id)
        eff_policy = None
        if self._policy_repo is not None:
            lookup = build_break_policy_lookup(
                await self._policy_repo.list_by_tenant(principal.tenant_id)
            )
            eff_policy = resolve_policy_for_employee(emp, lookup)

        report = evaluate_attendance_ledger(
            employee_id=emp.id,
            events=emp_events,
            birth_date=emp.birth_date,
            work_schedule_type=emp.work_schedule_type,
            contracted_weekly_hours=emp.contracted_weekly_hours,
            break_policy=eff_policy,
            now_utc=now_utc,
        )

        target_dates = set(unique_dates)
        checks: set[tuple[UUID, str]] = {
            (emp.primary_store_id, dt.strftime("%Y-%m")) for dt in unique_dates
        }
        for sess in report.sessions:
            if sess.local_date in target_dates:
                checks.add((sess.store_id, sess.local_date.strftime("%Y-%m")))
        await _check_store_months_open(
            self._payroll_repo,
            tenant_id=principal.tenant_id,
            checks=checks,
        )

        if command.action == "submit":
            existing_reviews = await self._day_review_repo.list_by_employee(
                principal.tenant_id,
                emp.id,
                date_from=min(unique_dates),
                date_to=max(unique_dates),
            )
            if any(
                r.status == AttendanceDayReviewStatus.APPROVED and r.local_date in target_dates
                for r in existing_reviews
            ):
                raise ConflictError("Cannot re-submit an already approved attendance day")

        if command.action == "approve":
            if any(sess.is_open for sess in report.sessions if sess.local_date in target_dates):
                raise ConflictError("Cannot approve attendance day with an open shift")
            if any(
                alert.severity == AlertSeverity.VIOLATION
                for alert in report.alerts
                if alert.local_date in target_dates
            ):
                raise ConflictError("Cannot approve attendance day with compliance violations")

        target_status = (
            AttendanceDayReviewStatus.SUBMITTED
            if command.action == "submit"
            else AttendanceDayReviewStatus.APPROVED
        )
        clean_note = command.note.strip()[:300]
        reviews = [
            AttendanceDayReview(
                id=uuid4(),
                tenant_id=principal.tenant_id,
                employee_id=emp.id,
                store_id=emp.primary_store_id,
                local_date=dt,
                status=target_status,
                reviewed_by_user_id=principal.user_id,
                reviewed_at_utc=now_utc,
                note=clean_note,
                created_at=now_utc,
                updated_at=now_utc,
            )
            for dt in unique_dates
        ]
        return await self._day_review_repo.upsert_many(reviews)


class ExportAttendancePdfUseCase:
    def __init__(
        self,
        ledger_use_case: GetAttendanceLedgerUseCase,
        tenant_repo: TenantRepository,
        pdf_renderer: AttendancePdfRendererPort,
        *,
        leave_repo: LeaveRepository | None = None,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        self._ledger_use_case = ledger_use_case
        self._tenant_repo = tenant_repo
        self._pdf_renderer = pdf_renderer
        self._leave_repo = leave_repo
        self._utc_now = utc_now or (lambda: datetime.now(UTC))

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        store_id: UUID | None = None,
        employee_id: UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[bytes, str, bool]:
        tenant = await self._tenant_repo.get_by_id(principal.tenant_id)
        if tenant is None:
            raise EntityNotFoundError("Tenant not found")

        if principal.role != UserRole.EMPLOYEE and employee_id is None:
            raise ValidationDomainError(
                "employee_id query parameter is required for attendance PDF export"
            )

        ledger = await self._ledger_use_case.execute(
            principal,
            store_id=store_id,
            employee_id=employee_id,
            date_from=date_from,
            date_to=date_to,
        )
        if not ledger.items:
            raise EntityNotFoundError("No attendance records found in scope")

        item = ledger.items[0]
        now_utc = _ensure_utc(self._utc_now())
        tz = resolve_timezone(item.store.timezone)

        redact_health = principal.role == UserRole.ACCOUNTANT and (
            item.employee.user_id != principal.user_id
            or item.employee.employment_status not in CLOCKABLE_STATUSES
            or item.employee.deleted_at is not None
        )

        if date_from is not None and date_to is not None:
            last_dom = calendar.monthrange(date_to.year, date_to.month)[1]
            if (
                date_from.day == 1
                and date_from.year == date_to.year
                and date_from.month == date_to.month
                and date_to.day == last_dom
            ):
                period_month = date_from.strftime("%Y-%m")
            else:
                period_month = f"{date_from.isoformat()}..{date_to.isoformat()}"
        elif date_from is not None:
            period_month = f"{date_from.isoformat()}.."
        elif date_to is not None:
            period_month = f"..{date_to.isoformat()}"
        else:
            period_month = now_utc.astimezone(tz).strftime("%Y-%m")

        vacation_days = 0
        sick_leave_days = 0
        paid_leave_days = 0
        unpaid_leave_days = 0
        if self._leave_repo is not None:
            if date_from is not None and date_to is not None:
                range_start, range_end = date_from, date_to
            elif item.report.daily_summaries:
                range_start = item.report.daily_summaries[0].local_date
                range_end = item.report.daily_summaries[-1].local_date
            else:
                today_local = now_utc.astimezone(tz).date()
                range_start = today_local.replace(day=1)
                range_end = today_local.replace(
                    day=calendar.monthrange(today_local.year, today_local.month)[1]
                )
            leaves = await self._leave_repo.list_by_employee(principal.tenant_id, item.employee.id)
            for lv in leaves:
                if lv.deleted_at is not None or lv.status != LeaveStatus.APPROVED:
                    continue
                ov_start = max(lv.start_date, range_start)
                ov_end = min(lv.end_date, range_end)
                if ov_end >= ov_start:
                    days = (ov_end - ov_start).days + 1
                    if lv.leave_type == LeaveType.VACATION:
                        vacation_days += days
                    elif lv.leave_type in {LeaveType.SICK_LEAVE, LeaveType.MEDICAL_APPOINTMENT}:
                        sick_leave_days += days
                    elif lv.leave_type == LeaveType.UNPAID_LEAVE:
                        unpaid_leave_days += days
                    else:
                        paid_leave_days += days

        alerts_by_date: dict[date, list[str]] = defaultdict(list)
        for alert in item.report.alerts:
            alerts_by_date[alert.local_date].append(alert.code.value)

        daily_rows = tuple(
            AttendancePdfDayRow(
                local_date=sess.local_date,
                clock_in_local=sess.clock_in_utc.astimezone(
                    resolve_timezone(sess.store_timezone)
                ).strftime("%H:%M"),
                clock_out_local=(
                    sess.clock_out_utc.astimezone(
                        resolve_timezone(sess.store_timezone)
                    ).strftime("%H:%M")
                    if sess.clock_out_utc is not None
                    else "Abierta"
                ),
                break_minutes=sess.break_minutes,
                net_hours=sess.net_hours,
                has_corrections=sess.has_corrections,
                notes=", ".join(alerts_by_date.get(sess.local_date, ())),
            )
            for sess in item.report.sessions
        )

        pdf_bytes = self._pdf_renderer.render_monthly_attendance_pdf(
            AttendancePdfDocumentInput(
                tenant_legal_name=tenant.legal_name,
                tenant_cif=tenant.cif_nif,
                store_code=item.store.code,
                store_name=item.store.name,
                store_timezone=item.store.timezone,
                period_month=period_month,
                revision_number=1,
                employee_full_name=item.employee.full_name,
                employee_code=item.employee.employee_code,
                nie_dni_masked=item.employee.nie_dni_masked,
                contract_type=item.employee.contract_type,
                work_schedule_type=item.employee.work_schedule_type,
                contracted_weekly_hours=item.employee.contracted_weekly_hours,
                worked_days=len({s.local_date for s in item.report.sessions if not s.is_open}),
                total_net_hours=item.report.total_net_hours,
                regular_hours=item.report.regular_hours,
                overtime_hours=item.report.overtime_hours,
                annual_overtime_hours=item.report.annual_overtime_hours,
                total_break_minutes=sum(
                    s.break_minutes for s in item.report.sessions if not s.is_open
                ),
                vacation_days=vacation_days,
                sick_leave_days=sick_leave_days,
                paid_leave_days=paid_leave_days,
                unpaid_leave_days=unpaid_leave_days,
                warning_alerts_count=sum(
                    1 for a in item.report.alerts if a.severity == AlertSeverity.WARNING
                ),
                violation_alerts_count=sum(
                    1 for a in item.report.alerts if a.severity == AlertSeverity.VIOLATION
                ),
                daily_rows=daily_rows,
                generated_at_utc=now_utc,
                redact_health=redact_health,
            )
        )
        safe_code = "".join(
            c if (c.isascii() and c.isalnum()) or c in "-_" else "_"
            for c in item.employee.employee_code
        )
        period_for_file = period_month.replace("..", "_to_")
        safe_period = "".join(
            c if (c.isascii() and c.isalnum()) or c in "-_." else "_"
            for c in period_for_file
        )
        filename = f"registro_jornada_{safe_code}_{safe_period}.pdf"
        return (pdf_bytes, filename, ledger.has_mutations)
