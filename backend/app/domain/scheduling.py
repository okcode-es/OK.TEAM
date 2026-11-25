"""Pure domain logic for Store Shift Scheduling & Spanish Labor Law compliance.

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.domain.attendance import (
    ADULT_CONTINUOUS_THRESHOLD_MINUTES,
    ADULT_DAILY_MAX_HOURS,
    ADULT_REQUIRED_BREAK_MINUTES,
    MIN_INTER_SHIFT_REST_SECONDS,
    MINOR_CONTINUOUS_THRESHOLD_MINUTES,
    MINOR_DAILY_MAX_HOURS,
    MINOR_REQUIRED_BREAK_MINUTES,
    AlertSeverity,
    compute_attendance_retention_until,
    resolve_timezone,
)
from app.domain.employees import (
    STATUTORY_MAX_WEEKLY_HOURS,
    Employee,
    EmploymentStatus,
    WorkScheduleType,
)
from app.domain.organization import ConflictError, ValidationDomainError

STATUTORY_WEEKLY_REST_SECONDS = 36 * 3600  # Art. 37.1 ET: 1.5 days = 36 hours continuous rest
MAX_SHIFT_DURATION_SECONDS = 24 * 3600

_UNSCHEDULABLE_STATUSES: frozenset[EmploymentStatus] = frozenset(
    {
        EmploymentStatus.TERMINATED,
        EmploymentStatus.LEGAL_HOLD,
        EmploymentStatus.ANONYMIZED,
    }
)


class ShiftStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    CANCELLED = "cancelled"


class ScheduleAlertCode(StrEnum):
    DAILY_MAX_EXCEEDED = "DAILY_MAX_EXCEEDED"
    INTER_SHIFT_REST_VIOLATION = "INTER_SHIFT_REST_VIOLATION"
    MISSING_CONTINUOUS_BREAK = "MISSING_CONTINUOUS_BREAK"
    WEEKLY_HOURS_EXCEEDED = "WEEKLY_HOURS_EXCEEDED"
    PART_TIME_OVERTIME_PROHIBITED = "PART_TIME_OVERTIME_PROHIBITED"
    WEEKLY_REST_VIOLATION = "WEEKLY_REST_VIOLATION"


@dataclass(frozen=True, slots=True)
class Shift:
    id: UUID
    tenant_id: UUID
    store_id: UUID
    employee_id: UUID
    start_at_utc: datetime
    end_at_utc: datetime
    planned_break_minutes: int
    status: ShiftStatus
    notes: str
    created_by_user_id: UUID
    published_by_user_id: UUID | None
    published_at_utc: datetime | None
    retention_until: datetime
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status != ShiftStatus.CANCELLED and self.deleted_at is None

    @property
    def gross_minutes(self) -> int:
        return int((self.end_at_utc - self.start_at_utc).total_seconds() // 60)

    @property
    def net_minutes(self) -> int:
        return max(0, self.gross_minutes - self.planned_break_minutes)

    @property
    def net_hours(self) -> Decimal:
        return (Decimal(self.net_minutes) / Decimal(60)).quantize(Decimal("0.01"))


@dataclass(frozen=True, slots=True)
class ScheduleAlert:
    code: ScheduleAlertCode
    severity: AlertSeverity
    employee_id: UUID
    local_date: date
    message: str
    actual_value: Decimal | None = None
    limit_value: Decimal | None = None


def normalize_week_start(target_date: date) -> date:
    """Return the Monday of the ISO week containing target_date."""
    return target_date - timedelta(days=target_date.weekday())


def compute_shift_retention_until(end_at_utc: datetime) -> datetime:
    """Compute statutory 4-year retention expiry from shift end UTC."""
    return compute_attendance_retention_until(end_at_utc)


def validate_shift_bounds(
    start_at_utc: datetime,
    end_at_utc: datetime,
    planned_break_minutes: int,
) -> tuple[datetime, datetime, int]:
    """Validate shift timestamps and break minutes, returning UTC-normalized bounds."""
    if start_at_utc.tzinfo is None or end_at_utc.tzinfo is None:
        raise ValidationDomainError("Shift timestamps must be timezone-aware")
    start_utc = start_at_utc.astimezone(UTC)
    end_utc = end_at_utc.astimezone(UTC)
    if end_utc <= start_utc:
        raise ValidationDomainError("Shift end_at_utc must be strictly after start_at_utc")
    total_seconds = (end_utc - start_utc).total_seconds()
    if total_seconds > MAX_SHIFT_DURATION_SECONDS:
        raise ValidationDomainError("Shift duration cannot exceed 24 hours")
    if planned_break_minutes < 0:
        raise ValidationDomainError("planned_break_minutes cannot be negative")
    gross_minutes = int(total_seconds // 60)
    if planned_break_minutes >= gross_minutes:
        raise ValidationDomainError("planned_break_minutes must be less than total shift duration")
    return start_utc, end_utc, planned_break_minutes


def ensure_employee_schedulable(employee: Employee) -> None:
    """Reject scheduling for terminated, legal_hold, anonymized, or soft-deleted employees."""
    if employee.deleted_at is not None or employee.employment_status in _UNSCHEDULABLE_STATUSES:
        raise ValidationDomainError(
            f"Employee {employee.employee_code} is {employee.employment_status.value} "
            "and cannot be scheduled"
        )


def is_minor_on_date(birth_date: date | None, on_date: date) -> bool:
    if birth_date is None:
        return False
    years = on_date.year - birth_date.year
    if (on_date.month, on_date.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years < 18


def shift_local_dates(
    start_at_utc: datetime,
    end_at_utc: datetime,
    store_timezone: str,
) -> set[date]:
    """Return all store-local calendar dates touched by [start_at_utc, end_at_utc)."""
    tz = resolve_timezone(store_timezone)
    start_local = start_at_utc.astimezone(tz).date()
    end_local = (end_at_utc - timedelta(microseconds=1)).astimezone(tz).date()
    dates: set[date] = set()
    cursor = start_local
    while cursor <= end_local:
        dates.add(cursor)
        cursor += timedelta(days=1)
    return dates


def check_shift_overlap(
    *,
    candidate_id: UUID | None,
    start_at_utc: datetime,
    end_at_utc: datetime,
    existing_shifts: Sequence[Shift],
) -> None:
    """Raise ConflictError if candidate overlaps any active existing shift for the employee."""
    for shift in existing_shifts:
        if not shift.is_active:
            continue
        if candidate_id is not None and shift.id == candidate_id:
            continue
        if start_at_utc < shift.end_at_utc and end_at_utc > shift.start_at_utc:
            raise ConflictError(
                f"Shift overlaps existing shift {shift.id} "
                f"({shift.start_at_utc.isoformat()} - {shift.end_at_utc.isoformat()})"
            )


def week_utc_window(week_start: date, store_timezone: str) -> tuple[datetime, datetime]:
    """Return [Monday 00:00 local -> UTC, next Monday 00:00 local -> UTC) for a store."""
    tz = resolve_timezone(store_timezone)
    monday = normalize_week_start(week_start)
    next_monday = monday + timedelta(days=7)
    start_local = datetime(monday.year, monday.month, monday.day, 0, 0, 0, tzinfo=tz)
    end_local = datetime(next_monday.year, next_monday.month, next_monday.day, 0, 0, 0, tzinfo=tz)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def evaluate_employee_week_schedule(
    *,
    employee: Employee,
    week_start: date,
    store_timezone: str,
    shifts: Sequence[Shift],
    adjacent_shifts: Sequence[Shift] = (),
) -> tuple[Decimal, list[ScheduleAlert]]:
    """Evaluate weekly scheduled hours and all 6 Spanish labor law rules for an employee."""
    monday = normalize_week_start(week_start)
    tz = resolve_timezone(store_timezone)
    win_start_utc, win_end_utc = week_utc_window(monday, store_timezone)

    active_week_shifts = sorted(
        (
            s
            for s in shifts
            if s.is_active
            and s.start_at_utc < win_end_utc
            and s.end_at_utc > win_start_utc
        ),
        key=lambda s: (s.start_at_utc, s.end_at_utc, str(s.id)),
    )
    started_in_week_shifts = [
        s for s in active_week_shifts if win_start_utc <= s.start_at_utc < win_end_utc
    ]

    alerts: list[ScheduleAlert] = []
    total_net_minutes = 0
    daily_net_minutes: dict[date, int] = {}

    for shift in started_in_week_shifts:
        local_day = shift.start_at_utc.astimezone(tz).date()
        net_mins = shift.net_minutes
        total_net_minutes += net_mins
        daily_net_minutes[local_day] = daily_net_minutes.get(local_day, 0) + net_mins

        minor = is_minor_on_date(employee.birth_date, local_day)
        # Rule 3: Continuous work break check (Pausa por bocadillo, Art. 34.4 ET)
        threshold_mins = (
            MINOR_CONTINUOUS_THRESHOLD_MINUTES if minor else ADULT_CONTINUOUS_THRESHOLD_MINUTES
        )
        threshold_label = "4.5h" if minor else "6h"
        required_break = MINOR_REQUIRED_BREAK_MINUTES if minor else ADULT_REQUIRED_BREAK_MINUTES
        if shift.net_minutes > threshold_mins and shift.planned_break_minutes < required_break:
            alerts.append(
                ScheduleAlert(
                    code=ScheduleAlertCode.MISSING_CONTINUOUS_BREAK,
                    severity=AlertSeverity.VIOLATION,
                    employee_id=employee.id,
                    local_date=local_day,
                    message=(
                        f"Shift exceeds {threshold_label} without >= {required_break}m break "
                        f"(Art. 34.4 ET)"
                    ),
                    actual_value=Decimal(shift.planned_break_minutes),
                    limit_value=Decimal(required_break),
                )
            )

    # Rule 1: Daily max cap (9h adult, 8h minor)
    for local_day, mins in sorted(daily_net_minutes.items()):
        day_hours = (Decimal(mins) / Decimal(60)).quantize(Decimal("0.01"))
        minor = is_minor_on_date(employee.birth_date, local_day)
        daily_limit = MINOR_DAILY_MAX_HOURS if minor else ADULT_DAILY_MAX_HOURS
        if day_hours > daily_limit:
            alerts.append(
                ScheduleAlert(
                    code=ScheduleAlertCode.DAILY_MAX_EXCEEDED,
                    severity=AlertSeverity.VIOLATION,
                    employee_id=employee.id,
                    local_date=local_day,
                    message=(
                        f"Daily scheduled hours {day_hours}h exceed {daily_limit}h limit "
                        f"({'minor' if minor else 'adult'}, Art. 34.3 ET)"
                    ),
                    actual_value=day_hours,
                    limit_value=daily_limit,
                )
            )

    # Rule 2: 12h UTC inter-shift rest (Art. 34.3 ET) across week + adjacent shifts
    seen_ids: set[UUID] = set()
    all_relevant: list[Shift] = []
    for s in (*active_week_shifts, *adjacent_shifts):
        if s.is_active and s.id not in seen_ids:
            seen_ids.add(s.id)
            all_relevant.append(s)
    all_relevant.sort(key=lambda s: (s.start_at_utc, s.end_at_utc, str(s.id)))

    for prev_shift, next_shift in zip(all_relevant, all_relevant[1:], strict=False):
        if (
            next_shift.start_at_utc < win_start_utc
            or prev_shift.end_at_utc > win_end_utc
        ):
            continue
        gap_seconds = (next_shift.start_at_utc - prev_shift.end_at_utc).total_seconds()
        if gap_seconds < MIN_INTER_SHIFT_REST_SECONDS:
            gap_hours = (Decimal(str(gap_seconds)) / Decimal(3600)).quantize(Decimal("0.01"))
            alerts.append(
                ScheduleAlert(
                    code=ScheduleAlertCode.INTER_SHIFT_REST_VIOLATION,
                    severity=AlertSeverity.VIOLATION,
                    employee_id=employee.id,
                    local_date=next_shift.start_at_utc.astimezone(tz).date(),
                    message=(
                        f"Inter-shift rest {gap_hours}h is below statutory 12.00h minimum "
                        "(Art. 34.3 ET)"
                    ),
                    actual_value=gap_hours,
                    limit_value=Decimal("12.00"),
                )
            )

    weekly_hours = (Decimal(total_net_minutes) / Decimal(60)).quantize(Decimal("0.01"))

    # Rule 4 & 5: Weekly cap / Part-time overtime prohibition (Art. 12.4.c ET)
    if employee.work_schedule_type == WorkScheduleType.PART_TIME:
        if weekly_hours > employee.contracted_weekly_hours:
            alerts.append(
                ScheduleAlert(
                    code=ScheduleAlertCode.PART_TIME_OVERTIME_PROHIBITED,
                    severity=AlertSeverity.VIOLATION,
                    employee_id=employee.id,
                    local_date=monday,
                    message=(
                        f"Part-time scheduled hours {weekly_hours}h exceed contracted "
                        f"{employee.contracted_weekly_hours}h (Art. 12.4.c ET)"
                    ),
                    actual_value=weekly_hours,
                    limit_value=employee.contracted_weekly_hours,
                )
            )
    elif weekly_hours > STATUTORY_MAX_WEEKLY_HOURS:
        alerts.append(
            ScheduleAlert(
                code=ScheduleAlertCode.WEEKLY_HOURS_EXCEEDED,
                severity=AlertSeverity.VIOLATION,
                employee_id=employee.id,
                local_date=monday,
                message=(
                    f"Weekly scheduled hours {weekly_hours}h exceed 40.00h statutory cap "
                    "(Art. 34.1 ET)"
                ),
                actual_value=weekly_hours,
                limit_value=STATUTORY_MAX_WEEKLY_HOURS,
            )
        )

    # Rule 6: Art. 37.1 ET 36h (1.5-day) continuous weekly rest inside the 7-day window
    if active_week_shifts:
        clipped: list[tuple[datetime, datetime]] = []
        for s in active_week_shifts:
            c_start = max(s.start_at_utc, win_start_utc)
            c_end = min(s.end_at_utc, win_end_utc)
            if c_end > c_start:
                clipped.append((c_start, c_end))
        clipped.sort()
        max_rest_seconds = 0.0
        cursor = win_start_utc
        for c_start, c_end in clipped:
            if c_start > cursor:
                max_rest_seconds = max(max_rest_seconds, (c_start - cursor).total_seconds())
            cursor = max(cursor, c_end)
        if win_end_utc > cursor:
            max_rest_seconds = max(max_rest_seconds, (win_end_utc - cursor).total_seconds())

        if max_rest_seconds < STATUTORY_WEEKLY_REST_SECONDS:
            max_rest_hours = (Decimal(str(max_rest_seconds)) / Decimal(3600)).quantize(
                Decimal("0.01")
            )
            alerts.append(
                ScheduleAlert(
                    code=ScheduleAlertCode.WEEKLY_REST_VIOLATION,
                    severity=AlertSeverity.VIOLATION,
                    employee_id=employee.id,
                    local_date=monday,
                    message=(
                        f"Maximum continuous weekly rest {max_rest_hours}h is below 36.00h "
                        "(1.5 days, Art. 37.1 ET)"
                    ),
                    actual_value=max_rest_hours,
                    limit_value=Decimal("36.00"),
                )
            )

    return weekly_hours, alerts


def enforce_hard_schedule_blocks(
    *,
    employee: Employee,
    candidate_shift: Shift,
    store_timezone: str,
    week_shifts: Sequence[Shift],
) -> list[ScheduleAlert]:
    """Enforce hard rejections (minor > 8h, part-time OT Art. 12.4.c) and return alerts."""
    tz = resolve_timezone(store_timezone)
    local_day = candidate_shift.start_at_utc.astimezone(tz).date()
    week_start = normalize_week_start(local_day)
    win_start_utc, win_end_utc = week_utc_window(week_start, store_timezone)

    combined = [s for s in week_shifts if s.id != candidate_shift.id] + [candidate_shift]
    in_week = [
        s
        for s in combined
        if s.is_active and s.start_at_utc < win_end_utc and s.end_at_utc > win_start_utc
    ]
    in_week_ids = {s.id for s in in_week}
    outside_week = [s for s in combined if s.is_active and s.id not in in_week_ids]

    _, alerts = evaluate_employee_week_schedule(
        employee=employee,
        week_start=week_start,
        store_timezone=store_timezone,
        shifts=in_week,
        adjacent_shifts=outside_week,
    )

    for alert in alerts:
        if alert.code == ScheduleAlertCode.PART_TIME_OVERTIME_PROHIBITED:
            raise ValidationDomainError(alert.message)
        if alert.code == ScheduleAlertCode.DAILY_MAX_EXCEEDED and is_minor_on_date(
            employee.birth_date, alert.local_date
        ):
            raise ValidationDomainError(alert.message)

    return alerts
