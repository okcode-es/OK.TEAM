"""Pure domain types and helpers for Admin & Employee Workspace Dashboards.

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from app.domain.attendance import (
    AttendanceEvent,
    AttendanceEventType,
    WorkMode,
    fold_attendance_events,
)


class WhoIsInStatus(StrEnum):
    WORKING = "working"
    REMOTE = "remote"
    ON_BREAK = "on_break"
    VACATION = "vacation"
    ABSENCE = "absence"
    ABSENTEEISM = "absenteeism"


@dataclass(frozen=True, slots=True)
class DashboardMemberItem:
    employee_id: UUID
    full_name: str
    preferred_name: str
    employee_code: str
    department: str
    store_name: str
    avatar_url: str | None
    since_utc: datetime | None = None
    job_title: str = ""


@dataclass(frozen=True, slots=True)
class WhoIsInGroup:
    status: WhoIsInStatus
    count: int
    preview_members: tuple[DashboardMemberItem, ...]
    overflow_count: int
    members: tuple[DashboardMemberItem, ...]


@dataclass(frozen=True, slots=True)
class UpcomingBirthdayItem:
    employee_id: UUID
    full_name: str
    preferred_name: str
    department: str
    store_name: str
    avatar_url: str | None
    birth_month: int
    birth_day: int
    days_until: int


@dataclass(frozen=True, slots=True)
class UpcomingAnniversaryItem:
    employee_id: UUID
    full_name: str
    preferred_name: str
    job_title: str
    department: str
    store_name: str
    avatar_url: str | None
    anniversary_month: int
    anniversary_day: int
    years_of_service: int
    days_until: int


@dataclass(frozen=True, slots=True)
class TodayClockSegment:
    kind: Literal["work", "break"]
    start_utc: datetime
    end_utc: datetime | None
    duration_minutes: int
    work_mode: WorkMode


@dataclass(frozen=True, slots=True)
class QuickReportEntry:
    key: str
    title_es: str
    title_zh: str
    endpoint: str
    available: bool


@dataclass(frozen=True, slots=True)
class DashboardAnnouncementSummary:
    id: UUID
    category: str
    title: str
    is_pinned: bool
    requires_acknowledgement: bool
    published_at_utc: datetime
    acknowledged_count: int
    target_count: int
    acknowledged_by_self: bool = False


def days_until_birthday(birth_date: date, today: date) -> int | None:
    """Compute days until next birthday in [0, 30] without exposing birth year."""
    month = birth_date.month
    day = birth_date.day
    for year_offset in (0, 1):
        target_year = today.year + year_offset
        try:
            candidate = date(target_year, month, day)
        except ValueError:
            # Feb 29 in non-leap year -> Feb 28
            candidate = date(target_year, 2, 28)
        delta = (candidate - today).days
        if 0 <= delta <= 30:
            return delta
    return None


def days_until_anniversary(hire_date: date, today: date) -> tuple[int, int] | None:
    """Compute (days_until, years_of_service) when next >=1y anniversary is in [0, 30] days."""
    month = hire_date.month
    day = hire_date.day
    for year_offset in (0, 1):
        target_year = today.year + year_offset
        years_of_service = target_year - hire_date.year
        if years_of_service < 1:
            continue
        try:
            candidate = date(target_year, month, day)
        except ValueError:
            # Feb 29 -> Feb 28 in non-leap year
            candidate = date(target_year, 2, 28)
        delta = (candidate - today).days
        if 0 <= delta <= 30:
            return (delta, years_of_service)
    return None


def build_today_clock_segments(
    employee_id: UUID,
    events: list[AttendanceEvent],
    local_today: date,
    now_utc: datetime,
) -> tuple[TodayClockSegment, ...]:
    """Derive today's chronological work/break segments from folded attendance events."""
    sessions = fold_attendance_events(employee_id, events)
    event_by_id = {ev.id: ev for ev in events}
    now = now_utc.astimezone(UTC)
    segments: list[TodayClockSegment] = []

    for session in sessions:
        if session.local_date != local_today and not session.is_open:
            continue
        seg_start: datetime | None = None
        in_break = False
        work_mode = WorkMode.ONSITE

        for eid in session.source_event_ids:
            ev = event_by_id.get(eid)
            if ev is None:
                continue
            punch_type = (
                AttendanceEventType(ev.corrected_event_type.value)
                if ev.event_type == AttendanceEventType.CORRECTION
                and ev.corrected_event_type is not None
                else ev.event_type
            )
            ts = ev.occurred_at_utc.astimezone(UTC)

            if punch_type == AttendanceEventType.CLOCK_IN:
                work_mode = ev.work_mode
                seg_start = ts
                in_break = False
            elif punch_type == AttendanceEventType.BREAK_START and seg_start is not None:
                segments.append(
                    TodayClockSegment(
                        kind="work",
                        start_utc=seg_start,
                        end_utc=ts,
                        duration_minutes=max(0, int((ts - seg_start).total_seconds()) // 60),
                        work_mode=work_mode,
                    )
                )
                seg_start = ts
                in_break = True
            elif punch_type == AttendanceEventType.BREAK_END and seg_start is not None:
                segments.append(
                    TodayClockSegment(
                        kind="break",
                        start_utc=seg_start,
                        end_utc=ts,
                        duration_minutes=max(0, int((ts - seg_start).total_seconds()) // 60),
                        work_mode=work_mode,
                    )
                )
                seg_start = ts
                in_break = False
            elif punch_type == AttendanceEventType.CLOCK_OUT and seg_start is not None:
                segments.append(
                    TodayClockSegment(
                        kind="work",
                        start_utc=seg_start,
                        end_utc=ts,
                        duration_minutes=max(0, int((ts - seg_start).total_seconds()) // 60),
                        work_mode=work_mode,
                    )
                )
                seg_start = None
                in_break = False

        if seg_start is not None:
            segments.append(
                TodayClockSegment(
                    kind="break" if in_break else "work",
                    start_utc=seg_start,
                    end_utc=None,
                    duration_minutes=max(0, int((now - seg_start).total_seconds()) // 60),
                    work_mode=work_mode,
                )
            )

    return tuple(segments)


def compute_target_daily_minutes(contracted_weekly_hours: Decimal) -> int:
    return int((contracted_weekly_hours * Decimal(60) / Decimal(5)).to_integral_value())
