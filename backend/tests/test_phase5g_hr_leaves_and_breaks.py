"""Phase 5G tests: HR Leave Entitlements, Direct Assign, 3-Tier Break Policies & Dashboard KPIs."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.domain.attendance import AlertCode, evaluate_attendance_ledger
from app.domain.break_policies import (
    BreakMode,
    BreakPolicy,
    BreakScopeType,
    BreakTrackingMode,
    BreakWindow,
    EffectiveBreakPolicy,
)
from app.domain.leaves import LeaveEntitlement, LeaveEntitlementAdjustment
from app.domain.payroll import MonthlyClosing, MonthlyClosingStatus
from app.interfaces.api.deps import (
    get_break_policy_repository,
    get_leave_entitlement_repository,
)
from tests.test_phase5e_workflows_and_reports import _build_env


class InMemoryLeaveEntitlementRepo:
    def __init__(self) -> None:
        self.entitlements: dict[tuple[UUID, UUID, int], LeaveEntitlement] = {}
        self.adjustments: list[LeaveEntitlementAdjustment] = []

    async def get_by_employee_year(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        year: int,
    ) -> LeaveEntitlement | None:
        return self.entitlements.get((tenant_id, employee_id, year))

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        year: int | None = None,
        employee_id: UUID | None = None,
    ) -> list[LeaveEntitlement]:
        return [
            e
            for (tid, eid, yr), e in self.entitlements.items()
            if tid == tenant_id
            and (year is None or yr == year)
            and (employee_id is None or eid == employee_id)
        ]

    async def upsert(self, entitlement: LeaveEntitlement) -> LeaveEntitlement:
        self.entitlements[(entitlement.tenant_id, entitlement.employee_id, entitlement.year)] = (
            entitlement
        )
        return entitlement

    async def list_adjustments(
        self,
        tenant_id: UUID,
        *,
        year: int | None = None,
        employee_id: UUID | None = None,
    ) -> list[LeaveEntitlementAdjustment]:
        return [
            a
            for a in reversed(self.adjustments)
            if a.tenant_id == tenant_id
            and (year is None or a.year == year)
            and (employee_id is None or a.employee_id == employee_id)
        ]

    async def append_adjustment(
        self,
        adjustment: LeaveEntitlementAdjustment,
    ) -> LeaveEntitlementAdjustment:
        self.adjustments.append(adjustment)
        return adjustment


class InMemoryBreakPolicyRepo:
    def __init__(self) -> None:
        self.policies: dict[UUID, BreakPolicy] = {}

    async def get_by_id(self, tenant_id: UUID, policy_id: UUID) -> BreakPolicy | None:
        p = self.policies.get(policy_id)
        if p is None or p.tenant_id != tenant_id or p.deleted_at is not None:
            return None
        return p

    async def get_by_scope(
        self,
        tenant_id: UUID,
        *,
        scope_type: BreakScopeType,
        store_id: UUID | None = None,
        employee_id: UUID | None = None,
    ) -> BreakPolicy | None:
        for p in self.policies.values():
            if p.tenant_id != tenant_id or p.deleted_at is not None or p.scope_type != scope_type:
                continue
            if scope_type == BreakScopeType.TENANT:
                return p
            if scope_type == BreakScopeType.STORE and p.store_id == store_id:
                return p
            if scope_type == BreakScopeType.EMPLOYEE and p.employee_id == employee_id:
                return p
        return None

    async def list_by_tenant(self, tenant_id: UUID) -> list[BreakPolicy]:
        return [
            p
            for p in self.policies.values()
            if p.tenant_id == tenant_id and p.deleted_at is None
        ]

    async def upsert(self, policy: BreakPolicy) -> BreakPolicy:
        existing = await self.get_by_scope(
            policy.tenant_id,
            scope_type=policy.scope_type,
            store_id=policy.store_id,
            employee_id=policy.employee_id,
        )
        if existing is not None and existing.id != policy.id:
            del self.policies[existing.id]
        self.policies[policy.id] = policy
        return policy

    async def soft_delete(
        self,
        tenant_id: UUID,
        policy_id: UUID,
        *,
        deleted_at: datetime,
        updated_by_user_id: UUID,
    ) -> BreakPolicy | None:
        p = await self.get_by_id(tenant_id, policy_id)
        if p is None:
            return None
        from dataclasses import replace

        deleted = replace(p, deleted_at=deleted_at, updated_at=deleted_at)
        self.policies[policy_id] = deleted
        return deleted


def _build_5g_env(tmp_path: Path, now_utc: datetime | None = None):
    env = _build_env(tmp_path, now_utc=now_utc)
    ent_repo = InMemoryLeaveEntitlementRepo()
    pol_repo = InMemoryBreakPolicyRepo()
    app = env["client"].app
    app.dependency_overrides[get_leave_entitlement_repository] = lambda: ent_repo
    app.dependency_overrides[get_break_policy_repository] = lambda: pol_repo
    env["ent_repo"] = ent_repo
    env["pol_repo"] = pol_repo
    return env


def test_leave_entitlements_floor_adjustments_and_rbac(tmp_path: Path) -> None:
    env = _build_5g_env(tmp_path)
    client: TestClient = env["client"]
    hr_headers = env["headers"](env["hr_id"])
    mgr_headers = env["headers"](env["mgr_a_id"])
    acc_headers = env["headers"](env["acc_id"])
    emp1_headers = env["headers"](env["emp1_uid"])
    emp1 = env["emp1"]
    emp_bcn = env["emp_bcn"]

    # RBAC: employee & accountant cannot update entitlements (403)
    r_emp_put = client.put(
        f"/api/v1/leaves/entitlements/{emp1.id}",
        headers=emp1_headers,
        json={"year": 2026, "base_vacation_days": 32, "reason": "Try self"},
    )
    assert r_emp_put.status_code == 403

    r_acc_put = client.put(
        f"/api/v1/leaves/entitlements/{emp1.id}",
        headers=acc_headers,
        json={"year": 2026, "base_vacation_days": 32, "reason": "Try acc"},
    )
    assert r_acc_put.status_code == 403

    # Out-of-scope store_manager cannot update BCN employee
    r_mgr_oob = client.put(
        f"/api/v1/leaves/entitlements/{emp_bcn.id}",
        headers=mgr_headers,
        json={"year": 2026, "base_vacation_days": 32, "reason": "OOB"},
    )
    assert r_mgr_oob.status_code == 403

    # Store manager cannot bulk-update (owner/hr only)
    r_mgr_bulk = client.post(
        "/api/v1/leaves/entitlements/bulk",
        headers=mgr_headers,
        json={"year": 2026, "base_vacation_days": 31, "reason": "Bulk"},
    )
    assert r_mgr_bulk.status_code == 403

    # Statutory 30-day floor enforced -> 422
    r_low_base = client.put(
        f"/api/v1/leaves/entitlements/{emp1.id}",
        headers=hr_headers,
        json={"year": 2026, "base_vacation_days": 29, "reason": "Too low"},
    )
    assert r_low_base.status_code == 422

    r_low_total = client.put(
        f"/api/v1/leaves/entitlements/{emp1.id}",
        headers=hr_headers,
        json={
            "year": 2026,
            "base_vacation_days": 30,
            "carryover_days": -2,
            "comp_off_days": 0,
            "reason": "Below 30 total",
        },
    )
    assert r_low_total.status_code == 422

    # Valid single adjustment: 30 base + 3 carryover + 2 comp_off = 35
    r_put = client.put(
        f"/api/v1/leaves/entitlements/{emp1.id}",
        headers=hr_headers,
        json={
            "year": 2026,
            "base_vacation_days": 30,
            "carryover_days": 3,
            "comp_off_days": 2,
            "reason": "2025 carryover + weekend comp-off",
        },
    )
    assert r_put.status_code == 200
    data = r_put.json()
    assert data["entitled_days"] == 35
    assert data["remaining_days"] == 35
    assert len(data["adjustments"]) == 1
    assert data["adjustments"][0]["delta_days"] == 5
    assert data["adjustments"][0]["previous_total_days"] == 30
    assert data["adjustments"][0]["new_total_days"] == 35

    # Balance endpoint & dashboard/me reflect updated entitlement
    r_bal = client.get(
        f"/api/v1/leaves/balance?employee_id={emp1.id}&year=2026",
        headers=emp1_headers,
    )
    assert r_bal.status_code == 200
    assert r_bal.json()["entitled_days"] == 35

    # Bulk update for Store A
    r_bulk = client.post(
        "/api/v1/leaves/entitlements/bulk",
        headers=hr_headers,
        json={
            "year": 2026,
            "store_id": str(env["store_a_id"]),
            "base_vacation_days": 32,
            "carryover_days": 0,
            "comp_off_days": 0,
            "reason": "Annual company agreement 2026",
        },
    )
    assert r_bulk.status_code == 200
    assert len(r_bulk.json()["items"]) == 2
    assert all(item["entitled_days"] == 32 for item in r_bulk.json()["items"])


def test_direct_assign_leave_and_justificante_preserves_flag(tmp_path: Path) -> None:
    env = _build_5g_env(tmp_path)
    client: TestClient = env["client"]
    hr_headers = env["headers"](env["hr_id"])
    mgr_headers = env["headers"](env["mgr_a_id"])
    acc_headers = env["headers"](env["acc_id"])
    emp1_headers = env["headers"](env["emp1_uid"])
    emp1 = env["emp1"]
    emp_bcn = env["emp_bcn"]
    payroll_repo = env["payroll_repo"]

    # RBAC 403 for employee, accountant, out-of-scope manager
    for bad_headers, target_id in (
        (emp1_headers, emp1.id),
        (acc_headers, emp1.id),
        (mgr_headers, emp_bcn.id),
    ):
        r_bad = client.post(
            "/api/v1/leaves/assign",
            headers=bad_headers,
            json={
                "employee_id": str(target_id),
                "leave_type": "vacation",
                "start_date": "2026-12-01",
                "end_date": "2026-12-03",
                "reason": "Direct assign",
            },
        )
        assert r_bad.status_code == 403

    # HR directly assigns 3 vacation days -> auto-approved, balance deducted
    r_ok = client.post(
        "/api/v1/leaves/assign",
        headers=hr_headers,
        json={
            "employee_id": str(emp1.id),
            "leave_type": "vacation",
            "start_date": "2026-12-01",
            "end_date": "2026-12-03",
            "reason": "Scheduled winter break",
        },
    )
    assert r_ok.status_code == 201
    assigned = r_ok.json()
    assert assigned["status"] == "approved"
    assert assigned["assigned_directly"] is True
    assert assigned["assigned_by_user_id"] == str(env["hr_id"])

    r_bal = client.get(
        f"/api/v1/leaves/balance?employee_id={emp1.id}&year=2026",
        headers=hr_headers,
    )
    assert r_bal.status_code == 200
    assert r_bal.json()["approved_days"] == 3
    assert r_bal.json()["remaining_days"] == 27

    # Overlap -> 409
    r_ov = client.post(
        "/api/v1/leaves/assign",
        headers=hr_headers,
        json={
            "employee_id": str(emp1.id),
            "leave_type": "vacation",
            "start_date": "2026-12-02",
            "end_date": "2026-12-04",
            "reason": "Overlap",
        },
    )
    assert r_ov.status_code == 409

    # Assign sick_leave and upload justificante -> preserves assigned_directly
    r_sick = client.post(
        "/api/v1/leaves/assign",
        headers=hr_headers,
        json={
            "employee_id": str(emp1.id),
            "leave_type": "sick_leave",
            "start_date": "2026-12-10",
            "end_date": "2026-12-11",
            "reason": "Flu",
        },
    )
    assert r_sick.status_code == 201
    sick_id = r_sick.json()["id"]

    r_up = client.post(
        f"/api/v1/leaves/{sick_id}/justificante",
        headers=emp1_headers,
        files={"file": ("parte.pdf", b"%PDF-1.4 test justificante", "application/pdf")},
    )
    assert r_up.status_code == 200
    assert r_up.json()["assigned_directly"] is True
    assert r_up.json()["assigned_by_user_id"] == str(env["hr_id"])
    assert r_up.json()["medical_document_id"] is not None

    # Closed payroll month -> 409
    from uuid import uuid4

    payroll_repo.closings.append(
        MonthlyClosing(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=env["store_a_id"],
            period_month="2026-10",
            status=MonthlyClosingStatus.CLOSED,
            revision_number=1,
            closed_by_user_id=env["hr_id"],
            closed_at_utc=datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
            reopened_by_user_id=None,
            reopened_at_utc=None,
            reopen_reason="",
            notes="",
            retention_until=datetime(2030, 11, 1, 10, 0, tzinfo=UTC),
            created_at=datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
            updated_at=datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
        )
    )
    r_closed = client.post(
        "/api/v1/leaves/assign",
        headers=hr_headers,
        json={
            "employee_id": str(emp1.id),
            "leave_type": "day_off",
            "start_date": "2026-10-15",
            "end_date": "2026-10-15",
            "reason": "Closed month",
        },
    )
    assert r_closed.status_code == 409


def test_3tier_break_policy_inheritance_and_labor_law_engine(tmp_path: Path) -> None:
    t0 = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
    env = _build_5g_env(tmp_path, now_utc=t0)
    client: TestClient = env["client"]
    hr_headers = env["headers"](env["hr_id"])
    mgr_headers = env["headers"](env["mgr_a_id"])
    acc_headers = env["headers"](env["acc_id"])
    emp1_headers = env["headers"](env["emp1_uid"])
    emp1 = env["emp1"]
    emp_bcn = env["emp_bcn"]
    store_a_id = env["store_a_id"]
    store_b_id = env["store_b_id"]

    # RBAC 403 checks
    assert (
        client.put(
            "/api/v1/break-policies/tenant",
            headers=mgr_headers,
            json={"break_mode": "fixed_duration", "duration_minutes": 15},
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/v1/break-policies/store/{store_b_id}",
            headers=mgr_headers,
            json={"break_mode": "fixed_duration", "duration_minutes": 15},
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/v1/break-policies/employee/{emp_bcn.id}",
            headers=mgr_headers,
            json={"break_mode": "fixed_duration", "duration_minutes": 15},
        ).status_code
        == 403
    )
    for bad_h in (emp1_headers, acc_headers):
        assert (
            client.put(
                f"/api/v1/break-policies/employee/{emp1.id}",
                headers=bad_h,
                json={"break_mode": "fixed_duration", "duration_minutes": 15},
            ).status_code
            == 403
        )

    # 1. Tenant policy: 15m paid bocadillo (manual_punch)
    r_ten = client.put(
        "/api/v1/break-policies/tenant",
        headers=hr_headers,
        json={
            "break_mode": "fixed_duration",
            "duration_minutes": 15,
            "is_paid": True,
            "tracking_mode": "manual_punch",
        },
    )
    assert r_ten.status_code == 200

    r_eff_1 = client.get(f"/api/v1/break-policies/effective/{emp1.id}", headers=emp1_headers)
    assert r_eff_1.status_code == 200
    assert r_eff_1.json()["inherited_from"] == "tenant"
    assert r_eff_1.json()["is_paid"] is True

    # Dashboard/me exposes effective_break_policy
    r_me = client.get("/api/v1/dashboard/me", headers=emp1_headers)
    assert r_me.status_code == 200
    assert r_me.json()["clock_state"]["effective_break_policy"]["inherited_from"] == "tenant"

    # 2. Store A override: 60m unpaid auto_deduct on >= 6h
    r_st = client.put(
        f"/api/v1/break-policies/store/{store_a_id}",
        headers=mgr_headers,
        json={
            "break_mode": "fixed_duration",
            "duration_minutes": 60,
            "is_paid": False,
            "tracking_mode": "auto_deduct",
            "min_shift_minutes_for_auto_deduct": 360,
        },
    )
    assert r_st.status_code == 200
    r_eff_2 = client.get(f"/api/v1/break-policies/effective/{emp1.id}", headers=hr_headers)
    assert r_eff_2.json()["inherited_from"] == "store"
    assert r_eff_2.json()["duration_minutes"] == 60

    # 3. Employee override: 30m paid
    r_emp_pol = client.put(
        f"/api/v1/break-policies/employee/{emp1.id}",
        headers=hr_headers,
        json={
            "break_mode": "fixed_duration",
            "duration_minutes": 30,
            "is_paid": True,
            "tracking_mode": "manual_punch",
        },
    )
    assert r_emp_pol.status_code == 200
    emp_pol_id = r_emp_pol.json()["id"]
    assert (
        client.get(f"/api/v1/break-policies/effective/{emp1.id}", headers=hr_headers).json()[
            "inherited_from"
        ]
        == "employee"
    )

    # Delete employee override -> falls back to store
    assert (
        client.delete(f"/api/v1/break-policies/{emp_pol_id}", headers=hr_headers).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/break-policies/effective/{emp1.id}", headers=hr_headers).json()[
            "inherited_from"
        ]
        == "store"
    )

    # Pure domain tests: Paid 15m bocadillo vs Unpaid auto_deduct vs Split shift
    from app.domain.attendance import AttendanceEvent, AttendanceEventType, AttendanceSource

    def _ev(etype: AttendanceEventType, hour: int, minute: int = 0) -> AttendanceEvent:
        ts = datetime(2026, 11, 20, hour, minute, tzinfo=UTC)
        return AttendanceEvent(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=store_a_id,
            employee_id=emp1.id,
            event_type=etype,
            occurred_at_utc=ts,
            store_timezone="Europe/Madrid",
            source=AttendanceSource.WEB,
            recorded_by_user_id=env["emp1_uid"],
            recorded_at_utc=ts,
            retention_until=datetime(2030, 11, 20, hour, minute, tzinfo=UTC),
        )

    # Paid 15m bocadillo: 09:00-17:00 with 11:00-11:15 break -> net_minutes == 480 (8h)
    paid_policy = EffectiveBreakPolicy(
        policy_id=None,
        inherited_from="tenant",
        break_mode=BreakMode.FIXED_DURATION,
        duration_minutes=15,
        windows=(),
        is_paid=True,
        tracking_mode=BreakTrackingMode.MANUAL_PUNCH,
        min_shift_minutes_for_auto_deduct=360,
        auto_end_manual_break=True,
    )
    rep_paid = evaluate_attendance_ledger(
        employee_id=emp1.id,
        events=[
            _ev(AttendanceEventType.CLOCK_IN, 9),
            _ev(AttendanceEventType.BREAK_START, 11, 0),
            _ev(AttendanceEventType.BREAK_END, 11, 15),
            _ev(AttendanceEventType.CLOCK_OUT, 17),
        ],
        birth_date=emp1.birth_date,
        work_schedule_type=emp1.work_schedule_type,
        contracted_weekly_hours=emp1.contracted_weekly_hours,
        break_policy=paid_policy,
    )
    assert rep_paid.sessions[0].break_minutes == 15
    assert rep_paid.sessions[0].paid_break_minutes == 15
    assert rep_paid.sessions[0].net_minutes == 480

    # Auto-deduct unpaid 60m on >= 6h without manual punch: 09:00-17:00 -> 60m deducted, net=420
    auto_policy = EffectiveBreakPolicy(
        policy_id=None,
        inherited_from="store",
        break_mode=BreakMode.FIXED_DURATION,
        duration_minutes=60,
        windows=(),
        is_paid=False,
        tracking_mode=BreakTrackingMode.AUTO_DEDUCT,
        min_shift_minutes_for_auto_deduct=360,
        auto_end_manual_break=False,
    )
    rep_auto = evaluate_attendance_ledger(
        employee_id=emp1.id,
        events=[_ev(AttendanceEventType.CLOCK_IN, 9), _ev(AttendanceEventType.CLOCK_OUT, 17)],
        birth_date=emp1.birth_date,
        work_schedule_type=emp1.work_schedule_type,
        contracted_weekly_hours=emp1.contracted_weekly_hours,
        break_policy=auto_policy,
    )
    assert rep_auto.sessions[0].auto_deducted_break_minutes == 60
    assert rep_auto.sessions[0].net_minutes == 420
    assert not any(a.code == AlertCode.MISSING_CONTINUOUS_BREAK for a in rep_auto.alerts)

    # Fixed-window auto-deduct: 13:00-14:00 window
    win_policy = EffectiveBreakPolicy(
        policy_id=None,
        inherited_from="store",
        break_mode=BreakMode.FIXED_WINDOW,
        duration_minutes=None,
        windows=(BreakWindow(start="13:00", end="14:00"),),
        is_paid=False,
        tracking_mode=BreakTrackingMode.AUTO_DEDUCT,
        min_shift_minutes_for_auto_deduct=360,
        auto_end_manual_break=False,
    )
    rep_win = evaluate_attendance_ledger(
        employee_id=emp1.id,
        events=[_ev(AttendanceEventType.CLOCK_IN, 8), _ev(AttendanceEventType.CLOCK_OUT, 16)],
        birth_date=emp1.birth_date,
        work_schedule_type=emp1.work_schedule_type,
        contracted_weekly_hours=emp1.contracted_weekly_hours,
        break_policy=win_policy,
    )
    assert rep_win.sessions[0].auto_deducted_break_minutes == 60
    assert rep_win.sessions[0].net_minutes == 420

    # Split-shift: 09:00-14:00 + 16:00-19:00 -> no Art. 34.4 MISSING_CONTINUOUS_BREAK
    rep_split = evaluate_attendance_ledger(
        employee_id=emp1.id,
        events=[
            _ev(AttendanceEventType.CLOCK_IN, 9),
            _ev(AttendanceEventType.CLOCK_OUT, 14),
            _ev(AttendanceEventType.CLOCK_IN, 16),
            _ev(AttendanceEventType.CLOCK_OUT, 19),
        ],
        birth_date=emp1.birth_date,
        work_schedule_type=emp1.work_schedule_type,
        contracted_weekly_hours=emp1.contracted_weekly_hours,
    )
    assert not any(a.code == AlertCode.MISSING_CONTINUOUS_BREAK for a in rep_split.alerts)

    # Split-shift > 9h triggers Art. 34.3 DAILY_MAX_EXCEEDED
    rep_split_over = evaluate_attendance_ledger(
        employee_id=emp1.id,
        events=[
            _ev(AttendanceEventType.CLOCK_IN, 8),
            _ev(AttendanceEventType.CLOCK_OUT, 13),
            _ev(AttendanceEventType.CLOCK_IN, 15),
            _ev(AttendanceEventType.CLOCK_OUT, 20),
        ],
        birth_date=emp1.birth_date,
        work_schedule_type=emp1.work_schedule_type,
        contracted_weekly_hours=emp1.contracted_weekly_hours,
    )
    assert any(a.code == AlertCode.DAILY_MAX_EXCEEDED for a in rep_split_over.alerts)


def test_admin_dashboard_4_kpis_departments_and_gdpr_art9(tmp_path: Path) -> None:
    t0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    env = _build_5g_env(tmp_path, now_utc=t0)
    client: TestClient = env["client"]
    hr_headers = env["headers"](env["hr_id"])
    acc_headers = env["headers"](env["acc_id"])
    emp1 = env["emp1"]

    # Create a sick_leave without justificante
    r_sick = client.post(
        "/api/v1/leaves/assign",
        headers=hr_headers,
        json={
            "employee_id": str(emp1.id),
            "leave_type": "sick_leave",
            "start_date": "2026-12-05",
            "end_date": "2026-12-06",
            "reason": "Medical check",
        },
    )
    assert r_sick.status_code == 201

    # Create an announcement requiring acknowledgement
    r_ann = client.post(
        "/api/v1/announcements",
        headers=hr_headers,
        data={
            "category": "policy",
            "title": "Break Policy Update",
            "body": "Please acknowledge the new break policy.",
            "requires_acknowledgement": "true",
        },
    )
    assert r_ann.status_code == 201

    # HR sees pending_justificantes_count == 1, unacknowledged_announcements_count >= 1,
    # and sorted available_departments
    r_hr = client.get("/api/v1/dashboard/admin", headers=hr_headers)
    assert r_hr.status_code == 200
    hr_data = r_hr.json()
    assert hr_data["pending_justificantes_count"] == 1
    assert hr_data["unacknowledged_announcements_count"] >= 1
    assert hr_data["available_departments"] == ["Cocina", "Sala"]
    assert isinstance(hr_data["attendance_alerts_count"], int)

    # Accountant GDPR Art. 9 guard: pending_justificantes_count MUST be 0
    r_acc = client.get("/api/v1/dashboard/admin", headers=acc_headers)
    assert r_acc.status_code == 200
    assert r_acc.json()["pending_justificantes_count"] == 0

    # P1-1: unacknowledged_announcements_count respects store_id & department filters
    r_scoped = client.get(
        f"/api/v1/dashboard/admin?store_id={env['store_a_id']}&department=Sala",
        headers=hr_headers,
    )
    assert r_scoped.status_code == 200
    assert r_scoped.json()["unacknowledged_announcements_count"] == 1


def test_p1_p2_red_team_regressions(tmp_path: Path) -> None:
    from dataclasses import replace
    from datetime import date
    from uuid import uuid4

    from app.domain.attendance import (
        AttendanceEvent,
        AttendanceEventType,
        AttendanceSource,
        compute_auto_break_end_utc,
    )
    from app.domain.employees import EmploymentStatus
    from app.domain.organization import Membership
    from app.domain.scheduling import Shift, ShiftStatus
    from app.domain.security import MembershipStatus, UserRole
    from app.infrastructure.supabase_jwt import sign_dev_hs256_token
    from app.interfaces.api.deps import (
        get_employee_repository,
        get_shift_repository,
        get_tenant_repository,
    )
    from tests.test_api_org_rbac import DEV_SECRET

    t0 = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
    env = _build_5g_env(tmp_path, now_utc=t0)
    client: TestClient = env["client"]
    clock = env["clock"]
    hr_headers = env["headers"](env["hr_id"])
    emp1_headers = env["headers"](env["emp1_uid"])
    emp1 = env["emp1"]
    emp2 = env["emp2"]
    store_a_id = env["store_a_id"]
    app = client.app
    emp_repo = app.dependency_overrides[get_employee_repository]()
    org_repos = app.dependency_overrides[get_tenant_repository]()
    shift_repo = app.dependency_overrides[get_shift_repository]()

    # P1-2: Department-scoped store_manager cannot upsert or delete store-wide break policy
    dept_mgr_uid = uuid4()
    dept_mgr_mid = uuid4()
    org_repos.memberships[dept_mgr_mid] = Membership(
        id=dept_mgr_mid,
        tenant_id=env["tenant_id"],
        user_id=dept_mgr_uid,
        email="deptmgr@dragon.es",
        display_name="Dept Mgr",
        role=UserRole.STORE_MANAGER,
        status=MembershipStatus.ACTIVE,
        store_ids=frozenset({store_a_id}),
        department_scope="Sala",
        created_at=t0,
        updated_at=t0,
    )
    dept_tok = sign_dev_hs256_token(user_id=dept_mgr_uid, email="d@d.es", secret=DEV_SECRET)
    dept_mgr_headers = {
        "Authorization": f"Bearer {dept_tok}",
        "X-Tenant-Id": str(env["tenant_id"]),
    }

    r_store_pol = client.put(
        f"/api/v1/break-policies/store/{store_a_id}",
        headers=hr_headers,
        json={"break_mode": "fixed_duration", "duration_minutes": 30},
    )
    assert r_store_pol.status_code == 200
    store_pol_id = r_store_pol.json()["id"]

    assert (
        client.put(
            f"/api/v1/break-policies/store/{store_a_id}",
            headers=dept_mgr_headers,
            json={"break_mode": "fixed_duration", "duration_minutes": 45},
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/break-policies/{store_pol_id}",
            headers=dept_mgr_headers,
        ).status_code
        == 403
    )

    # P1-3: ReviewLeaveRequestUseCase uses LeaveEntitlement on final approval
    assert (
        client.put(
            f"/api/v1/leaves/entitlements/{emp1.id}",
            headers=hr_headers,
            json={
                "year": 2027,
                "base_vacation_days": 30,
                "carryover_days": 5,
                "comp_off_days": 0,
                "reason": "Carryover 5d",
            },
        ).status_code
        == 200
    )
    r_req = client.post(
        "/api/v1/leaves",
        headers=emp1_headers,
        json={
            "leave_type": "vacation",
            "start_date": "2027-07-01",
            "end_date": "2027-08-01",  # 32 days (> 30 base, <= 35 with carryover)
            "reason": "Long summer trip",
        },
    )
    assert r_req.status_code == 201
    leave_id = r_req.json()["id"]
    mgr_headers = env["headers"](env["mgr_a_id"])
    assert (
        client.post(
            f"/api/v1/leaves/{leave_id}/review",
            headers=mgr_headers,
            json={"approve": True, "review_note": "OK mgr"},
        ).status_code
        == 200
    )
    r_hr_rev = client.post(
        f"/api/v1/leaves/{leave_id}/review",
        headers=hr_headers,
        json={"approve": True, "review_note": "OK hr"},
    )
    assert r_hr_rev.status_code == 200
    assert r_hr_rev.json()["status"] == "approved"

    # P1-4: DirectAssignLeaveUseCase rejects published shift conflict with 409
    shift_repo.shifts.append(
        Shift(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=store_a_id,
            employee_id=emp1.id,
            start_at_utc=datetime(2026, 12, 20, 8, 0, tzinfo=UTC),
            end_at_utc=datetime(2026, 12, 20, 16, 0, tzinfo=UTC),
            planned_break_minutes=0,
            status=ShiftStatus.PUBLISHED,
            notes="Morning",
            created_by_user_id=env["hr_id"],
            published_by_user_id=env["hr_id"],
            published_at_utc=t0,
            retention_until=datetime(2030, 12, 20, 16, 0, tzinfo=UTC),
            created_at=t0,
            updated_at=t0,
        )
    )
    r_conflict = client.post(
        "/api/v1/leaves/assign",
        headers=hr_headers,
        json={
            "employee_id": str(emp1.id),
            "leave_type": "day_off",
            "start_date": "2026-12-20",
            "end_date": "2026-12-20",
            "reason": "Conflicts with shift",
        },
    )
    assert r_conflict.status_code == 409

    # P2-5: UpsertLeaveEntitlementUseCase rejects terminated employee
    emp_repo.employees[emp2.id] = replace(
        emp2,
        employment_status=EmploymentStatus.TERMINATED,
        termination_date=date(2026, 11, 1),
    )
    r_term = client.put(
        f"/api/v1/leaves/entitlements/{emp2.id}",
        headers=hr_headers,
        json={"year": 2026, "base_vacation_days": 30, "reason": "Frozen"},
    )
    assert r_term.status_code == 422

    # P2-6: Paid break live counter in GetMyDashboardUseCase does not subtract break
    assert (
        client.put(
            f"/api/v1/break-policies/employee/{emp1.id}",
            headers=hr_headers,
            json={
                "break_mode": "fixed_duration",
                "duration_minutes": 15,
                "is_paid": True,
                "tracking_mode": "manual_punch",
                "auto_end_manual_break": True,
            },
        ).status_code
        == 200
    )
    clock.now = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
    assert (
        client.post(
            "/api/v1/attendance/clock",
            headers=emp1_headers,
            json={"event_type": "clock_in", "source": "web"},
        ).status_code
        == 201
    )
    clock.now = datetime(2026, 11, 26, 11, 0, tzinfo=UTC)
    assert (
        client.post(
            "/api/v1/attendance/clock",
            headers=emp1_headers,
            json={"event_type": "break_start", "source": "web"},
        ).status_code
        == 201
    )
    clock.now = datetime(2026, 11, 26, 11, 10, tzinfo=UTC)
    r_live = client.get("/api/v1/dashboard/me", headers=emp1_headers)
    assert r_live.status_code == 200
    # 09:00 -> 11:10 is 130m; since break is paid, worked_minutes_today == 130
    assert r_live.json()["clock_state"]["worked_minutes_today"] == 130

    # P2-7: Multi-window auto_break_end_utc uses matching window (not sum of all windows)
    multi_win_pol = EffectiveBreakPolicy(
        policy_id=None,
        inherited_from="store",
        break_mode=BreakMode.FIXED_WINDOW,
        duration_minutes=None,
        windows=(
            BreakWindow(start="11:00", end="11:15"),
            BreakWindow(start="15:00", end="15:30"),
        ),
        is_paid=False,
        tracking_mode=BreakTrackingMode.MANUAL_PUNCH,
        min_shift_minutes_for_auto_deduct=360,
        auto_end_manual_break=True,
    )
    # In Europe/Madrid (UTC+1 in Nov), 10:00 UTC is 11:00 local -> matches 11:00-11:15 (10:15 UTC)
    ev_in = AttendanceEvent(
        id=uuid4(),
        tenant_id=env["tenant_id"],
        store_id=store_a_id,
        employee_id=emp1.id,
        event_type=AttendanceEventType.CLOCK_IN,
        occurred_at_utc=datetime(2026, 11, 20, 8, 0, tzinfo=UTC),
        store_timezone="Europe/Madrid",
        source=AttendanceSource.WEB,
        recorded_by_user_id=env["emp1_uid"],
        recorded_at_utc=datetime(2026, 11, 20, 8, 0, tzinfo=UTC),
        retention_until=datetime(2030, 11, 20, 8, 0, tzinfo=UTC),
    )
    ev_bs = AttendanceEvent(
        id=uuid4(),
        tenant_id=env["tenant_id"],
        store_id=store_a_id,
        employee_id=emp1.id,
        event_type=AttendanceEventType.BREAK_START,
        occurred_at_utc=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
        store_timezone="Europe/Madrid",
        source=AttendanceSource.WEB,
        recorded_by_user_id=env["emp1_uid"],
        recorded_at_utc=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
        retention_until=datetime(2030, 11, 20, 10, 0, tzinfo=UTC),
    )
    sched, due = compute_auto_break_end_utc(
        [ev_in, ev_bs],
        emp1.id,
        None,
        datetime(2026, 11, 20, 10, 10, tzinfo=UTC),
        effective_policy=multi_win_pol,
    )
    assert sched == datetime(2026, 11, 20, 10, 15, tzinfo=UTC)
    assert due is None
