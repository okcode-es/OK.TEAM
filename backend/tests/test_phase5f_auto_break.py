"""Phase 5F tests: Fixed break duration & server-authoritative auto-end break."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.domain.attendance import compute_auto_break_end_utc
from app.domain.employees import validate_default_break_minutes
from app.domain.organization import ValidationDomainError
from app.domain.payroll import MonthlyClosing, MonthlyClosingStatus
from app.interfaces.api.deps import (
    get_attendance_repository,
    get_commit_fn,
    get_employee_repository,
)
from tests.test_phase5e_workflows_and_reports import _build_env


def test_default_break_minutes_domain_validation() -> None:
    assert validate_default_break_minutes(None) is None
    assert validate_default_break_minutes(5) == 5
    assert validate_default_break_minutes(30) == 30
    assert validate_default_break_minutes(180) == 180

    with pytest.raises(ValidationDomainError):
        validate_default_break_minutes(4)
    with pytest.raises(ValidationDomainError):
        validate_default_break_minutes(181)


def test_employee_default_break_minutes_api_validation_and_persistence(tmp_path: Path) -> None:
    env = _build_env(tmp_path)
    client: TestClient = env["client"]
    hr_headers = env["headers"](env["hr_id"])
    store_id = str(env["store_a_id"])

    base_payload = {
        "primary_store_id": store_id,
        "employee_code": "EMP-BRK-1",
        "full_name": "Elena Break",
        "contract_type": "indefinido",
        "work_schedule_type": "full_time",
        "contracted_weekly_hours": "40.00",
        "annual_vacation_days": 30,
        "hire_date": "2026-01-01",
    }

    # Invalid < 5 and > 180 -> 422
    r_low = client.post(
        "/api/v1/employees",
        headers=hr_headers,
        json={**base_payload, "default_break_minutes": 4},
    )
    assert r_low.status_code == 422

    r_high = client.post(
        "/api/v1/employees",
        headers=hr_headers,
        json={**base_payload, "default_break_minutes": 181},
    )
    assert r_high.status_code == 422

    # Valid 30 -> 201
    r_create = client.post(
        "/api/v1/employees",
        headers=hr_headers,
        json={**base_payload, "default_break_minutes": 30},
    )
    assert r_create.status_code == 201
    created = r_create.json()
    assert created["default_break_minutes"] == 30
    emp_id = created["id"]

    # Update to 60 -> 200
    r_upd_60 = client.patch(
        f"/api/v1/employees/{emp_id}",
        headers=hr_headers,
        json={**base_payload, "default_break_minutes": 60},
    )
    assert r_upd_60.status_code == 200
    assert r_upd_60.json()["default_break_minutes"] == 60

    # Update to None -> 200
    r_upd_none = client.patch(
        f"/api/v1/employees/{emp_id}",
        headers=hr_headers,
        json={**base_payload, "default_break_minutes": None},
    )
    assert r_upd_none.status_code == 200
    assert r_upd_none.json()["default_break_minutes"] is None


def test_open_break_before_and_after_auto_end_is_idempotent(tmp_path: Path) -> None:
    t0 = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
    env = _build_env(tmp_path, now_utc=t0)
    client: TestClient = env["client"]
    clock = env["clock"]
    emp1 = env["emp1"]
    emp_repo = client.app.dependency_overrides[get_employee_repository]()  # type: ignore[union-attr]
    att_repo = client.app.dependency_overrides[get_attendance_repository]()  # type: ignore[union-attr]
    emp1_headers = env["headers"](env["emp1_uid"])

    # Configure 30m fixed break on emp1
    from dataclasses import replace

    emp_repo.employees[emp1.id] = replace(emp1, default_break_minutes=30)

    commit_calls = 0

    async def _counting_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1

    client.app.dependency_overrides[get_commit_fn] = lambda: _counting_commit  # type: ignore[union-attr]

    # 09:00 clock_in
    r_in = client.post(
        "/api/v1/attendance/clock",
        headers=emp1_headers,
        json={"event_type": "clock_in", "source": "web"},
    )
    assert r_in.status_code == 201

    # 14:00 break_start
    break_start_at = datetime(2026, 11, 26, 14, 0, tzinfo=UTC)
    clock.now = break_start_at
    r_bs = client.post(
        "/api/v1/attendance/clock",
        headers=emp1_headers,
        json={"event_type": "break_start", "source": "web"},
    )
    assert r_bs.status_code == 201
    commits_before_dash = commit_calls

    # 14:15 (before 30m elapsed) -> status="on_break", auto_break_end_utc=14:30, no break_end
    clock.now = datetime(2026, 11, 26, 14, 15, tzinfo=UTC)
    r_dash_before = client.get("/api/v1/dashboard/me", headers=emp1_headers)
    assert r_dash_before.status_code == 200
    cs_before = r_dash_before.json()["clock_state"]
    assert cs_before["status"] == "on_break"
    assert cs_before["default_break_minutes"] == 30
    assert datetime.fromisoformat(cs_before["auto_break_end_utc"]) == (
        break_start_at + timedelta(minutes=30)
    )
    assert len(att_repo.events) == 2
    assert commit_calls == commits_before_dash

    # 14:35 (after 30m elapsed) -> auto-appends break_end at exact 14:30, returns status="working"
    clock.now = datetime(2026, 11, 26, 14, 35, tzinfo=UTC)
    r_dash_after = client.get("/api/v1/dashboard/me", headers=emp1_headers)
    assert r_dash_after.status_code == 200
    cs_after = r_dash_after.json()["clock_state"]
    assert cs_after["status"] == "working"
    assert cs_after["default_break_minutes"] == 30
    assert cs_after["auto_break_end_utc"] is None
    assert len(att_repo.events) == 3
    auto_ev = att_repo.events[-1]
    assert auto_ev.event_type.value == "break_end"
    assert auto_ev.occurred_at_utc == break_start_at + timedelta(minutes=30)
    assert auto_ev.correction_reason == "auto_break_end:30m"
    assert commit_calls == commits_before_dash + 1

    # Second call is idempotent (no duplicate break_end, no extra commit)
    r_dash_again = client.get("/api/v1/dashboard/me", headers=emp1_headers)
    assert r_dash_again.status_code == 200
    assert r_dash_again.json()["clock_state"]["status"] == "working"
    assert len(att_repo.events) == 3
    assert commit_calls == commits_before_dash + 1

    # Manual break_end clicked 1s after auto-end returns clear 422 (no open break)
    r_manual_be = client.post(
        "/api/v1/attendance/clock",
        headers=emp1_headers,
        json={"event_type": "break_end", "source": "web"},
    )
    assert r_manual_be.status_code == 422
    assert len(att_repo.events) == 3


def test_clock_out_after_elapsed_fixed_break_auto_materializes_break_end(tmp_path: Path) -> None:
    t0 = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
    env = _build_env(tmp_path, now_utc=t0)
    client: TestClient = env["client"]
    clock = env["clock"]
    emp1 = env["emp1"]
    emp_repo = client.app.dependency_overrides[get_employee_repository]()  # type: ignore[union-attr]
    att_repo = client.app.dependency_overrides[get_attendance_repository]()  # type: ignore[union-attr]
    emp1_headers = env["headers"](env["emp1_uid"])

    from dataclasses import replace

    emp_repo.employees[emp1.id] = replace(emp1, default_break_minutes=30)

    # 10:00 clock_in
    assert (
        client.post(
            "/api/v1/attendance/clock",
            headers=emp1_headers,
            json={"event_type": "clock_in", "source": "web"},
        ).status_code
        == 201
    )

    # 14:00 break_start
    clock.now = datetime(2026, 11, 26, 14, 0, tzinfo=UTC)
    assert (
        client.post(
            "/api/v1/attendance/clock",
            headers=emp1_headers,
            json={"event_type": "break_start", "source": "web"},
        ).status_code
        == 201
    )

    # 18:00 clock_out directly (without manual break_end)
    clock.now = datetime(2026, 11, 26, 18, 0, tzinfo=UTC)
    r_out = client.post(
        "/api/v1/attendance/clock",
        headers=emp1_headers,
        json={"event_type": "clock_out", "source": "web"},
    )
    assert r_out.status_code == 201

    # Ledger shows 30m break and 7h30m (450m) net work (10:00-18:00 minus 30m)
    r_ledger = client.get("/api/v1/attendance/ledger", headers=emp1_headers)
    assert r_ledger.status_code == 200
    sessions = r_ledger.json()["items"][0]["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["break_minutes"] == 30
    assert sessions[0]["net_minutes"] == 450
    assert len(att_repo.events) == 4
    assert att_repo.events[2].event_type.value == "break_end"
    assert att_repo.events[2].occurred_at_utc == datetime(2026, 11, 26, 14, 30, tzinfo=UTC)
    assert att_repo.events[2].correction_reason == "auto_break_end:30m"


def test_closed_payroll_month_skips_auto_break_cleanly_without_500(tmp_path: Path) -> None:
    t0 = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
    env = _build_env(tmp_path, now_utc=t0)
    client: TestClient = env["client"]
    clock = env["clock"]
    emp1 = env["emp1"]
    emp_repo = client.app.dependency_overrides[get_employee_repository]()  # type: ignore[union-attr]
    att_repo = client.app.dependency_overrides[get_attendance_repository]()  # type: ignore[union-attr]
    payroll_repo = env["payroll_repo"]
    emp1_headers = env["headers"](env["emp1_uid"])
    hr_headers = env["headers"](env["hr_id"])

    from dataclasses import replace

    emp_repo.employees[emp1.id] = replace(emp1, default_break_minutes=30)

    assert (
        client.post(
            "/api/v1/attendance/clock",
            headers=emp1_headers,
            json={"event_type": "clock_in", "source": "web"},
        ).status_code
        == 201
    )
    clock.now = datetime(2026, 11, 26, 14, 0, tzinfo=UTC)
    assert (
        client.post(
            "/api/v1/attendance/clock",
            headers=emp1_headers,
            json={"event_type": "break_start", "source": "web"},
        ).status_code
        == 201
    )

    # Close 2026-11 payroll month for store A
    payroll_repo.closings.append(
        MonthlyClosing(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=env["store_a_id"],
            period_month="2026-11",
            status=MonthlyClosingStatus.CLOSED,
            revision_number=1,
            closed_by_user_id=env["hr_id"],
            closed_at_utc=datetime(2026, 11, 26, 14, 10, tzinfo=UTC),
            reopened_by_user_id=None,
            reopened_at_utc=None,
            reopen_reason="",
            notes="",
            retention_until=datetime(2030, 11, 26, 14, 10, tzinfo=UTC),
            created_at=datetime(2026, 11, 26, 14, 10, tzinfo=UTC),
            updated_at=datetime(2026, 11, 26, 14, 10, tzinfo=UTC),
        )
    )

    # Advance past 14:30 -> dashboard/me, dashboard/admin, and ledger must not 500 or append
    clock.now = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)
    r_me = client.get("/api/v1/dashboard/me", headers=emp1_headers)
    assert r_me.status_code == 200
    assert r_me.json()["clock_state"]["status"] == "on_break"

    r_admin = client.get("/api/v1/dashboard/admin", headers=hr_headers)
    assert r_admin.status_code == 200

    r_ledger = client.get("/api/v1/attendance/ledger", headers=emp1_headers)
    assert r_ledger.status_code == 200
    assert len(att_repo.events) == 2

    # Direct domain helper sanity check when no open break exists
    sched, due = compute_auto_break_end_utc([], UUID(int=1), 30, clock.now)
    assert (sched, due) == (None, None)
