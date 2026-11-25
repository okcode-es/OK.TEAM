"""Application use cases for Phase 3 Append-Only Attendance, Corrections, Ledger & ITSS CSV."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from app.domain.attendance import (
    AlertCode,
    AttendanceEvent,
    AttendanceEventType,
    AttendanceLedgerReport,
    AttendanceSource,
    CorrectedEventType,
    compute_attendance_retention_until,
    evaluate_attendance_ledger,
    fold_attendance_events,
    resolve_timezone,
)
from app.domain.employees import (
    Employee,
    EmploymentStatus,
    FrozenRecordError,
    normalize_employee_code,
)
from app.domain.organization import (
    EntityNotFoundError,
    Store,
    ValidationDomainError,
    validate_non_empty_text,
)
from app.domain.repositories import (
    AttendanceRepository,
    EmployeeRepository,
    StoreRepository,
    TenantRepository,
)
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


@dataclass(frozen=True, slots=True)
class ClockAttendanceResult:
    event: AttendanceEvent
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


@dataclass(frozen=True, slots=True)
class AttendanceLedgerResult:
    items: tuple[EmployeeAttendanceLedgerItem, ...]


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValidationDomainError("Datetime must include timezone information (UTC)")
    return dt.astimezone(UTC)


class ClockAttendanceUseCase:
    def __init__(
        self,
        attendance_repo: AttendanceRepository,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        *,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        self._attendance_repo = attendance_repo
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._utc_now = utc_now or (lambda: datetime.now(UTC))

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

            normalized_code = normalize_employee_code(command.employee_code)
            store_employees = await self._employee_repo.list_by_tenant(
                principal.tenant_id,
                store_id=command.store_id,
            )
            matched = next(
                (
                    emp
                    for emp in store_employees
                    if emp.employee_code.upper() == normalized_code and emp.deleted_at is None
                ),
                None,
            )
            if matched is None:
                raise EntityNotFoundError(
                    f"Employee '{normalized_code}' was not found in the selected store"
                )
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
        occurred_at = now_utc

        existing_events = await self._attendance_repo.list_by_employee(
            principal.tenant_id,
            employee.id,
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
            retention_until=compute_attendance_retention_until(occurred_at),
        )
        fold_attendance_events(employee.id, [*existing_events, candidate])

        created = await self._attendance_repo.append(candidate)
        return ClockAttendanceResult(event=created, employee=employee, store=store)


class AppendCorrectionUseCase:
    def __init__(
        self,
        attendance_repo: AttendanceRepository,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
    ) -> None:
        self._attendance_repo = attendance_repo
        self._employee_repo = employee_repo
        self._store_repo = store_repo

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

        if employee.employment_status not in CLOCKABLE_STATUSES or employee.deleted_at is not None:
            raise FrozenRecordError(
                f"Employee with status '{employee.employment_status.value}' cannot be corrected "
                "(Art. 32 LOPDGDD)"
            )

        store = await self._store_repo.get_by_id(principal.tenant_id, target_store_id)
        if store is None:
            raise EntityNotFoundError("Store not found")

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
        fold_attendance_events(employee.id, [*existing_events, candidate])
        return await self._attendance_repo.append(candidate)


class GetAttendanceLedgerUseCase:
    def __init__(
        self,
        attendance_repo: AttendanceRepository,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
    ) -> None:
        self._attendance_repo = attendance_repo
        self._employee_repo = employee_repo
        self._store_repo = store_repo

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
                if store_id is not None and emp.primary_store_id != store_id:
                    employees = []
                else:
                    employees = [emp]
            else:
                employees = await self._employee_repo.list_by_tenant(
                    principal.tenant_id,
                    allowed_store_ids=principal.store_ids,
                    allowed_statuses=MANAGER_VISIBLE_STATUSES,
                    store_id=store_id,
                )
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

        all_events = await self._attendance_repo.list_by_tenant(
            principal.tenant_id,
            allowed_store_ids=(
                principal.store_ids if principal.role == UserRole.STORE_MANAGER else None
            ),
            store_id=store_id,
            employee_id=target_event_employee_id,
        )
        events_by_emp: dict[UUID, list[AttendanceEvent]] = defaultdict(list)
        for ev in all_events:
            events_by_emp[ev.employee_id].append(ev)

        items: list[EmployeeAttendanceLedgerItem] = []
        for emp in employees:
            store = store_map.get(emp.primary_store_id)
            if store is None:
                continue
            emp_events = events_by_emp.get(emp.id, [])
            report = evaluate_attendance_ledger(
                employee_id=emp.id,
                events=emp_events,
                birth_date=emp.birth_date,
                work_schedule_type=emp.work_schedule_type,
                contracted_weekly_hours=emp.contracted_weekly_hours,
                date_from=date_from,
                date_to=date_to,
            )
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
                )
            )

        return AttendanceLedgerResult(items=tuple(items))


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

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
    stripped = value.lstrip(" \t\r")
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
