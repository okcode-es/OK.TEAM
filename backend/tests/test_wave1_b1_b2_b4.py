"""Wave 1 / B1, B2, B4: Expiry alerts, Kiosk PIN & cross-store, overnight & cross-store shifts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.domain.employees import EmploymentStatus, hash_kiosk_pin
from app.domain.leaves import LeaveRequest, LeaveStatus, LeaveType
from app.domain.organization import Membership
from app.domain.payroll import MonthlyClosing, MonthlyClosingStatus
from app.domain.security import MembershipStatus, UserRole
from app.interfaces.api.deps import (
    get_attendance_repository,
    get_employee_repository,
    get_leave_repository,
    get_membership_repository,
    get_store_repository,
)
from tests.test_phase5e_workflows_and_reports import _build_env

_NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def _add_member(
    env: dict[str, Any],
    *,
    role: UserRole,
    store_ids: frozenset[UUID] = frozenset(),
    department_scope: str | None = None,
) -> UUID:
    repo = env["client"].app.dependency_overrides[get_membership_repository]()
    uid = uuid4()
    mid = uuid4()
    repo._state.memberships[mid] = Membership(
        id=mid,
        tenant_id=env["tenant_id"],
        user_id=uid,
        email=f"{role.value}-{str(uid)[:6]}@dragon.es",
        display_name=role.value,
        role=role,
        status=MembershipStatus.ACTIVE,
        store_ids=store_ids,
        department_scope=department_scope,
        created_at=_NOW,
        updated_at=_NOW,
    )
    return uid


# ---------------------------------------------------------------------------
# B1: Work permit & contract end dates, GDPR anonymize, and 60d dashboard alerts
# ---------------------------------------------------------------------------


def test_b1_expiry_dates_crud_patch_unset_null_anonymize_and_privacy(tmp_path: Path) -> None:
    env = _build_env(tmp_path, now_utc=_NOW)
    client: TestClient = env["client"]
    hr_h = env["headers"](env["hr_id"])
    emp_h = env["headers"](env["emp1_uid"])

    # Invalid: contract_end_date before hire_date -> 422
    bad_create = client.post(
        "/api/v1/employees",
        headers=hr_h,
        json={
            "primary_store_id": str(env["store_a_id"]),
            "employee_code": "EMP-EXP-BAD",
            "full_name": "Bad Contract Date",
            "hire_date": "2026-06-01",
            "contract_end_date": "2026-05-01",
        },
    )
    assert bad_create.status_code == 422

    # Create with both expiry dates
    created = client.post(
        "/api/v1/employees",
        headers=hr_h,
        json={
            "primary_store_id": str(env["store_a_id"]),
            "employee_code": "EMP-EXP-1",
            "full_name": "Sofia Permit",
            "department": "ventas",
            "hire_date": "2026-01-01",
            "work_permit_expiry_date": "2026-12-20",
            "contract_end_date": "2027-01-10",
        },
    )
    assert created.status_code == 201
    body = created.json()
    emp_id = body["id"]
    assert body["work_permit_expiry_date"] == "2026-12-20"
    assert body["contract_end_date"] == "2027-01-10"
    assert body["has_kiosk_pin"] is False
    assert "kiosk_pin_hash" not in body

    # PATCH with UNSET (omitted) preserves both dates
    base_patch = {
        "primary_store_id": str(env["store_a_id"]),
        "employee_code": "EMP-EXP-1",
        "full_name": "Sofia Permit",
        "department": "ventas",
        "contract_type": "indefinido",
        "work_schedule_type": "full_time",
        "contracted_weekly_hours": "40.00",
        "annual_vacation_days": 30,
        "hire_date": "2026-01-01",
    }
    patch_keep = client.patch(
        f"/api/v1/employees/{emp_id}",
        headers=hr_h,
        json={**base_patch, "preferred_name": "Sofi"},
    )
    assert patch_keep.status_code == 200
    assert patch_keep.json()["work_permit_expiry_date"] == "2026-12-20"
    assert patch_keep.json()["contract_end_date"] == "2027-01-10"

    # PATCH with explicit null clears work_permit_expiry_date while keeping contract_end_date
    patch_clear = client.patch(
        f"/api/v1/employees/{emp_id}",
        headers=hr_h,
        json={**base_patch, "work_permit_expiry_date": None},
    )
    assert patch_clear.status_code == 200
    assert patch_clear.json()["work_permit_expiry_date"] is None
    assert patch_clear.json()["contract_end_date"] == "2027-01-10"

    # Public org chart must not leak expiry dates or kiosk_pin_hash
    org_res = client.get("/api/v1/organization/org-chart", headers=emp_h)
    assert org_res.status_code == 200
    for node in org_res.json()["nodes"]:
        assert "work_permit_expiry_date" not in node
        assert "contract_end_date" not in node
        assert "kiosk_pin_hash" not in node

    # Set a kiosk PIN, terminate, then anonymize -> clears dates & kiosk_pin_hash
    pin_res = client.put(
        f"/api/v1/employees/{emp_id}/kiosk-pin",
        headers=hr_h,
        json={"pin": "2468"},
    )
    assert pin_res.status_code == 200
    assert pin_res.json()["has_kiosk_pin"] is True

    term_res = client.post(
        f"/api/v1/employees/{emp_id}/terminate",
        headers=hr_h,
        json={"termination_date": "2026-11-26"},
    )
    assert term_res.status_code == 200

    # Fast-forward past retention_until so anonymize succeeds
    emp_repo = client.app.dependency_overrides[get_employee_repository]()
    stored = emp_repo.employees[UUID(emp_id)]
    emp_repo.employees[UUID(emp_id)] = replace(
        stored, retention_until=_NOW - timedelta(days=1)
    )
    anon_res = client.post(f"/api/v1/employees/{emp_id}/anonymize", headers=hr_h)
    assert anon_res.status_code == 200
    anon_body = anon_res.json()
    assert anon_body["work_permit_expiry_date"] is None
    assert anon_body["contract_end_date"] is None
    assert anon_body["has_kiosk_pin"] is False
    assert emp_repo.employees[UUID(emp_id)].kiosk_pin_hash is None


def test_b1_dashboard_60d_expiry_alerts_and_rbac_scoping(tmp_path: Path) -> None:
    env = _build_env(tmp_path, now_utc=_NOW)
    client: TestClient = env["client"]
    emp_repo = client.app.dependency_overrides[get_employee_repository]()
    today = date(2026, 11, 26)

    # emp1 (store_a, dept ventas): expired 5 days ago (-5) and contract ending in 30 days
    emp_repo.employees[env["emp1"].id] = replace(
        env["emp1"],
        department="ventas",
        work_permit_expiry_date=today - timedelta(days=5),
        contract_end_date=today + timedelta(days=30),
    )
    # emp2 (store_a, dept almacen): contract ending in 60 days (boundary included),
    # permit in 61 days (excluded)
    emp_repo.employees[env["emp2"].id] = replace(
        env["emp2"],
        department="almacen",
        work_permit_expiry_date=today + timedelta(days=61),
        contract_end_date=today + timedelta(days=60),
    )
    # emp_bcn (store_b, dept ventas): permit ending in 10 days
    emp_repo.employees[env["emp_bcn"].id] = replace(
        env["emp_bcn"],
        department="ventas",
        work_permit_expiry_date=today + timedelta(days=10),
    )

    owner_uid = _add_member(env, role=UserRole.OWNER)
    scoped_mgr_uid = _add_member(
        env,
        role=UserRole.STORE_MANAGER,
        store_ids=frozenset({env["store_a_id"]}),
        department_scope="ventas",
    )

    # Owner / HR see all 4 alerts across tenant, sorted by days_remaining ascending
    for uid in (owner_uid, env["hr_id"]):
        res = client.get("/api/v1/dashboard/admin", headers=env["headers"](uid))
        assert res.status_code == 200
        data = res.json()
        assert data["expiry_alerts_count"] == 4
        assert [a["days_remaining"] for a in data["expiry_alerts"]] == [-5, 10, 30, 60]

    # Store manager with store_a + department_scope="ventas" sees only emp1's 2 alerts
    mgr_res = client.get("/api/v1/dashboard/admin", headers=env["headers"](scoped_mgr_uid))
    assert mgr_res.status_code == 200
    mgr_data = mgr_res.json()
    assert mgr_data["expiry_alerts_count"] == 2
    assert {a["employee_id"] for a in mgr_data["expiry_alerts"]} == {str(env["emp1"].id)}

    # Accountant gets empty expiry alerts
    acc_res = client.get("/api/v1/dashboard/admin", headers=env["headers"](env["acc_id"]))
    assert acc_res.status_code == 200
    assert acc_res.json()["expiry_alerts_count"] == 0
    assert acc_res.json()["expiry_alerts"] == []

    # Regular employee cannot access /dashboard/admin (403)
    emp_res = client.get("/api/v1/dashboard/admin", headers=env["headers"](env["emp1_uid"]))
    assert emp_res.status_code == 403


# ---------------------------------------------------------------------------
# B2: Salted Kiosk PIN management + cross-store Kiosk clocking
# ---------------------------------------------------------------------------


def test_b2_kiosk_pin_management_and_cross_store_kiosk_clock(tmp_path: Path) -> None:
    env = _build_env(tmp_path, now_utc=_NOW)
    client: TestClient = env["client"]
    hr_h = env["headers"](env["hr_id"])
    mgr_a_h = env["headers"](env["mgr_a_id"])
    emp_h = env["headers"](env["emp1_uid"])
    acc_h = env["headers"](env["acc_id"])
    emp_repo = client.app.dependency_overrides[get_employee_repository]()

    emp_bcn = env["emp_bcn"]  # primary_store_id == store_b_id

    # Invalid PIN formats -> 422
    for bad_pin in ("123", "1234567", "12a4", " 1234", "१२३४"):
        bad = client.put(
            f"/api/v1/employees/{emp_bcn.id}/kiosk-pin",
            headers=hr_h,
            json={"pin": bad_pin},
        )
        assert bad.status_code == 422

    # RBAC: employee, accountant, and out-of-scope store_manager -> 403
    for h in (emp_h, acc_h, mgr_a_h):
        forb = client.put(
            f"/api/v1/employees/{emp_bcn.id}/kiosk-pin",
            headers=h,
            json={"pin": "1234"},
        )
        assert forb.status_code == 403

    # Frozen (LEGAL_HOLD) employee -> 422
    emp_repo.employees[env["emp2"].id] = replace(
        env["emp2"], employment_status=EmploymentStatus.LEGAL_HOLD
    )
    frozen_res = client.put(
        f"/api/v1/employees/{env['emp2'].id}/kiosk-pin",
        headers=hr_h,
        json={"pin": "1234"},
    )
    assert frozen_res.status_code == 422

    # HR sets 6-digit PIN on emp_bcn; stored hash is salted PBKDF2 and never leaked
    set_res = client.put(
        f"/api/v1/employees/{emp_bcn.id}/kiosk-pin",
        headers=hr_h,
        json={"pin": "009876"},
    )
    assert set_res.status_code == 200
    assert set_res.json()["has_kiosk_pin"] is True
    assert "kiosk_pin_hash" not in set_res.json()
    stored_hash = emp_repo.employees[emp_bcn.id].kiosk_pin_hash
    assert stored_hash is not None and stored_hash.startswith("pbkdf2_sha256$")
    assert "009876" not in stored_hash

    # Different random salts produce different hashes for the same PIN
    assert hash_kiosk_pin("009876") != hash_kiosk_pin("009876")

    # Cross-store Kiosk clock: emp_bcn (store_b) punches at store_a tablet (mgr_a_h)
    # Missing PIN -> 403
    missing_pin = client.post(
        "/api/v1/attendance/clock",
        headers=mgr_a_h,
        json={
            "event_type": "clock_in",
            "source": "kiosk",
            "store_id": str(env["store_a_id"]),
            "employee_code": emp_bcn.employee_code,
        },
    )
    assert missing_pin.status_code == 403

    # Wrong PIN -> 403
    wrong_pin = client.post(
        "/api/v1/attendance/clock",
        headers=mgr_a_h,
        json={
            "event_type": "clock_in",
            "source": "kiosk",
            "store_id": str(env["store_a_id"]),
            "employee_code": emp_bcn.employee_code,
            "kiosk_pin": "999999",
        },
    )
    assert wrong_pin.status_code == 403

    # Correct PIN -> 201, and event is recorded under the tablet's store_id (store_a_id)
    ok_kiosk = client.post(
        "/api/v1/attendance/clock",
        headers=mgr_a_h,
        json={
            "event_type": "clock_in",
            "source": "kiosk",
            "store_id": str(env["store_a_id"]),
            "employee_code": emp_bcn.employee_code,
            "kiosk_pin": "009876",
        },
    )
    assert ok_kiosk.status_code == 201
    assert ok_kiosk.json()["event"]["store_id"] == str(env["store_a_id"])
    assert ok_kiosk.json()["event"]["employee_id"] == str(emp_bcn.id)

    # Clear PIN (null) -> subsequent kiosk clock_out succeeds without kiosk_pin
    clear_res = client.put(
        f"/api/v1/employees/{emp_bcn.id}/kiosk-pin",
        headers=hr_h,
        json={"pin": None},
    )
    assert clear_res.status_code == 200
    assert clear_res.json()["has_kiosk_pin"] is False

    env["clock"].now = _NOW + timedelta(hours=4)
    no_pin_out = client.post(
        "/api/v1/attendance/clock",
        headers=mgr_a_h,
        json={
            "event_type": "clock_out",
            "source": "kiosk",
            "store_id": str(env["store_a_id"]),
            "employee_code": emp_bcn.employee_code,
        },
    )
    assert no_pin_out.status_code == 201


def test_b2_cross_store_kiosk_ledger_and_dashboard_no_truncation(tmp_path: Path) -> None:
    """P1 #1: Cross-store kiosk punch + home-store punch must not truncate in ledger/dashboard."""
    env = _build_env(tmp_path, now_utc=_NOW)
    client: TestClient = env["client"]
    hr_h = env["headers"](env["hr_id"])
    emp_bcn = env["emp_bcn"]  # primary_store_id == store_b_id
    mgr_b_uid = _add_member(
        env, role=UserRole.STORE_MANAGER, store_ids=frozenset({env["store_b_id"]})
    )
    mgr_b_h = env["headers"](mgr_b_uid)

    # Day 1: emp_bcn clocks in via Kiosk at store_a (08:00) and clocks out at store_b (16:00)
    env["clock"].now = datetime(2026, 11, 25, 7, 0, tzinfo=UTC)
    c_in = client.post(
        "/api/v1/attendance/clock",
        headers=hr_h,
        json={
            "event_type": "clock_in",
            "source": "kiosk",
            "store_id": str(env["store_a_id"]),
            "employee_code": emp_bcn.employee_code,
        },
    )
    assert c_in.status_code == 201

    env["clock"].now = datetime(2026, 11, 25, 15, 0, tzinfo=UTC)
    c_out = client.post(
        "/api/v1/attendance/clock",
        headers=hr_h,
        json={
            "event_type": "clock_out",
            "source": "kiosk",
            "store_id": str(env["store_b_id"]),
            "employee_code": emp_bcn.employee_code,
        },
    )
    assert c_out.status_code == 201

    # Day 2: emp_bcn clocks in at store_a at 23:00 UTC (8h rest < 12h) -> triggers 12h rest alert
    env["clock"].now = datetime(2026, 11, 25, 23, 0, tzinfo=UTC)
    c_in2 = client.post(
        "/api/v1/attendance/clock",
        headers=hr_h,
        json={
            "event_type": "clock_in",
            "source": "kiosk",
            "store_id": str(env["store_a_id"]),
            "employee_code": emp_bcn.employee_code,
        },
    )
    assert c_in2.status_code == 201

    env["clock"].now = _NOW
    # store_b manager queries ledger filtered by store_b_id -> full chain preserved, no 422
    ledger_res = client.get(
        f"/api/v1/attendance/ledger?store_id={env['store_b_id']}"
        "&date_from=2026-11-25&date_to=2026-11-26",
        headers=mgr_b_h,
    )
    assert ledger_res.status_code == 200
    bcn_items = [i for i in ledger_res.json()["items"] if i["employee_id"] == str(emp_bcn.id)]
    assert len(bcn_items) == 1
    assert float(bcn_items[0]["total_net_hours"]) >= 8.0
    assert "INTER_SHIFT_REST_VIOLATION" in {a["code"] for a in bcn_items[0]["alerts"]}

    # store_b manager queries admin dashboard filtered by store_b_id -> succeeds & sees emp_bcn
    dash_res = client.get(
        f"/api/v1/dashboard/admin?store_id={env['store_b_id']}",
        headers=mgr_b_h,
    )
    assert dash_res.status_code == 200
    who_groups = {g["status"]: g["count"] for g in dash_res.json()["who_is_in"]}
    assert who_groups.get("working", 0) >= 1


# ---------------------------------------------------------------------------
# B4: Cross-store shifts + overnight manual-day attendance & Art. 34.3 ET rest
# ---------------------------------------------------------------------------


def test_b4_cross_store_shift_creation_and_rbac(tmp_path: Path) -> None:
    env = _build_env(tmp_path, now_utc=_NOW)
    client: TestClient = env["client"]
    hr_h = env["headers"](env["hr_id"])
    mgr_a_h = env["headers"](env["mgr_a_id"])

    # Set store_b timezone to Atlantic/Canary so we can verify target store timezone is used
    store_repo = client.app.dependency_overrides[get_store_repository]()
    store_repo._state.stores[env["store_b_id"]] = replace(
        store_repo._state.stores[env["store_b_id"]],
        timezone="Atlantic/Canary",
    )

    # store_manager (mgr_a, only store_a) cannot create shift at store_b -> 403
    mgr_cross = client.post(
        "/api/v1/shifts",
        headers=mgr_a_h,
        json={
            "employee_id": str(env["emp1"].id),
            "store_id": str(env["store_b_id"]),
            "start_at_utc": "2026-12-01T09:00:00Z",
            "end_at_utc": "2026-12-01T17:00:00Z",
            "planned_break_minutes": 30,
        },
    )
    assert mgr_cross.status_code == 403

    # Unknown / cross-tenant store_id -> 404 for HR
    other_store = client.post(
        "/api/v1/shifts",
        headers=hr_h,
        json={
            "employee_id": str(env["emp1"].id),
            "store_id": str(uuid4()),
            "start_at_utc": "2026-12-01T09:00:00Z",
            "end_at_utc": "2026-12-01T17:00:00Z",
            "planned_break_minutes": 30,
        },
    )
    assert other_store.status_code == 404

    # P0: store_manager (mgr_a, only store_a) cannot schedule store_b employee (emp_bcn)
    # into store_a, even if emp_bcn has an approved sick leave (must return 403 without leaking).
    leave_repo = client.app.dependency_overrides[get_leave_repository]()
    leave_repo.leaves.append(
        LeaveRequest(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=env["store_b_id"],
            employee_id=env["emp_bcn"].id,
            leave_type=LeaveType.SICK_LEAVE,
            start_date=date(2026, 12, 1),
            end_date=date(2026, 12, 2),
            natural_days=2,
            status=LeaveStatus.APPROVED,
            reason="Baja",
            requested_by_user_id=env["hr_id"],
            reviewed_by_user_id=env["hr_id"],
            reviewed_at_utc=_NOW,
            review_note="ok",
            manager_reviewed_by_user_id=None,
            manager_reviewed_at_utc=None,
            manager_review_note="",
            hr_reviewed_by_user_id=env["hr_id"],
            hr_reviewed_at_utc=_NOW,
            hr_review_note="ok",
            medical_document_id=None,
            retention_until=_NOW + timedelta(days=1461),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    mgr_out_emp = client.post(
        "/api/v1/shifts",
        headers=mgr_a_h,
        json={
            "employee_id": str(env["emp_bcn"].id),
            "store_id": str(env["store_a_id"]),
            "start_at_utc": "2026-12-01T09:00:00Z",
            "end_at_utc": "2026-12-01T17:00:00Z",
            "planned_break_minutes": 30,
        },
    )
    assert mgr_out_emp.status_code == 403
    assert "sick" not in mgr_out_emp.text.lower()
    assert "leave" not in mgr_out_emp.text.lower()

    # HR creates cross-store shift for emp1 (store_a) at store_b (published) -> 201
    hr_cross = client.post(
        "/api/v1/shifts",
        headers=hr_h,
        json={
            "employee_id": str(env["emp1"].id),
            "store_id": str(env["store_b_id"]),
            "start_at_utc": "2026-12-01T09:00:00Z",
            "end_at_utc": "2026-12-01T17:00:00Z",
            "planned_break_minutes": 30,
            "status": "published",
            "notes": "Soporte tienda B",
        },
    )
    assert hr_cross.status_code == 201
    assert hr_cross.json()["shift"]["store_id"] == str(env["store_b_id"])

    # P1 #2: Weekly schedule for store_b includes emp1's cross-store row & shift
    week_b = client.get(
        "/api/v1/shifts/weekly?week_start=2026-11-30&store_id=" + str(env["store_b_id"]),
        headers=hr_h,
    )
    assert week_b.status_code == 200
    assert week_b.json()["store_timezone"] == "Atlantic/Canary"
    emp1_rows = [r for r in week_b.json()["rows"] if r["employee_id"] == str(env["emp1"].id)]
    assert len(emp1_rows) == 1
    assert len(emp1_rows[0]["shifts"]) == 1
    assert emp1_rows[0]["shifts"][0]["store_id"] == str(env["store_b_id"])

    # Employee querying own weekly schedule sees the published cross-store shift at store_b
    emp_week = client.get(
        "/api/v1/shifts/weekly?week_start=2026-11-30",
        headers=env["headers"](env["emp1_uid"]),
    )
    assert emp_week.status_code == 200
    assert len(emp_week.json()["rows"]) == 1
    assert any(
        s["store_id"] == str(env["store_b_id"]) for s in emp_week.json()["rows"][0]["shifts"]
    )


def test_b4_manual_day_overnight_shift_closing_lock_and_12h_rest(tmp_path: Path) -> None:
    # Set now to 2026-11-26 12:00 UTC
    env = _build_env(tmp_path, now_utc=_NOW)
    client: TestClient = env["client"]
    emp_h = env["headers"](env["emp1_uid"])
    hr_h = env["headers"](env["hr_id"])
    emp1 = env["emp1"]

    # 1. > 16h shift rejected -> 422 (e.g. 08:00 -> 01:00 = 17h)
    too_long = client.post(
        "/api/v1/attendance/manual-day",
        headers=emp_h,
        json={
            "local_date": "2026-11-10",
            "clock_in_time": "08:00",
            "clock_out_time": "01:00",
            "note": "Too long",
        },
    )
    assert too_long.status_code == 422

    # 2. Cross-month closing lock: close 2026-11 (leave 2026-10 open),
    # then try overnight 2026-10-31 22:00 -> 2026-11-01 06:00 -> 409
    env["payroll_repo"].closings.append(
        MonthlyClosing(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=env["store_a_id"],
            period_month="2026-11",
            revision_number=1,
            status=MonthlyClosingStatus.CLOSED,
            closed_by_user_id=env["hr_id"],
            closed_at_utc=_NOW,
            reopened_by_user_id=None,
            reopened_at_utc=None,
            reopen_reason="",
            notes="",
            retention_until=_NOW + timedelta(days=1461),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    locked_cross_month = client.post(
        "/api/v1/attendance/manual-day",
        headers=emp_h,
        json={
            "local_date": "2026-10-31",
            "clock_in_time": "22:00",
            "clock_out_time": "06:00",
            "note": "Halloween overnight",
        },
    )
    assert locked_cross_month.status_code == 409

    # Reopen 2026-11 so we can record November overnight shifts
    env["payroll_repo"].closings.clear()

    # 3. Valid overnight shift on 2026-11-15: 22:00 -> 06:00 (+1d) with break 02:00 -> 02:30 (+1d)
    overnight_ok = client.post(
        "/api/v1/attendance/manual-day",
        headers=emp_h,
        json={
            "local_date": "2026-11-15",
            "clock_in_time": "22:00",
            "break_start_time": "02:00",
            "break_end_time": "02:30",
            "clock_out_time": "06:00",
            "note": "Inventario nocturno",
        },
    )
    assert overnight_ok.status_code == 201
    events = overnight_ok.json()["events"]
    assert len(events) == 4
    # In Europe/Madrid (CET = UTC+1 in Nov): 22:00 -> 21:00Z, 06:00(+1d) -> 05:00Z
    assert events[0]["occurred_at_utc"].startswith("2026-11-15T21:00:00")
    assert events[1]["occurred_at_utc"].startswith("2026-11-16T01:00:00")
    assert events[2]["occurred_at_utc"].startswith("2026-11-16T01:30:00")
    assert events[3]["occurred_at_utc"].startswith("2026-11-16T05:00:00")

    # 4. Art. 34.3 ET 12h rest check: next shift on 2026-11-16 at 14:00 (only 8h after 06:00)
    next_day_short_rest = client.post(
        "/api/v1/attendance/manual-day",
        headers=emp_h,
        json={
            "local_date": "2026-11-16",
            "clock_in_time": "14:00",
            "clock_out_time": "20:00",
            "note": "Turno tarde con poco descanso",
        },
    )
    assert next_day_short_rest.status_code == 201

    att_repo = client.app.dependency_overrides[get_attendance_repository]()
    assert len(att_repo.events) >= 6

    ledger = client.get(
        f"/api/v1/attendance/ledger?employee_id={emp1.id}&date_from=2026-11-15&date_to=2026-11-17",
        headers=hr_h,
    )
    assert ledger.status_code == 200
    alert_codes = {
        alert["code"]
        for item in ledger.json()["items"]
        for alert in item["alerts"]
    }
    assert "INTER_SHIFT_REST_VIOLATION" in alert_codes
