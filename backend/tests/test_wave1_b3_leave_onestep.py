"""Wave 1 / B3: HR & Owner one-step final review of PENDING_MANAGER leave requests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.domain.leaves import LeaveRequest, LeaveStatus, LeaveType
from app.domain.organization import Membership
from app.domain.payroll import MonthlyClosing, MonthlyClosingStatus
from app.domain.scheduling import Shift, ShiftStatus
from app.domain.security import MembershipStatus, UserRole
from app.interfaces.api.deps import (
    get_leave_repository,
    get_membership_repository,
    get_shift_repository,
)
from tests.test_phase5e_workflows_and_reports import _build_env

_NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def _env(tmp_path: Path) -> dict[str, Any]:
    env = _build_env(tmp_path)
    app = env["client"].app
    env["leave_repo"] = app.dependency_overrides[get_leave_repository]()
    env["shift_repo"] = app.dependency_overrides[get_shift_repository]()
    membership_repo = app.dependency_overrides[get_membership_repository]()
    owner_uid = uuid4()
    mid = uuid4()
    membership_repo._state.memberships[mid] = Membership(
        id=mid,
        tenant_id=env["tenant_id"],
        user_id=owner_uid,
        email="owner@dragon.es",
        display_name="Owner",
        role=UserRole.OWNER,
        status=MembershipStatus.ACTIVE,
        store_ids=frozenset(),
        department_scope=None,
        created_at=_NOW,
        updated_at=_NOW,
    )
    env["owner_id"] = owner_uid
    return env


def _seed_leave(
    env: dict[str, Any],
    *,
    start: date,
    end: date,
    status: LeaveStatus = LeaveStatus.PENDING_MANAGER,
    leave_type: LeaveType = LeaveType.SICK_LEAVE,
    employee: Any = None,
) -> LeaveRequest:
    emp = employee or env["emp1"]
    leave = LeaveRequest(
        id=uuid4(),
        tenant_id=env["tenant_id"],
        store_id=emp.primary_store_id,
        employee_id=emp.id,
        leave_type=leave_type,
        start_date=start,
        end_date=end,
        natural_days=(end - start).days + 1,
        status=status,
        reason="test",
        requested_by_user_id=emp.user_id or uuid4(),
        reviewed_by_user_id=None,
        reviewed_at_utc=None,
        review_note="",
        manager_reviewed_by_user_id=None,
        manager_reviewed_at_utc=None,
        manager_review_note="",
        hr_reviewed_by_user_id=None,
        hr_reviewed_at_utc=None,
        hr_review_note="",
        medical_document_id=None,
        assigned_directly=False,
        assigned_by_user_id=None,
        retention_until=_NOW + timedelta(days=1460),
        created_at=_NOW,
        updated_at=_NOW,
    )
    env["leave_repo"].leaves.append(leave)
    return leave


def _stored(env: dict[str, Any], leave_id: UUID) -> LeaveRequest:
    return next(lv for lv in env["leave_repo"].leaves if lv.id == leave_id)


def _review(
    env: dict[str, Any],
    leave: LeaveRequest,
    uid: UUID,
    *,
    approve: bool,
    note: str = "",
):
    client: TestClient = env["client"]
    return client.post(
        f"/api/v1/leaves/{leave.id}/review",
        json={"approve": approve, "review_note": note},
        headers=env["headers"](uid),
    )


@pytest.mark.parametrize("actor", ["hr_id", "owner_id"])
def test_one_step_approve_by_hr_or_owner(tmp_path: Path, actor: str) -> None:
    env = _env(tmp_path)
    leave = _seed_leave(env, start=date(2026, 12, 10), end=date(2026, 12, 12))
    uid = env[actor]

    res = _review(env, leave, uid, approve=True, note="Aprobado directo")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "approved"
    assert body["hr_reviewed_by_user_id"] == str(uid)
    assert body["hr_review_note"] == "Aprobado directo"
    assert body["hr_reviewed_at_utc"] is not None
    # Manager stage was skipped -> no manager audit data fabricated
    assert body["manager_reviewed_by_user_id"] is None
    assert body["manager_review_note"] == ""

    stored = _stored(env, leave.id)
    assert stored.status == LeaveStatus.APPROVED
    assert stored.hr_reviewed_by_user_id == uid
    assert stored.hr_reviewed_at_utc == _NOW
    assert stored.hr_review_note == "Aprobado directo"
    assert stored.manager_reviewed_by_user_id is None
    assert stored.reviewed_by_user_id == uid


@pytest.mark.parametrize("actor", ["hr_id", "owner_id"])
def test_one_step_reject_by_hr_or_owner(tmp_path: Path, actor: str) -> None:
    env = _env(tmp_path)
    leave = _seed_leave(env, start=date(2026, 12, 10), end=date(2026, 12, 12))
    uid = env[actor]

    res = _review(env, leave, uid, approve=False, note="Sin cobertura")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "rejected"
    assert body["hr_reviewed_by_user_id"] == str(uid)
    assert body["hr_review_note"] == "Sin cobertura"
    assert body["hr_reviewed_at_utc"] is not None
    assert body["manager_reviewed_by_user_id"] is None

    stored = _stored(env, leave.id)
    assert stored.status == LeaveStatus.REJECTED
    assert stored.hr_reviewed_by_user_id == uid
    assert stored.manager_reviewed_by_user_id is None


def test_one_step_approve_vacation_with_sufficient_balance(tmp_path: Path) -> None:
    env = _env(tmp_path)
    leave = _seed_leave(
        env,
        start=date(2026, 12, 14),
        end=date(2026, 12, 18),
        leave_type=LeaveType.VACATION,
    )
    res = _review(env, leave, env["hr_id"], approve=True)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "approved"


@pytest.mark.parametrize("actor", ["hr_id", "owner_id"])
def test_one_step_approve_blocked_by_closed_month_409(tmp_path: Path, actor: str) -> None:
    env = _env(tmp_path)
    env["payroll_repo"].closings.append(
        MonthlyClosing(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=env["store_a_id"],
            period_month="2026-12",
            status=MonthlyClosingStatus.CLOSED,
            revision_number=1,
            closed_by_user_id=env["hr_id"],
            closed_at_utc=_NOW,
            reopened_by_user_id=None,
            reopened_at_utc=None,
            reopen_reason="",
            notes="",
            retention_until=_NOW + timedelta(days=1460),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    leave = _seed_leave(env, start=date(2026, 12, 10), end=date(2026, 12, 12))

    approve = _review(env, leave, env[actor], approve=True)
    assert approve.status_code == 409
    assert approve.json()["error_code"] == "PAYROLL_MONTH_CLOSED"
    # Lock applies to rejection as well
    reject = _review(env, leave, env[actor], approve=False)
    assert reject.status_code == 409
    assert reject.json()["error_code"] == "PAYROLL_MONTH_CLOSED"
    assert _stored(env, leave.id).status == LeaveStatus.PENDING_MANAGER


@pytest.mark.parametrize("actor", ["hr_id", "owner_id"])
def test_one_step_approve_blocked_by_overlap_409(tmp_path: Path, actor: str) -> None:
    env = _env(tmp_path)
    _seed_leave(
        env,
        start=date(2026, 12, 11),
        end=date(2026, 12, 15),
        status=LeaveStatus.APPROVED,
    )
    leave = _seed_leave(env, start=date(2026, 12, 10), end=date(2026, 12, 12))

    res = _review(env, leave, env[actor], approve=True)
    assert res.status_code == 409
    assert res.json()["error_code"] == "LEAVE_OVERLAP"
    assert _stored(env, leave.id).status == LeaveStatus.PENDING_MANAGER

    # Rejection is still possible (no final-approval validation needed)
    rej = _review(env, leave, env[actor], approve=False)
    assert rej.status_code == 200
    assert rej.json()["status"] == "rejected"


@pytest.mark.parametrize("actor", ["hr_id", "owner_id"])
def test_one_step_approve_blocked_by_published_shift_409(tmp_path: Path, actor: str) -> None:
    env = _env(tmp_path)
    emp = env["emp1"]
    env["shift_repo"].shifts.append(
        Shift(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=emp.primary_store_id,
            employee_id=emp.id,
            start_at_utc=datetime(2026, 12, 11, 9, 0, tzinfo=UTC),
            end_at_utc=datetime(2026, 12, 11, 16, 0, tzinfo=UTC),
            planned_break_minutes=30,
            status=ShiftStatus.PUBLISHED,
            notes="",
            created_by_user_id=env["hr_id"],
            published_by_user_id=env["hr_id"],
            published_at_utc=_NOW,
            retention_until=_NOW + timedelta(days=1460),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    leave = _seed_leave(env, start=date(2026, 12, 10), end=date(2026, 12, 12))

    res = _review(env, leave, env[actor], approve=True)
    assert res.status_code == 409
    assert res.json()["error_code"] == "SHIFT_CONFLICT"
    assert _stored(env, leave.id).status == LeaveStatus.PENDING_MANAGER


@pytest.mark.parametrize("actor", ["hr_id", "owner_id"])
def test_one_step_approve_blocked_by_insufficient_vacation_balance_422(
    tmp_path: Path, actor: str
) -> None:
    env = _env(tmp_path)
    # emp1 has 30 annual days; 31 natural days exceeds the balance
    leave = _seed_leave(
        env,
        start=date(2026, 12, 1),
        end=date(2026, 12, 31),
        leave_type=LeaveType.VACATION,
    )
    res = _review(env, leave, env[actor], approve=True)
    assert res.status_code == 422
    assert res.json()["error_code"] == "LEAVE_INSUFFICIENT_BALANCE"
    assert _stored(env, leave.id).status == LeaveStatus.PENDING_MANAGER


def test_balance_exhausted_by_prior_approved_vacation_422(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _seed_leave(
        env,
        start=date(2026, 3, 1),
        end=date(2026, 3, 28),
        status=LeaveStatus.APPROVED,
        leave_type=LeaveType.VACATION,
    )
    leave = _seed_leave(
        env,
        start=date(2026, 12, 1),
        end=date(2026, 12, 5),
        leave_type=LeaveType.VACATION,
    )
    res = _review(env, leave, env["owner_id"], approve=True)
    assert res.status_code == 422
    assert res.json()["error_code"] == "LEAVE_INSUFFICIENT_BALANCE"


def test_one_step_reject_ignores_balance_and_conflicts(tmp_path: Path) -> None:
    env = _env(tmp_path)
    leave = _seed_leave(
        env,
        start=date(2026, 12, 1),
        end=date(2026, 12, 31),
        leave_type=LeaveType.VACATION,
    )
    res = _review(env, leave, env["hr_id"], approve=False, note="No")
    assert res.status_code == 200
    assert res.json()["status"] == "rejected"


def test_store_manager_first_review_flow_unchanged(tmp_path: Path) -> None:
    env = _env(tmp_path)
    leave = _seed_leave(env, start=date(2026, 12, 10), end=date(2026, 12, 12))

    ok = _review(env, leave, env["mgr_a_id"], approve=True, note="OK tienda")
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "pending_hr"
    assert body["manager_reviewed_by_user_id"] == str(env["mgr_a_id"])
    assert body["manager_review_note"] == "OK tienda"
    assert body["hr_reviewed_by_user_id"] is None

    # Manager cannot give final approval
    again = _review(env, leave, env["mgr_a_id"], approve=True)
    assert again.status_code == 403
    assert again.json()["error_code"] == "LEAVE_REQUIRES_HR_FINAL"

    # HR/Owner second review unchanged
    final = _review(env, leave, env["hr_id"], approve=True, note="Final RRHH")
    assert final.status_code == 200
    assert final.json()["status"] == "approved"
    assert final.json()["manager_reviewed_by_user_id"] == str(env["mgr_a_id"])
    assert final.json()["hr_reviewed_by_user_id"] == str(env["hr_id"])
    assert final.json()["hr_review_note"] == "Final RRHH"


def test_store_manager_reject_flow_unchanged(tmp_path: Path) -> None:
    env = _env(tmp_path)
    leave = _seed_leave(env, start=date(2026, 12, 10), end=date(2026, 12, 12))
    res = _review(env, leave, env["mgr_a_id"], approve=False, note="No cubierto")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "rejected"
    assert body["manager_reviewed_by_user_id"] == str(env["mgr_a_id"])
    assert body["manager_review_note"] == "No cubierto"
    assert body["hr_reviewed_by_user_id"] is None


def test_owner_second_review_from_pending_hr_unchanged(tmp_path: Path) -> None:
    env = _env(tmp_path)
    leave = _seed_leave(
        env, start=date(2026, 12, 10), end=date(2026, 12, 12), status=LeaveStatus.PENDING_HR
    )
    res = _review(env, leave, env["owner_id"], approve=True, note="Owner final")
    assert res.status_code == 200
    assert res.json()["status"] == "approved"
    assert res.json()["hr_reviewed_by_user_id"] == str(env["owner_id"])


def test_store_manager_still_cannot_review_out_of_scope_store(tmp_path: Path) -> None:
    env = _env(tmp_path)
    leave = _seed_leave(
        env,
        start=date(2026, 12, 10),
        end=date(2026, 12, 12),
        employee=env["emp_bcn"],
    )
    res = _review(env, leave, env["mgr_a_id"], approve=True)
    assert res.status_code == 403
    assert _stored(env, leave.id).status == LeaveStatus.PENDING_MANAGER


@pytest.mark.parametrize("uid_key", ["emp1_uid", "acc_id"])
def test_employee_and_accountant_cannot_review(tmp_path: Path, uid_key: str) -> None:
    env = _env(tmp_path)
    leave = _seed_leave(env, start=date(2026, 12, 10), end=date(2026, 12, 12))
    res = _review(env, leave, env[uid_key], approve=True)
    assert res.status_code == 403
    assert _stored(env, leave.id).status == LeaveStatus.PENDING_MANAGER


def test_final_states_cannot_be_reviewed_again(tmp_path: Path) -> None:
    env = _env(tmp_path)
    leave = _seed_leave(env, start=date(2026, 12, 10), end=date(2026, 12, 12))
    assert _review(env, leave, env["hr_id"], approve=True).status_code == 200
    second = _review(env, leave, env["owner_id"], approve=False)
    assert second.status_code == 422
    assert _stored(env, leave.id).status == LeaveStatus.APPROVED
