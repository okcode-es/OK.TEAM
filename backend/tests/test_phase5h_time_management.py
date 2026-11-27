"""Phase 5H tests: Time Management segments, planned hours, day reviews, and PDF export."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.domain.attendance import (
    AttendanceDayReview,
    AttendanceEvent,
    AttendanceEventType,
    AttendanceSource,
    SegmentType,
    compute_attendance_retention_until,
    evaluate_attendance_ledger,
    fold_attendance_events,
)
from app.domain.break_policies import (
    BreakMode,
    BreakTrackingMode,
    BreakWindow,
    EffectiveBreakPolicy,
)
from app.domain.employees import EmploymentStatus, WorkScheduleType
from app.domain.leaves import LeaveRequest, LeaveStatus, LeaveType
from app.domain.payroll import (
    AttendancePdfDocumentInput,
    MonthlyClosing,
    MonthlyClosingStatus,
)
from app.domain.scheduling import Shift, ShiftStatus
from app.infrastructure.pdf_generator import (
    ReportLabAttendancePdfRenderer,
    _format_absence_line,
)
from app.interfaces.api.deps import (
    get_attendance_day_review_repository,
    get_attendance_repository,
    get_break_policy_repository,
    get_employee_repository,
    get_leave_repository,
    get_membership_repository,
    get_pdf_renderer,
    get_shift_repository,
)
from tests.test_phase5e_workflows_and_reports import _build_env
from tests.test_phase5g_hr_leaves_and_breaks import InMemoryBreakPolicyRepo


class InMemoryAttendanceDayReviewRepo:
    def __init__(self) -> None:
        self.reviews: dict[tuple[UUID, UUID, date], AttendanceDayReview] = {}

    async def list_by_employee(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[AttendanceDayReview]:
        return [
            r
            for (tid, eid, dt), r in sorted(self.reviews.items(), key=lambda kv: kv[0][2])
            if tid == tenant_id
            and eid == employee_id
            and (date_from is None or dt >= date_from)
            and (date_to is None or dt <= date_to)
        ]

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        employee_ids: Sequence[UUID] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[AttendanceDayReview]:
        allowed = set(employee_ids) if employee_ids is not None else None
        return [
            r
            for (tid, eid, dt), r in sorted(
                self.reviews.items(), key=lambda kv: (kv[0][2], str(kv[0][1]))
            )
            if tid == tenant_id
            and (allowed is None or eid in allowed)
            and (date_from is None or dt >= date_from)
            and (date_to is None or dt <= date_to)
        ]

    async def upsert_many(
        self,
        reviews: Sequence[AttendanceDayReview],
    ) -> list[AttendanceDayReview]:
        saved: list[AttendanceDayReview] = []
        for rev in reviews:
            key = (rev.tenant_id, rev.employee_id, rev.local_date)
            existing = self.reviews.get(key)
            if existing is not None:
                rev = replace(rev, id=existing.id, created_at=existing.created_at)
            self.reviews[key] = rev
            saved.append(rev)
        return saved


class InMemoryShiftRepo:
    def __init__(self) -> None:
        self.shifts: list[Shift] = []

    async def list_by_employee_window(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        window_start_utc: datetime,
        window_end_utc: datetime,
    ) -> list[Shift]:
        return [
            s
            for s in self.shifts
            if s.tenant_id == tenant_id
            and s.employee_id == employee_id
            and s.deleted_at is None
            and s.end_at_utc > window_start_utc
            and s.start_at_utc < window_end_utc
        ]


def _make_event(
    *,
    tenant_id: UUID,
    store_id: UUID,
    employee_id: UUID,
    event_type: AttendanceEventType,
    occurred_at_utc: datetime,
    user_id: UUID,
) -> AttendanceEvent:
    return AttendanceEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=store_id,
        employee_id=employee_id,
        event_type=event_type,
        occurred_at_utc=occurred_at_utc,
        store_timezone="Europe/Madrid",
        source=AttendanceSource.WEB,
        recorded_by_user_id=user_id,
        recorded_at_utc=occurred_at_utc,
        retention_until=compute_attendance_retention_until(occurred_at_utc),
    )


def _build_5h_env(tmp_path: Path, now_utc: datetime | None = None):
    env = _build_env(tmp_path, now_utc=now_utc)
    pol_repo = InMemoryBreakPolicyRepo()
    rev_repo = InMemoryAttendanceDayReviewRepo()
    shift_repo = InMemoryShiftRepo()
    app = env["client"].app
    app.dependency_overrides[get_break_policy_repository] = lambda: pol_repo
    app.dependency_overrides[get_attendance_day_review_repository] = lambda: rev_repo
    app.dependency_overrides[get_shift_repository] = lambda: shift_repo
    env["pol_repo"] = pol_repo
    env["rev_repo"] = rev_repo
    env["shift_repo"] = shift_repo
    env["att_repo"] = app.dependency_overrides[get_attendance_repository]()
    env["emp_repo"] = app.dependency_overrides[get_employee_repository]()
    env["leave_repo"] = app.dependency_overrides[get_leave_repository]()
    env["membership_repo"] = app.dependency_overrides[get_membership_repository]()
    return env


def test_domain_segments_manual_split_auto_deduct_open_and_minor_limit() -> None:
    tid, sid, eid, uid = uuid4(), uuid4(), uuid4(), uuid4()

    # 1. Manual work + paid/unpaid break + split shift
    e1 = _make_event(
        tenant_id=tid,
        store_id=sid,
        employee_id=eid,
        event_type=AttendanceEventType.CLOCK_IN,
        occurred_at_utc=datetime(2026, 11, 2, 8, 0, tzinfo=UTC),
        user_id=uid,
    )
    e2 = _make_event(
        tenant_id=tid,
        store_id=sid,
        employee_id=eid,
        event_type=AttendanceEventType.BREAK_START,
        occurred_at_utc=datetime(2026, 11, 2, 11, 0, tzinfo=UTC),
        user_id=uid,
    )
    e3 = _make_event(
        tenant_id=tid,
        store_id=sid,
        employee_id=eid,
        event_type=AttendanceEventType.BREAK_END,
        occurred_at_utc=datetime(2026, 11, 2, 11, 30, tzinfo=UTC),
        user_id=uid,
    )
    e4 = _make_event(
        tenant_id=tid,
        store_id=sid,
        employee_id=eid,
        event_type=AttendanceEventType.CLOCK_OUT,
        occurred_at_utc=datetime(2026, 11, 2, 13, 0, tzinfo=UTC),
        user_id=uid,
    )
    e5 = _make_event(
        tenant_id=tid,
        store_id=sid,
        employee_id=eid,
        event_type=AttendanceEventType.CLOCK_IN,
        occurred_at_utc=datetime(2026, 11, 2, 15, 0, tzinfo=UTC),
        user_id=uid,
    )
    e6 = _make_event(
        tenant_id=tid,
        store_id=sid,
        employee_id=eid,
        event_type=AttendanceEventType.CLOCK_OUT,
        occurred_at_utc=datetime(2026, 11, 2, 18, 0, tzinfo=UTC),
        user_id=uid,
    )

    paid_policy = EffectiveBreakPolicy(
        policy_id=None,
        inherited_from="tenant",
        break_mode=BreakMode.FIXED_DURATION,
        duration_minutes=30,
        windows=(),
        is_paid=True,
        tracking_mode=BreakTrackingMode.MANUAL_PUNCH,
        min_shift_minutes_for_auto_deduct=360,
        auto_end_manual_break=True,
    )
    report_paid = evaluate_attendance_ledger(
        employee_id=eid,
        events=[e1, e2, e3, e4, e5, e6],
        birth_date=date(2010, 1, 1),  # minor (<18 on 2026-11-02) -> 480m limit
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
        break_policy=paid_policy,
    )
    assert len(report_paid.daily_summaries) == 1
    summary = report_paid.daily_summaries[0]
    assert summary.daily_limit_minutes == 480
    assert [s.type for s in summary.segments] == [
        SegmentType.WORK,
        SegmentType.BREAK,
        SegmentType.WORK,
        SegmentType.WORK,
    ]
    assert [s.duration_minutes for s in summary.segments] == [180, 30, 90, 180]
    assert summary.segments[1].is_paid is True

    # 2. Auto-deduct FIXED_WINDOW (Madrid in Nov is UTC+1 -> 13:00-14:00 local = 12:00-13:00 UTC)
    fw_policy = EffectiveBreakPolicy(
        policy_id=None,
        inherited_from="tenant",
        break_mode=BreakMode.FIXED_WINDOW,
        duration_minutes=None,
        windows=(BreakWindow(start="13:00", end="14:00"),),
        is_paid=False,
        tracking_mode=BreakTrackingMode.AUTO_DEDUCT,
        min_shift_minutes_for_auto_deduct=360,
        auto_end_manual_break=False,
    )
    fw_sessions = fold_attendance_events(
        eid,
        [
            _make_event(
                tenant_id=tid,
                store_id=sid,
                employee_id=eid,
                event_type=AttendanceEventType.CLOCK_IN,
                occurred_at_utc=datetime(2026, 11, 3, 8, 0, tzinfo=UTC),
                user_id=uid,
            ),
            _make_event(
                tenant_id=tid,
                store_id=sid,
                employee_id=eid,
                event_type=AttendanceEventType.CLOCK_OUT,
                occurred_at_utc=datetime(2026, 11, 3, 16, 0, tzinfo=UTC),
                user_id=uid,
            ),
        ],
        break_policy=fw_policy,
    )
    assert len(fw_sessions) == 1
    assert [(s.type, s.duration_minutes, s.is_paid) for s in fw_sessions[0].segments] == [
        (SegmentType.WORK, 240, True),
        (SegmentType.BREAK, 60, False),
        (SegmentType.WORK, 180, True),
    ]

    # 3. Auto-deduct FIXED_DURATION centered split
    fd_policy = EffectiveBreakPolicy(
        policy_id=None,
        inherited_from="tenant",
        break_mode=BreakMode.FIXED_DURATION,
        duration_minutes=30,
        windows=(),
        is_paid=False,
        tracking_mode=BreakTrackingMode.AUTO_DEDUCT,
        min_shift_minutes_for_auto_deduct=360,
        auto_end_manual_break=False,
    )
    fd_sessions = fold_attendance_events(
        eid,
        [
            _make_event(
                tenant_id=tid,
                store_id=sid,
                employee_id=eid,
                event_type=AttendanceEventType.CLOCK_IN,
                occurred_at_utc=datetime(2026, 11, 4, 8, 0, tzinfo=UTC),
                user_id=uid,
            ),
            _make_event(
                tenant_id=tid,
                store_id=sid,
                employee_id=eid,
                event_type=AttendanceEventType.CLOCK_OUT,
                occurred_at_utc=datetime(2026, 11, 4, 16, 0, tzinfo=UTC),
                user_id=uid,
            ),
        ],
        break_policy=fd_policy,
    )
    assert [(s.type, s.duration_minutes, s.is_paid) for s in fd_sessions[0].segments] == [
        (SegmentType.WORK, 225, True),
        (SegmentType.BREAK, 30, False),
        (SegmentType.WORK, 225, True),
    ]

    # 4. Open shift & open break live duration with now_utc + adult limit (540m)
    open_now = datetime(2026, 11, 5, 11, 45, tzinfo=UTC)
    open_report = evaluate_attendance_ledger(
        employee_id=eid,
        events=[
            _make_event(
                tenant_id=tid,
                store_id=sid,
                employee_id=eid,
                event_type=AttendanceEventType.CLOCK_IN,
                occurred_at_utc=datetime(2026, 11, 5, 8, 0, tzinfo=UTC),
                user_id=uid,
            ),
            _make_event(
                tenant_id=tid,
                store_id=sid,
                employee_id=eid,
                event_type=AttendanceEventType.BREAK_START,
                occurred_at_utc=datetime(2026, 11, 5, 11, 0, tzinfo=UTC),
                user_id=uid,
            ),
        ],
        birth_date=date(1995, 1, 1),
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
        break_policy=paid_policy,
        now_utc=open_now,
    )
    open_summary = open_report.daily_summaries[0]
    assert open_summary.daily_limit_minutes == 540
    assert [(s.type, s.end_at, s.duration_minutes, s.is_paid) for s in open_summary.segments] == [
        (SegmentType.WORK, datetime(2026, 11, 5, 11, 0, tzinfo=UTC), 180, True),
        (SegmentType.BREAK, None, 45, True),
    ]


def test_ledger_planned_minutes_locked_months_and_reviews(tmp_path: Path) -> None:
    now_utc = datetime(2026, 11, 16, 18, 0, tzinfo=UTC)
    env = _build_5h_env(tmp_path, now_utc=now_utc)
    client: TestClient = env["client"]
    emp1 = env["emp1"]
    store_a_id = env["store_a_id"]
    hr_headers = env["headers"](env["hr_id"])

    # Add attendance on 2026-11-10 (Tue, has shift) and 2026-11-11 (Wed, contract fallback)
    for dt_in, dt_out in [
        (datetime(2026, 11, 10, 8, 0, tzinfo=UTC), datetime(2026, 11, 10, 16, 0, tzinfo=UTC)),
        (datetime(2026, 11, 11, 8, 0, tzinfo=UTC), datetime(2026, 11, 11, 16, 0, tzinfo=UTC)),
    ]:
        env["att_repo"].events.append(
            _make_event(
                tenant_id=env["tenant_id"],
                store_id=store_a_id,
                employee_id=emp1.id,
                event_type=AttendanceEventType.CLOCK_IN,
                occurred_at_utc=dt_in,
                user_id=env["emp1_uid"],
            )
        )
        env["att_repo"].events.append(
            _make_event(
                tenant_id=env["tenant_id"],
                store_id=store_a_id,
                employee_id=emp1.id,
                event_type=AttendanceEventType.CLOCK_OUT,
                occurred_at_utc=dt_out,
                user_id=env["emp1_uid"],
            )
        )

    # Shift on 2026-11-10: 6h gross - 30m break = 330m planned
    env["shift_repo"].shifts.append(
        Shift(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=store_a_id,
            employee_id=emp1.id,
            start_at_utc=datetime(2026, 11, 10, 8, 0, tzinfo=UTC),
            end_at_utc=datetime(2026, 11, 10, 14, 0, tzinfo=UTC),
            planned_break_minutes=30,
            status=ShiftStatus.PUBLISHED,
            notes="",
            created_by_user_id=env["hr_id"],
            published_by_user_id=env["hr_id"],
            published_at_utc=now_utc,
            retention_until=now_utc + timedelta(days=1460),
            created_at=now_utc,
            updated_at=now_utc,
        )
    )

    # Close 2026-11 for store_a
    env["payroll_repo"].closings.append(
        MonthlyClosing(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=store_a_id,
            period_month="2026-11",
            status=MonthlyClosingStatus.CLOSED,
            revision_number=1,
            closed_by_user_id=env["hr_id"],
            closed_at_utc=now_utc,
            reopened_by_user_id=None,
            reopened_at_utc=None,
            reopen_reason="",
            notes="",
            retention_until=now_utc + timedelta(days=1460),
            created_at=now_utc,
            updated_at=now_utc,
        )
    )

    res = client.get(
        f"/api/v1/attendance/ledger?employee_id={emp1.id}&date_from=2026-11-01&date_to=2026-11-30",
        headers=hr_headers,
    )
    assert res.status_code == 200
    body = res.json()["items"][0]
    assert body["closed_months"] == ["2026-11"]
    assert Decimal(str(body["contracted_weekly_hours"])) == Decimal("40.00")
    assert body["effective_break_policy"] is not None
    summaries = {d["local_date"]: d for d in body["daily_summaries"]}
    assert summaries["2026-11-10"]["planned_minutes"] == 330
    assert summaries["2026-11-11"]["planned_minutes"] == 480
    assert summaries["2026-11-10"]["is_locked"] is True
    assert len(summaries["2026-11-10"]["segments"]) == 1
    assert summaries["2026-11-10"]["review_status"] is None


def test_day_reviews_rbac_and_compliance_guards(tmp_path: Path) -> None:
    now_utc = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    env = _build_5h_env(tmp_path, now_utc=now_utc)
    client: TestClient = env["client"]
    emp1 = env["emp1"]
    emp_bcn = env["emp_bcn"]
    store_a_id = env["store_a_id"]

    emp_frozen = replace(
        emp1,
        id=uuid4(),
        user_id=None,
        employee_code="EMP-FROZEN",
        employment_status=EmploymentStatus.TERMINATED,
    )
    env["emp_repo"].employees[emp_frozen.id] = emp_frozen

    emp1_headers = env["headers"](env["emp1_uid"])
    mgr_a_headers = env["headers"](env["mgr_a_id"])
    hr_headers = env["headers"](env["hr_id"])
    acc_headers = env["headers"](env["acc_id"])

    # Clean closed day on 2026-11-18
    env["att_repo"].events.extend(
        [
            _make_event(
                tenant_id=env["tenant_id"],
                store_id=store_a_id,
                employee_id=emp1.id,
                event_type=AttendanceEventType.CLOCK_IN,
                occurred_at_utc=datetime(2026, 11, 18, 8, 0, tzinfo=UTC),
                user_id=env["emp1_uid"],
            ),
            _make_event(
                tenant_id=env["tenant_id"],
                store_id=store_a_id,
                employee_id=emp1.id,
                event_type=AttendanceEventType.CLOCK_OUT,
                occurred_at_utc=datetime(2026, 11, 18, 13, 0, tzinfo=UTC),
                user_id=env["emp1_uid"],
            ),
        ]
    )

    # Accountant -> 403
    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=acc_headers,
            json={"employee_id": str(emp1.id), "dates": ["2026-11-18"], "action": "submit"},
        ).status_code
        == 403
    )

    # Employee can submit own day (200), cannot approve own day (403), cannot submit other (403)
    r_sub = client.post(
        "/api/v1/attendance/day-reviews",
        headers=emp1_headers,
        json={
            "employee_id": str(emp1.id),
            "dates": ["2026-11-18"],
            "action": "submit",
            "note": "OK",
        },
    )
    assert r_sub.status_code == 200
    assert r_sub.json()["items"][0]["status"] == "submitted"

    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=emp1_headers,
            json={"employee_id": str(emp1.id), "dates": ["2026-11-18"], "action": "approve"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=emp1_headers,
            json={"employee_id": str(emp_bcn.id), "dates": ["2026-11-18"], "action": "submit"},
        ).status_code
        == 403
    )

    # Store manager can approve in-scope store/dept (200), blocked on out-of-scope store (403)
    r_mgr_app = client.post(
        "/api/v1/attendance/day-reviews",
        headers=mgr_a_headers,
        json={"employee_id": str(emp1.id), "dates": ["2026-11-18"], "action": "approve"},
    )
    assert r_mgr_app.status_code == 200
    assert r_mgr_app.json()["items"][0]["status"] == "approved"

    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=mgr_a_headers,
            json={"employee_id": str(emp_bcn.id), "dates": ["2026-11-18"], "action": "approve"},
        ).status_code
        == 403
    )

    # Department-restricted store_manager blocked on wrong department (403)
    mgr_a_mem = next(
        m
        for m in env["membership_repo"]._state.memberships.values()
        if m.user_id == env["mgr_a_id"]
    )
    env["membership_repo"]._state.memberships[mgr_a_mem.id] = replace(
        mgr_a_mem, department_scope="Cocina"
    )
    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=mgr_a_headers,
            json={"employee_id": str(emp1.id), "dates": ["2026-11-18"], "action": "approve"},
        ).status_code
        == 403
    )

    # Future date -> 422
    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=hr_headers,
            json={"employee_id": str(emp1.id), "dates": ["2026-11-25"], "action": "approve"},
        ).status_code
        == 422
    )

    # Frozen employee -> 422 (FrozenRecordError)
    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=hr_headers,
            json={"employee_id": str(emp_frozen.id), "dates": ["2026-11-18"], "action": "approve"},
        ).status_code
        == 422
    )

    # Open shift on 2026-11-20 -> approve returns 409
    env["att_repo"].events.append(
        _make_event(
            tenant_id=env["tenant_id"],
            store_id=store_a_id,
            employee_id=emp1.id,
            event_type=AttendanceEventType.CLOCK_IN,
            occurred_at_utc=datetime(2026, 11, 20, 8, 0, tzinfo=UTC),
            user_id=env["emp1_uid"],
        )
    )
    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=hr_headers,
            json={"employee_id": str(emp1.id), "dates": ["2026-11-20"], "action": "approve"},
        ).status_code
        == 409
    )

    # Violation alert on 2026-11-15 (>9h daily max) -> approve returns 409
    env["att_repo"].events.extend(
        [
            _make_event(
                tenant_id=env["tenant_id"],
                store_id=store_a_id,
                employee_id=emp1.id,
                event_type=AttendanceEventType.CLOCK_IN,
                occurred_at_utc=datetime(2026, 11, 15, 7, 0, tzinfo=UTC),
                user_id=env["emp1_uid"],
            ),
            _make_event(
                tenant_id=env["tenant_id"],
                store_id=store_a_id,
                employee_id=emp1.id,
                event_type=AttendanceEventType.CLOCK_OUT,
                occurred_at_utc=datetime(2026, 11, 15, 18, 0, tzinfo=UTC),
                user_id=env["emp1_uid"],
            ),
        ]
    )
    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=hr_headers,
            json={"employee_id": str(emp1.id), "dates": ["2026-11-15"], "action": "approve"},
        ).status_code
        == 409
    )

    # Closed payroll month -> 409
    env["payroll_repo"].closings.append(
        MonthlyClosing(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=store_a_id,
            period_month="2026-11",
            status=MonthlyClosingStatus.CLOSED,
            revision_number=1,
            closed_by_user_id=env["hr_id"],
            closed_at_utc=now_utc,
            reopened_by_user_id=None,
            reopened_at_utc=None,
            reopen_reason="",
            notes="",
            retention_until=now_utc + timedelta(days=1460),
            created_at=now_utc,
            updated_at=now_utc,
        )
    )
    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=hr_headers,
            json={"employee_id": str(emp1.id), "dates": ["2026-11-18"], "action": "approve"},
        ).status_code
        == 409
    )


def test_export_attendance_pdf_and_rbac_isolation(tmp_path: Path) -> None:
    now_utc = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    env = _build_5h_env(tmp_path, now_utc=now_utc)
    client: TestClient = env["client"]
    emp1 = env["emp1"]
    emp_bcn = env["emp_bcn"]
    store_a_id = env["store_a_id"]

    emp1_headers = env["headers"](env["emp1_uid"])
    mgr_a_headers = env["headers"](env["mgr_a_id"])
    acc_headers = env["headers"](env["acc_id"])

    env["att_repo"].events.extend(
        [
            _make_event(
                tenant_id=env["tenant_id"],
                store_id=store_a_id,
                employee_id=emp1.id,
                event_type=AttendanceEventType.CLOCK_IN,
                occurred_at_utc=datetime(2026, 11, 18, 8, 0, tzinfo=UTC),
                user_id=env["emp1_uid"],
            ),
            _make_event(
                tenant_id=env["tenant_id"],
                store_id=store_a_id,
                employee_id=emp1.id,
                event_type=AttendanceEventType.CLOCK_OUT,
                occurred_at_utc=datetime(2026, 11, 18, 13, 0, tzinfo=UTC),
                user_id=env["emp1_uid"],
            ),
        ]
    )

    # Employee exports self -> valid PDF
    r_emp_pdf = client.get(
        "/api/v1/attendance/export.pdf?date_from=2026-11-01&date_to=2026-11-30",
        headers=emp1_headers,
    )
    assert r_emp_pdf.status_code == 200
    assert r_emp_pdf.headers["content-type"].startswith("application/pdf")
    assert r_emp_pdf.headers["cache-control"] == "no-store"
    assert r_emp_pdf.headers["x-content-type-options"] == "nosniff"
    assert "filename*=" in r_emp_pdf.headers["content-disposition"]
    assert r_emp_pdf.content.startswith(b"%PDF-")
    assert b"%%EOF" in r_emp_pdf.content

    # Employee cannot export another employee -> 403
    assert (
        client.get(
            f"/api/v1/attendance/export.pdf?employee_id={emp_bcn.id}",
            headers=emp1_headers,
        ).status_code
        == 403
    )

    # Store manager cannot export out-of-scope store -> 403
    assert (
        client.get(
            f"/api/v1/attendance/export.pdf?employee_id={emp_bcn.id}",
            headers=mgr_a_headers,
        ).status_code
        == 403
    )

    # Accountant can export in-tenant -> 200
    r_acc_pdf = client.get(
        f"/api/v1/attendance/export.pdf?employee_id={emp1.id}&date_from=2026-11-10&date_to=2026-11-20",
        headers=acc_headers,
    )
    assert r_acc_pdf.status_code == 200
    assert r_acc_pdf.content.startswith(b"%PDF-")
    assert b"%%EOF" in r_acc_pdf.content

    # Ensure EmploymentStatus import is valid
    assert EmploymentStatus.ACTIVE.value == "active"


def test_red_team_regressions_gdpr_pdf_rbac_draft_shifts_and_secondary_store_lock(
    tmp_path: Path,
) -> None:
    now_utc = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    env = _build_5h_env(tmp_path, now_utc=now_utc)
    client: TestClient = env["client"]
    emp1 = env["emp1"]
    store_a_id = env["store_a_id"]
    store_b_id = env["store_b_id"]

    emp1_headers = env["headers"](env["emp1_uid"])
    mgr_a_headers = env["headers"](env["mgr_a_id"])
    hr_headers = env["headers"](env["hr_id"])
    acc_headers = env["headers"](env["acc_id"])

    # Create an active employee linked to mgr_a_id (for self-approval check) and acc_id (self PDF)
    emp_mgr = replace(
        emp1,
        id=uuid4(),
        user_id=env["mgr_a_id"],
        employee_code="EMP-MGR",
    )
    emp_acc = replace(
        emp1,
        id=uuid4(),
        user_id=env["acc_id"],
        employee_code="EMP-ACC",
    )
    emp_term = replace(
        emp1,
        id=uuid4(),
        user_id=None,
        employee_code="EMP-TERM",
        employment_status=EmploymentStatus.TERMINATED,
    )
    env["emp_repo"].employees[emp_mgr.id] = emp_mgr
    env["emp_repo"].employees[emp_acc.id] = emp_acc
    env["emp_repo"].employees[emp_term.id] = emp_term

    # Attendance on 2026-11-12 for emp1 (secondary store_b_id) and emp_mgr/emp_acc (store_a_id)
    for target_emp, uid, sid in [
        (emp1, env["emp1_uid"], store_b_id),
        (emp_mgr, env["mgr_a_id"], store_a_id),
        (emp_acc, env["acc_id"], store_a_id),
    ]:
        env["att_repo"].events.extend(
            [
                _make_event(
                    tenant_id=env["tenant_id"],
                    store_id=sid,
                    employee_id=target_emp.id,
                    event_type=AttendanceEventType.CLOCK_IN,
                    occurred_at_utc=datetime(2026, 11, 12, 8, 0, tzinfo=UTC),
                    user_id=uid,
                ),
                _make_event(
                    tenant_id=env["tenant_id"],
                    store_id=sid,
                    employee_id=target_emp.id,
                    event_type=AttendanceEventType.CLOCK_OUT,
                    occurred_at_utc=datetime(2026, 11, 12, 13, 0, tzinfo=UTC),
                    user_id=uid,
                ),
            ]
        )

    # Approved sick_leave + medical_appointment for emp1 and emp_acc
    for target_emp, ltype in [
        (emp1, LeaveType.SICK_LEAVE),
        (emp1, LeaveType.MEDICAL_APPOINTMENT),
        (emp_acc, LeaveType.SICK_LEAVE),
    ]:
        lv = LeaveRequest(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=store_a_id,
            employee_id=target_emp.id,
            leave_type=ltype,
            start_date=date(2026, 11, 10),
            end_date=date(2026, 11, 10),
            natural_days=1,
            status=LeaveStatus.APPROVED,
            reason="Test",
            requested_by_user_id=env["hr_id"],
            reviewed_by_user_id=env["hr_id"],
            reviewed_at_utc=now_utc,
            review_note="",
            medical_document_id=None,
            retention_until=now_utc + timedelta(days=1460),
            created_at=now_utc,
            updated_at=now_utc,
        )
        env["leave_repo"].leaves.append(lv)

    captured_pdf_inputs: list[AttendancePdfDocumentInput] = []
    real_renderer = ReportLabAttendancePdfRenderer()

    class _SpyRenderer:
        def render_monthly_attendance_pdf(self, data: AttendancePdfDocumentInput) -> bytes:
            captured_pdf_inputs.append(data)
            return real_renderer.render_monthly_attendance_pdf(data)

    client.app.dependency_overrides[get_pdf_renderer] = lambda: _SpyRenderer()

    # (a) Accountant PDF export for another employee -> GDPR Art. 9 redacted
    r_acc_other = client.get(
        f"/api/v1/attendance/export.pdf?employee_id={emp1.id}&date_from=2026-11-01&date_to=2026-11-30",
        headers=acc_headers,
    )
    assert r_acc_other.status_code == 200
    assert r_acc_other.content.startswith(b"%PDF-")
    assert len(captured_pdf_inputs) == 1
    assert captured_pdf_inputs[0].redact_health is True
    other_absence_line = _format_absence_line(captured_pdf_inputs[0])
    assert "Baja" not in other_absence_line
    assert "Otras Ausencias: 2d" in other_absence_line

    # Active self accountant PDF export -> NOT redacted
    r_acc_self = client.get(
        f"/api/v1/attendance/export.pdf?employee_id={emp_acc.id}&date_from=2026-11-01&date_to=2026-11-30",
        headers=acc_headers,
    )
    assert r_acc_self.status_code == 200
    assert r_acc_self.content.startswith(b"%PDF-")
    assert len(captured_pdf_inputs) == 2
    assert captured_pdf_inputs[1].redact_health is False
    self_absence_line = _format_absence_line(captured_pdf_inputs[1])
    assert "Baja Médica (IT): 1d" in self_absence_line

    # (e) export.pdf without employee_id as HR -> 422
    assert (
        client.get(
            "/api/v1/attendance/export.pdf?date_from=2026-11-01&date_to=2026-11-30",
            headers=hr_headers,
        ).status_code
        == 422
    )

    # (b) store_manager self-approval -> 403; terminated employee -> 403
    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=mgr_a_headers,
            json={"employee_id": str(emp_mgr.id), "dates": ["2026-11-12"], "action": "approve"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=mgr_a_headers,
            json={"employee_id": str(emp_term.id), "dates": ["2026-11-12"], "action": "approve"},
        ).status_code
        == 403
    )

    # Employee re-submitting already-approved day -> 409
    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=hr_headers,
            json={"employee_id": str(emp1.id), "dates": ["2026-11-12"], "action": "approve"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=emp1_headers,
            json={"employee_id": str(emp1.id), "dates": ["2026-11-12"], "action": "submit"},
        ).status_code
        == 409
    )

    # (c) EMPLOYEE ledger excludes DRAFT shifts from planned_minutes (falls back to 480m)
    env["shift_repo"].shifts.append(
        Shift(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=store_a_id,
            employee_id=emp1.id,
            start_at_utc=datetime(2026, 11, 12, 8, 0, tzinfo=UTC),
            end_at_utc=datetime(2026, 11, 12, 12, 0, tzinfo=UTC),
            planned_break_minutes=0,
            status=ShiftStatus.DRAFT,
            notes="",
            created_by_user_id=env["hr_id"],
            published_by_user_id=None,
            published_at_utc=None,
            retention_until=now_utc + timedelta(days=1460),
            created_at=now_utc,
            updated_at=now_utc,
        )
    )
    r_emp_ledger = client.get(
        "/api/v1/attendance/ledger?date_from=2026-11-12&date_to=2026-11-12",
        headers=emp1_headers,
    )
    assert r_emp_ledger.status_code == 200
    assert r_emp_ledger.json()["items"][0]["daily_summaries"][0]["planned_minutes"] == 480

    # (d) day-reviews blocked (409) when secondary session store_b_id is closed
    env["payroll_repo"].closings.append(
        MonthlyClosing(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=store_b_id,
            period_month="2026-11",
            status=MonthlyClosingStatus.CLOSED,
            revision_number=1,
            closed_by_user_id=env["hr_id"],
            closed_at_utc=now_utc,
            reopened_by_user_id=None,
            reopened_at_utc=None,
            reopen_reason="",
            notes="",
            retention_until=now_utc + timedelta(days=1460),
            created_at=now_utc,
            updated_at=now_utc,
        )
    )
    assert (
        client.post(
            "/api/v1/attendance/day-reviews",
            headers=hr_headers,
            json={"employee_id": str(emp1.id), "dates": ["2026-11-12"], "action": "approve"},
        ).status_code
        == 409
    )
