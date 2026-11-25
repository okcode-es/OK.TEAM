"""Pure domain logic for Annual Vacation & Leave Management (Art. 38 & Art. 37.3 ET).

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from app.domain.attendance import resolve_timezone
from app.domain.employees import STATUTORY_MIN_VACATION_DAYS, STATUTORY_RETENTION_YEARS, Employee
from app.domain.organization import ConflictError, ValidationDomainError
from app.domain.scheduling import Shift, shift_local_dates

MAX_LEAVE_SPAN_DAYS = 365


class LeaveType(StrEnum):
    VACATION = "vacation"
    SICK_LEAVE = "sick_leave"
    PAID_LEAVE = "paid_leave"
    UNPAID_LEAVE = "unpaid_leave"


class LeaveStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class LeaveRequest:
    id: UUID
    tenant_id: UUID
    store_id: UUID
    employee_id: UUID
    leave_type: LeaveType
    start_date: date
    end_date: date
    natural_days: int
    status: LeaveStatus
    reason: str
    requested_by_user_id: UUID
    reviewed_by_user_id: UUID | None
    reviewed_at_utc: datetime | None
    review_note: str
    medical_document_id: UUID | None
    retention_until: datetime
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class VacationBalance:
    employee_id: UUID
    year: int
    entitled_days: int
    approved_days: int
    pending_days: int
    remaining_days: int


def calculate_natural_days(start_date: date, end_date: date) -> int:
    """Calculate inclusive natural days (días naturales) for a leave request."""
    if end_date < start_date:
        raise ValidationDomainError("Leave end_date cannot be before start_date")
    days = (end_date - start_date).days + 1
    if days > MAX_LEAVE_SPAN_DAYS:
        raise ValidationDomainError(f"Leave request cannot exceed {MAX_LEAVE_SPAN_DAYS} days")
    return days


def split_days_by_year(start_date: date, end_date: date) -> dict[int, int]:
    """Split inclusive [start_date, end_date] natural days across calendar years."""
    calculate_natural_days(start_date, end_date)
    counts: dict[int, int] = {}
    cursor = start_date
    while cursor <= end_date:
        year_end = date(cursor.year, 12, 31)
        segment_end = min(end_date, year_end)
        counts[cursor.year] = (segment_end - cursor).days + 1
        cursor = segment_end + timedelta(days=1)
    return counts


def compute_leave_retention_until(end_date: date) -> datetime:
    """Compute statutory 4-year retention expiry from end_date UTC end-of-day."""
    anchor = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=UTC)
    target_year = anchor.year + STATUTORY_RETENTION_YEARS
    try:
        return anchor.replace(year=target_year)
    except ValueError:
        return anchor.replace(year=target_year, day=28)


def compute_vacation_balance(
    employee: Employee,
    requests: Sequence[LeaveRequest],
    year: int,
) -> VacationBalance:
    """Compute Art. 38 ET annual vacation balance (>= 30 natural days) for a calendar year."""
    entitled = max(employee.annual_vacation_days, STATUTORY_MIN_VACATION_DAYS)
    approved_days = 0
    pending_days = 0
    for req in requests:
        if req.deleted_at is not None or req.leave_type != LeaveType.VACATION:
            continue
        if req.status not in (LeaveStatus.APPROVED, LeaveStatus.PENDING):
            continue
        days_in_year = split_days_by_year(req.start_date, req.end_date).get(year, 0)
        if req.status == LeaveStatus.APPROVED:
            approved_days += days_in_year
        else:
            pending_days += days_in_year
    remaining = entitled - approved_days - pending_days
    return VacationBalance(
        employee_id=employee.id,
        year=year,
        entitled_days=entitled,
        approved_days=approved_days,
        pending_days=pending_days,
        remaining_days=remaining,
    )


def check_leave_overlap(
    *,
    start_date: date,
    end_date: date,
    existing_requests: Sequence[LeaveRequest],
    exclude_id: UUID | None = None,
) -> None:
    """Raise ConflictError if [start_date, end_date] overlaps any pending or approved leave."""
    for req in existing_requests:
        if req.deleted_at is not None:
            continue
        if exclude_id is not None and req.id == exclude_id:
            continue
        if req.status not in (LeaveStatus.PENDING, LeaveStatus.APPROVED):
            continue
        if start_date <= req.end_date and end_date >= req.start_date:
            raise ConflictError(
                f"Leave request overlaps existing {req.status.value} leave {req.id} "
                f"({req.start_date} - {req.end_date})"
            )


def validate_vacation_balance_for_request(
    *,
    employee: Employee,
    start_date: date,
    end_date: date,
    existing_requests: Sequence[LeaveRequest],
) -> None:
    """Ensure requested vacation days do not exceed remaining Art. 38 ET balance in any year."""
    per_year = split_days_by_year(start_date, end_date)
    for year, requested_days in per_year.items():
        balance = compute_vacation_balance(employee, existing_requests, year)
        if requested_days > balance.remaining_days:
            raise ValidationDomainError(
                f"Insufficient vacation balance for {year}: requested {requested_days} days, "
                f"remaining {balance.remaining_days} of {balance.entitled_days} natural days "
                "(Art. 38 ET)"
            )


def check_shift_leave_conflict(
    *,
    start_at_utc: datetime,
    end_at_utc: datetime,
    store_timezone: str,
    approved_leaves: Sequence[LeaveRequest],
) -> None:
    """Raise ConflictError if a shift touches any date covered by an approved leave."""
    touched_dates = shift_local_dates(start_at_utc, end_at_utc, store_timezone)
    for leave in approved_leaves:
        if leave.deleted_at is not None or leave.status != LeaveStatus.APPROVED:
            continue
        if any(leave.start_date <= d <= leave.end_date for d in touched_dates):
            raise ConflictError(
                f"Shift conflicts with approved {leave.leave_type.value} leave {leave.id} "
                f"({leave.start_date} - {leave.end_date})"
            )


def check_leave_published_shift_conflict(
    *,
    start_date: date,
    end_date: date,
    store_timezone: str,
    shifts: Sequence[Shift],
) -> None:
    """Raise ConflictError if approving a leave overlaps any non-cancelled published shift."""
    resolve_timezone(store_timezone)
    for shift in shifts:
        if not shift.is_active or shift.status.value != "published":
            continue
        touched = shift_local_dates(shift.start_at_utc, shift.end_at_utc, store_timezone)
        if any(start_date <= d <= end_date for d in touched):
            raise ConflictError(
                f"Cannot approve leave overlapping published shift {shift.id} "
                f"({shift.start_at_utc.isoformat()})"
            )


def validate_leave_transition(current: LeaveStatus, target: LeaveStatus) -> None:
    """Only pending -> approved | rejected | cancelled transitions are permitted."""
    if current != LeaveStatus.PENDING:
        raise ValidationDomainError(
            f"Cannot transition leave request from '{current.value}' to '{target.value}'"
        )
    if target not in (LeaveStatus.APPROVED, LeaveStatus.REJECTED, LeaveStatus.CANCELLED):
        raise ValidationDomainError(f"Invalid target leave status '{target.value}'")
