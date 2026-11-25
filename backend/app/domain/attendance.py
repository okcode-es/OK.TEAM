"""Domain entities, ledger folding, state machine, and Spanish labor law engine.

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.employees import (
    STATUTORY_MAX_WEEKLY_HOURS,
    STATUTORY_RETENTION_YEARS,
    WorkScheduleType,
)
from app.domain.organization import ValidationDomainError

ADULT_DAILY_MAX_HOURS = Decimal("9.00")
MINOR_DAILY_MAX_HOURS = Decimal("8.00")
MIN_INTER_SHIFT_REST_SECONDS = 12 * 3600
ADULT_CONTINUOUS_THRESHOLD_MINUTES = 360  # 6.00h
ADULT_REQUIRED_BREAK_MINUTES = 15
MINOR_CONTINUOUS_THRESHOLD_MINUTES = 270  # 4.50h
MINOR_REQUIRED_BREAK_MINUTES = 30
ANNUAL_OVERTIME_WARNING_HOURS = Decimal("60.00")
ANNUAL_OVERTIME_MAX_HOURS = Decimal("80.00")


class AttendanceEventType(StrEnum):
    CLOCK_IN = "clock_in"
    BREAK_START = "break_start"
    BREAK_END = "break_end"
    CLOCK_OUT = "clock_out"
    CORRECTION = "correction"


class CorrectedEventType(StrEnum):
    CLOCK_IN = "clock_in"
    BREAK_START = "break_start"
    BREAK_END = "break_end"
    CLOCK_OUT = "clock_out"
    VOID = "void"


class AttendanceSource(StrEnum):
    WEB = "web"
    KIOSK = "kiosk"
    MANAGER_CORRECTION = "manager_correction"


class AlertSeverity(StrEnum):
    WARNING = "warning"
    VIOLATION = "violation"


class AlertCode(StrEnum):
    DAILY_MAX_EXCEEDED = "DAILY_MAX_EXCEEDED"
    INTER_SHIFT_REST_VIOLATION = "INTER_SHIFT_REST_VIOLATION"
    MISSING_CONTINUOUS_BREAK = "MISSING_CONTINUOUS_BREAK"
    WEEKLY_OVERTIME = "WEEKLY_OVERTIME"
    PART_TIME_OVERTIME_PROHIBITED = "PART_TIME_OVERTIME_PROHIBITED"
    ANNUAL_OVERTIME_WARNING = "ANNUAL_OVERTIME_WARNING"
    ANNUAL_OVERTIME_EXCEEDED = "ANNUAL_OVERTIME_EXCEEDED"


class _ShiftState(StrEnum):
    NOT_CLOCKED_IN = "not_clocked_in"
    WORKING = "working"
    ON_BREAK = "on_break"


@dataclass(frozen=True, slots=True)
class AttendanceEvent:
    id: UUID
    tenant_id: UUID
    store_id: UUID
    employee_id: UUID
    event_type: AttendanceEventType
    occurred_at_utc: datetime
    store_timezone: str
    source: AttendanceSource
    recorded_by_user_id: UUID
    recorded_at_utc: datetime
    retention_until: datetime
    supersedes_event_id: UUID | None = None
    corrected_event_type: CorrectedEventType | None = None
    correction_reason: str = ""


@dataclass(frozen=True, slots=True)
class ComplianceAlert:
    code: AlertCode
    severity: AlertSeverity
    employee_id: UUID
    local_date: date
    message: str
    actual_value: Decimal | None = None
    limit_value: Decimal | None = None


@dataclass(frozen=True, slots=True)
class WorkSession:
    employee_id: UUID
    store_id: UUID
    store_timezone: str
    local_date: date
    clock_in_utc: datetime
    clock_out_utc: datetime | None
    break_minutes: int
    net_minutes: int
    net_hours: Decimal
    has_corrections: bool
    is_open: bool
    has_open_break: bool
    source_event_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class DailyAttendanceSummary:
    employee_id: UUID
    store_id: UUID
    store_timezone: str
    local_date: date
    sessions: tuple[WorkSession, ...]
    total_break_minutes: int
    total_net_minutes: int
    total_net_hours: Decimal
    has_corrections: bool
    alerts: tuple[ComplianceAlert, ...]


@dataclass(frozen=True, slots=True)
class AttendanceLedgerReport:
    employee_id: UUID
    daily_summaries: tuple[DailyAttendanceSummary, ...]
    sessions: tuple[WorkSession, ...]
    total_net_hours: Decimal
    regular_hours: Decimal
    overtime_hours: Decimal
    annual_overtime_hours: Decimal
    alerts: tuple[ComplianceAlert, ...]


@dataclass(frozen=True, slots=True)
class _EffectivePunch:
    event_id: UUID
    store_id: UUID
    store_timezone: str
    punch_type: AttendanceEventType
    occurred_at_utc: datetime
    recorded_at_utc: datetime
    is_corrected: bool


def _last_sunday_of_month(year: int, month: int) -> date:
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    # weekday(): Monday=0 .. Sunday=6
    offset = (last_day.weekday() + 1) % 7
    return last_day - timedelta(days=offset)


class _SpainFallbackTz(tzinfo):
    """Pure-stdlib EU/Spain DST fallback with PEP-495 fold semantics when tzdata is absent."""

    def __init__(self, name: str, std_hours: int) -> None:
        self._name = name
        self._std = timedelta(hours=std_hours)
        self._dst = timedelta(hours=1)

    @staticmethod
    def _dst_bounds_utc(year: int) -> tuple[datetime, datetime]:
        spring_utc = datetime.combine(
            _last_sunday_of_month(year, 3), datetime.min.time()
        ) + timedelta(hours=1)
        autumn_utc = datetime.combine(
            _last_sunday_of_month(year, 10), datetime.min.time()
        ) + timedelta(hours=1)
        return spring_utc, autumn_utc

    def _is_utc_dst(self, utc_naive: datetime) -> bool:
        spring_utc, autumn_utc = self._dst_bounds_utc(utc_naive.year)
        return spring_utc <= utc_naive < autumn_utc

    def fromutc(self, dt: datetime) -> datetime:
        utc_naive = dt.replace(tzinfo=None)
        is_dst = self._is_utc_dst(utc_naive)
        offset = self._std + (self._dst if is_dst else timedelta(0))
        local_dt = (utc_naive + offset).replace(tzinfo=self)
        _, autumn_utc = self._dst_bounds_utc(utc_naive.year)
        if not is_dst and autumn_utc <= utc_naive < autumn_utc + self._dst:
            return local_dt.replace(fold=1)
        return local_dt

    def utcoffset(self, dt: datetime | None) -> timedelta:
        if dt is None:
            return self._std
        if dt.tzinfo is not self and dt.tzinfo is not None:
            utc_naive = dt.astimezone(UTC).replace(tzinfo=None)
            return self._std + (self._dst if self._is_utc_dst(utc_naive) else timedelta(0))
        local_naive = dt.replace(tzinfo=None)
        spring_utc, autumn_utc = self._dst_bounds_utc(local_naive.year)
        spring_local = spring_utc + self._std
        autumn_local_start = autumn_utc + self._std
        autumn_local_end = autumn_local_start + self._dst
        if local_naive < spring_local:
            is_dst = False
        elif local_naive < autumn_local_start:
            is_dst = True
        elif local_naive < autumn_local_end:
            is_dst = dt.fold == 0
        else:
            is_dst = False
        return self._std + (self._dst if is_dst else timedelta(0))

    def dst(self, dt: datetime | None) -> timedelta:
        return self.utcoffset(dt) - self._std

    def tzname(self, dt: datetime | None) -> str:
        return self._name


_FALLBACK_TZS: dict[str, tzinfo] = {
    "UTC": UTC,
    "Europe/Madrid": _SpainFallbackTz("Europe/Madrid", 1),
    "Africa/Ceuta": _SpainFallbackTz("Africa/Ceuta", 1),
    "Atlantic/Canary": _SpainFallbackTz("Atlantic/Canary", 0),
}


def resolve_timezone(tz_name: str) -> tzinfo:
    """Resolve IANA timezone via zoneinfo, with pure-stdlib Spain fallback if tzdata missing."""
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        if tz_name in _FALLBACK_TZS:
            return _FALLBACK_TZS[tz_name]
        raise ValidationDomainError(f"Invalid IANA timezone: '{tz_name}'") from exc


def compute_attendance_retention_until(occurred_at_utc: datetime) -> datetime:
    """Compute statutory 4-year retention expiry in UTC (leap-day safe, RDL 8/2019)."""
    utc_dt = occurred_at_utc.astimezone(UTC)
    target_year = utc_dt.year + STATUTORY_RETENTION_YEARS
    try:
        return utc_dt.replace(year=target_year)
    except ValueError:
        return utc_dt.replace(year=target_year, day=28)


def is_minor_on_date(birth_date: date | None, work_date: date) -> bool:
    """Return True when employee is strictly under 18 years old on work_date."""
    if birth_date is None:
        return False
    age = work_date.year - birth_date.year - (
        (work_date.month, work_date.day) < (birth_date.month, birth_date.day)
    )
    return age < 18


def _minutes_to_hours(minutes: int) -> Decimal:
    return (Decimal(minutes) / Decimal(60)).quantize(Decimal("0.01"))


def _resolve_effective_punches(events: list[AttendanceEvent]) -> list[_EffectivePunch]:
    """Resolve raw + correction events (including void) into chronological effective punches."""
    superseded_ids: set[UUID] = {
        e.supersedes_event_id
        for e in events
        if e.event_type == AttendanceEventType.CORRECTION and e.supersedes_event_id is not None
    }

    effective: list[_EffectivePunch] = []
    for event in events:
        if event.id in superseded_ids:
            continue

        if event.event_type == AttendanceEventType.CORRECTION:
            if event.corrected_event_type is None:
                raise ValidationDomainError(
                    "Correction event must specify corrected_event_type"
                )
            if event.corrected_event_type == CorrectedEventType.VOID:
                continue
            punch_type = AttendanceEventType(event.corrected_event_type.value)
            effective.append(
                _EffectivePunch(
                    event_id=event.id,
                    store_id=event.store_id,
                    store_timezone=event.store_timezone,
                    punch_type=punch_type,
                    occurred_at_utc=event.occurred_at_utc.astimezone(UTC),
                    recorded_at_utc=event.recorded_at_utc.astimezone(UTC),
                    is_corrected=True,
                )
            )
        else:
            effective.append(
                _EffectivePunch(
                    event_id=event.id,
                    store_id=event.store_id,
                    store_timezone=event.store_timezone,
                    punch_type=event.event_type,
                    occurred_at_utc=event.occurred_at_utc.astimezone(UTC),
                    recorded_at_utc=event.recorded_at_utc.astimezone(UTC),
                    is_corrected=False,
                )
            )

    effective.sort(key=lambda p: (p.occurred_at_utc, p.recorded_at_utc, str(p.event_id)))
    return effective


def fold_attendance_events(
    employee_id: UUID,
    events: list[AttendanceEvent],
) -> tuple[WorkSession, ...]:
    """Fold raw and correction events into chronological WorkSessions, enforcing state machine."""
    effective = _resolve_effective_punches(events)
    if not effective:
        return ()

    sessions: list[WorkSession] = []
    state = _ShiftState.NOT_CLOCKED_IN

    current_in: _EffectivePunch | None = None
    current_break_start: datetime | None = None
    break_seconds = 0
    has_corrections = False
    event_ids: list[UUID] = []

    for punch in effective:
        if punch.punch_type == AttendanceEventType.CLOCK_IN:
            if state != _ShiftState.NOT_CLOCKED_IN:
                raise ValidationDomainError(
                    "Invalid state transition: cannot clock_in while shift is already open"
                )
            state = _ShiftState.WORKING
            current_in = punch
            current_break_start = None
            break_seconds = 0
            has_corrections = punch.is_corrected
            event_ids = [punch.event_id]

        elif punch.punch_type == AttendanceEventType.BREAK_START:
            if state != _ShiftState.WORKING:
                raise ValidationDomainError(
                    "Invalid state transition: break_start requires an active working shift"
                )
            state = _ShiftState.ON_BREAK
            current_break_start = punch.occurred_at_utc
            has_corrections = has_corrections or punch.is_corrected
            event_ids.append(punch.event_id)

        elif punch.punch_type == AttendanceEventType.BREAK_END:
            if state != _ShiftState.ON_BREAK or current_break_start is None:
                raise ValidationDomainError(
                    "Invalid state transition: break_end requires an open break"
                )
            break_seconds += int((punch.occurred_at_utc - current_break_start).total_seconds())
            current_break_start = None
            state = _ShiftState.WORKING
            has_corrections = has_corrections or punch.is_corrected
            event_ids.append(punch.event_id)

        elif punch.punch_type == AttendanceEventType.CLOCK_OUT:
            if state == _ShiftState.ON_BREAK:
                raise ValidationDomainError(
                    "Invalid state transition: cannot clock_out while on an open break"
                )
            if state != _ShiftState.WORKING or current_in is None:
                raise ValidationDomainError(
                    "Invalid state transition: clock_out requires an active clock_in"
                )

            total_seconds = int(
                (punch.occurred_at_utc - current_in.occurred_at_utc).total_seconds()
            )
            net_seconds = max(0, total_seconds - break_seconds)
            break_mins = break_seconds // 60
            net_mins = net_seconds // 60
            local_dt = current_in.occurred_at_utc.astimezone(
                resolve_timezone(current_in.store_timezone)
            )
            has_corrections = has_corrections or punch.is_corrected
            event_ids.append(punch.event_id)

            sessions.append(
                WorkSession(
                    employee_id=employee_id,
                    store_id=current_in.store_id,
                    store_timezone=current_in.store_timezone,
                    local_date=local_dt.date(),
                    clock_in_utc=current_in.occurred_at_utc,
                    clock_out_utc=punch.occurred_at_utc,
                    break_minutes=break_mins,
                    net_minutes=net_mins,
                    net_hours=_minutes_to_hours(net_mins),
                    has_corrections=has_corrections,
                    is_open=False,
                    has_open_break=False,
                    source_event_ids=tuple(event_ids),
                )
            )
            state = _ShiftState.NOT_CLOCKED_IN
            current_in = None
            current_break_start = None
            break_seconds = 0
            has_corrections = False
            event_ids = []

    if state != _ShiftState.NOT_CLOCKED_IN and current_in is not None:
        local_dt = current_in.occurred_at_utc.astimezone(
            resolve_timezone(current_in.store_timezone)
        )
        sessions.append(
            WorkSession(
                employee_id=employee_id,
                store_id=current_in.store_id,
                store_timezone=current_in.store_timezone,
                local_date=local_dt.date(),
                clock_in_utc=current_in.occurred_at_utc,
                clock_out_utc=None,
                break_minutes=break_seconds // 60,
                net_minutes=0,
                net_hours=Decimal("0.00"),
                has_corrections=has_corrections,
                is_open=True,
                has_open_break=(state == _ShiftState.ON_BREAK),
                source_event_ids=tuple(event_ids),
            )
        )

    return tuple(sessions)


def evaluate_attendance_ledger(
    *,
    employee_id: UUID,
    events: list[AttendanceEvent],
    birth_date: date | None,
    work_schedule_type: WorkScheduleType,
    contracted_weekly_hours: Decimal,
    date_from: date | None = None,
    date_to: date | None = None,
) -> AttendanceLedgerReport:
    """Fold events and evaluate all 6 Spanish statutory labor law rules."""
    all_sessions = fold_attendance_events(employee_id, events)
    alerts: list[ComplianceAlert] = []

    # 1. Group closed sessions by local_date for Daily Max (Art. 34.3 ET)
    daily_minutes: dict[date, int] = defaultdict(int)
    for session in all_sessions:
        if not session.is_open:
            daily_minutes[session.local_date] += session.net_minutes

    for local_dt, total_mins in sorted(daily_minutes.items()):
        daily_hours = _minutes_to_hours(total_mins)
        minor = is_minor_on_date(birth_date, local_dt)
        daily_limit = MINOR_DAILY_MAX_HOURS if minor else ADULT_DAILY_MAX_HOURS
        if daily_hours > daily_limit:
            alerts.append(
                ComplianceAlert(
                    code=AlertCode.DAILY_MAX_EXCEEDED,
                    severity=AlertSeverity.VIOLATION,
                    employee_id=employee_id,
                    local_date=local_dt,
                    message=(
                        f"Daily worked hours ({daily_hours}h) exceed statutory limit "
                        f"({daily_limit}h, Art. 34.3 ET)"
                    ),
                    actual_value=daily_hours,
                    limit_value=daily_limit,
                )
            )

    # 2. Inter-Shift Rest >= 12h UTC gap (Art. 34.3 ET)
    for idx in range(1, len(all_sessions)):
        prev_session = all_sessions[idx - 1]
        curr_session = all_sessions[idx]
        if prev_session.clock_out_utc is None:
            continue
        gap_seconds = int(
            (curr_session.clock_in_utc - prev_session.clock_out_utc).total_seconds()
        )
        if gap_seconds < MIN_INTER_SHIFT_REST_SECONDS:
            gap_hours = (Decimal(gap_seconds) / Decimal(3600)).quantize(Decimal("0.01"))
            alerts.append(
                ComplianceAlert(
                    code=AlertCode.INTER_SHIFT_REST_VIOLATION,
                    severity=AlertSeverity.VIOLATION,
                    employee_id=employee_id,
                    local_date=curr_session.local_date,
                    message=(
                        f"Inter-shift rest ({gap_hours}h) is below statutory minimum "
                        "of 12.00h (Art. 34.3 ET)"
                    ),
                    actual_value=gap_hours,
                    limit_value=Decimal("12.00"),
                )
            )

    # 3. Continuous Shift Break (Art. 34.4 ET)
    for session in all_sessions:
        if session.is_open:
            continue
        minor = is_minor_on_date(birth_date, session.local_date)
        threshold_mins = (
            MINOR_CONTINUOUS_THRESHOLD_MINUTES if minor else ADULT_CONTINUOUS_THRESHOLD_MINUTES
        )
        required_break = MINOR_REQUIRED_BREAK_MINUTES if minor else ADULT_REQUIRED_BREAK_MINUTES
        if session.net_minutes > threshold_mins and session.break_minutes < required_break:
            alerts.append(
                ComplianceAlert(
                    code=AlertCode.MISSING_CONTINUOUS_BREAK,
                    severity=AlertSeverity.VIOLATION,
                    employee_id=employee_id,
                    local_date=session.local_date,
                    message=(
                        f"Continuous shift ({_minutes_to_hours(session.net_minutes)}h) lacks "
                        f"required >= {required_break} min break (Art. 34.4 ET)"
                    ),
                    actual_value=Decimal(session.break_minutes),
                    limit_value=Decimal(required_break),
                )
            )

    # 4 & 5. Weekly Regular Limit (Art. 34.1 ET) & Part-Time Overtime Prohibition (Art. 12.4.c ET)
    regular_weekly_limit = min(contracted_weekly_hours, STATUTORY_MAX_WEEKLY_HOURS)
    weekly_minutes: dict[tuple[int, int], int] = defaultdict(int)
    weekly_last_date: dict[tuple[int, int], date] = {}
    for session in all_sessions:
        if session.is_open:
            continue
        iso = session.local_date.isocalendar()
        week_key = (iso.year, iso.week)
        weekly_minutes[week_key] += session.net_minutes
        if week_key not in weekly_last_date or session.local_date > weekly_last_date[week_key]:
            weekly_last_date[week_key] = session.local_date

    annual_overtime_by_year: dict[int, Decimal] = defaultdict(lambda: Decimal("0.00"))
    weekly_overtime_by_week: dict[tuple[int, int], Decimal] = {}

    for week_key, mins in sorted(weekly_minutes.items()):
        weekly_hours = _minutes_to_hours(mins)
        week_date = weekly_last_date[week_key]
        if weekly_hours > regular_weekly_limit:
            overtime = (weekly_hours - regular_weekly_limit).quantize(Decimal("0.01"))
            weekly_overtime_by_week[week_key] = overtime
            annual_overtime_by_year[week_date.year] += overtime

            if work_schedule_type == WorkScheduleType.PART_TIME:
                alerts.append(
                    ComplianceAlert(
                        code=AlertCode.PART_TIME_OVERTIME_PROHIBITED,
                        severity=AlertSeverity.VIOLATION,
                        employee_id=employee_id,
                        local_date=week_date,
                        message=(
                            f"Part-time employee worked {weekly_hours}h exceeding contracted "
                            f"{regular_weekly_limit}h/week (Art. 12.4.c ET)"
                        ),
                        actual_value=weekly_hours,
                        limit_value=regular_weekly_limit,
                    )
                )
            else:
                alerts.append(
                    ComplianceAlert(
                        code=AlertCode.WEEKLY_OVERTIME,
                        severity=AlertSeverity.WARNING,
                        employee_id=employee_id,
                        local_date=week_date,
                        message=(
                            f"Weekly worked hours ({weekly_hours}h) exceed regular limit "
                            f"({regular_weekly_limit}h): {overtime}h overtime (Art. 34.1 ET)"
                        ),
                        actual_value=weekly_hours,
                        limit_value=regular_weekly_limit,
                    )
                )

    # 6. Annual Overtime Cap (Art. 35.2 ET): >= 60h warning, > 80h violation
    for year, year_overtime in sorted(annual_overtime_by_year.items()):
        year_end_date = max(
            (s.local_date for s in all_sessions if s.local_date.year == year),
            default=date(year, 12, 31),
        )
        if year_overtime > ANNUAL_OVERTIME_MAX_HOURS:
            alerts.append(
                ComplianceAlert(
                    code=AlertCode.ANNUAL_OVERTIME_EXCEEDED,
                    severity=AlertSeverity.VIOLATION,
                    employee_id=employee_id,
                    local_date=year_end_date,
                    message=(
                        f"Annual overtime ({year_overtime}h) exceeds statutory 80.00h/year cap "
                        "(Art. 35.2 ET)"
                    ),
                    actual_value=year_overtime,
                    limit_value=ANNUAL_OVERTIME_MAX_HOURS,
                )
            )
        elif year_overtime >= ANNUAL_OVERTIME_WARNING_HOURS:
            alerts.append(
                ComplianceAlert(
                    code=AlertCode.ANNUAL_OVERTIME_WARNING,
                    severity=AlertSeverity.WARNING,
                    employee_id=employee_id,
                    local_date=year_end_date,
                    message=(
                        f"Annual overtime ({year_overtime}h) approaching statutory 80.00h/year cap "
                        "(Art. 35.2 ET)"
                    ),
                    actual_value=year_overtime,
                    limit_value=ANNUAL_OVERTIME_MAX_HOURS,
                )
            )

    # Apply optional date_from / date_to filter to returned summaries, sessions, and alerts
    filtered_sessions = tuple(
        s
        for s in all_sessions
        if (date_from is None or s.local_date >= date_from)
        and (date_to is None or s.local_date <= date_to)
    )
    filtered_alerts = tuple(
        a
        for a in alerts
        if (date_from is None or a.local_date >= date_from)
        and (date_to is None or a.local_date <= date_to)
    )

    alerts_by_date: dict[date, list[ComplianceAlert]] = defaultdict(list)
    for alert in filtered_alerts:
        alerts_by_date[alert.local_date].append(alert)

    sessions_by_date: dict[date, list[WorkSession]] = defaultdict(list)
    for session in filtered_sessions:
        sessions_by_date[session.local_date].append(session)

    daily_summaries: list[DailyAttendanceSummary] = []
    for dt in sorted(sessions_by_date.keys()):
        day_sessions = tuple(sessions_by_date[dt])
        total_break = sum(s.break_minutes for s in day_sessions)
        total_net = sum(s.net_minutes for s in day_sessions)
        daily_summaries.append(
            DailyAttendanceSummary(
                employee_id=employee_id,
                store_id=day_sessions[0].store_id,
                store_timezone=day_sessions[0].store_timezone,
                local_date=dt,
                sessions=day_sessions,
                total_break_minutes=total_break,
                total_net_minutes=total_net,
                total_net_hours=_minutes_to_hours(total_net),
                has_corrections=any(s.has_corrections for s in day_sessions),
                alerts=tuple(alerts_by_date.get(dt, [])),
            )
        )

    total_net_mins = sum(s.net_minutes for s in filtered_sessions if not s.is_open)
    total_net_hours = _minutes_to_hours(total_net_mins)

    # Calculate overtime for the filtered weeks (or total)
    filtered_week_keys = {
        (s.local_date.isocalendar().year, s.local_date.isocalendar().week)
        for s in filtered_sessions
        if not s.is_open
    }
    overtime_hours = sum(
        (weekly_overtime_by_week.get(wk, Decimal("0.00")) for wk in filtered_week_keys),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    regular_hours = max(Decimal("0.00"), (total_net_hours - overtime_hours)).quantize(
        Decimal("0.01")
    )

    ref_year = (
        date_to.year
        if date_to is not None
        else (filtered_sessions[-1].local_date.year if filtered_sessions else 0)
    )
    annual_overtime_hours = annual_overtime_by_year.get(ref_year, Decimal("0.00")).quantize(
        Decimal("0.01")
    )

    return AttendanceLedgerReport(
        employee_id=employee_id,
        daily_summaries=tuple(daily_summaries),
        sessions=filtered_sessions,
        total_net_hours=total_net_hours,
        regular_hours=regular_hours,
        overtime_hours=overtime_hours,
        annual_overtime_hours=annual_overtime_hours,
        alerts=filtered_alerts,
    )
