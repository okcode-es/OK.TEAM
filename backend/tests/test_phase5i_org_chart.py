"""Phase 5I automated tests: Interactive Organization Chart & employees.manager_id."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.application.employees import (
    AnonymizeEmployeeUseCase,
    CreateEmployeeCommand,
    CreateEmployeeUseCase,
)
from app.domain.employees import (
    ContractType,
    Employee,
    EmploymentStatus,
    WorkScheduleType,
)
from app.domain.org_chart import build_org_chart
from app.domain.organization import (
    Membership,
    Store,
    Tenant,
    TenantMembershipSummary,
    ValidationDomainError,
)
from app.domain.security import (
    AuthenticatedPrincipal,
    MembershipStatus,
    UserRole,
)
from app.infrastructure.config import Settings
from app.infrastructure.pii_crypto import AesGcmPiiCipher
from app.infrastructure.supabase_jwt import SupabaseJWTVerifier, sign_dev_hs256_token
from app.interfaces.api.deps import (
    get_commit_fn,
    get_employee_repository,
    get_jwt_verifier,
    get_membership_repository,
    get_pii_cipher,
    get_store_repository,
    get_tenant_repository,
)
from app.main import create_app

_DEV_SECRET = "dev-jwt-secret-phase5i-0123456789abcdef"
_TEST_PII_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

_EXPECTED_NODE_KEYS = frozenset(
    {
        "id",
        "full_name",
        "avatar_url",
        "job_title",
        "department_id",
        "department_name",
        "store_id",
        "store_name",
        "work_email",
        "manager_id",
        "direct_reports_count",
        "total_reports_count",
    }
)

_FORBIDDEN_PII_KEYS = frozenset(
    {
        "nie_dni",
        "nie_dni_enc",
        "nie_dni_masked",
        "nie_dni_blind_index",
        "iban",
        "iban_enc",
        "iban_masked",
        "naf",
        "naf_enc",
        "naf_masked",
        "phone",
        "birth_date",
        "contract_type",
        "work_schedule_type",
        "contracted_weekly_hours",
        "annual_vacation_days",
        "emergency_contact_name",
        "emergency_contact_phone",
        "employment_status",
        "employee_code",
        "user_id",
        "retention_until",
        "anonymized_at",
    }
)


def _make_employee(
    *,
    emp_id: UUID | None = None,
    tenant_id: UUID,
    store_id: UUID,
    user_id: UUID | None = None,
    manager_id: UUID | None = None,
    code: str = "EMP-001",
    full_name: str = "Test Employee",
    department: str = "Sala",
    job_title: str = "Camarero",
    email: str = "emp@dragon.es",
    status: EmploymentStatus = EmploymentStatus.ACTIVE,
    deleted_at: datetime | None = None,
) -> Employee:
    now = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)
    return Employee(
        id=emp_id or uuid4(),
        tenant_id=tenant_id,
        primary_store_id=store_id,
        user_id=user_id,
        employee_code=code,
        full_name=full_name,
        preferred_name="",
        department=department,
        job_title=job_title,
        emergency_contact_name="Contacto Urgencia",
        emergency_contact_phone="+34600111222",
        email=email,
        phone="+34600999888",
        birth_date=date(1992, 5, 10),
        contract_type=ContractType.INDEFINIDO,
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
        annual_vacation_days=30,
        hire_date=date(2025, 1, 1),
        termination_date=None,
        nie_dni_enc="enc-nie",
        nie_dni_masked="*****567X",
        nie_dni_blind_index="blind-nie",
        iban_enc="enc-iban",
        iban_masked="ES91 **** **** 1332",
        naf_enc="enc-naf",
        naf_masked="28******7840",
        employment_status=status,
        retention_until=None,
        anonymized_at=None,
        created_at=now,
        updated_at=now,
        default_break_minutes=30,
        manager_id=manager_id,
        deleted_at=deleted_at,
    )


class _OrgRepos:
    def __init__(self) -> None:
        self.tenants: dict[UUID, Tenant] = {}
        self.stores: dict[UUID, Store] = {}
        self.memberships: dict[UUID, Membership] = {}

    async def get_by_id(self, tenant_id: UUID) -> Tenant | None:
        return self.tenants.get(tenant_id)


class _StoreRepo:
    def __init__(self, org: _OrgRepos) -> None:
        self._org = org

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        include_inactive: bool = False,
    ) -> list[Store]:
        out = [s for s in self._org.stores.values() if s.tenant_id == tenant_id]
        if allowed_store_ids is not None:
            out = [s for s in out if s.id in allowed_store_ids]
        if not include_inactive:
            out = [s for s in out if s.is_active]
        return out

    async def get_by_id(self, tenant_id: UUID, store_id: UUID) -> Store | None:
        store = self._org.stores.get(store_id)
        if store is None or store.tenant_id != tenant_id:
            return None
        return store


class _MembershipRepo:
    def __init__(self, org: _OrgRepos) -> None:
        self._org = org

    async def list_summaries_for_user(self, user_id: UUID) -> list[TenantMembershipSummary]:
        out: list[TenantMembershipSummary] = []
        for m in self._org.memberships.values():
            if m.user_id == user_id and m.status == MembershipStatus.ACTIVE:
                t = self._org.tenants[m.tenant_id]
                out.append(
                    TenantMembershipSummary(
                        tenant_id=t.id,
                        tenant_trade_name=t.trade_name,
                        role=m.role,
                        status=m.status,
                    )
                )
        return out

    async def get_active_for_user_in_tenant(
        self,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Membership | None:
        for m in self._org.memberships.values():
            if (
                m.tenant_id == tenant_id
                and m.user_id == user_id
                and m.status == MembershipStatus.ACTIVE
            ):
                return m
        return None


class _EmployeeRepo:
    def __init__(self) -> None:
        self.employees: dict[UUID, Employee] = {}

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        allowed_statuses: frozenset[EmploymentStatus] | None = None,
        only_user_id: UUID | None = None,
        user_ids: frozenset[UUID] | None = None,
        store_id: UUID | None = None,
        status: EmploymentStatus | None = None,
    ) -> list[Employee]:
        out = [e for e in self.employees.values() if e.tenant_id == tenant_id]
        if allowed_store_ids is not None:
            out = [e for e in out if e.primary_store_id in allowed_store_ids]
        if allowed_statuses is not None:
            out = [
                e
                for e in out
                if e.employment_status in allowed_statuses and e.deleted_at is None
            ]
        if only_user_id is not None:
            out = [e for e in out if e.user_id == only_user_id]
        if user_ids is not None:
            out = [e for e in out if e.user_id in user_ids]
        if store_id is not None:
            out = [e for e in out if e.primary_store_id == store_id]
        if status is not None:
            out = [e for e in out if e.employment_status == status]
        return out

    async def get_by_id(self, tenant_id: UUID, employee_id: UUID) -> Employee | None:
        emp = self.employees.get(employee_id)
        if emp is None or emp.tenant_id != tenant_id:
            return None
        return emp

    async def exists_active_code(
        self,
        tenant_id: UUID,
        employee_code: str,
        *,
        exclude_employee_id: UUID | None = None,
    ) -> bool:
        return any(
            e.tenant_id == tenant_id
            and e.deleted_at is None
            and e.employee_code.upper() == employee_code.upper()
            and e.id != exclude_employee_id
            for e in self.employees.values()
        )

    async def exists_active_nie_blind_index(
        self,
        tenant_id: UUID,
        nie_dni_blind_index: str,
        *,
        exclude_employee_id: UUID | None = None,
    ) -> bool:
        return False

    async def create(self, employee: Employee) -> Employee:
        self.employees[employee.id] = employee
        return employee

    async def update(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        primary_store_id: UUID,
        user_id: UUID | None,
        employee_code: str,
        full_name: str,
        preferred_name: str,
        department: str,
        email: str,
        phone: str,
        birth_date: date | None,
        contract_type: ContractType,
        work_schedule_type: WorkScheduleType,
        contracted_weekly_hours: Decimal,
        annual_vacation_days: int,
        hire_date: date,
        employment_status: EmploymentStatus,
        nie_dni_enc: str | None,
        nie_dni_masked: str | None,
        nie_dni_blind_index: str | None,
        iban_enc: str | None,
        iban_masked: str | None,
        naf_enc: str | None,
        naf_masked: str | None,
        updated_at: datetime,
        job_title: str = "",
        emergency_contact_name: str = "",
        emergency_contact_phone: str = "",
        default_break_minutes: int | None = None,
        manager_id: UUID | None = None,
        work_permit_expiry_date: date | None = None,
        contract_end_date: date | None = None,
    ) -> Employee | None:
        existing = await self.get_by_id(tenant_id, employee_id)
        if existing is None or existing.deleted_at is not None:
            return None
        updated = replace(
            existing,
            primary_store_id=primary_store_id,
            user_id=user_id,
            employee_code=employee_code,
            full_name=full_name,
            preferred_name=preferred_name,
            department=department,
            job_title=job_title,
            emergency_contact_name=emergency_contact_name,
            emergency_contact_phone=emergency_contact_phone,
            default_break_minutes=default_break_minutes,
            manager_id=manager_id,
            work_permit_expiry_date=work_permit_expiry_date,
            contract_end_date=contract_end_date,
            email=email,
            phone=phone,
            birth_date=birth_date,
            contract_type=contract_type,
            work_schedule_type=work_schedule_type,
            contracted_weekly_hours=contracted_weekly_hours,
            annual_vacation_days=annual_vacation_days,
            hire_date=hire_date,
            employment_status=employment_status,
            updated_at=updated_at,
        )
        self.employees[employee_id] = updated
        return updated

    async def update_avatar(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        avatar_storage_backend: str | None,
        avatar_storage_key: str | None,
        avatar_mime_type: str | None,
        avatar_updated_at: datetime | None,
        updated_at: datetime,
    ) -> Employee | None:
        existing = await self.get_by_id(tenant_id, employee_id)
        if existing is None or existing.deleted_at is not None:
            return None
        updated = replace(
            existing,
            avatar_storage_backend=avatar_storage_backend,
            avatar_storage_key=avatar_storage_key,
            avatar_mime_type=avatar_mime_type,
            avatar_updated_at=avatar_updated_at,
            updated_at=updated_at,
        )
        self.employees[employee_id] = updated
        return updated

    async def anonymize(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        anonymized_full_name: str,
        anonymized_at: datetime,
    ) -> Employee | None:
        existing = await self.get_by_id(tenant_id, employee_id)
        if existing is None:
            return None
        wiped = replace(
            existing,
            user_id=None,
            manager_id=None,
            full_name=anonymized_full_name,
            preferred_name="",
            job_title="",
            emergency_contact_name="",
            emergency_contact_phone="",
            email="",
            phone="",
            birth_date=None,
            nie_dni_enc=None,
            nie_dni_masked=None,
            nie_dni_blind_index=None,
            iban_enc=None,
            iban_masked=None,
            naf_enc=None,
            naf_masked=None,
            work_permit_expiry_date=None,
            contract_end_date=None,
            kiosk_pin_hash=None,
            avatar_storage_backend=None,
            avatar_storage_key=None,
            avatar_mime_type=None,
            avatar_updated_at=None,
            employment_status=EmploymentStatus.ANONYMIZED,
            anonymized_at=anonymized_at,
            deleted_at=anonymized_at,
            updated_at=anonymized_at,
        )
        self.employees[employee_id] = wiped
        for sub_id, sub in list(self.employees.items()):
            if sub.tenant_id == tenant_id and sub.manager_id == employee_id:
                self.employees[sub_id] = replace(
                    sub,
                    manager_id=None,
                    updated_at=anonymized_at,
                )
        return wiped


def _build_client(tmp_path) -> tuple[TestClient, _OrgRepos, _EmployeeRepo]:
    settings = Settings(
        app_env="development",
        supabase_jwt_secret=_DEV_SECRET,
        pii_encryption_key=_TEST_PII_KEY,
        local_storage_dir=str(tmp_path),
    )
    app = create_app(settings_override=settings)
    org = _OrgRepos()
    emp_repo = _EmployeeRepo()
    verifier = SupabaseJWTVerifier(
        supabase_url="https://example.supabase.co",
        jwt_secret=_DEV_SECRET,
    )
    cipher = AesGcmPiiCipher(_TEST_PII_KEY)

    async def _noop_commit() -> None:
        return None

    app.dependency_overrides[get_jwt_verifier] = lambda: verifier
    app.dependency_overrides[get_tenant_repository] = lambda: org
    app.dependency_overrides[get_store_repository] = lambda: _StoreRepo(org)
    app.dependency_overrides[get_membership_repository] = lambda: _MembershipRepo(org)
    app.dependency_overrides[get_employee_repository] = lambda: emp_repo
    app.dependency_overrides[get_pii_cipher] = lambda: cipher
    app.dependency_overrides[get_commit_fn] = lambda: _noop_commit
    return TestClient(app), org, emp_repo


def _auth_headers(user_id: UUID, email: str, tenant_id: UUID | None = None) -> dict[str, str]:
    token = sign_dev_hs256_token(user_id=user_id, email=email, secret=_DEV_SECRET)
    headers = {"Authorization": f"Bearer {token}"}
    if tenant_id is not None:
        headers["X-Tenant-Id"] = str(tenant_id)
    return headers


def _seed_tenant_and_store(
    org: _OrgRepos,
    *,
    trade_name: str,
    store_name: str,
) -> tuple[UUID, UUID]:
    now = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)
    tid = uuid4()
    sid = uuid4()
    org.tenants[tid] = Tenant(
        id=tid,
        legal_name=f"{trade_name} S.L.",
        trade_name=trade_name,
        cif_nif="B12345678",
        default_timezone="Europe/Madrid",
        default_locale="es",
        created_at=now,
        updated_at=now,
    )
    org.stores[sid] = Store(
        id=sid,
        tenant_id=tid,
        name=store_name,
        code="MAD-01",
        timezone="Europe/Madrid",
        address="Calle Mayor 1",
        city="Madrid",
        postal_code="28001",
        region_code="MD",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    return tid, sid


def _add_membership(
    org: _OrgRepos,
    *,
    tenant_id: UUID,
    user_id: UUID,
    email: str,
    role: UserRole,
    store_ids: frozenset[UUID] = frozenset(),
) -> None:
    now = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)
    mid = uuid4()
    org.memberships[mid] = Membership(
        id=mid,
        tenant_id=tenant_id,
        user_id=user_id,
        email=email,
        display_name=email,
        role=role,
        status=MembershipStatus.ACTIVE,
        store_ids=store_ids,
        created_at=now,
        updated_at=now,
    )


def test_domain_build_org_chart_cycle_detection_and_counts() -> None:
    tid = uuid4()
    sid = uuid4()
    # Deterministic UUIDs so lexicographical tie-break is predictable
    id_a = UUID("00000000-0000-0000-0000-000000000001")
    id_b = UUID("00000000-0000-0000-0000-000000000002")
    id_c = UUID("00000000-0000-0000-0000-000000000003")
    id_d = UUID("00000000-0000-0000-0000-000000000004")

    # 1. Two-node cycle A <-> B
    a = _make_employee(emp_id=id_a, tenant_id=tid, store_id=sid, manager_id=id_b, full_name="Alice")
    b = _make_employee(emp_id=id_b, tenant_id=tid, store_id=sid, manager_id=id_a, full_name="Bob")
    res_2 = build_org_chart([a, b], store_names={sid: "Madrid"}, avatar_urls={})
    assert res_2.cycle_broken_count == 1
    by_id_2 = {n.id: n for n in res_2.nodes}
    assert by_id_2[id_a].manager_id is None  # smallest UUID broken
    assert by_id_2[id_b].manager_id == id_a
    assert by_id_2[id_a].direct_reports_count == 1
    assert by_id_2[id_a].total_reports_count == 1
    assert by_id_2[id_b].direct_reports_count == 0

    # 2. Three-node cycle + tail: D -> C -> B -> A -> C (cycle is A,B,C; smallest is A)
    a3 = _make_employee(emp_id=id_a, tenant_id=tid, store_id=sid, manager_id=id_c, full_name="A")
    b3 = _make_employee(emp_id=id_b, tenant_id=tid, store_id=sid, manager_id=id_a, full_name="B")
    c3 = _make_employee(emp_id=id_c, tenant_id=tid, store_id=sid, manager_id=id_b, full_name="C")
    d3 = _make_employee(emp_id=id_d, tenant_id=tid, store_id=sid, manager_id=id_c, full_name="D")
    res_3 = build_org_chart([d3, c3, b3, a3], store_names={sid: "Madrid"}, avatar_urls={})
    assert res_3.cycle_broken_count == 1
    by_id_3 = {n.id: n for n in res_3.nodes}
    assert by_id_3[id_a].manager_id is None
    assert by_id_3[id_a].direct_reports_count == 1  # B
    assert by_id_3[id_a].total_reports_count == 3  # B -> C -> D
    assert by_id_3[id_b].total_reports_count == 2  # C -> D
    assert by_id_3[id_c].total_reports_count == 1  # D
    assert by_id_3[id_d].total_reports_count == 0

    # 3. Self-loop A -> A is sanitized without hanging
    self_emp = _make_employee(
        emp_id=id_a, tenant_id=tid, store_id=sid, manager_id=id_a, full_name="Self"
    )
    res_self = build_org_chart([self_emp], store_names={sid: "Madrid"}, avatar_urls={})
    assert res_self.nodes[0].manager_id is None
    assert res_self.unassigned_ids == (id_a,)
    assert res_self.root_ids == ()


def test_org_chart_api_rbac_gdpr_isolation_and_partitioning(tmp_path) -> None:
    client, org, emp_repo = _build_client(tmp_path)
    tid_a, sid_a = _seed_tenant_and_store(org, trade_name="Dragon", store_name="Madrid Centro")
    tid_b, sid_b = _seed_tenant_and_store(org, trade_name="Bazar B", store_name="Barcelona")

    owner_uid, hr_uid, mgr_uid, acc_uid, emp_uid = (uuid4() for _ in range(5))
    other_tenant_uid = uuid4()

    _add_membership(
        org, tenant_id=tid_a, user_id=owner_uid, email="owner@dragon.es", role=UserRole.OWNER
    )
    _add_membership(org, tenant_id=tid_a, user_id=hr_uid, email="hr@dragon.es", role=UserRole.HR)
    _add_membership(
        org,
        tenant_id=tid_a,
        user_id=mgr_uid,
        email="mgr@dragon.es",
        role=UserRole.STORE_MANAGER,
        store_ids=frozenset({sid_a}),
    )
    _add_membership(
        org, tenant_id=tid_a, user_id=acc_uid, email="acc@dragon.es", role=UserRole.ACCOUNTANT
    )
    _add_membership(
        org, tenant_id=tid_a, user_id=emp_uid, email="emp@dragon.es", role=UserRole.EMPLOYEE
    )
    _add_membership(
        org, tenant_id=tid_b, user_id=other_tenant_uid, email="b@bazar.es", role=UserRole.OWNER
    )

    # Active hierarchy in Tenant A:
    # Root 1: Elena (HR) -> Carlos (Manager) -> Ming (Employee)
    # Root 2 (terminated!) -> Orphaned report becomes unassigned
    # Standalone: Andres (unassigned)
    # Excluded: Terminated, Legal Hold, Anonymized, Soft-deleted
    elena_id, carlos_id, ming_id, andres_id = uuid4(), uuid4(), uuid4(), uuid4()
    terminated_mgr_id, orphaned_id = uuid4(), uuid4()
    hold_id, anon_id, deleted_id, tenant_b_id = uuid4(), uuid4(), uuid4(), uuid4()

    for emp in [
        _make_employee(
            emp_id=elena_id,
            tenant_id=tid_a,
            store_id=sid_a,
            user_id=hr_uid,
            manager_id=None,
            code="EMP-001",
            full_name="Elena Chen",
            department="  RR. HH. ",
            job_title="Directora RR. HH.",
            email="hr@dragon.es",
        ),
        _make_employee(
            emp_id=carlos_id,
            tenant_id=tid_a,
            store_id=sid_a,
            user_id=mgr_uid,
            manager_id=elena_id,
            code="EMP-002",
            full_name="Carlos Manager",
            department="Operaciones",
            job_title="Encargado",
            email="mgr@dragon.es",
        ),
        _make_employee(
            emp_id=ming_id,
            tenant_id=tid_a,
            store_id=sid_a,
            user_id=emp_uid,
            manager_id=carlos_id,
            code="EMP-003",
            full_name="Ming Zhou",
            department="Sala",
            job_title="Camarero",
            email="emp@dragon.es",
            status=EmploymentStatus.ON_LEAVE,
        ),
        _make_employee(
            emp_id=andres_id,
            tenant_id=tid_a,
            store_id=sid_a,
            manager_id=None,
            code="EMP-004",
            full_name="Andrés Navarro",
            department="",
            job_title="Asesor",
            email="andres@dragon.es",
        ),
        _make_employee(
            emp_id=terminated_mgr_id,
            tenant_id=tid_a,
            store_id=sid_a,
            code="EMP-005",
            full_name="Former Boss",
            status=EmploymentStatus.TERMINATED,
        ),
        _make_employee(
            emp_id=orphaned_id,
            tenant_id=tid_a,
            store_id=sid_a,
            manager_id=terminated_mgr_id,
            code="EMP-006",
            full_name="Orphaned Active",
        ),
        _make_employee(
            emp_id=hold_id,
            tenant_id=tid_a,
            store_id=sid_a,
            code="EMP-007",
            full_name="Legal Hold Emp",
            status=EmploymentStatus.LEGAL_HOLD,
        ),
        _make_employee(
            emp_id=anon_id,
            tenant_id=tid_a,
            store_id=sid_a,
            code="EMP-008",
            full_name="Anonymized Emp",
            status=EmploymentStatus.ANONYMIZED,
        ),
        _make_employee(
            emp_id=deleted_id,
            tenant_id=tid_a,
            store_id=sid_a,
            code="EMP-009",
            full_name="Soft Deleted Emp",
            deleted_at=datetime(2026, 11, 20, tzinfo=UTC),
        ),
        _make_employee(
            emp_id=tenant_b_id,
            tenant_id=tid_b,
            store_id=sid_b,
            user_id=other_tenant_uid,
            code="EMP-B1",
            full_name="Secret Tenant B",
            email="secret@bazar.es",
        ),
    ]:
        emp_repo.employees[emp.id] = emp

    # 1. All 5 roles can read the org chart
    for uid, email, expected_current in [
        (owner_uid, "owner@dragon.es", None),
        (hr_uid, "hr@dragon.es", str(elena_id)),
        (mgr_uid, "mgr@dragon.es", str(carlos_id)),
        (acc_uid, "acc@dragon.es", None),
        (emp_uid, "emp@dragon.es", str(ming_id)),
    ]:
        res = client.get("/api/v1/organization/org-chart", headers=_auth_headers(uid, email, tid_a))
        assert res.status_code == 200
        assert res.headers.get("cache-control") == "no-store"
        assert res.headers.get("x-content-type-options") == "nosniff"
        body = res.json()
        assert body["current_employee_id"] == expected_current
        assert body["total_active_employees"] == 5
        assert body["root_ids"] == [str(elena_id)]
        assert set(body["unassigned_ids"]) == {str(andres_id), str(orphaned_id)}

        # Exact 12-field GDPR whitelist & negative assertions
        node_ids = set()
        for node in body["nodes"]:
            assert set(node.keys()) == _EXPECTED_NODE_KEYS
            assert _FORBIDDEN_PII_KEYS.isdisjoint(node.keys())
            node_ids.add(node["id"])
        assert str(tenant_b_id) not in node_ids
        assert str(terminated_mgr_id) not in node_ids
        assert str(hold_id) not in node_ids
        assert str(anon_id) not in node_ids
        assert str(deleted_id) not in node_ids

        by_id = {n["id"]: n for n in body["nodes"]}
        assert by_id[str(elena_id)]["department_name"] == "RR. HH."
        assert by_id[str(elena_id)]["department_id"] == "rr. hh."
        assert by_id[str(elena_id)]["direct_reports_count"] == 1
        assert by_id[str(elena_id)]["total_reports_count"] == 2
        assert by_id[str(andres_id)]["department_id"] is None
        assert by_id[str(orphaned_id)]["manager_id"] is None

    # 2. Cross-tenant isolation: Tenant B sees only Tenant B, and forged X-Tenant-Id = tid_a -> 403
    res_b = client.get(
        "/api/v1/organization/org-chart",
        headers=_auth_headers(other_tenant_uid, "b@bazar.es", tid_b),
    )
    assert res_b.status_code == 200
    assert [n["id"] for n in res_b.json()["nodes"]] == [str(tenant_b_id)]
    assert (
        client.get(
            "/api/v1/organization/org-chart",
            headers=_auth_headers(other_tenant_uid, "b@bazar.es", tid_a),
        ).status_code
        == 403
    )


@pytest.mark.asyncio
async def test_create_and_update_employee_manager_id_validation(tmp_path) -> None:
    client, org, emp_repo = _build_client(tmp_path)
    tid_a, sid_a = _seed_tenant_and_store(org, trade_name="Dragon", store_name="Madrid")
    tid_b, sid_b = _seed_tenant_and_store(org, trade_name="Bazar B", store_name="Barcelona")

    hr_uid = uuid4()
    _add_membership(org, tenant_id=tid_a, user_id=hr_uid, email="hr@dragon.es", role=UserRole.HR)
    headers = _auth_headers(hr_uid, "hr@dragon.es", tid_a)

    # Seed terminated employee in A and active employee in B
    terminated_id = uuid4()
    emp_repo.employees[terminated_id] = _make_employee(
        emp_id=terminated_id,
        tenant_id=tid_a,
        store_id=sid_a,
        code="EMP-TERM",
        status=EmploymentStatus.TERMINATED,
    )
    foreign_id = uuid4()
    emp_repo.employees[foreign_id] = _make_employee(
        emp_id=foreign_id,
        tenant_id=tid_b,
        store_id=sid_b,
        code="EMP-B1",
    )

    # Create A (root) and B (reports to A)
    res_a = client.post(
        "/api/v1/employees",
        headers=headers,
        json={
            "primary_store_id": str(sid_a),
            "employee_code": "EMP-A",
            "full_name": "Alice",
            "hire_date": "2026-01-01",
        },
    )
    assert res_a.status_code == 201
    emp_a_id = res_a.json()["id"]
    assert res_a.json()["manager_id"] is None

    res_b = client.post(
        "/api/v1/employees",
        headers=headers,
        json={
            "primary_store_id": str(sid_a),
            "employee_code": "EMP-B",
            "full_name": "Bob",
            "manager_id": emp_a_id,
            "hire_date": "2026-01-01",
        },
    )
    assert res_b.status_code == 201
    emp_b_id = res_b.json()["id"]
    assert res_b.json()["manager_id"] == emp_a_id

    # Reject create with cross-tenant or terminated manager -> 422
    for bad_mgr in (str(foreign_id), str(terminated_id), str(uuid4())):
        bad_create = client.post(
            "/api/v1/employees",
            headers=headers,
            json={
                "primary_store_id": str(sid_a),
                "employee_code": "EMP-BAD",
                "full_name": "Bad",
                "manager_id": bad_mgr,
                "hire_date": "2026-01-01",
            },
        )
        assert bad_create.status_code == 422

    # Reject self-manager on update -> 422
    patch_base = {
        "primary_store_id": str(sid_a),
        "employee_code": "EMP-A",
        "full_name": "Alice",
        "contract_type": "indefinido",
        "work_schedule_type": "full_time",
        "contracted_weekly_hours": "40.00",
        "annual_vacation_days": 30,
        "hire_date": "2026-01-01",
        "employment_status": "active",
    }
    assert (
        client.patch(
            f"/api/v1/employees/{emp_a_id}",
            headers=headers,
            json={**patch_base, "manager_id": emp_a_id},
        ).status_code
        == 422
    )

    # Reject cycle A -> B -> A on update -> 422
    assert (
        client.patch(
            f"/api/v1/employees/{emp_a_id}",
            headers=headers,
            json={**patch_base, "manager_id": emp_b_id},
        ).status_code
        == 422
    )

    # Direct use-case validation also raises ValidationDomainError for terminated manager
    use_case = CreateEmployeeUseCase(emp_repo, _StoreRepo(org), AesGcmPiiCipher(_TEST_PII_KEY))
    principal = AuthenticatedPrincipal(
        user_id=hr_uid,
        tenant_id=tid_a,
        role=UserRole.HR,
        store_ids=frozenset(),
    )
    with pytest.raises(ValidationDomainError):
        await use_case.execute(
            principal,
            CreateEmployeeCommand(
                primary_store_id=sid_a,
                employee_code="EMP-X",
                full_name="Invalid",
                manager_id=terminated_id,
            ),
        )


def test_patch_omitted_manager_id_preserves_and_explicit_null_clears_and_3cycle(tmp_path) -> None:
    client, org, _ = _build_client(tmp_path)
    tid, sid = _seed_tenant_and_store(org, trade_name="Dragon", store_name="Madrid")
    hr_uid, emp_uid = uuid4(), uuid4()
    _add_membership(org, tenant_id=tid, user_id=hr_uid, email="hr@dragon.es", role=UserRole.HR)
    _add_membership(
        org, tenant_id=tid, user_id=emp_uid, email="emp@dragon.es", role=UserRole.EMPLOYEE
    )
    hr_h = _auth_headers(hr_uid, "hr@dragon.es", tid)
    emp_h = _auth_headers(emp_uid, "emp@dragon.es", tid)

    # Create A -> B -> C
    a_id = client.post(
        "/api/v1/employees",
        headers=hr_h,
        json={
            "primary_store_id": str(sid),
            "employee_code": "EMP-A",
            "full_name": "Alice",
            "hire_date": "2026-01-01",
        },
    ).json()["id"]
    b_id = client.post(
        "/api/v1/employees",
        headers=hr_h,
        json={
            "primary_store_id": str(sid),
            "employee_code": "EMP-B",
            "full_name": "Bob",
            "manager_id": a_id,
            "hire_date": "2026-01-01",
        },
    ).json()["id"]
    c_id = client.post(
        "/api/v1/employees",
        headers=hr_h,
        json={
            "primary_store_id": str(sid),
            "employee_code": "EMP-C",
            "full_name": "Carol",
            "manager_id": b_id,
            "hire_date": "2026-01-01",
        },
    ).json()["id"]

    # 3-cycle rejection: setting A.manager_id = C creates A -> C -> B -> A -> 422
    patch_a_base = {
        "primary_store_id": str(sid),
        "employee_code": "EMP-A",
        "full_name": "Alice",
        "contract_type": "indefinido",
        "work_schedule_type": "full_time",
        "contracted_weekly_hours": "40.00",
        "annual_vacation_days": 30,
        "hire_date": "2026-01-01",
        "employment_status": "active",
    }
    assert (
        client.patch(
            f"/api/v1/employees/{a_id}",
            headers=hr_h,
            json={**patch_a_base, "manager_id": c_id},
        ).status_code
        == 422
    )

    # Non-admin (employee) gets 403 on PATCH /employees/{id} with manager_id
    assert (
        client.patch(
            f"/api/v1/employees/{b_id}",
            headers=emp_h,
            json={**patch_a_base, "employee_code": "EMP-B", "full_name": "Bob", "manager_id": a_id},
        ).status_code
        == 403
    )

    # Omitting manager_id on PATCH preserves existing manager_id (a_id)
    patch_b_omit = client.patch(
        f"/api/v1/employees/{b_id}",
        headers=hr_h,
        json={
            "primary_store_id": str(sid),
            "employee_code": "EMP-B",
            "full_name": "Bob Updated",
            "contract_type": "indefinido",
            "work_schedule_type": "full_time",
            "contracted_weekly_hours": "40.00",
            "annual_vacation_days": 30,
            "hire_date": "2026-01-01",
            "employment_status": "active",
        },
    )
    assert patch_b_omit.status_code == 200
    assert patch_b_omit.json()["manager_id"] == a_id

    # Explicit "manager_id": null clears it to None
    patch_b_clear = client.patch(
        f"/api/v1/employees/{b_id}",
        headers=hr_h,
        json={
            "primary_store_id": str(sid),
            "employee_code": "EMP-B",
            "full_name": "Bob Cleared",
            "manager_id": None,
            "contract_type": "indefinido",
            "work_schedule_type": "full_time",
            "contracted_weekly_hours": "40.00",
            "annual_vacation_days": 30,
            "hire_date": "2026-01-01",
            "employment_status": "active",
        },
    )
    assert patch_b_clear.status_code == 200
    assert patch_b_clear.json()["manager_id"] is None


def test_domain_deep_chain_and_early_sorting_lasso() -> None:
    tid, sid = uuid4(), uuid4()

    # 1. Deep 1,200-node chain (exceeds default Python recursion limit 1000)
    chain: list[Employee] = []
    prev_id: UUID | None = None
    for idx in range(1200):
        emp_id = uuid4()
        chain.append(
            _make_employee(
                emp_id=emp_id,
                tenant_id=tid,
                store_id=sid,
                manager_id=prev_id,
                code=f"EMP-{idx:04d}",
                full_name=f"Node {idx:04d}",
            )
        )
        prev_id = emp_id

    deep_res = build_org_chart(chain, store_names={sid: "Madrid"}, avatar_urls={})
    assert deep_res.cycle_broken_count == 0
    assert deep_res.total_active_employees == 1200
    by_id = {n.id: n for n in deep_res.nodes}
    assert by_id[chain[0].id].total_reports_count == 1199
    assert by_id[chain[-1].id].total_reports_count == 0

    # 2. Lasso where tail sorts BEFORE cycle nodes: AAA Tail -> MMM <-> ZZZ
    id_tail = UUID("00000000-0000-0000-0000-000000000001")
    id_mmm = UUID("00000000-0000-0000-0000-000000000002")
    id_zzz = UUID("00000000-0000-0000-0000-000000000003")
    lasso_res = build_org_chart(
        [
            _make_employee(
                emp_id=id_tail, tenant_id=tid, store_id=sid, manager_id=id_mmm, full_name="AAA Tail"
            ),
            _make_employee(
                emp_id=id_mmm, tenant_id=tid, store_id=sid, manager_id=id_zzz, full_name="MMM Cycle"
            ),
            _make_employee(
                emp_id=id_zzz, tenant_id=tid, store_id=sid, manager_id=id_mmm, full_name="ZZZ Cycle"
            ),
        ],
        store_names={sid: "Madrid"},
        avatar_urls={},
    )
    assert lasso_res.cycle_broken_count == 1
    lasso_by_id = {n.id: n for n in lasso_res.nodes}
    assert lasso_by_id[id_mmm].manager_id is None
    assert lasso_by_id[id_zzz].manager_id == id_mmm
    assert lasso_by_id[id_tail].manager_id == id_mmm
    assert lasso_by_id[id_mmm].direct_reports_count == 2
    assert lasso_by_id[id_mmm].total_reports_count == 2


def test_org_chart_auth_errors_and_employee_avatar_fetch(tmp_path) -> None:
    client, org, _ = _build_client(tmp_path)
    tid, sid = _seed_tenant_and_store(org, trade_name="Dragon", store_name="Madrid")
    hr_uid, emp_uid = uuid4(), uuid4()
    _add_membership(org, tenant_id=tid, user_id=hr_uid, email="hr@dragon.es", role=UserRole.HR)
    _add_membership(
        org, tenant_id=tid, user_id=emp_uid, email="emp@dragon.es", role=UserRole.EMPLOYEE
    )
    hr_h = _auth_headers(hr_uid, "hr@dragon.es", tid)
    emp_h = _auth_headers(emp_uid, "emp@dragon.es", tid)

    # Unauthenticated -> 401; missing X-Tenant-Id -> 400
    assert client.get("/api/v1/organization/org-chart").status_code == 401
    assert (
        client.get(
            "/api/v1/organization/org-chart",
            headers=_auth_headers(emp_uid, "emp@dragon.es", None),
        ).status_code
        == 400
    )

    # Create colleague & upload PNG avatar
    colleague_id = client.post(
        "/api/v1/employees",
        headers=hr_h,
        json={
            "primary_store_id": str(sid),
            "employee_code": "EMP-AV",
            "full_name": "Avatar Colleague",
            "hire_date": "2026-01-01",
        },
    ).json()["id"]
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    up = client.post(
        f"/api/v1/employees/{colleague_id}/avatar",
        headers=hr_h,
        files={"file": ("avatar.png", png_bytes, "image/png")},
    )
    assert up.status_code == 200

    # Employee role reads org chart, gets avatar_url, and fetches it with security headers
    chart = client.get("/api/v1/organization/org-chart", headers=emp_h)
    assert chart.status_code == 200
    avatar_url = chart.json()["nodes"][0]["avatar_url"]
    assert avatar_url is not None
    assert "/api/v1/employees/avatars/local/" in avatar_url

    img_res = client.get(avatar_url, headers=emp_h)
    assert img_res.status_code == 200
    assert img_res.headers.get("x-content-type-options") == "nosniff"
    assert "no-store" in img_res.headers.get("cache-control", "")
    assert "default-src 'none'" in img_res.headers.get("content-security-policy", "")


@pytest.mark.asyncio
async def test_anonymize_clears_manager_id_on_self_and_subordinates(tmp_path) -> None:
    _, org, emp_repo = _build_client(tmp_path)
    tid, sid = _seed_tenant_and_store(org, trade_name="Dragon", store_name="Madrid")
    hr_uid = uuid4()
    _add_membership(org, tenant_id=tid, user_id=hr_uid, email="hr@dragon.es", role=UserRole.HR)

    top_id, target_id, sub_id = uuid4(), uuid4(), uuid4()
    emp_repo.employees[top_id] = _make_employee(
        emp_id=top_id, tenant_id=tid, store_id=sid, code="EMP-1", full_name="Top"
    )
    # Target is on expired legal hold so AnonymizeEmployeeUseCase permits anonymization
    target = replace(
        _make_employee(
            emp_id=target_id,
            tenant_id=tid,
            store_id=sid,
            manager_id=top_id,
            code="EMP-2",
            full_name="Target",
            status=EmploymentStatus.LEGAL_HOLD,
        ),
        retention_until=datetime(2020, 1, 1, tzinfo=UTC),
    )
    emp_repo.employees[target_id] = target
    emp_repo.employees[sub_id] = _make_employee(
        emp_id=sub_id,
        tenant_id=tid,
        store_id=sid,
        manager_id=target_id,
        code="EMP-3",
        full_name="Subordinate",
    )

    principal = AuthenticatedPrincipal(
        user_id=hr_uid,
        tenant_id=tid,
        role=UserRole.HR,
        store_ids=frozenset(),
    )
    anonymized = await AnonymizeEmployeeUseCase(emp_repo).execute(principal, target_id)
    assert anonymized.manager_id is None
    assert emp_repo.employees[sub_id].manager_id is None
