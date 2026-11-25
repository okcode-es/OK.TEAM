"""Pure domain entities, readiness evaluation, and RDL 8/2019 closing rules.

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.domain.attendance import (
    AlertSeverity,
    AttendanceEvent,
    evaluate_attendance_ledger,
    resolve_timezone,
)
from app.domain.employees import (
    STATUTORY_RETENTION_YEARS,
    ContractType,
    Employee,
    EmploymentStatus,
    WorkScheduleType,
)
from app.domain.leaves import LeaveRequest, LeaveStatus, LeaveType
from app.domain.organization import ValidationDomainError

_PERIOD_MONTH_PATTERN = re.compile(r"^([0-9]{4})-(0[1-9]|1[0-2])$")


class MonthlyClosingStatus(StrEnum):
    CLOSED = "closed"
    REOPENED = "reopened"


class ClosingBlockerCode(StrEnum):
    OPEN_SHIFT = "OPEN_SHIFT"
    PENDING_LEAVE = "PENDING_LEAVE"
    ALREADY_CLOSED = "ALREADY_CLOSED"


@dataclass(frozen=True, slots=True)
class MonthlyClosing:
    id: UUID
    tenant_id: UUID
    store_id: UUID
    period_month: str
    status: MonthlyClosingStatus
    revision_number: int
    closed_by_user_id: UUID
    closed_at_utc: datetime
    reopened_by_user_id: UUID | None
    reopened_at_utc: datetime | None
    reopen_reason: str
    notes: str
    retention_until: datetime
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MonthlyClosingItem:
    id: UUID
    tenant_id: UUID
    closing_id: UUID
    store_id: UUID
    period_month: str
    revision_number: int
    employee_id: UUID
    employee_code: str
    employee_full_name: str
    nie_dni_masked: str | None
    contract_type: ContractType
    work_schedule_type: WorkScheduleType
    contracted_weekly_hours: Decimal
    worked_days: int
    total_net_hours: Decimal
    regular_hours: Decimal
    overtime_hours: Decimal
    annual_overtime_hours: Decimal
    total_break_minutes: int
    vacation_days: int
    sick_leave_days: int
    paid_leave_days: int
    unpaid_leave_days: int
    warning_alerts_count: int
    violation_alerts_count: int
    attendance_report_document_id: UUID | None
    retention_until: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ClosingBlocker:
    code: ClosingBlockerCode
    employee_id: UUID | None
    employee_code: str | None
    employee_full_name: str | None
    reference_id: UUID | None
    local_date: date | None
    message: str


@dataclass(frozen=True, slots=True)
class AttendancePdfDayRow:
    local_date: date
    clock_in_local: str
    clock_out_local: str
    break_minutes: int
    net_hours: Decimal
    has_corrections: bool
    notes: str


@dataclass(frozen=True, slots=True)
class AttendancePdfDocumentInput:
    tenant_legal_name: str
    tenant_cif: str
    store_code: str
    store_name: str
    store_timezone: str
    period_month: str
    revision_number: int
    employee_full_name: str
    employee_code: str
    nie_dni_masked: str | None
    contract_type: ContractType
    work_schedule_type: WorkScheduleType
    contracted_weekly_hours: Decimal
    worked_days: int
    total_net_hours: Decimal
    regular_hours: Decimal
    overtime_hours: Decimal
    annual_overtime_hours: Decimal
    total_break_minutes: int
    vacation_days: int
    sick_leave_days: int
    paid_leave_days: int
    unpaid_leave_days: int
    warning_alerts_count: int
    violation_alerts_count: int
    daily_rows: tuple[AttendancePdfDayRow, ...]
    generated_at_utc: datetime


@dataclass(frozen=True, slots=True)
class MonthlyEmployeeSummary:
    employee_id: UUID
    employee_code: str
    employee_full_name: str
    nie_dni_masked: str | None
    contract_type: ContractType
    work_schedule_type: WorkScheduleType
    contracted_weekly_hours: Decimal
    worked_days: int
    total_net_hours: Decimal
    regular_hours: Decimal
    overtime_hours: Decimal
    annual_overtime_hours: Decimal
    total_break_minutes: int
    vacation_days: int
    sick_leave_days: int
    paid_leave_days: int
    unpaid_leave_days: int
    warning_alerts_count: int
    violation_alerts_count: int
    daily_rows: tuple[AttendancePdfDayRow, ...]


@dataclass(frozen=True, slots=True)
class MonthlyReadinessReport:
    store_id: UUID
    period_month: str
    month_start: date
    month_end: date
    existing_closing: MonthlyClosing | None
    can_close: bool
    blockers: tuple[ClosingBlocker, ...]
    employees: tuple[MonthlyEmployeeSummary, ...]


class AttendancePdfRendererPort(Protocol):
    def render_monthly_attendance_pdf(self, data: AttendancePdfDocumentInput) -> bytes: ...


def month_date_bounds(period_month: str) -> tuple[date, date]:
    """Validate YYYY-MM and return inclusive (month_start, month_end) dates."""
    cleaned = period_month.strip()
    match = _PERIOD_MONTH_PATTERN.match(cleaned)
    if not match:
        raise ValidationDomainError("period_month must match YYYY-MM format")
    year = int(match.group(1))
    month = int(match.group(2))
    _, last_day = calendar.monthrange(year, month)
    return date(year, month, 1), date(year, month, last_day)


def spanned_period_months(start_date: date, end_date: date) -> tuple[str, ...]:
    """Return ordered YYYY-MM strings spanned by an inclusive [start_date, end_date] range."""
    if end_date < start_date:
        raise ValidationDomainError("end_date cannot be before start_date")
    months: list[str] = []
    year = start_date.year
    month = start_date.month
    while (year, month) <= (end_date.year, end_date.month):
        months.append(f"{year:04d}-{month:02d}")
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return tuple(months)


def count_overlap_days(
    start_date: date,
    end_date: date,
    window_start: date,
    window_end: date,
) -> int:
    """Count inclusive overlapping calendar days between two date ranges."""
    overlap_start = max(start_date, window_start)
    overlap_end = min(end_date, window_end)
    if overlap_end < overlap_start:
        return 0
    return (overlap_end - overlap_start).days + 1


def should_include_employee_in_month(
    employee: Employee,
    *,
    month_start: date,
    month_end: date,
    has_events_in_month: bool,
    has_leaves_in_month: bool,
) -> bool:
    """Determine if an employee belongs in a store's monthly closing snapshot."""
    if employee.deleted_at is not None:
        return False
    if has_events_in_month or has_leaves_in_month:
        return True
    return (
        employee.employment_status in (EmploymentStatus.ACTIVE, EmploymentStatus.ON_LEAVE)
        and employee.hire_date <= month_end
        and (employee.termination_date is None or employee.termination_date >= month_start)
    )


def compute_closing_retention_until(period_month: str, now_utc: datetime) -> datetime:
    """Compute statutory >= 4-year retention expiry in UTC from month end or now."""
    _, month_end = month_date_bounds(period_month)
    month_end_eod = datetime(
        month_end.year,
        month_end.month,
        month_end.day,
        23,
        59,
        59,
        tzinfo=UTC,
    )
    anchor = max(month_end_eod, now_utc.astimezone(UTC))
    target_year = anchor.year + STATUTORY_RETENTION_YEARS
    try:
        return anchor.replace(year=target_year)
    except ValueError:
        return anchor.replace(year=target_year, day=28)


def evaluate_employee_monthly_summary(
    *,
    employee: Employee,
    events: list[AttendanceEvent],
    leaves: list[LeaveRequest],
    month_start: date,
    month_end: date,
    store_timezone: str,
) -> tuple[MonthlyEmployeeSummary, tuple[ClosingBlocker, ...]]:
    """Compute an employee's monthly attendance & leave summary plus hard blockers."""
    report = evaluate_attendance_ledger(
        employee_id=employee.id,
        events=events,
        work_schedule_type=employee.work_schedule_type,
        contracted_weekly_hours=employee.contracted_weekly_hours,
        birth_date=employee.birth_date,
        date_from=month_start,
        date_to=month_end,
    )

    blockers: list[ClosingBlocker] = []
    tz = resolve_timezone(store_timezone)

    daily_rows: list[AttendancePdfDayRow] = []
    for session in report.sessions:
        if session.is_open:
            blockers.append(
                ClosingBlocker(
                    code=ClosingBlockerCode.OPEN_SHIFT,
                    employee_id=employee.id,
                    employee_code=employee.employee_code,
                    employee_full_name=employee.full_name,
                    reference_id=session.source_event_ids[0] if session.source_event_ids else None,
                    local_date=session.local_date,
                    message=(
                        f"Open attendance session for {employee.employee_code} on "
                        f"{session.local_date.isoformat()} must be clocked out or corrected"
                    ),
                )
            )
        in_local = session.clock_in_utc.astimezone(tz).strftime("%H:%M:%S")
        out_local = (
            session.clock_out_utc.astimezone(tz).strftime("%H:%M:%S")
            if session.clock_out_utc is not None
            else "--:--:--"
        )
        session_tz = resolve_timezone(session.store_timezone)
        if session.store_timezone != store_timezone:
            in_local = session.clock_in_utc.astimezone(session_tz).strftime("%H:%M:%S")
            if session.clock_out_utc is not None:
                out_local = session.clock_out_utc.astimezone(session_tz).strftime("%H:%M:%S")
        notes = "Abierta" if session.is_open else ("Corregida" if session.has_corrections else "")
        daily_rows.append(
            AttendancePdfDayRow(
                local_date=session.local_date,
                clock_in_local=in_local,
                clock_out_local=out_local,
                break_minutes=session.break_minutes,
                net_hours=session.net_hours,
                has_corrections=session.has_corrections,
                notes=notes,
            )
        )

    vacation_days = 0
    sick_leave_days = 0
    paid_leave_days = 0
    unpaid_leave_days = 0

    for lv in leaves:
        overlap = count_overlap_days(lv.start_date, lv.end_date, month_start, month_end)
        if overlap <= 0:
            continue
        if lv.status == LeaveStatus.PENDING:
            blockers.append(
                ClosingBlocker(
                    code=ClosingBlockerCode.PENDING_LEAVE,
                    employee_id=employee.id,
                    employee_code=employee.employee_code,
                    employee_full_name=employee.full_name,
                    reference_id=lv.id,
                    local_date=lv.start_date,
                    message=(
                        f"Pending {lv.leave_type.value} request for {employee.employee_code} "
                        f"({lv.start_date.isoformat()}..{lv.end_date.isoformat()}) must be reviewed"
                    ),
                )
            )
        elif lv.status == LeaveStatus.APPROVED:
            if lv.leave_type == LeaveType.VACATION:
                vacation_days += overlap
            elif lv.leave_type == LeaveType.SICK_LEAVE:
                sick_leave_days += overlap
            elif lv.leave_type == LeaveType.PAID_LEAVE:
                paid_leave_days += overlap
            elif lv.leave_type == LeaveType.UNPAID_LEAVE:
                unpaid_leave_days += overlap

    worked_days = len({s.local_date for s in report.sessions if not s.is_open})
    total_break_minutes = sum(s.break_minutes for s in report.sessions)
    warning_count = sum(1 for a in report.alerts if a.severity == AlertSeverity.WARNING)
    violation_count = sum(1 for a in report.alerts if a.severity == AlertSeverity.VIOLATION)

    summary = MonthlyEmployeeSummary(
        employee_id=employee.id,
        employee_code=employee.employee_code,
        employee_full_name=employee.full_name,
        nie_dni_masked=employee.nie_dni_masked,
        contract_type=employee.contract_type,
        work_schedule_type=employee.work_schedule_type,
        contracted_weekly_hours=employee.contracted_weekly_hours,
        worked_days=worked_days,
        total_net_hours=report.total_net_hours,
        regular_hours=report.regular_hours,
        overtime_hours=report.overtime_hours,
        annual_overtime_hours=report.annual_overtime_hours,
        total_break_minutes=total_break_minutes,
        vacation_days=vacation_days,
        sick_leave_days=sick_leave_days,
        paid_leave_days=paid_leave_days,
        unpaid_leave_days=unpaid_leave_days,
        warning_alerts_count=warning_count,
        violation_alerts_count=violation_count,
        daily_rows=tuple(daily_rows),
    )
    return summary, tuple(blockers)
