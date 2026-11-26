"""Phase 5C tests: Employee profile fields, dashboard enrichments, and GDPR-safe team calendar."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.application.employees import AnonymizeEmployeeUseCase
from app.domain.attendance import (
    AttendanceEvent,
    AttendanceEventType,
    AttendanceSource,
    WorkMode,
)
from app.domain.dashboard import build_today_clock_segments, days_until_anniversary
from app.domain.employees import ContractType, Employee, EmploymentStatus, WorkScheduleType
from app.domain.leaves import LeaveRequest, LeaveStatus, LeaveType
from app.domain.organization import Membership, Store, Tenant
from app.domain.security import AuthenticatedPrincipal, MembershipStatus, UserRole
from app.infrastructure.config import Settings
from app.infrastructure.document_storage import LocalDocumentStorageAdapter
from app.infrastructure.pii_crypto import AesGcmPiiCipher
from app.infrastructure.supabase_jwt import SupabaseJWTVerifier, sign_dev_hs256_token
from app.interfaces.api.deps import (
    get_announcement_repository,
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
        legal_name="Bistro Madrid SL",
        trade_name="Bistro Madrid",
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
            email=f"{uid}@test.es",
            role=role,
            status=MembershipStatus.ACTIVE,
            store_ids=store_ids,
            department_scope=department_scope,
            display_name=display_name or f"User {role.value}",
            created_at=now,
            updated_at=now,
        )
        return uid

    owner_id = _add_member(UserRole.OWNER, display_name="Owner")
    hr_id = _add_member(UserRole.HR, display_name="HR")
    accountant_id = _add_member(UserRole.ACCOUNTANT, display_name="Accountant")
    mgr_a_cocina_id = _add_member(
        UserRole.STORE_MANAGER,
        store_ids=frozenset({store_a_id}),
        department_scope="Cocina",
        display_name="Manager Cocina",
    )
    emp_cocina_uid = _add_member(UserRole.EMPLOYEE, display_name="Chef Ana")
    emp_sala_uid = _add_member(UserRole.EMPLOYEE, display_name="Waiter Luis")
    emp_bcn_uid = _add_member(UserRole.EMPLOYEE, display_name="Barista Marc")

    settings = Settings(
        app_env="local",
        supabase_jwt_secret=DEV_SECRET,
        supabase_jwt_audience="authenticated",
    )
    app = create_app(settings)
    verifier = SupabaseJWTVerifier(
        supabase_url="https://placeholder.supabase.co",
        audience="authenticated",
        jwt_secret=DEV_SECRET,
    )
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

    app.dependency_overrides[get_payroll_closing_repository] = lambda: payroll_repo
    app.dependency_overrides[get_break_policy_repository] = lambda: _EmptyBreakPolicyRepo()
    app.dependency_overrides[get_leave_entitlement_repository] = (
        lambda: _EmptyLeaveEntitlementRepo()
    )
    app.dependency_overrides[get_pii_cipher] = lambda: cipher
    app.dependency_overrides[get_document_storage] = lambda: storage
    app.dependency_overrides[get_utc_now] = lambda: clock.now

    async def _noop_commit() -> None:
        return None

    app.dependency_overrides[get_commit_fn] = lambda: _noop_commit

    def _auth_headers(user_id: UUID) -> dict[str, str]:
        token = sign_dev_hs256_token(
            user_id=user_id,
            secret=DEV_SECRET,
            email=f"{user_id}@test.es",
        )
        return {"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant_id)}

    return {
        "client": TestClient(app),
        "clock": clock,
        "tenant_id": tenant_id,
        "store_a_id": store_a_id,
        "store_b_id": store_b_id,
        "owner_id": owner_id,
        "hr_id": hr_id,
        "accountant_id": accountant_id,
        "mgr_a_cocina_id": mgr_a_cocina_id,
        "emp_cocina_uid": emp_cocina_uid,
        "emp_sala_uid": emp_sala_uid,
        "emp_bcn_uid": emp_bcn_uid,
        "emp_repo": emp_repo,
        "att_repo": att_repo,
        "leave_repo": leave_repo,
        "org_repos": org_repos,
        "auth": _auth_headers,
    }


def _make_emp(
    *,
    tenant_id: UUID,
    store_id: UUID,
    user_id: UUID | None,
    code: str,
    full_name: str,
    department: str = "Cocina",
    job_title: str = "",
    hire_date: date = date(2024, 1, 10),
    birth_date: date | None = date(1992, 5, 14),
    status: EmploymentStatus = EmploymentStatus.ACTIVE,
) -> Employee:
    now = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    return Employee(
        id=uuid4(),
        tenant_id=tenant_id,
        primary_store_id=store_id,
        user_id=user_id,
        employee_code=code,
        full_name=full_name,
        preferred_name=full_name.split()[0],
        department=department,
        job_title=job_title,
        emergency_contact_name="Contacto",
        emergency_contact_phone="+34600000000",
        email=f"{code.lower()}@empresa.es",
        phone="+34600111222",
        birth_date=birth_date,
        contract_type=ContractType.INDEFINIDO,
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
        annual_vacation_days=30,
        hire_date=hire_date,
        termination_date=None,
        nie_dni_enc=None,
        nie_dni_masked=None,
        nie_dni_blind_index=None,
        iban_enc=None,
        iban_masked=None,
        naf_enc=None,
        naf_masked=None,
        employment_status=status,
        retention_until=None,
        anonymized_at=None,
        created_at=now,
        updated_at=now,
    )


async def test_employee_profile_fields_create_update_anonymize_and_import(tmp_path: Path) -> None:
    env = _build_env(tmp_path)
    client: TestClient = env["client"]
    hr_headers = env["auth"](env["hr_id"])

    # Create with job_title and emergency contact fields
    resp = client.post(
        "/api/v1/employees",
        headers=hr_headers,
        json={
            "primary_store_id": str(env["store_a_id"]),
            "employee_code": "EMP-5C-01",
            "full_name": "Elena Torres",
            "department": "Cocina",
            "job_title": "Jefa de Cocina",
            "emergency_contact_name": "Carlos Torres",
            "emergency_contact_phone": "+34611223344",
            "hire_date": "2024-06-01",
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["job_title"] == "Jefa de Cocina"
    assert created["emergency_contact_name"] == "Carlos Torres"
    assert created["emergency_contact_phone"] == "+34611223344"

    emp_id = UUID(created["id"])

    # Update profile fields
    patch_resp = client.patch(
        f"/api/v1/employees/{emp_id}",
        headers=hr_headers,
        json={
            "primary_store_id": str(env["store_a_id"]),
            "employee_code": "EMP-5C-01",
            "full_name": "Elena Torres",
            "department": "Cocina",
            "job_title": "Chef Ejecutiva",
            "emergency_contact_name": "Laura Torres",
            "emergency_contact_phone": "+34699887766",
            "hire_date": "2024-06-01",
            "contract_type": "indefinido",
            "work_schedule_type": "full_time",
            "contracted_weekly_hours": "40.00",
            "annual_vacation_days": 30,
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    updated = patch_resp.json()
    assert updated["job_title"] == "Chef Ejecutiva"
    assert updated["emergency_contact_name"] == "Laura Torres"
    assert updated["emergency_contact_phone"] == "+34699887766"

    # Bulk import template includes job_title & CSV import persists job_title
    tpl_resp = client.get("/api/v1/employees/import/template?format=csv", headers=hr_headers)
    assert tpl_resp.status_code == 200
    assert "job_title" in tpl_resp.text.splitlines()[0]

    csv_body = (
        b"employee_code,full_name,store_code,department,job_title,"
        b"contract_type,work_schedule_type,contracted_weekly_hours,annual_vacation_days,hire_date\n"
        b"EMP-5C-02,Mateo Ruiz,MAD-01,Sala,Sumiller,indefinido,full_time,40.00,30,2025-03-01\n"
    )
    imp_resp = client.post(
        "/api/v1/employees/import",
        headers=hr_headers,
        files={"file": ("staff.csv", csv_body, "text/csv")},
    )
    assert imp_resp.status_code == 200, imp_resp.text
    assert imp_resp.json()["created_count"] == 1
    imported = [
        e for e in env["emp_repo"].employees.values() if e.employee_code == "EMP-5C-02"
    ][0]
    assert imported.job_title == "Sumiller"

    # Anonymization clears job_title, emergency_contact_name, emergency_contact_phone
    emp_repo: InMemoryEmployeeRepo = env["emp_repo"]
    emp_repo.employees[emp_id] = replace(
        emp_repo.employees[emp_id],
        employment_status=EmploymentStatus.LEGAL_HOLD,
        termination_date=date(2020, 1, 1),
        retention_until=datetime(2024, 1, 2, tzinfo=UTC),
    )
    principal = AuthenticatedPrincipal(
        user_id=env["owner_id"],
        tenant_id=env["tenant_id"],
        role=UserRole.OWNER,
        store_ids=frozenset(),
    )
    anonymized = await AnonymizeEmployeeUseCase(emp_repo).execute(
        principal, emp_id, now_utc=datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    )
    assert anonymized.job_title == ""
    assert anonymized.emergency_contact_name == ""
    assert anonymized.emergency_contact_phone == ""


def test_days_until_anniversary_pure_logic() -> None:
    # 1 year anniversary today -> (0, 1)
    assert days_until_anniversary(date(2025, 11, 26), date(2026, 11, 26)) == (0, 1)
    # Hired today -> 0 years of service -> None
    assert days_until_anniversary(date(2026, 11, 26), date(2026, 11, 26)) is None
    # Boundary 30 days vs 31 days
    assert days_until_anniversary(date(2023, 12, 26), date(2026, 11, 26)) == (30, 3)
    assert days_until_anniversary(date(2023, 12, 27), date(2026, 11, 26)) is None
    # Year boundary wrap-around
    assert days_until_anniversary(date(2024, 1, 10), date(2026, 12, 20)) == (21, 3)
    # Feb 29 -> Feb 28 in non-leap year
    assert days_until_anniversary(date(2024, 2, 29), date(2025, 2, 20)) == (8, 1)
    # Feb 29 -> Feb 29 in leap year
    assert days_until_anniversary(date(2024, 2, 29), date(2028, 2, 20)) == (9, 4)


def test_today_clock_segments_unit_and_dashboard_api(tmp_path: Path) -> None:
    now = datetime(2026, 11, 26, 14, 30, tzinfo=UTC)  # 15:30 Europe/Madrid
    env = _build_env(tmp_path, now_utc=now)
    client: TestClient = env["client"]

    emp_self = _make_emp(
        tenant_id=env["tenant_id"],
        store_id=env["store_a_id"],
        user_id=env["emp_cocina_uid"],
        code="CHEF-1",
        full_name="Ana Chef",
        department="Cocina",
        job_title="Jefa de Partida",
        hire_date=date(2023, 12, 5),  # 9 days until 3y anniversary
    )
    emp_teammate = _make_emp(
        tenant_id=env["tenant_id"],
        store_id=env["store_a_id"],
        user_id=env["emp_sala_uid"],
        code="CHEF-2",
        full_name="Luis Ayudante",
        department="Cocina",
        job_title="Ayudante de Cocina",
        hire_date=date(2026, 11, 26),  # 0y -> excluded from anniversaries
    )
    env["emp_repo"].employees[emp_self.id] = emp_self
    env["emp_repo"].employees[emp_teammate.id] = emp_teammate

    def _ev(etype: AttendanceEventType, hour: int, minute: int, mode: WorkMode) -> AttendanceEvent:
        ts = datetime(2026, 11, 26, hour, minute, tzinfo=UTC)
        return AttendanceEvent(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=env["store_a_id"],
            employee_id=emp_self.id,
            event_type=etype,
            occurred_at_utc=ts,
            store_timezone="Europe/Madrid",
            source=AttendanceSource.WEB,
            recorded_by_user_id=env["emp_cocina_uid"],
            recorded_at_utc=ts,
            retention_until=ts + timedelta(days=365 * 4),
            work_mode=mode,
        )

    events = [
        _ev(AttendanceEventType.CLOCK_IN, 8, 0, WorkMode.REMOTE),
        _ev(AttendanceEventType.BREAK_START, 12, 0, WorkMode.REMOTE),
        _ev(AttendanceEventType.BREAK_END, 12, 30, WorkMode.REMOTE),
        _ev(AttendanceEventType.CLOCK_OUT, 13, 0, WorkMode.REMOTE),
        _ev(AttendanceEventType.CLOCK_IN, 14, 0, WorkMode.ONSITE),  # still open at 14:30
    ]
    for ev in events:
        env["att_repo"].events.append(ev)

    # Pure domain helper check
    segs = build_today_clock_segments(emp_self.id, events, date(2026, 11, 26), now)
    assert len(segs) == 4
    assert [(s.kind, s.duration_minutes, s.work_mode, s.end_utc is None) for s in segs] == [
        ("work", 240, WorkMode.REMOTE, False),
        ("break", 30, WorkMode.REMOTE, False),
        ("work", 30, WorkMode.REMOTE, False),
        ("work", 30, WorkMode.ONSITE, True),
    ]

    # /api/v1/dashboard/me check
    me_resp = client.get("/api/v1/dashboard/me", headers=env["auth"](env["emp_cocina_uid"]))
    assert me_resp.status_code == 200, me_resp.text
    me_data = me_resp.json()
    assert me_data["employee"]["job_title"] == "Jefa de Partida"
    assert me_data["teammates"][0]["job_title"] == "Ayudante de Cocina"
    assert len(me_data["clock_state"]["today_segments"]) == 4
    assert me_data["clock_state"]["today_segments"][-1]["end_utc"] is None
    assert me_data["clock_state"]["today_segments"][-1]["duration_minutes"] == 30
    assert len(me_data["upcoming_anniversaries"]) == 1
    assert me_data["upcoming_anniversaries"][0]["years_of_service"] == 3
    assert me_data["upcoming_anniversaries"][0]["days_until"] == 9
    assert me_data["upcoming_anniversaries"][0]["job_title"] == "Jefa de Partida"

    # /api/v1/dashboard/admin check
    admin_resp = client.get("/api/v1/dashboard/admin", headers=env["auth"](env["owner_id"]))
    assert admin_resp.status_code == 200, admin_resp.text
    admin_data = admin_resp.json()
    working_group = next(g for g in admin_data["who_is_in"] if g["status"] == "working")
    assert working_group["members"][0]["job_title"] == "Jefa de Partida"
    assert len(admin_data["upcoming_anniversaries"]) == 1
    assert admin_data["upcoming_anniversaries"][0]["years_of_service"] == 3


def test_team_calendar_gdpr_art9_and_rbac_isolation(tmp_path: Path) -> None:
    env = _build_env(tmp_path)
    client: TestClient = env["client"]

    emp_cocina = _make_emp(
        tenant_id=env["tenant_id"],
        store_id=env["store_a_id"],
        user_id=env["emp_cocina_uid"],
        code="MAD-C1",
        full_name="Ana Cocina",
        department="Cocina",
        job_title="Cocinera",
    )
    emp_cocina_2 = _make_emp(
        tenant_id=env["tenant_id"],
        store_id=env["store_a_id"],
        user_id=None,
        code="MAD-C2",
        full_name="Pedro Cocina",
        department="Cocina",
        job_title="Pinche",
    )
    emp_sala = _make_emp(
        tenant_id=env["tenant_id"],
        store_id=env["store_a_id"],
        user_id=env["emp_sala_uid"],
        code="MAD-S1",
        full_name="Luis Sala",
        department="Sala",
        job_title="Camarero",
    )
    emp_bcn = _make_emp(
        tenant_id=env["tenant_id"],
        store_id=env["store_b_id"],
        user_id=env["emp_bcn_uid"],
        code="BCN-01",
        full_name="Marc BCN",
        department="Cocina",
        job_title="Barista",
    )
    emp_terminated = _make_emp(
        tenant_id=env["tenant_id"],
        store_id=env["store_a_id"],
        user_id=None,
        code="MAD-TERM",
        full_name="Ex Empleado",
        department="Cocina",
        status=EmploymentStatus.TERMINATED,
    )
    for e in (emp_cocina, emp_cocina_2, emp_sala, emp_bcn, emp_terminated):
        env["emp_repo"].employees[e.id] = e

    now = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)

    def _add_leave(
        emp: Employee,
        ltype: LeaveType,
        lstatus: LeaveStatus,
        start: date,
        end: date,
        reason: str = "Confidencial",
    ) -> None:
        lv = LeaveRequest(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=emp.primary_store_id,
            employee_id=emp.id,
            leave_type=ltype,
            start_date=start,
            end_date=end,
            natural_days=(end - start).days + 1,
            status=lstatus,
            reason=reason,
            requested_by_user_id=env["hr_id"],
            reviewed_by_user_id=(
                env["hr_id"]
                if lstatus not in (LeaveStatus.PENDING_MANAGER, LeaveStatus.PENDING_HR)
                else None
            ),
            reviewed_at_utc=(
                now
                if lstatus not in (LeaveStatus.PENDING_MANAGER, LeaveStatus.PENDING_HR)
                else None
            ),
            review_note="Nota interna",
            medical_document_id=uuid4() if ltype == LeaveType.SICK_LEAVE else None,
            retention_until=now + timedelta(days=365 * 4),
            created_at=now,
            updated_at=now,
        )
        env["leave_repo"].leaves.append(lv)

    _add_leave(
        emp_cocina, LeaveType.SICK_LEAVE, LeaveStatus.APPROVED, date(2026, 12, 2), date(2026, 12, 4)
    )
    _add_leave(
        emp_cocina_2,
        LeaveType.MEDICAL_APPOINTMENT,
        LeaveStatus.PENDING_MANAGER,
        date(2026, 12, 10),
        date(2026, 12, 10),
    )
    _add_leave(
        emp_cocina_2,
        LeaveType.VACATION,
        LeaveStatus.REJECTED,
        date(2026, 12, 15),
        date(2026, 12, 18),
    )
    _add_leave(
        emp_sala, LeaveType.VACATION, LeaveStatus.APPROVED, date(2026, 12, 20), date(2026, 12, 22)
    )
    _add_leave(
        emp_bcn, LeaveType.SICK_LEAVE, LeaveStatus.APPROVED, date(2026, 12, 5), date(2026, 12, 7)
    )

    # 1. Span > 93 days -> 422, to_date < from_date -> 422
    r_too_long = client.get(
        "/api/v1/leaves/team-calendar?from_date=2026-12-01&to_date=2027-03-05",
        headers=env["auth"](env["hr_id"]),
    )
    assert r_too_long.status_code == 422
    r_reversed = client.get(
        "/api/v1/leaves/team-calendar?from_date=2026-12-10&to_date=2026-12-01",
        headers=env["auth"](env["hr_id"]),
    )
    assert r_reversed.status_code == 422

    # 2. Employee view: scoped to own store + own department, self unredacted, peer health redacted
    r_emp = client.get(
        "/api/v1/leaves/team-calendar?from_date=2026-12-01&to_date=2026-12-31",
        headers=env["auth"](env["emp_cocina_uid"]),
    )
    assert r_emp.status_code == 200, r_emp.text
    emp_data = r_emp.json()
    member_ids = {m["employee_id"] for m in emp_data["members"]}
    assert member_ids == {str(emp_cocina.id), str(emp_cocina_2.id)}
    self_member = next(m for m in emp_data["members"] if m["employee_id"] == str(emp_cocina.id))
    peer_member = next(m for m in emp_data["members"] if m["employee_id"] == str(emp_cocina_2.id))
    assert self_member["entries"][0]["display_type"] == "sick_leave"
    assert len(peer_member["entries"]) == 1  # rejected vacation excluded
    assert peer_member["entries"][0]["display_type"] == "absence_redacted"
    for forbidden_key in ("reason", "review_note", "medical_document_id"):
        assert forbidden_key not in peer_member["entries"][0]
    # Holidays in Dec 2026 (Constitución Dec 6, Inmaculada Dec 8, Navidad Dec 25)
    holiday_dates = {h["date"] for h in emp_data["holidays"]}
    assert {"2026-12-06", "2026-12-08", "2026-12-25"}.issubset(holiday_dates)

    # Employee cross-store -> 403
    r_emp_xstore = client.get(
        f"/api/v1/leaves/team-calendar?from_date=2026-12-01&to_date=2026-12-31&store_id={env['store_b_id']}",
        headers=env["auth"](env["emp_cocina_uid"]),
    )
    assert r_emp_xstore.status_code == 403

    # 3. Accountant view: tenant-wide active/on_leave, ALL others' health leaves redacted
    r_acc = client.get(
        "/api/v1/leaves/team-calendar?from_date=2026-12-01&to_date=2026-12-31",
        headers=env["auth"](env["accountant_id"]),
    )
    assert r_acc.status_code == 200
    acc_members = {m["employee_id"]: m for m in r_acc.json()["members"]}
    assert str(emp_terminated.id) not in acc_members
    assert acc_members[str(emp_cocina.id)]["entries"][0]["display_type"] == "absence_redacted"
    assert acc_members[str(emp_cocina_2.id)]["entries"][0]["display_type"] == "absence_redacted"
    assert acc_members[str(emp_bcn.id)]["entries"][0]["display_type"] == "absence_redacted"
    assert acc_members[str(emp_sala.id)]["entries"][0]["display_type"] == "vacation"

    # 4. Store manager (store_a + department_scope="Cocina"): unredacted, isolated to Cocina
    r_mgr = client.get(
        "/api/v1/leaves/team-calendar?from_date=2026-12-01&to_date=2026-12-31",
        headers=env["auth"](env["mgr_a_cocina_id"]),
    )
    assert r_mgr.status_code == 200
    mgr_members = {m["employee_id"]: m for m in r_mgr.json()["members"]}
    assert set(mgr_members.keys()) == {str(emp_cocina.id), str(emp_cocina_2.id)}
    assert mgr_members[str(emp_cocina.id)]["entries"][0]["display_type"] == "sick_leave"
    assert mgr_members[str(emp_cocina_2.id)]["entries"][0]["display_type"] == "medical_appointment"

    r_mgr_xstore = client.get(
        f"/api/v1/leaves/team-calendar?from_date=2026-12-01&to_date=2026-12-31&store_id={env['store_b_id']}",
        headers=env["auth"](env["mgr_a_cocina_id"]),
    )
    assert r_mgr_xstore.status_code == 403

    r_mgr_xdept = client.get(
        "/api/v1/leaves/team-calendar?from_date=2026-12-01&to_date=2026-12-31&department=Sala",
        headers=env["auth"](env["mgr_a_cocina_id"]),
    )
    assert r_mgr_xdept.status_code == 403

    # 5. HR / Owner: unredacted across tenant
    r_hr = client.get(
        "/api/v1/leaves/team-calendar?from_date=2026-12-01&to_date=2026-12-31",
        headers=env["auth"](env["hr_id"]),
    )
    assert r_hr.status_code == 200
    hr_members = {m["employee_id"]: m for m in r_hr.json()["members"]}
    assert hr_members[str(emp_cocina.id)]["entries"][0]["display_type"] == "sick_leave"
    assert hr_members[str(emp_bcn.id)]["entries"][0]["display_type"] == "sick_leave"


def test_phase5c_red_team_regressions(tmp_path: Path) -> None:
    # 1. Midnight-crossing open shift still included in build_today_clock_segments
    emp_id = uuid4()
    tenant_id = uuid4()
    store_id = uuid4()
    uid = uuid4()
    overnight_in = datetime(2026, 11, 25, 22, 30, tzinfo=UTC)  # 23:30 Europe/Madrid on Nov 25
    now_after_midnight = datetime(2026, 11, 26, 1, 0, tzinfo=UTC)  # 02:00 Europe/Madrid on Nov 26
    overnight_ev = AttendanceEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=store_id,
        employee_id=emp_id,
        event_type=AttendanceEventType.CLOCK_IN,
        occurred_at_utc=overnight_in,
        store_timezone="Europe/Madrid",
        source=AttendanceSource.WEB,
        recorded_by_user_id=uid,
        recorded_at_utc=overnight_in,
        retention_until=overnight_in + timedelta(days=365 * 4),
        work_mode=WorkMode.ONSITE,
    )
    segs = build_today_clock_segments(
        emp_id, [overnight_ev], date(2026, 11, 26), now_after_midnight
    )
    assert len(segs) == 1
    assert segs[0].kind == "work"
    assert segs[0].end_utc is None
    assert segs[0].duration_minutes == 150

    # 2. Non-vacation leave collapse (no Art. 9 inference oracle), employee out-of-dept 403,
    #    terminated employee 403, and store-local date evaluation for anniversaries.
    # At 2026-11-26 23:30 UTC: Europe/Madrid is Nov 27 00:30, Atlantic/Canary is Nov 26 23:30
    env = _build_env(tmp_path, now_utc=datetime(2026, 11, 26, 23, 30, tzinfo=UTC))
    client: TestClient = env["client"]

    # Update store_b to Atlantic/Canary to test store-local date boundary
    env["org_repos"].stores[env["store_b_id"]] = replace(
        env["org_repos"].stores[env["store_b_id"]],
        timezone="Atlantic/Canary",
    )

    emp_cocina = _make_emp(
        tenant_id=env["tenant_id"],
        store_id=env["store_a_id"],
        user_id=env["emp_cocina_uid"],
        code="MAD-C1",
        full_name="Ana Cocina",
        department="Cocina",
        hire_date=date(2025, 11, 26),  # Already past in Madrid (Nov 27) -> NOT in [0, 30]
    )
    emp_canary = _make_emp(
        tenant_id=env["tenant_id"],
        store_id=env["store_b_id"],
        user_id=env["emp_bcn_uid"],
        code="CAN-01",
        full_name="Marc Canary",
        department="Cocina",
        hire_date=date(2025, 11, 26),  # Still Nov 26 in Canary -> days_until=0!
    )
    emp_no_dept = _make_emp(
        tenant_id=env["tenant_id"],
        store_id=env["store_a_id"],
        user_id=env["emp_sala_uid"],
        code="MAD-NODEPT",
        full_name="Sin Dept",
        department="",
    )
    emp_cocina_peer = _make_emp(
        tenant_id=env["tenant_id"],
        store_id=env["store_a_id"],
        user_id=None,
        code="MAD-C2",
        full_name="Pedro Cocina",
        department="Cocina",
    )
    for e in (emp_cocina, emp_canary, emp_no_dept, emp_cocina_peer):
        env["emp_repo"].employees[e.id] = e

    # Non-vacation leave (PAID_LEAVE) on peer -> collapsed to absence_redacted for employee/acc
    now = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    env["leave_repo"].leaves.append(
        LeaveRequest(
            id=uuid4(),
            tenant_id=env["tenant_id"],
            store_id=env["store_a_id"],
            employee_id=emp_cocina_peer.id,
            leave_type=LeaveType.PAID_LEAVE,
            start_date=date(2026, 12, 5),
            end_date=date(2026, 12, 6),
            natural_days=2,
            status=LeaveStatus.APPROVED,
            reason="Permiso retribuido",
            requested_by_user_id=env["hr_id"],
            reviewed_by_user_id=env["hr_id"],
            reviewed_at_utc=now,
            review_note="",
            medical_document_id=None,
            retention_until=now + timedelta(days=365 * 4),
            created_at=now,
            updated_at=now,
        )
    )

    # Employee viewing peer's PAID_LEAVE sees absence_redacted (cannot distinguish from sick_leave)
    r_emp = client.get(
        "/api/v1/leaves/team-calendar?from_date=2026-12-01&to_date=2026-12-31",
        headers=env["auth"](env["emp_cocina_uid"]),
    )
    assert r_emp.status_code == 200
    peer_entry = next(
        m for m in r_emp.json()["members"] if m["employee_id"] == str(emp_cocina_peer.id)
    )["entries"][0]
    assert peer_entry["display_type"] == "absence_redacted"

    # Employee requesting another department -> 403
    assert (
        client.get(
            "/api/v1/leaves/team-calendar?from_date=2026-12-01&to_date=2026-12-31&department=Sala",
            headers=env["auth"](env["emp_cocina_uid"]),
        ).status_code
        == 403
    )

    # Employee with empty department cannot see Cocina peers AND gets 403 on ?department=Cocina
    r_nodept = client.get(
        "/api/v1/leaves/team-calendar?from_date=2026-12-01&to_date=2026-12-31",
        headers=env["auth"](env["emp_sala_uid"]),
    )
    assert r_nodept.status_code == 200
    assert {m["employee_id"] for m in r_nodept.json()["members"]} == {str(emp_no_dept.id)}
    assert (
        client.get(
            "/api/v1/leaves/team-calendar?from_date=2026-12-01&to_date=2026-12-31&department=Cocina",
            headers=env["auth"](env["emp_sala_uid"]),
        ).status_code
        == 403
    )

    # Terminated employee -> 403 on team-calendar
    env["emp_repo"].employees[emp_cocina.id] = replace(
        emp_cocina, employment_status=EmploymentStatus.TERMINATED
    )
    assert (
        client.get(
            "/api/v1/leaves/team-calendar?from_date=2026-12-01&to_date=2026-12-31",
            headers=env["auth"](env["emp_cocina_uid"]),
        ).status_code
        == 403
    )

    # Admin dashboard uses store-local date: Canary employee has days_until=0, Madrid does not
    r_admin = client.get("/api/v1/dashboard/admin", headers=env["auth"](env["owner_id"]))
    assert r_admin.status_code == 200
    ann_ids = [a["employee_id"] for a in r_admin.json()["upcoming_anniversaries"]]
    assert ann_ids == [str(emp_canary.id)]
