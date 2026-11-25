"""Phase 3 tests: Attendance domain rules, Spanish labor law, RBAC, kiosk & ITSS CSV."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.domain.attendance import (
    AlertCode,
    AlertSeverity,
    AttendanceEvent,
    AttendanceEventType,
    AttendanceSource,
    CorrectedEventType,
    _SpainFallbackTz,
    compute_attendance_retention_until,
    evaluate_attendance_ledger,
    fold_attendance_events,
    resolve_timezone,
)
from app.domain.employees import (
    ContractType,
    Employee,
    EmploymentStatus,
    WorkScheduleType,
)
from app.domain.organization import Membership, Store, Tenant, ValidationDomainError
from app.domain.security import MembershipStatus, UserRole
from app.infrastructure.config import Settings
from app.infrastructure.supabase_jwt import SupabaseJWTVerifier, sign_dev_hs256_token
from app.interfaces.api.deps import (
    get_attendance_repository,
    get_commit_fn,
    get_employee_repository,
    get_jwt_verifier,
    get_membership_repository,
    get_store_repository,
    get_tenant_repository,
    get_utc_now,
)
from app.main import create_app
from tests.test_api_org_rbac import (
    DEV_SECRET,
    InMemoryMembershipRepo,
    InMemoryOrgRepos,
    InMemoryStoreRepo,
)
from tests.test_phase2_employees import InMemoryEmployeeRepo


class InMemoryAttendanceRepo:
    def __init__(self) -> None:
        self.events: list[AttendanceEvent] = []

    async def append(self, event: AttendanceEvent) -> AttendanceEvent:
        self.events.append(event)
        return event

    async def get_by_id(self, tenant_id: UUID, event_id: UUID) -> AttendanceEvent | None:
        for ev in self.events:
            if ev.tenant_id == tenant_id and ev.id == event_id:
                return ev
        return None

    async def list_by_employee(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        occurred_from_utc: datetime | None = None,
        occurred_to_utc: datetime | None = None,
    ) -> list[AttendanceEvent]:
        result = [
            ev
            for ev in self.events
            if ev.tenant_id == tenant_id
            and ev.employee_id == employee_id
            and (occurred_from_utc is None or ev.occurred_at_utc >= occurred_from_utc)
            and (occurred_to_utc is None or ev.occurred_at_utc <= occurred_to_utc)
        ]
        return sorted(result, key=lambda e: (e.occurred_at_utc, e.recorded_at_utc, str(e.id)))

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        store_id: UUID | None = None,
        employee_id: UUID | None = None,
        occurred_from_utc: datetime | None = None,
        occurred_to_utc: datetime | None = None,
    ) -> list[AttendanceEvent]:
        if allowed_store_ids is not None and not allowed_store_ids:
            return []
        result = [
            ev
            for ev in self.events
            if ev.tenant_id == tenant_id
            and (allowed_store_ids is None or ev.store_id in allowed_store_ids)
            and (store_id is None or ev.store_id == store_id)
            and (employee_id is None or ev.employee_id == employee_id)
            and (occurred_from_utc is None or ev.occurred_at_utc >= occurred_from_utc)
            and (occurred_to_utc is None or ev.occurred_at_utc <= occurred_to_utc)
        ]
        return sorted(result, key=lambda e: (e.occurred_at_utc, e.recorded_at_utc, str(e.id)))


def _make_event(
    *,
    tenant_id: UUID,
    store_id: UUID,
    employee_id: UUID,
    event_type: AttendanceEventType,
    occurred_at_utc: datetime,
    store_timezone: str = "Europe/Madrid",
    source: AttendanceSource = AttendanceSource.WEB,
    supersedes_event_id: UUID | None = None,
    corrected_event_type: CorrectedEventType | None = None,
    correction_reason: str = "",
) -> AttendanceEvent:
    return AttendanceEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=store_id,
        employee_id=employee_id,
        event_type=event_type,
        occurred_at_utc=occurred_at_utc,
        store_timezone=store_timezone,
        source=source,
        recorded_by_user_id=uuid4(),
        recorded_at_utc=occurred_at_utc,
        retention_until=compute_attendance_retention_until(occurred_at_utc),
        supersedes_event_id=supersedes_event_id,
        corrected_event_type=corrected_event_type,
        correction_reason=correction_reason,
    )


# ---------------------------------------------------------------------------
# 1. Domain Unit Tests (Spanish Labor Law, DST, Folding, State Machine, 4y)
# ---------------------------------------------------------------------------


def test_attendance_retention_4_years_leap_day_safe() -> None:
    normal = datetime(2026, 11, 25, 9, 0, 0, tzinfo=UTC)
    assert compute_attendance_retention_until(normal) == datetime(
        2030, 11, 25, 9, 0, 0, tzinfo=UTC
    )
    # 2096-02-29 -> 2100 is NOT a leap year, must safely fall back to Feb 28
    leap_to_non_leap = datetime(2096, 2, 29, 12, 30, 0, tzinfo=UTC)
    assert compute_attendance_retention_until(leap_to_non_leap) == datetime(
        2100, 2, 28, 12, 30, 0, tzinfo=UTC
    )


def test_spain_fallback_tz_pep495_fold_and_unknown_zone() -> None:
    madrid = _SpainFallbackTz("Europe/Madrid", 1)
    utc_fold0 = datetime(2026, 10, 25, 0, 30, 0, tzinfo=UTC)
    utc_fold1 = datetime(2026, 10, 25, 1, 30, 0, tzinfo=UTC)

    local_fold0 = utc_fold0.astimezone(madrid)
    local_fold1 = utc_fold1.astimezone(madrid)
    assert (local_fold0.hour, local_fold0.minute, local_fold0.fold) == (2, 30, 0)
    assert (local_fold1.hour, local_fold1.minute, local_fold1.fold) == (2, 30, 1)
    assert local_fold0.utcoffset() == timedelta(hours=2)
    assert local_fold1.utcoffset() == timedelta(hours=1)
    assert local_fold0.astimezone(UTC) == utc_fold0
    assert local_fold1.astimezone(UTC) == utc_fold1

    canary = _SpainFallbackTz("Atlantic/Canary", 0)
    c_local0 = utc_fold0.astimezone(canary)
    c_local1 = utc_fold1.astimezone(canary)
    assert (c_local0.hour, c_local0.minute, c_local0.fold) == (1, 30, 0)
    assert (c_local1.hour, c_local1.minute, c_local1.fold) == (1, 30, 1)
    assert c_local0.astimezone(UTC) == utc_fold0
    assert c_local1.astimezone(UTC) == utc_fold1

    try:
        resolve_timezone("Invalid/Zone_Does_Not_Exist")
        raise AssertionError("Expected ValidationDomainError")
    except ValidationDomainError:
        pass


def test_state_machine_rejects_invalid_transitions() -> None:
    t_id, s_id, e_id = uuid4(), uuid4(), uuid4()
    t0 = datetime(2026, 5, 4, 8, 0, 0, tzinfo=UTC)

    # clock_out without clock_in
    try:
        fold_attendance_events(
            e_id,
            [
                _make_event(
                    tenant_id=t_id,
                    store_id=s_id,
                    employee_id=e_id,
                    event_type=AttendanceEventType.CLOCK_OUT,
                    occurred_at_utc=t0,
                )
            ],
        )
        raise AssertionError("Expected ValidationDomainError")
    except ValidationDomainError:
        pass

    # double clock_in
    try:
        fold_attendance_events(
            e_id,
            [
                _make_event(
                    tenant_id=t_id,
                    store_id=s_id,
                    employee_id=e_id,
                    event_type=AttendanceEventType.CLOCK_IN,
                    occurred_at_utc=t0,
                ),
                _make_event(
                    tenant_id=t_id,
                    store_id=s_id,
                    employee_id=e_id,
                    event_type=AttendanceEventType.CLOCK_IN,
                    occurred_at_utc=t0 + timedelta(hours=1),
                ),
            ],
        )
        raise AssertionError("Expected ValidationDomainError")
    except ValidationDomainError:
        pass

    # break_start without open shift
    try:
        fold_attendance_events(
            e_id,
            [
                _make_event(
                    tenant_id=t_id,
                    store_id=s_id,
                    employee_id=e_id,
                    event_type=AttendanceEventType.BREAK_START,
                    occurred_at_utc=t0,
                )
            ],
        )
        raise AssertionError("Expected ValidationDomainError")
    except ValidationDomainError:
        pass

    # clock_out while on open break
    try:
        fold_attendance_events(
            e_id,
            [
                _make_event(
                    tenant_id=t_id,
                    store_id=s_id,
                    employee_id=e_id,
                    event_type=AttendanceEventType.CLOCK_IN,
                    occurred_at_utc=t0,
                ),
                _make_event(
                    tenant_id=t_id,
                    store_id=s_id,
                    employee_id=e_id,
                    event_type=AttendanceEventType.BREAK_START,
                    occurred_at_utc=t0 + timedelta(hours=2),
                ),
                _make_event(
                    tenant_id=t_id,
                    store_id=s_id,
                    employee_id=e_id,
                    event_type=AttendanceEventType.CLOCK_OUT,
                    occurred_at_utc=t0 + timedelta(hours=4),
                ),
            ],
        )
        raise AssertionError("Expected ValidationDomainError")
    except ValidationDomainError:
        pass


def test_ledger_folding_correction_and_void() -> None:
    t_id, s_id, e_id = uuid4(), uuid4(), uuid4()
    ev_in = _make_event(
        tenant_id=t_id,
        store_id=s_id,
        employee_id=e_id,
        event_type=AttendanceEventType.CLOCK_IN,
        occurred_at_utc=datetime(2026, 5, 4, 7, 0, 0, tzinfo=UTC),
    )
    ev_accidental_break = _make_event(
        tenant_id=t_id,
        store_id=s_id,
        employee_id=e_id,
        event_type=AttendanceEventType.BREAK_START,
        occurred_at_utc=datetime(2026, 5, 4, 9, 0, 0, tzinfo=UTC),
    )
    ev_void_break = _make_event(
        tenant_id=t_id,
        store_id=s_id,
        employee_id=e_id,
        event_type=AttendanceEventType.CORRECTION,
        occurred_at_utc=datetime(2026, 5, 4, 9, 0, 0, tzinfo=UTC),
        source=AttendanceSource.MANAGER_CORRECTION,
        supersedes_event_id=ev_accidental_break.id,
        corrected_event_type=CorrectedEventType.VOID,
        correction_reason="Accidental kiosk tap",
    )
    ev_out_wrong = _make_event(
        tenant_id=t_id,
        store_id=s_id,
        employee_id=e_id,
        event_type=AttendanceEventType.CLOCK_OUT,
        occurred_at_utc=datetime(2026, 5, 4, 14, 0, 0, tzinfo=UTC),
    )
    ev_out_fixed = _make_event(
        tenant_id=t_id,
        store_id=s_id,
        employee_id=e_id,
        event_type=AttendanceEventType.CORRECTION,
        occurred_at_utc=datetime(2026, 5, 4, 15, 0, 0, tzinfo=UTC),
        source=AttendanceSource.MANAGER_CORRECTION,
        supersedes_event_id=ev_out_wrong.id,
        corrected_event_type=CorrectedEventType.CLOCK_OUT,
        correction_reason="Actual departure was 17:00 CEST",
    )

    sessions = fold_attendance_events(
        e_id,
        [ev_in, ev_accidental_break, ev_void_break, ev_out_wrong, ev_out_fixed],
    )
    assert len(sessions) == 1
    assert sessions[0].net_hours == Decimal("8.00")
    assert sessions[0].has_corrections is True
    assert sessions[0].is_open is False


def test_spanish_labor_law_daily_max_and_continuous_break_adult_vs_minor() -> None:
    t_id, s_id, adult_id, minor_id = uuid4(), uuid4(), uuid4(), uuid4()

    # Adult works 9h30m with 0 break -> DAILY_MAX_EXCEEDED (>9h) + MISSING_CONTINUOUS_BREAK
    adult_events = [
        _make_event(
            tenant_id=t_id,
            store_id=s_id,
            employee_id=adult_id,
            event_type=AttendanceEventType.CLOCK_IN,
            occurred_at_utc=datetime(2026, 5, 4, 7, 0, 0, tzinfo=UTC),
        ),
        _make_event(
            tenant_id=t_id,
            store_id=s_id,
            employee_id=adult_id,
            event_type=AttendanceEventType.CLOCK_OUT,
            occurred_at_utc=datetime(2026, 5, 4, 16, 30, 0, tzinfo=UTC),
        ),
    ]
    adult_report = evaluate_attendance_ledger(
        employee_id=adult_id,
        events=adult_events,
        birth_date=date(1990, 1, 1),
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
    )
    adult_codes = {a.code for a in adult_report.alerts}
    assert AlertCode.DAILY_MAX_EXCEEDED in adult_codes
    assert AlertCode.MISSING_CONTINUOUS_BREAK in adult_codes

    # Minor (17 years old on 2026-05-04) works 8h30m with only 20m break:
    # -> DAILY_MAX_EXCEEDED (>8h for minor) + MISSING_CONTINUOUS_BREAK (>4.5h needs >=30m for minor)
    minor_events = [
        _make_event(
            tenant_id=t_id,
            store_id=s_id,
            employee_id=minor_id,
            event_type=AttendanceEventType.CLOCK_IN,
            occurred_at_utc=datetime(2026, 5, 4, 7, 0, 0, tzinfo=UTC),
        ),
        _make_event(
            tenant_id=t_id,
            store_id=s_id,
            employee_id=minor_id,
            event_type=AttendanceEventType.BREAK_START,
            occurred_at_utc=datetime(2026, 5, 4, 11, 0, 0, tzinfo=UTC),
        ),
        _make_event(
            tenant_id=t_id,
            store_id=s_id,
            employee_id=minor_id,
            event_type=AttendanceEventType.BREAK_END,
            occurred_at_utc=datetime(2026, 5, 4, 11, 20, 0, tzinfo=UTC),
        ),
        _make_event(
            tenant_id=t_id,
            store_id=s_id,
            employee_id=minor_id,
            event_type=AttendanceEventType.CLOCK_OUT,
            occurred_at_utc=datetime(2026, 5, 4, 15, 50, 0, tzinfo=UTC),
        ),
    ]
    minor_report = evaluate_attendance_ledger(
        employee_id=minor_id,
        events=minor_events,
        birth_date=date(2009, 1, 1),
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
    )
    minor_codes = {a.code for a in minor_report.alerts}
    assert AlertCode.DAILY_MAX_EXCEEDED in minor_codes
    assert AlertCode.MISSING_CONTINUOUS_BREAK in minor_codes


def test_inter_shift_rest_dst_spring_23h_and_autumn_25h_days() -> None:
    """UTC gap must be used for 12h inter-shift rest across Europe/Madrid DST transitions."""
    t_id, s_id, e_id = uuid4(), uuid4(), uuid4()

    # 2026-03-29 is Europe/Madrid spring-forward (23h day): 02:00 CET -> 03:00 CEST (UTC+1 -> UTC+2)
    # Shift 1 ends 2026-03-28 21:00 UTC (22:00 CET).
    # Shift 2 starts 2026-03-29 08:30 UTC (10:30 CEST).
    # Local wall-clock looks like 12h30m, but actual UTC gap is 11h30m -> VIOLATION!
    spring_events = [
        _make_event(
            tenant_id=t_id,
            store_id=s_id,
            employee_id=e_id,
            event_type=AttendanceEventType.CLOCK_IN,
            occurred_at_utc=datetime(2026, 3, 28, 13, 0, 0, tzinfo=UTC),
        ),
        _make_event(
            tenant_id=t_id,
            store_id=s_id,
            employee_id=e_id,
            event_type=AttendanceEventType.CLOCK_OUT,
            occurred_at_utc=datetime(2026, 3, 28, 21, 0, 0, tzinfo=UTC),
        ),
        _make_event(
            tenant_id=t_id,
            store_id=s_id,
            employee_id=e_id,
            event_type=AttendanceEventType.CLOCK_IN,
            occurred_at_utc=datetime(2026, 3, 29, 8, 30, 0, tzinfo=UTC),
        ),
        _make_event(
            tenant_id=t_id,
            store_id=s_id,
            employee_id=e_id,
            event_type=AttendanceEventType.CLOCK_OUT,
            occurred_at_utc=datetime(2026, 3, 29, 13, 30, 0, tzinfo=UTC),
        ),
    ]
    spring_report = evaluate_attendance_ledger(
        employee_id=e_id,
        events=spring_events,
        birth_date=date(1992, 6, 1),
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
    )
    assert any(a.code == AlertCode.INTER_SHIFT_REST_VIOLATION for a in spring_report.alerts)

    # 2026-10-25 is Europe/Madrid fall-back (25h day): 03:00 CEST -> 02:00 CET (UTC+2 -> UTC+1)
    # Shift 1 ends 2026-10-24 20:00 UTC (22:00 CEST).
    # Shift 2 starts 2026-10-25 08:30 UTC (09:30 CET).
    # Local wall-clock looks like 11h30m, but actual UTC gap is 12h30m -> NO violation!
    autumn_events = [
        _make_event(
            tenant_id=t_id,
            store_id=s_id,
            employee_id=e_id,
            event_type=AttendanceEventType.CLOCK_IN,
            occurred_at_utc=datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC),
        ),
        _make_event(
            tenant_id=t_id,
            store_id=s_id,
            employee_id=e_id,
            event_type=AttendanceEventType.CLOCK_OUT,
            occurred_at_utc=datetime(2026, 10, 24, 20, 0, 0, tzinfo=UTC),
        ),
        _make_event(
            tenant_id=t_id,
            store_id=s_id,
            employee_id=e_id,
            event_type=AttendanceEventType.CLOCK_IN,
            occurred_at_utc=datetime(2026, 10, 25, 8, 30, 0, tzinfo=UTC),
        ),
        _make_event(
            tenant_id=t_id,
            store_id=s_id,
            employee_id=e_id,
            event_type=AttendanceEventType.CLOCK_OUT,
            occurred_at_utc=datetime(2026, 10, 25, 13, 30, 0, tzinfo=UTC),
        ),
    ]
    autumn_report = evaluate_attendance_ledger(
        employee_id=e_id,
        events=autumn_events,
        birth_date=date(1992, 6, 1),
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
    )
    assert not any(a.code == AlertCode.INTER_SHIFT_REST_VIOLATION for a in autumn_report.alerts)


def test_part_time_overtime_prohibited_and_annual_overtime_caps() -> None:
    t_id, s_id, pt_id, ft_id = uuid4(), uuid4(), uuid4(), uuid4()

    # Part-time (20h/wk) working 22h in one week -> PART_TIME_OVERTIME_PROHIBITED
    pt_events: list[AttendanceEvent] = []
    for day_offset in range(4):
        d = datetime(2026, 6, 1 + day_offset, 8, 0, 0, tzinfo=UTC)
        pt_events.append(
            _make_event(
                tenant_id=t_id,
                store_id=s_id,
                employee_id=pt_id,
                event_type=AttendanceEventType.CLOCK_IN,
                occurred_at_utc=d,
            )
        )
        pt_events.append(
            _make_event(
                tenant_id=t_id,
                store_id=s_id,
                employee_id=pt_id,
                event_type=AttendanceEventType.CLOCK_OUT,
                occurred_at_utc=d + timedelta(hours=5, minutes=30),
            )
        )

    pt_report = evaluate_attendance_ledger(
        employee_id=pt_id,
        events=pt_events,
        birth_date=date(1995, 3, 10),
        work_schedule_type=WorkScheduleType.PART_TIME,
        contracted_weekly_hours=Decimal("20.00"),
    )
    assert any(
        a.code == AlertCode.PART_TIME_OVERTIME_PROHIBITED
        and a.severity == AlertSeverity.VIOLATION
        for a in pt_report.alerts
    )

    # Full-time (40h/wk) working 50h/week for 9 weeks -> 90h annual overtime (>80h violation)
    ft_events: list[AttendanceEvent] = []
    start_monday = datetime(2026, 1, 5, 8, 0, 0, tzinfo=UTC)
    for week in range(9):
        for day in range(5):
            shift_start = start_monday + timedelta(weeks=week, days=day)
            # 10h net with 30m break (10h30m span) => 50h/week => 10h/week overtime
            ft_events.extend(
                [
                    _make_event(
                        tenant_id=t_id,
                        store_id=s_id,
                        employee_id=ft_id,
                        event_type=AttendanceEventType.CLOCK_IN,
                        occurred_at_utc=shift_start,
                    ),
                    _make_event(
                        tenant_id=t_id,
                        store_id=s_id,
                        employee_id=ft_id,
                        event_type=AttendanceEventType.BREAK_START,
                        occurred_at_utc=shift_start + timedelta(hours=5),
                    ),
                    _make_event(
                        tenant_id=t_id,
                        store_id=s_id,
                        employee_id=ft_id,
                        event_type=AttendanceEventType.BREAK_END,
                        occurred_at_utc=shift_start + timedelta(hours=5, minutes=30),
                    ),
                    _make_event(
                        tenant_id=t_id,
                        store_id=s_id,
                        employee_id=ft_id,
                        event_type=AttendanceEventType.CLOCK_OUT,
                        occurred_at_utc=shift_start + timedelta(hours=10, minutes=30),
                    ),
                ]
            )

    # First 6 weeks -> 60h overtime => ANNUAL_OVERTIME_WARNING
    warn_report = evaluate_attendance_ledger(
        employee_id=ft_id,
        events=ft_events[: 6 * 5 * 4],
        birth_date=date(1990, 1, 1),
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
    )
    assert warn_report.annual_overtime_hours == Decimal("60.00")
    assert any(a.code == AlertCode.ANNUAL_OVERTIME_WARNING for a in warn_report.alerts)

    # All 9 weeks -> 90h overtime => ANNUAL_OVERTIME_EXCEEDED
    exceed_report = evaluate_attendance_ledger(
        employee_id=ft_id,
        events=ft_events,
        birth_date=date(1990, 1, 1),
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
    )
    assert exceed_report.annual_overtime_hours == Decimal("90.00")
    assert any(
        a.code == AlertCode.ANNUAL_OVERTIME_EXCEEDED and a.severity == AlertSeverity.VIOLATION
        for a in exceed_report.alerts
    )


# ---------------------------------------------------------------------------
# 2. API Integration Tests (5 Roles, Kiosk, Corrections, LOPDGDD, ITSS CSV)
# ---------------------------------------------------------------------------


def _build_phase3_test_app():
    now = datetime(2026, 5, 1, 9, 0, 0, tzinfo=UTC)
    tenant_a = Tenant(
        id=uuid4(),
        legal_name="Bar Madrid Centro S.L.",
        trade_name="Bar Madrid",
        cif_nif="B12345678",
        default_timezone="Europe/Madrid",
        default_locale="es-ES",
        created_at=now,
        updated_at=now,
    )
    tenant_b = Tenant(
        id=uuid4(),
        legal_name="Other Tenant S.L.",
        trade_name="Other",
        cif_nif="B87654321",
        default_timezone="Europe/Madrid",
        default_locale="es-ES",
        created_at=now,
        updated_at=now,
    )
    store_a1 = Store(
        id=uuid4(),
        tenant_id=tenant_a.id,
        name="Centro Sol",
        code="MAD-SOL",
        timezone="Europe/Madrid",
        address="Puerta del Sol 1",
        city="Madrid",
        postal_code="28013",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    store_a2 = Store(
        id=uuid4(),
        tenant_id=tenant_a.id,
        name="Canarias Norte",
        code="TFN-01",
        timezone="Atlantic/Canary",
        address="Calle Real 5",
        city="Santa Cruz",
        postal_code="38001",
        is_active=True,
        created_at=now,
        updated_at=now,
    )

    owner_uid, hr_uid, mgr_uid, acc_uid, emp_uid = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    other_emp_uid = uuid4()

    org_state = InMemoryOrgRepos()
    org_state.tenants = {tenant_a.id: tenant_a, tenant_b.id: tenant_b}
    org_state.stores = {store_a1.id: store_a1, store_a2.id: store_a2}
    for m in [
        Membership(
            id=uuid4(),
            tenant_id=tenant_a.id,
            user_id=owner_uid,
            email="owner@okteam.es",
            display_name="Owner",
            role=UserRole.OWNER,
            status=MembershipStatus.ACTIVE,
            store_ids=frozenset(),
            created_at=now,
            updated_at=now,
        ),
        Membership(
            id=uuid4(),
            tenant_id=tenant_a.id,
            user_id=hr_uid,
            email="hr@okteam.es",
            display_name="HR",
            role=UserRole.HR,
            status=MembershipStatus.ACTIVE,
            store_ids=frozenset(),
            created_at=now,
            updated_at=now,
        ),
        Membership(
            id=uuid4(),
            tenant_id=tenant_a.id,
            user_id=mgr_uid,
            email="mgr@okteam.es",
            display_name="Manager Sol",
            role=UserRole.STORE_MANAGER,
            status=MembershipStatus.ACTIVE,
            store_ids=frozenset({store_a1.id}),
            created_at=now,
            updated_at=now,
        ),
        Membership(
            id=uuid4(),
            tenant_id=tenant_a.id,
            user_id=acc_uid,
            email="gestoria@okteam.es",
            display_name="Gestoria",
            role=UserRole.ACCOUNTANT,
            status=MembershipStatus.ACTIVE,
            store_ids=frozenset(),
            created_at=now,
            updated_at=now,
        ),
        Membership(
            id=uuid4(),
            tenant_id=tenant_a.id,
            user_id=emp_uid,
            email="emp1@okteam.es",
            display_name="Carlos",
            role=UserRole.EMPLOYEE,
            status=MembershipStatus.ACTIVE,
            store_ids=frozenset({store_a1.id}),
            created_at=now,
            updated_at=now,
        ),
    ]:
        org_state.memberships[m.id] = m

    tenant_repo = org_state
    store_repo = InMemoryStoreRepo(org_state)
    membership_repo = InMemoryMembershipRepo(org_state)

    emp_a1 = Employee(
        id=uuid4(),
        tenant_id=tenant_a.id,
        primary_store_id=store_a1.id,
        user_id=emp_uid,
        employee_code="EMP-001",
        full_name="Carlos García López",
        preferred_name="Carlos",
        email="emp1@okteam.es",
        phone="",
        birth_date=date(1994, 4, 15),
        contract_type=ContractType.INDEFINIDO,
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
        annual_vacation_days=30,
        hire_date=date(2025, 1, 10),
        termination_date=None,
        nie_dni_enc=None,
        nie_dni_masked=None,
        nie_dni_blind_index=None,
        iban_enc=None,
        iban_masked=None,
        naf_enc=None,
        naf_masked=None,
        employment_status=EmploymentStatus.ACTIVE,
        retention_until=None,
        anonymized_at=None,
        created_at=now,
        updated_at=now,
    )
    emp_a1_hold = replace(
        emp_a1,
        id=uuid4(),
        user_id=other_emp_uid,
        employee_code="EMP-HOLD",
        full_name="Frozen Legal Hold",
        preferred_name="Frozen",
        employment_status=EmploymentStatus.LEGAL_HOLD,
        termination_date=date(2026, 4, 30),
        retention_until=datetime(2030, 4, 30, 23, 59, 59, tzinfo=UTC),
    )
    emp_a2 = replace(
        emp_a1,
        id=uuid4(),
        primary_store_id=store_a2.id,
        user_id=None,
        employee_code="EMP-TFN",
        full_name="Lucía Tenerife",
        preferred_name="Lucía",
    )
    emp_mgr = replace(
        emp_a1,
        id=uuid4(),
        primary_store_id=store_a1.id,
        user_id=mgr_uid,
        employee_code="EMP-MGR",
        full_name="Manager Sol",
        preferred_name="Manager",
    )
    emp_formula = replace(
        emp_a1,
        id=uuid4(),
        primary_store_id=store_a1.id,
        user_id=None,
        employee_code="EMP-CSV",
        full_name="=CMD|' /C calc'!A0",
        preferred_name="CSV",
    )
    employee_repo = InMemoryEmployeeRepo()
    employee_repo.employees = {
        emp_a1.id: emp_a1,
        emp_a1_hold.id: emp_a1_hold,
        emp_a2.id: emp_a2,
        emp_mgr.id: emp_mgr,
        emp_formula.id: emp_formula,
    }
    attendance_repo = InMemoryAttendanceRepo()
    clock_state = {"now": datetime(2026, 5, 4, 7, 0, 0, tzinfo=UTC)}

    settings = Settings(
        app_env="local",
        enable_dev_auth=True,
        dev_jwt_secret=DEV_SECRET,
        supabase_jwt_audience="authenticated",
    )
    app = create_app(settings)
    verifier = SupabaseJWTVerifier(
        supabase_url="https://example.supabase.co",
        audience="authenticated",
        jwt_secret=DEV_SECRET,
    )

    async def _noop_commit() -> None:
        return None

    app.dependency_overrides[get_jwt_verifier] = lambda: verifier
    app.dependency_overrides[get_tenant_repository] = lambda: tenant_repo
    app.dependency_overrides[get_store_repository] = lambda: store_repo
    app.dependency_overrides[get_membership_repository] = lambda: membership_repo
    app.dependency_overrides[get_employee_repository] = lambda: employee_repo
    app.dependency_overrides[get_attendance_repository] = lambda: attendance_repo
    app.dependency_overrides[get_commit_fn] = lambda: _noop_commit
    app.dependency_overrides[get_utc_now] = lambda: clock_state["now"]

    return TestClient(app), clock_state, attendance_repo, {
        "tenant_a": tenant_a.id,
        "tenant_b": tenant_b.id,
        "store_a1": store_a1.id,
        "store_a2": store_a2.id,
        "emp_a1": emp_a1.id,
        "emp_a1_hold": emp_a1_hold.id,
        "emp_a2": emp_a2.id,
        "emp_formula": emp_formula.id,
        "owner": owner_uid,
        "hr": hr_uid,
        "mgr": mgr_uid,
        "acc": acc_uid,
        "emp": emp_uid,
    }


def _headers(uid: UUID, tenant_id: UUID) -> dict[str, str]:
    token = sign_dev_hs256_token(user_id=uid, email="user@okteam.es", secret=DEV_SECRET)
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": str(tenant_id)}


def test_api_attendance_clock_kiosk_corrections_rbac_and_csv() -> None:
    client, clock_state, attendance_repo, ids = _build_phase3_test_app()
    t_a = ids["tenant_a"]
    t_b = ids["tenant_b"]

    # 1. Accountant cannot clock (403)
    res_acc = client.post(
        "/api/v1/attendance/clock",
        headers=_headers(ids["acc"], t_a),
        json={"event_type": "clock_in", "source": "web"},
    )
    assert res_acc.status_code == 403

    # 2. Employee cannot use kiosk mode (403) or web-clock at another store (403)
    res_emp_kiosk = client.post(
        "/api/v1/attendance/clock",
        headers=_headers(ids["emp"], t_a),
        json={
            "event_type": "clock_in",
            "source": "kiosk",
            "store_id": str(ids["store_a1"]),
            "employee_code": "EMP-001",
        },
    )
    assert res_emp_kiosk.status_code == 403

    res_emp_other_store = client.post(
        "/api/v1/attendance/clock",
        headers=_headers(ids["emp"], t_a),
        json={
            "event_type": "clock_in",
            "source": "web",
            "store_id": str(ids["store_a2"]),
        },
    )
    assert res_emp_other_store.status_code == 403

    # 3. Store manager cannot use kiosk OR web clock on unassigned store_a2 (403)
    res_mgr_unassigned = client.post(
        "/api/v1/attendance/clock",
        headers=_headers(ids["mgr"], t_a),
        json={
            "event_type": "clock_in",
            "source": "kiosk",
            "store_id": str(ids["store_a2"]),
            "employee_code": "EMP-TFN",
        },
    )
    assert res_mgr_unassigned.status_code == 403

    res_mgr_web_unassigned = client.post(
        "/api/v1/attendance/clock",
        headers=_headers(ids["mgr"], t_a),
        json={
            "event_type": "clock_in",
            "source": "web",
            "store_id": str(ids["store_a2"]),
        },
    )
    assert res_mgr_web_unassigned.status_code == 403

    # 4. Legal hold employee blocked from kiosk clock (422)
    res_hold_kiosk = client.post(
        "/api/v1/attendance/clock",
        headers=_headers(ids["mgr"], t_a),
        json={
            "event_type": "clock_in",
            "source": "kiosk",
            "store_id": str(ids["store_a1"]),
            "employee_code": "EMP-HOLD",
        },
    )
    assert res_hold_kiosk.status_code == 422

    # 5. Employee self-clocks IN (web) — client-spoofed occurred_at_utc is ignored
    clock_state["now"] = datetime(2026, 5, 4, 7, 0, 0, tzinfo=UTC)
    res_in = client.post(
        "/api/v1/attendance/clock",
        headers=_headers(ids["emp"], t_a),
        json={
            "event_type": "clock_in",
            "source": "web",
            "occurred_at_utc": "2020-01-01T00:00:00Z",
        },
    )
    assert res_in.status_code == 201
    assert res_in.json()["event"]["occurred_at_utc"].startswith("2026-05-04T07:00:00")
    in_event_id = res_in.json()["event"]["id"]

    # 6. Double clock_in rejected (422)
    clock_state["now"] = datetime(2026, 5, 4, 7, 5, 0, tzinfo=UTC)
    res_dup_in = client.post(
        "/api/v1/attendance/clock",
        headers=_headers(ids["emp"], t_a),
        json={"event_type": "clock_in", "source": "web"},
    )
    assert res_dup_in.status_code == 422

    # 7. Store manager clocks break_start & break_end & clock_out via KIOSK
    for ev_type, ts in [
        ("break_start", datetime(2026, 5, 4, 11, 0, 0, tzinfo=UTC)),
        ("break_end", datetime(2026, 5, 4, 11, 30, 0, tzinfo=UTC)),
        ("clock_out", datetime(2026, 5, 4, 15, 30, 0, tzinfo=UTC)),
    ]:
        clock_state["now"] = ts
        res_kiosk = client.post(
            "/api/v1/attendance/clock",
            headers=_headers(ids["mgr"], t_a),
            json={
                "event_type": ev_type,
                "source": "kiosk",
                "store_id": str(ids["store_a1"]),
                "employee_code": "EMP-001",
            },
        )
        assert res_kiosk.status_code == 201
        assert res_kiosk.json()["employee_code"] == "EMP-001"

    # 8. Employee & Accountant cannot append corrections (403)
    for role_key in ("emp", "acc"):
        res_corr_forbidden = client.post(
            "/api/v1/attendance/corrections",
            headers=_headers(ids[role_key], t_a),
            json={
                "employee_id": str(ids["emp_a1"]),
                "corrected_event_type": "clock_in",
                "occurred_at_utc": "2026-05-04T06:45:00Z",
                "correction_reason": "Attempted unauthorized change",
                "supersedes_event_id": in_event_id,
            },
        )
        assert res_corr_forbidden.status_code == 403

    # 9. HR appends correction superseding clock_in to 06:30 UTC
    res_corr_ok = client.post(
        "/api/v1/attendance/corrections",
        headers=_headers(ids["hr"], t_a),
        json={
            "employee_id": str(ids["emp_a1"]),
            "corrected_event_type": "clock_in",
            "occurred_at_utc": "2026-05-04T06:30:00Z",
            "correction_reason": "Employee arrived at 08:30 CEST",
            "supersedes_event_id": in_event_id,
        },
    )
    assert res_corr_ok.status_code == 201
    assert res_corr_ok.json()["event_type"] == "correction"
    assert res_corr_ok.json()["supersedes_event_id"] == in_event_id

    # 10. Store manager blocked from legal_hold & unassigned stores across all endpoints
    res_mgr_hold_ledger = client.get(
        f"/api/v1/attendance/ledger?employee_id={ids['emp_a1_hold']}",
        headers=_headers(ids["mgr"], t_a),
    )
    assert res_mgr_hold_ledger.status_code == 403

    res_mgr_hold_corr = client.post(
        "/api/v1/attendance/corrections",
        headers=_headers(ids["mgr"], t_a),
        json={
            "employee_id": str(ids["emp_a1_hold"]),
            "corrected_event_type": "clock_in",
            "occurred_at_utc": "2026-05-04T07:00:00Z",
            "correction_reason": "Blocked by Art. 32 LOPDGDD",
        },
    )
    assert res_mgr_hold_corr.status_code == 403

    # Owner/HR also blocked (422) from mutating legal_hold employee via corrections
    res_hr_hold_corr = client.post(
        "/api/v1/attendance/corrections",
        headers=_headers(ids["hr"], t_a),
        json={
            "employee_id": str(ids["emp_a1_hold"]),
            "corrected_event_type": "clock_in",
            "occurred_at_utc": "2026-05-04T07:00:00Z",
            "correction_reason": "Frozen under Art. 32 LOPDGDD",
        },
    )
    assert res_hr_hold_corr.status_code == 422

    # Store manager 403 on unassigned store_a2 / emp_a2 for ledger, export.csv, corrections
    assert (
        client.get(
            f"/api/v1/attendance/ledger?store_id={ids['store_a2']}",
            headers=_headers(ids["mgr"], t_a),
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/attendance/export.csv?store_id={ids['store_a2']}",
            headers=_headers(ids["mgr"], t_a),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/attendance/corrections",
            headers=_headers(ids["mgr"], t_a),
            json={
                "employee_id": str(ids["emp_a2"]),
                "corrected_event_type": "clock_in",
                "occurred_at_utc": "2026-05-04T07:00:00Z",
                "correction_reason": "Unassigned store",
            },
        ).status_code
        == 403
    )

    # Store manager 403 when superseding an event from an unassigned store_a2
    unassigned_ev = _make_event(
        tenant_id=t_a,
        store_id=ids["store_a2"],
        employee_id=ids["emp_a1"],
        event_type=AttendanceEventType.CLOCK_IN,
        occurred_at_utc=datetime(2026, 5, 5, 7, 0, 0, tzinfo=UTC),
    )
    attendance_repo.events.append(unassigned_ev)
    assert (
        client.post(
            "/api/v1/attendance/corrections",
            headers=_headers(ids["mgr"], t_a),
            json={
                "employee_id": str(ids["emp_a1"]),
                "corrected_event_type": "void",
                "occurred_at_utc": "2026-05-05T07:00:00Z",
                "correction_reason": "Cross-store supersede attempt",
                "supersedes_event_id": str(unassigned_ev.id),
            },
        ).status_code
        == 403
    )

    # 11. Cross-tenant isolation (tenant_b -> 403)
    res_cross_tenant = client.get(
        "/api/v1/attendance/ledger",
        headers=_headers(ids["owner"], t_b),
    )
    assert res_cross_tenant.status_code == 403

    # 12. Accountant can read tenant-wide ledger and export ITSS CSV
    res_acc_ledger = client.get(
        "/api/v1/attendance/ledger",
        headers=_headers(ids["acc"], t_a),
    )
    assert res_acc_ledger.status_code == 200
    items = res_acc_ledger.json()["items"]
    emp1_item = next(i for i in items if i["employee_id"] == str(ids["emp_a1"]))
    # 06:30Z to 15:30Z = 9h gross - 30m break = 8.50h net
    assert Decimal(str(emp1_item["total_net_hours"])) == Decimal("8.50")
    assert len(emp1_item["events"]) == 6  # 4 raw + 1 correction + 1 unassigned_ev

    # 13. Employee sees ONLY self in ledger, and gets 403 querying/exporting another employee
    res_emp_ledger = client.get(
        "/api/v1/attendance/ledger",
        headers=_headers(ids["emp"], t_a),
    )
    assert res_emp_ledger.status_code == 200
    assert len(res_emp_ledger.json()["items"]) == 1
    assert res_emp_ledger.json()["items"][0]["employee_id"] == str(ids["emp_a1"])
    assert (
        client.get(
            f"/api/v1/attendance/ledger?employee_id={ids['emp_a2']}",
            headers=_headers(ids["emp"], t_a),
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/attendance/export.csv?employee_id={ids['emp_a2']}",
            headers=_headers(ids["emp"], t_a),
        ).status_code
        == 403
    )

    # 14. ITSS Spanish CSV export & CSV formula injection neutralization (CWE-1236)
    clock_state["now"] = datetime(2026, 5, 6, 8, 0, 0, tzinfo=UTC)
    assert (
        client.post(
            "/api/v1/attendance/clock",
            headers=_headers(ids["mgr"], t_a),
            json={
                "event_type": "clock_in",
                "source": "kiosk",
                "store_id": str(ids["store_a1"]),
                "employee_code": "EMP-CSV",
            },
        ).status_code
        == 201
    )
    clock_state["now"] = datetime(2026, 5, 6, 12, 0, 0, tzinfo=UTC)
    assert (
        client.post(
            "/api/v1/attendance/clock",
            headers=_headers(ids["mgr"], t_a),
            json={
                "event_type": "clock_out",
                "source": "kiosk",
                "store_id": str(ids["store_a1"]),
                "employee_code": "EMP-CSV",
            },
        ).status_code
        == 201
    )

    res_csv = client.get(
        "/api/v1/attendance/export.csv",
        headers=_headers(ids["acc"], t_a),
    )
    assert res_csv.status_code == 200
    assert res_csv.headers["content-type"].startswith("text/csv")
    csv_body = res_csv.text
    assert csv_body.startswith("\ufeffRazón Social,CIF,Código Centro,Nombre Centro")
    assert "Bar Madrid Centro S.L.,B12345678,MAD-SOL,Centro Sol,Europe/Madrid,EMP-001" in csv_body
    assert ",30,8.50,Sí," in csv_body
    # Formula injection neutralized with leading single quote
    assert "'=CMD|' /C calc'!A0" in csv_body
