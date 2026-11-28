"""Phase 5E tests: Self-selectable attendance, 2-stage leaves, and Management Reports."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.domain.attendance import parse_hhmm_to_utc
from app.domain.employees import ContractType, Employee, EmploymentStatus, WorkScheduleType
from app.domain.organization import Membership, Store, Tenant, ValidationDomainError
from app.domain.payroll import MonthlyClosing, MonthlyClosingStatus
from app.domain.reports import escape_csv_cell
from app.domain.security import MembershipStatus, UserRole
from app.infrastructure.config import Settings
from app.infrastructure.document_storage import LocalDocumentStorageAdapter
from app.infrastructure.pii_crypto import AesGcmPiiCipher
from app.infrastructure.supabase_jwt import SupabaseJWTVerifier, sign_dev_hs256_token
from app.interfaces.api.deps import (
    get_announcement_repository,
    get_attendance_day_review_repository,
    get_attendance_repository,
    get_break_policy_repository,
    get_commit_fn,
    get_document_repository,
    get_document_storage,
    get_employee_repository,
    get_jwt_verifier,
    get_leave_entitlement_repository,
    get_leave_repository,
    get_membership_repository,
    get_payroll_closing_repository,
    get_pii_cipher,
    get_shift_repository,
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
from tests.test_phase2_employees import TEST_PII_KEY, InMemoryEmployeeRepo
from tests.test_phase3_attendance import InMemoryAttendanceRepo
from tests.test_phase4_scheduling_leaves_docs import (
    InMemoryDocumentRepo,
    InMemoryLeaveRepo,
    InMemoryShiftRepo,
)
from tests.test_phase5_payroll_closing import InMemoryPayrollClosingRepo
from tests.test_phase5a_workspace_ops import InMemoryAnnouncementRepo


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now


def _build_env(tmp_path: Path, now_utc: datetime | None = None):
    now = now_utc or datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    clock = _Clock(now)
    tenant_id = uuid4()
    store_a_id = uuid4()
    store_b_id = uuid4()

    org_repos = InMemoryOrgRepos()
    org_repos.tenants[tenant_id] = Tenant(
        id=tenant_id,
        legal_name="Dragon Hostelería SL",
        trade_name="Dragon",
        cif_nif="B12345678",
        default_timezone="Europe/Madrid",
        default_locale="es-ES",
        created_at=now,
        updated_at=now,
    )
    store_repo = InMemoryStoreRepo(org_repos)
    org_repos.stores[store_a_id] = Store(
        id=store_a_id,
        tenant_id=tenant_id,
        name="Madrid Centro",
        code="MAD-01",
        timezone="Europe/Madrid",
        address="Gran Vía 1",
        city="Madrid",
        postal_code="28013",
        region_code="ES-MD",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    org_repos.stores[store_b_id] = Store(
        id=store_b_id,
        tenant_id=tenant_id,
        name="Barcelona Gràcia",
        code="BCN-01",
        timezone="Europe/Madrid",
        address="Diagonal 100",
        city="Barcelona",
        postal_code="08019",
        region_code="ES-CT",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    membership_repo = InMemoryMembershipRepo(org_repos)
    emp_repo = InMemoryEmployeeRepo()
    att_repo = InMemoryAttendanceRepo()
    shift_repo = InMemoryShiftRepo()
    leave_repo = InMemoryLeaveRepo()
    doc_repo = InMemoryDocumentRepo()
    ann_repo = InMemoryAnnouncementRepo()
    payroll_repo = InMemoryPayrollClosingRepo()
    cipher = AesGcmPiiCipher(TEST_PII_KEY)
    storage = LocalDocumentStorageAdapter(
        base_dir=tmp_path / "docs",
        signing_secret=DEV_SECRET,
        public_api_base_url="http://testserver",
    )

    def _add_member(
        role: UserRole,
        store_ids: frozenset[UUID] = frozenset(),
        department_scope: str | None = None,
        display_name: str = "",
    ) -> UUID:
        uid = uuid4()
        mid = uuid4()
        org_repos.memberships[mid] = Membership(
            id=mid,
            tenant_id=tenant_id,
            user_id=uid,
            email=f"{role.value}-{str(uid)[:6]}@dragon.es",
            display_name=display_name or role.value,
            role=role,
            status=MembershipStatus.ACTIVE,
            store_ids=store_ids,
            department_scope=department_scope,
            created_at=now,
            updated_at=now,
        )
        return uid

    hr_id = _add_member(UserRole.HR, display_name="Elena HR")
    mgr_a_id = _add_member(
        UserRole.STORE_MANAGER, store_ids=frozenset({store_a_id}), display_name="Carlos Mgr"
    )
    acc_id = _add_member(UserRole.ACCOUNTANT, display_name="Sofia Acc")
    emp1_uid = _add_member(UserRole.EMPLOYEE, display_name="Ming Zhou")
    emp2_uid = _add_member(UserRole.EMPLOYEE, display_name="Lucia Gomez")

    def _add_emp(
        code: str,
        name: str,
        store_id: UUID,
        dept: str,
        user_id: UUID | None = None,
    ) -> Employee:
        eid = uuid4()
        emp = Employee(
            id=eid,
            tenant_id=tenant_id,
            primary_store_id=store_id,
            user_id=user_id,
            employee_code=code,
            full_name=name,
            preferred_name=name.split()[0],
            email=f"{code.lower()}@dragon.es",
            phone="+34600000000",
            birth_date=date(1995, 5, 10),
            contract_type=ContractType.INDEFINIDO,
            work_schedule_type=WorkScheduleType.FULL_TIME,
            contracted_weekly_hours=Decimal("40.00"),
            annual_vacation_days=30,
            hire_date=date(2024, 1, 15),
            termination_date=None,
            nie_dni_enc=cipher.encrypt(
                "12345678Z", tenant_id=tenant_id, employee_id=eid, field_name="nie_dni"
            ),
            nie_dni_masked="****5678Z",
            nie_dni_blind_index=f"idx-{code}",
            iban_enc=cipher.encrypt(
                "ES9121000418450200051332", tenant_id=tenant_id, employee_id=eid, field_name="iban"
            ),
            iban_masked="ES91****1332",
            naf_enc=cipher.encrypt(
                "281234567890", tenant_id=tenant_id, employee_id=eid, field_name="naf"
            ),
            naf_masked="28****7890",
            employment_status=EmploymentStatus.ACTIVE,
            retention_until=None,
            anonymized_at=None,
            created_at=now,
            updated_at=now,
            department=dept,
            job_title="Camarero",
            emergency_contact_name="Contacto Secreto",
            emergency_contact_phone="+34600111222",
        )
        emp_repo.employees[eid] = emp
        return emp

    emp1 = _add_emp("EMP-001", "Ming Zhou", store_a_id, "Sala", user_id=emp1_uid)
    emp2 = _add_emp("EMP-002", "=CMD|'/C calc'!A0", store_a_id, "Cocina", user_id=emp2_uid)
    emp_bcn = _add_emp("EMP-003", "Jordi BCN", store_b_id, "Sala", user_id=None)

    settings = Settings(
        app_env="development",
        dev_auth_enabled=True,
        supabase_jwt_secret=DEV_SECRET,
        pii_encryption_key=TEST_PII_KEY,
    )
    app = create_app(settings_override=settings)
    verifier = SupabaseJWTVerifier(
        supabase_url="https://placeholder.supabase.co",
        jwt_secret=DEV_SECRET,
    )

    async def _noop_commit() -> None:
        return None

    app.dependency_overrides[get_jwt_verifier] = lambda: verifier
    app.dependency_overrides[get_tenant_repository] = lambda: org_repos
    app.dependency_overrides[get_store_repository] = lambda: store_repo
    app.dependency_overrides[get_membership_repository] = lambda: membership_repo
    app.dependency_overrides[get_employee_repository] = lambda: emp_repo
    app.dependency_overrides[get_attendance_repository] = lambda: att_repo
    app.dependency_overrides[get_shift_repository] = lambda: shift_repo
    app.dependency_overrides[get_leave_repository] = lambda: leave_repo
    app.dependency_overrides[get_document_repository] = lambda: doc_repo
    app.dependency_overrides[get_announcement_repository] = lambda: ann_repo
    class _EmptyBreakPolicyRepo:
        async def list_by_tenant(self, _tenant_id: UUID) -> list[object]:
            return []

    class _EmptyLeaveEntitlementRepo:
        async def get_by_employee_year(
            self, _tenant_id: UUID, _employee_id: UUID, _year: int
        ) -> None:
            return None

        async def list_by_tenant(
            self, _tenant_id: UUID, *, year: int | None = None, employee_id: UUID | None = None
        ) -> list[object]:
            return []

    class _EmptyDayReviewRepo:
        async def list_by_tenant(
            self,
            _tenant_id: UUID,
            *,
            employee_ids: object = None,
            date_from: object = None,
            date_to: object = None,
        ) -> list[object]:
            return []

    app.dependency_overrides[get_payroll_closing_repository] = lambda: payroll_repo
    app.dependency_overrides[get_break_policy_repository] = lambda: _EmptyBreakPolicyRepo()
    app.dependency_overrides[get_attendance_day_review_repository] = lambda: _EmptyDayReviewRepo()
    app.dependency_overrides[get_leave_entitlement_repository] = (
        lambda: _EmptyLeaveEntitlementRepo()
    )
    app.dependency_overrides[get_pii_cipher] = lambda: cipher
    app.dependency_overrides[get_document_storage] = lambda: storage
    app.dependency_overrides[get_commit_fn] = lambda: _noop_commit
    app.dependency_overrides[get_utc_now] = lambda: clock.now

    def _headers(uid: UUID) -> dict[str, str]:
        tok = sign_dev_hs256_token(user_id=uid, email="x@dragon.es", secret=DEV_SECRET)
        return {"Authorization": f"Bearer {tok}", "X-Tenant-Id": str(tenant_id)}

    client = TestClient(app)
    return {
        "client": client,
        "clock": clock,
        "tenant_id": tenant_id,
        "store_a_id": store_a_id,
        "store_b_id": store_b_id,
        "hr_id": hr_id,
        "mgr_a_id": mgr_a_id,
        "acc_id": acc_id,
        "emp1_uid": emp1_uid,
        "emp2_uid": emp2_uid,
        "emp1": emp1,
        "emp2": emp2,
        "emp_bcn": emp_bcn,
        "payroll_repo": payroll_repo,
        "headers": _headers,
    }


def test_report_counts_pending_announcements_for_target_department(tmp_path: Path) -> None:
    env = _build_env(tmp_path)
    client: TestClient = env["client"]
    hr_headers = env["headers"](env["hr_id"])
    report = "/api/v1/reports/executive-summary?from_date=2026-11-01&to_date=2026-11-30"
    created = client.post(
        "/api/v1/announcements",
        headers=hr_headers,
        data={
            "category": "policy",
            "title": "Training",
            "body": "Read the updated training policy.",
            "store_id": str(env["store_a_id"]),
            "department_scope": "sala",
            "requires_acknowledgement": "true",
        },
    )
    assert created.status_code == 201
    assert client.get(report, headers=hr_headers).json()["announcements_pending_ack_count"] == 1
    assert client.get(report + "&department=Cocina", headers=hr_headers).json()[
        "announcements_pending_ack_count"
    ] == 0

    announcement = "/api/v1/announcements/" + created.json()["id"]
    acknowledged = client.post(
        announcement + "/acknowledge", headers=env["headers"](env["emp1_uid"])
    )
    assert acknowledged.status_code == 201
    assert client.get(announcement + "/acknowledgements", headers=hr_headers).json()[
        "pending_count"
    ] == 0
    assert client.get(report, headers=hr_headers).json()["announcements_pending_ack_count"] == 0

    # A store-wide notice has two recipients here, but is counted once per notice.
    store_notice = client.post(
        "/api/v1/announcements",
        headers=hr_headers,
        data={
            "category": "policy",
            "title": "Store training",
            "body": "Training for both departments.",
            "store_id": str(env["store_a_id"]),
            "requires_acknowledgement": "true",
        },
    )
    assert store_notice.status_code == 201
    notice_url = "/api/v1/announcements/" + store_notice.json()["id"]
    assert client.get(notice_url + "/acknowledgements", headers=hr_headers).json()[
        "pending_count"
    ] == 2
    assert client.get(report, headers=hr_headers).json()["announcements_pending_ack_count"] == 1


def test_self_selectable_clock_and_manual_day_workflows(tmp_path: Path) -> None:
    env = _build_env(tmp_path, now_utc=datetime(2026, 11, 26, 18, 0, tzinfo=UTC))
    client: TestClient = env["client"]
    h = env["headers"]
    emp1_h = h(env["emp1_uid"])
    emp2_h = h(env["emp2_uid"])
    acc_h = h(env["acc_id"])
    emp1: Employee = env["emp1"]
    emp2: Employee = env["emp2"]

    # 1. Custom occurred_at_utc on /clock (past 10 hours, valid) -> recorded_at_utc == now_utc
    res_in = client.post(
        "/api/v1/attendance/clock",
        json={
            "event_type": "clock_in",
            "occurred_at_utc": "2026-11-26T08:00:00Z",
        },
        headers=emp1_h,
    )
    assert res_in.status_code == 201
    ev_in = res_in.json()["event"]
    assert ev_in["occurred_at_utc"].startswith("2026-11-26T08:00:00")
    assert ev_in["recorded_at_utc"].startswith("2026-11-26T18:00:00")

    # Future > 5 min -> 422 ATTENDANCE_CHRONOLOGY_ERROR
    res_future = client.post(
        "/api/v1/attendance/clock",
        json={
            "event_type": "clock_out",
            "occurred_at_utc": "2026-11-26T18:10:00Z",
        },
        headers=emp1_h,
    )
    assert res_future.status_code == 422
    assert res_future.json()["error_code"] == "ATTENDANCE_CHRONOLOGY_ERROR"

    # Older than 31 days -> 422 ATTENDANCE_INVALID_TIME_RANGE
    res_old = client.post(
        "/api/v1/attendance/clock",
        json={
            "event_type": "clock_out",
            "occurred_at_utc": "2026-10-01T08:00:00Z",
        },
        headers=emp1_h,
    )
    assert res_old.status_code == 422
    assert res_old.json()["error_code"] == "ATTENDANCE_INVALID_TIME_RANGE"

    # Before latest effective punch (07:30 < 08:00) -> 422 ATTENDANCE_CHRONOLOGY_ERROR
    res_chrono = client.post(
        "/api/v1/attendance/clock",
        json={
            "event_type": "clock_out",
            "occurred_at_utc": "2026-11-26T07:30:00Z",
        },
        headers=emp1_h,
    )
    assert res_chrono.status_code == 422
    assert res_chrono.json()["error_code"] == "ATTENDANCE_CHRONOLOGY_ERROR"

    # Close shift at 16:00
    assert (
        client.post(
            "/api/v1/attendance/clock",
            json={"event_type": "clock_out", "occurred_at_utc": "2026-11-26T16:00:00Z"},
            headers=emp1_h,
        ).status_code
        == 201
    )

    # 2. Manual day: valid work + break + clock_out on yesterday (2026-11-25)
    res_man = client.post(
        "/api/v1/attendance/manual-day",
        json={
            "local_date": "2026-11-25",
            "clock_in_time": "09:00",
            "break_start_time": "13:00",
            "break_end_time": "13:30",
            "clock_out_time": "17:30",
            "work_mode": "onsite",
            "note": "Turno completo",
        },
        headers=emp1_h,
    )
    assert res_man.status_code == 201
    man_events = res_man.json()["events"]
    assert [e["event_type"] for e in man_events] == [
        "clock_in",
        "break_start",
        "break_end",
        "clock_out",
    ]

    # Overlap with existing session on 2026-11-25 -> 409 ATTENDANCE_SESSION_OVERLAP
    res_overlap = client.post(
        "/api/v1/attendance/manual-day",
        json={
            "local_date": "2026-11-25",
            "clock_in_time": "10:00",
            "clock_out_time": "14:00",
        },
        headers=emp1_h,
    )
    assert res_overlap.status_code == 409
    assert res_overlap.json()["error_code"] == "ATTENDANCE_SESSION_OVERLAP"

    # Invalid time order -> 422 ATTENDANCE_INVALID_TIME_RANGE
    res_bad_order = client.post(
        "/api/v1/attendance/manual-day",
        json={
            "local_date": "2026-11-24",
            "clock_in_time": "09:00",
            "break_start_time": "14:00",
            "break_end_time": "13:30",
            "clock_out_time": "17:00",
        },
        headers=emp1_h,
    )
    assert res_bad_order.status_code == 422
    assert res_bad_order.json()["error_code"] == "ATTENDANCE_INVALID_TIME_RANGE"

    # Future date -> 422
    res_fut_day = client.post(
        "/api/v1/attendance/manual-day",
        json={
            "local_date": "2026-11-27",
            "clock_in_time": "09:00",
            "clock_out_time": "17:00",
        },
        headers=emp1_h,
    )
    assert res_fut_day.status_code == 422

    # Cross-employee for Employee -> 403; Accountant -> 403
    assert (
        client.post(
            "/api/v1/attendance/manual-day",
            json={
                "employee_id": str(emp2.id),
                "local_date": "2026-11-24",
                "clock_in_time": "09:00",
                "clock_out_time": "17:00",
            },
            headers=emp1_h,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/attendance/manual-day",
            json={
                "employee_id": str(emp1.id),
                "local_date": "2026-11-24",
                "clock_in_time": "09:00",
                "clock_out_time": "17:00",
            },
            headers=acc_h,
        ).status_code
        == 403
    )

    # Closed payroll month -> 409 PAYROLL_MONTH_CLOSED
    payroll_repo: InMemoryPayrollClosingRepo = env["payroll_repo"]
    payroll_repo.closings.append(
        MonthlyClosing(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=env["store_a_id"],
            period_month="2026-11",
            status=MonthlyClosingStatus.CLOSED,
            revision_number=1,
            closed_by_user_id=env["hr_id"],
            closed_at_utc=datetime(2026, 11, 26, 12, 0, tzinfo=UTC),
            reopened_by_user_id=None,
            reopened_at_utc=None,
            reopen_reason="",
            notes="",
            retention_until=datetime(2030, 11, 26, 12, 0, tzinfo=UTC),
            created_at=datetime(2026, 11, 26, 12, 0, tzinfo=UTC),
            updated_at=datetime(2026, 11, 26, 12, 0, tzinfo=UTC),
        )
    )
    res_closed = client.post(
        "/api/v1/attendance/manual-day",
        json={
            "local_date": "2026-11-24",
            "clock_in_time": "09:00",
            "clock_out_time": "17:00",
        },
        headers=emp2_h,
    )
    assert res_closed.status_code == 409
    assert res_closed.json()["error_code"] == "PAYROLL_MONTH_CLOSED"


def test_two_stage_leave_approval_workflow(tmp_path: Path) -> None:
    env = _build_env(tmp_path)
    client: TestClient = env["client"]
    h = env["headers"]
    emp_h = h(env["emp1_uid"])
    mgr_h = h(env["mgr_a_id"])
    hr_h = h(env["hr_id"])
    emp1: Employee = env["emp1"]

    # 1. Employee submits -> pending_manager; HR can one-step approve directly (B3)
    create_onestep_res = client.post(
        "/api/v1/leaves",
        json={
            "leave_type": "vacation",
            "start_date": "2026-12-01",
            "end_date": "2026-12-02",
            "reason": "One-step HR approval",
        },
        headers=emp_h,
    )
    assert create_onestep_res.status_code == 201
    onestep_id = create_onestep_res.json()["id"]
    assert create_onestep_res.json()["status"] == "pending_manager"

    hr_onestep = client.post(
        f"/api/v1/leaves/{onestep_id}/review",
        json={"approve": True, "review_note": "Direct HR final"},
        headers=hr_h,
    )
    assert hr_onestep.status_code == 200
    assert hr_onestep.json()["status"] == "approved"
    assert hr_onestep.json()["hr_reviewed_by_user_id"] == str(env["hr_id"])
    assert hr_onestep.json()["hr_review_note"] == "Direct HR final"

    # 2. Employee submits another -> pending_manager -> Store Manager -> pending_hr -> HR
    create_res = client.post(
        "/api/v1/leaves",
        json={
            "leave_type": "vacation",
            "start_date": "2026-12-10",
            "end_date": "2026-12-12",
            "reason": "Navidad",
        },
        headers=emp_h,
    )
    assert create_res.status_code == 201
    leave_id = create_res.json()["id"]
    assert create_res.json()["status"] == "pending_manager"

    # 3. Store Manager approves -> pending_hr
    mgr_ok = client.post(
        f"/api/v1/leaves/{leave_id}/review",
        json={"approve": True, "review_note": "OK tienda"},
        headers=mgr_h,
    )
    assert mgr_ok.status_code == 200
    assert mgr_ok.json()["status"] == "pending_hr"
    assert mgr_ok.json()["manager_reviewed_by_user_id"] == str(env["mgr_a_id"])
    assert mgr_ok.json()["manager_review_note"] == "OK tienda"

    # 4. Store Manager blocked from final approval -> 403 LEAVE_REQUIRES_HR_FINAL
    mgr_final = client.post(
        f"/api/v1/leaves/{leave_id}/review",
        json={"approve": True},
        headers=mgr_h,
    )
    assert mgr_final.status_code == 403
    assert mgr_final.json()["error_code"] == "LEAVE_REQUIRES_HR_FINAL"

    # 5. HR approves -> approved
    hr_ok = client.post(
        f"/api/v1/leaves/{leave_id}/review",
        json={"approve": True, "review_note": "Aprobado RRHH"},
        headers=hr_h,
    )
    assert hr_ok.status_code == 200
    assert hr_ok.json()["status"] == "approved"
    assert hr_ok.json()["hr_reviewed_by_user_id"] == str(env["hr_id"])
    assert hr_ok.json()["hr_review_note"] == "Aprobado RRHH"

    # 6. Store Manager creates on employee's behalf -> starts at pending_hr -> HR approves
    mgr_create = client.post(
        "/api/v1/leaves",
        json={
            "employee_id": str(emp1.id),
            "leave_type": "sick_leave",
            "start_date": "2026-12-20",
            "end_date": "2026-12-21",
            "reason": "Gripe",
        },
        headers=mgr_h,
    )
    assert mgr_create.status_code == 201
    assert mgr_create.json()["status"] == "pending_hr"
    assert mgr_create.json()["manager_reviewed_by_user_id"] == str(env["mgr_a_id"])

    hr_final2 = client.post(
        f"/api/v1/leaves/{mgr_create.json()['id']}/review",
        json={"approve": True},
        headers=hr_h,
    )
    assert hr_final2.status_code == 200
    assert hr_final2.json()["status"] == "approved"


def test_management_reports_center_and_gdpr_redaction(tmp_path: Path) -> None:
    env = _build_env(tmp_path, now_utc=datetime(2026, 11, 26, 18, 0, tzinfo=UTC))
    client: TestClient = env["client"]
    h = env["headers"]
    hr_h = h(env["hr_id"])
    mgr_h = h(env["mgr_a_id"])
    acc_h = h(env["acc_id"])
    emp_h = h(env["emp1_uid"])

    # Seed some attendance & approved sick leave
    assert (
        client.post(
            "/api/v1/attendance/manual-day",
            json={
                "local_date": "2026-11-25",
                "clock_in_time": "09:00",
                "clock_out_time": "17:00",
            },
            headers=emp_h,
        ).status_code
        == 201
    )
    lv = client.post(
        "/api/v1/leaves",
        json={
            "employee_id": str(env["emp1"].id),
            "leave_type": "sick_leave",
            "start_date": "2026-11-20",
            "end_date": "2026-11-21",
        },
        headers=hr_h,
    )
    assert lv.status_code == 201
    assert (
        client.post(
            f"/api/v1/leaves/{lv.json()['id']}/review",
            json={"approve": True},
            headers=hr_h,
        ).status_code
        == 200
    )

    # Employee -> 403 on all report endpoints
    for path in (
        "/api/v1/reports/executive-summary?from_date=2026-11-01&to_date=2026-11-30",
        "/api/v1/reports/attendance-summary?from_date=2026-11-01&to_date=2026-11-30",
        "/api/v1/reports/leave-summary?year=2026",
        "/api/v1/reports/headcount-roster",
    ):
        assert client.get(path, headers=emp_h).status_code == 403

    # Store manager cross-store -> 403
    assert (
        client.get(
            f"/api/v1/reports/headcount-roster?store_id={env['store_b_id']}",
            headers=mgr_h,
        ).status_code
        == 403
    )

    # Executive summary: HR sees approved_sick_days=2, Accountant sees None (GDPR Art. 9)
    exec_hr = client.get(
        "/api/v1/reports/executive-summary?from_date=2026-11-01&to_date=2026-11-30",
        headers=hr_h,
    )
    assert exec_hr.status_code == 200
    assert exec_hr.json()["total_active_employees"] == 3
    assert exec_hr.json()["approved_sick_days"] == 2
    assert float(exec_hr.json()["total_worked_hours"]) == 8.0

    exec_acc = client.get(
        "/api/v1/reports/executive-summary?from_date=2026-11-01&to_date=2026-11-30",
        headers=acc_h,
    )
    assert exec_acc.status_code == 200
    assert exec_acc.json()["approved_sick_days"] is None

    # Attendance summary CSV + PDF
    att_csv = client.get(
        "/api/v1/reports/attendance-summary?from_date=2026-11-01&to_date=2026-11-30&format=csv",
        headers=hr_h,
    )
    assert att_csv.status_code == 200
    assert att_csv.content.startswith(b"\xef\xbb\xbf")
    assert b"EMP-001" in att_csv.content

    att_pdf = client.get(
        "/api/v1/reports/attendance-summary?from_date=2026-11-01&to_date=2026-11-30&format=pdf",
        headers=hr_h,
    )
    assert att_pdf.status_code == 200
    assert att_pdf.content.startswith(b"%PDF-1.4")

    # Leave summary CSV + PDF + GDPR Art. 9 redaction for Accountant
    lv_hr_csv = client.get("/api/v1/reports/leave-summary?year=2026&format=csv", headers=hr_h)
    assert lv_hr_csv.status_code == 200
    assert b"sick_days" in lv_hr_csv.content

    lv_acc_csv = client.get("/api/v1/reports/leave-summary?year=2026&format=csv", headers=acc_h)
    assert lv_acc_csv.status_code == 200
    assert b"sick_days" not in lv_acc_csv.content
    assert b"medical_appointment_days" not in lv_acc_csv.content
    assert b"other_absence_days" in lv_acc_csv.content

    lv_pdf = client.get("/api/v1/reports/leave-summary?year=2026&format=pdf", headers=hr_h)
    assert lv_pdf.status_code == 200
    assert lv_pdf.content.startswith(b"%PDF-1.4")

    # Headcount roster CSV + PDF + zero PII + CSV formula-injection escaping
    roster_csv = client.get("/api/v1/reports/headcount-roster?format=csv", headers=hr_h)
    assert roster_csv.status_code == 200
    text = roster_csv.content.decode("utf-8-sig")
    assert "'=CMD|'/C calc'!A0" in text
    assert "12345678Z" not in text
    assert "ES9121000418450200051332" not in text
    assert "Contacto Secreto" not in text

    roster_pdf = client.get("/api/v1/reports/headcount-roster?format=pdf", headers=hr_h)
    assert roster_pdf.status_code == 200
    assert roster_pdf.content.startswith(b"%PDF-1.4")


def test_phase5e_redteam_regressions(tmp_path: Path) -> None:
    # 1. Leading-space / tab / CR / LF CSV formula injection escaping
    assert escape_csv_cell("  =SUM(1,2)") == "'  =SUM(1,2)"
    assert escape_csv_cell("\t+1+1") == "'\t+1+1"
    assert escape_csv_cell("\r-cmd") == "'\r-cmd"
    assert escape_csv_cell("\n@evil") == "'\n@evil"

    # 2. Spring-forward DST gap time rejection (Europe/Madrid 2026-03-29 02:30 does not exist)
    try:
        parse_hhmm_to_utc(date(2026, 3, 29), "02:30", "Europe/Madrid")
        raise AssertionError("Expected ValidationDomainError for DST gap time")
    except ValidationDomainError:
        pass

    env = _build_env(tmp_path, now_utc=datetime(2026, 11, 26, 18, 0, tzinfo=UTC))
    client: TestClient = env["client"]
    h = env["headers"]
    emp_h = h(env["emp1_uid"])
    mgr_h = h(env["mgr_a_id"])
    hr_h = h(env["hr_id"])

    # 3. 367-day span rejected (422), 366-day span allowed (200)
    res_367 = client.get(
        "/api/v1/reports/executive-summary?from_date=2026-01-01&to_date=2027-01-02",
        headers=hr_h,
    )
    assert res_367.status_code == 422
    res_366 = client.get(
        "/api/v1/reports/executive-summary?from_date=2026-01-01&to_date=2027-01-01",
        headers=hr_h,
    )
    assert res_366.status_code == 200

    # 4. Exact timestamp equality on custom /clock rejected (422)
    assert (
        client.post(
            "/api/v1/attendance/clock",
            json={"event_type": "clock_in", "occurred_at_utc": "2026-11-26T09:00:00Z"},
            headers=emp_h,
        ).status_code
        == 201
    )
    res_eq = client.post(
        "/api/v1/attendance/clock",
        json={"event_type": "clock_out", "occurred_at_utc": "2026-11-26T09:00:00Z"},
        headers=emp_h,
    )
    assert res_eq.status_code == 422

    # 5. Justificante upload preserves manager_reviewed_* and hr_reviewed_* audit fields
    lv = client.post(
        "/api/v1/leaves",
        json={
            "leave_type": "sick_leave",
            "start_date": "2026-12-01",
            "end_date": "2026-12-02",
        },
        headers=emp_h,
    )
    assert lv.status_code == 201
    lv_id = lv.json()["id"]
    mgr_rev = client.post(
        f"/api/v1/leaves/{lv_id}/review",
        json={"approve": True, "review_note": "Visto gerente"},
        headers=mgr_h,
    )
    assert mgr_rev.status_code == 200
    assert mgr_rev.json()["manager_reviewed_by_user_id"] == str(env["mgr_a_id"])

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    up = client.post(
        f"/api/v1/leaves/{lv_id}/justificante",
        files={"file": ("parte.png", png_bytes, "image/png")},
        headers=emp_h,
    )
    assert up.status_code == 200
    assert up.json()["manager_reviewed_by_user_id"] == str(env["mgr_a_id"])
    assert up.json()["manager_review_note"] == "Visto gerente"
    assert up.json()["manager_reviewed_at_utc"] is not None
