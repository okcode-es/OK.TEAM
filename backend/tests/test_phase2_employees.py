"""Phase 2 tests: Employee domain rules, AES-256-GCM PII crypto, RBAC & 4y legal hold."""

from __future__ import annotations

import base64
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.application.employees import AnonymizeEmployeeUseCase
from app.domain.employees import (
    ContractType,
    Employee,
    EmploymentStatus,
    RetentionLockError,
    WorkScheduleType,
    compute_retention_until,
    mask_iban,
    mask_naf,
    mask_nie_dni,
    normalize_iban,
    normalize_naf,
    normalize_nie_dni,
    validate_annual_vacation_days,
    validate_contracted_weekly_hours,
)
from app.domain.organization import ValidationDomainError
from app.domain.security import AuthenticatedPrincipal, UserRole
from app.infrastructure.config import Settings
from app.infrastructure.pii_crypto import AesGcmPiiCipher, PiiEncryptionError
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
from tests.test_api_org_rbac import (
    DEV_SECRET,
    InMemoryMembershipRepo,
    InMemoryOrgRepos,
    InMemoryStoreRepo,
)

TEST_PII_KEY = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode("ascii")


class InMemoryEmployeeRepo:
    def __init__(self) -> None:
        self.employees: dict[UUID, Employee] = {}

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        allowed_statuses: frozenset[EmploymentStatus] | None = None,
        only_user_id: UUID | None = None,
        store_id: UUID | None = None,
        status: EmploymentStatus | None = None,
    ) -> list[Employee]:
        if allowed_store_ids is not None and not allowed_store_ids:
            return []
        if allowed_statuses is not None and not allowed_statuses:
            return []
        result = [
            e
            for e in self.employees.values()
            if e.tenant_id == tenant_id
            and (allowed_store_ids is None or e.primary_store_id in allowed_store_ids)
            and (
                allowed_statuses is None
                or (e.employment_status in allowed_statuses and e.deleted_at is None)
            )
            and (only_user_id is None or e.user_id == only_user_id)
            and (store_id is None or e.primary_store_id == store_id)
            and (status is None or e.employment_status == status)
        ]
        return sorted(result, key=lambda e: (e.employee_code, e.full_name))

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
        return any(
            e.tenant_id == tenant_id
            and e.deleted_at is None
            and e.nie_dni_blind_index == nie_dni_blind_index
            and e.id != exclude_employee_id
            for e in self.employees.values()
        )

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
            email=email,
            phone=phone,
            birth_date=birth_date,
            contract_type=contract_type,
            work_schedule_type=work_schedule_type,
            contracted_weekly_hours=contracted_weekly_hours,
            annual_vacation_days=annual_vacation_days,
            hire_date=hire_date,
            employment_status=employment_status,
            nie_dni_enc=nie_dni_enc,
            nie_dni_masked=nie_dni_masked,
            nie_dni_blind_index=nie_dni_blind_index,
            iban_enc=iban_enc,
            iban_masked=iban_masked,
            naf_enc=naf_enc,
            naf_masked=naf_masked,
            updated_at=updated_at,
        )
        self.employees[employee_id] = updated
        return updated

    async def terminate_and_hold(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        termination_date: date,
        retention_until: datetime,
        updated_at: datetime,
    ) -> Employee | None:
        existing = await self.get_by_id(tenant_id, employee_id)
        if existing is None or existing.deleted_at is not None:
            return None
        frozen = replace(
            existing,
            termination_date=termination_date,
            employment_status=EmploymentStatus.LEGAL_HOLD,
            retention_until=retention_until,
            updated_at=updated_at,
        )
        self.employees[employee_id] = frozen
        return frozen

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
            full_name=anonymized_full_name,
            preferred_name="",
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
            employment_status=EmploymentStatus.ANONYMIZED,
            anonymized_at=anonymized_at,
            deleted_at=anonymized_at,
            updated_at=anonymized_at,
        )
        self.employees[employee_id] = wiped
        return wiped


def _build_client() -> tuple[TestClient, InMemoryOrgRepos, InMemoryEmployeeRepo, AesGcmPiiCipher]:
    settings = Settings(
        app_env="development",
        dev_auth_enabled=True,
        supabase_jwt_secret=DEV_SECRET,
        pii_encryption_key=TEST_PII_KEY,
    )
    app = create_app(settings_override=settings)
    org_state = InMemoryOrgRepos()
    store_repo = InMemoryStoreRepo(org_state)
    membership_repo = InMemoryMembershipRepo(org_state)
    employee_repo = InMemoryEmployeeRepo()
    cipher = AesGcmPiiCipher(TEST_PII_KEY)
    verifier = SupabaseJWTVerifier(
        supabase_url="https://example.supabase.co",
        jwt_secret=DEV_SECRET,
    )

    async def _noop_commit() -> None:
        return None

    app.dependency_overrides[get_jwt_verifier] = lambda: verifier
    app.dependency_overrides[get_tenant_repository] = lambda: org_state
    app.dependency_overrides[get_store_repository] = lambda: store_repo
    app.dependency_overrides[get_membership_repository] = lambda: membership_repo
    app.dependency_overrides[get_employee_repository] = lambda: employee_repo
    app.dependency_overrides[get_pii_cipher] = lambda: cipher
    app.dependency_overrides[get_commit_fn] = lambda: _noop_commit
    return TestClient(app), org_state, employee_repo, cipher


def _headers(user_id: UUID, email: str, tenant_id: str | None = None) -> dict[str, str]:
    token = sign_dev_hs256_token(user_id=user_id, email=email, secret=DEV_SECRET)
    out = {"Authorization": f"Bearer {token}"}
    if tenant_id is not None:
        out["X-Tenant-Id"] = tenant_id
    return out


# --- Domain & Crypto Unit Tests ---


def test_spanish_contract_and_pii_domain_validations() -> None:
    assert normalize_nie_dni(" y-1234 567x ") == "Y1234567X"
    assert normalize_nie_dni("12345678z") == "12345678Z"
    assert mask_nie_dni("Y1234567X") == "*****567X"

    assert normalize_iban("es91 2100 0418 4502 0005 1332") == "ES9121000418450200051332"
    assert mask_iban("ES9121000418450200051332") == "ES91 **** **** 1332"

    assert normalize_naf("28/12345678/40") == "281234567840"
    assert mask_naf("281234567840") == "28******7840"

    assert (
        validate_contracted_weekly_hours(Decimal("40"), WorkScheduleType.FULL_TIME)
        == Decimal("40.00")
    )
    assert (
        validate_contracted_weekly_hours(Decimal("20"), WorkScheduleType.PART_TIME)
        == Decimal("20.00")
    )
    with pytest.raises(ValidationDomainError):
        validate_contracted_weekly_hours(Decimal("41"), WorkScheduleType.FULL_TIME)
    with pytest.raises(ValidationDomainError):
        validate_contracted_weekly_hours(Decimal("40"), WorkScheduleType.PART_TIME)

    assert validate_annual_vacation_days(30) == 30
    with pytest.raises(ValidationDomainError):
        validate_annual_vacation_days(29)

    # Leap-day retention date calculation
    ret = compute_retention_until(date(2024, 2, 29), datetime(2024, 2, 29, 10, 0, tzinfo=UTC))
    assert ret.year == 2028 and ret.month == 2 and ret.day == 29
    ret_non_leap = compute_retention_until(
        date(2026, 6, 15),
        datetime(2026, 6, 15, 10, 0, tzinfo=UTC),
    )
    assert ret_non_leap.year == 2030


def test_aes_gcm_cipher_round_trip_aad_binding_and_key_validation() -> None:
    with pytest.raises(PiiEncryptionError):
        AesGcmPiiCipher("CHANGE_ME_32_BYTE_BASE64_KEY")
    with pytest.raises(PiiEncryptionError):
        AesGcmPiiCipher(base64.urlsafe_b64encode(b"short-key").decode("ascii"))

    cipher = AesGcmPiiCipher(TEST_PII_KEY)
    t1, t2 = uuid4(), uuid4()
    e1, e2 = uuid4(), uuid4()

    ct1 = cipher.encrypt("Y1234567X", tenant_id=t1, employee_id=e1, field_name="nie_dni")
    ct2 = cipher.encrypt("Y1234567X", tenant_id=t1, employee_id=e1, field_name="nie_dni")
    assert ct1 != ct2  # Random 96-bit nonce
    assert "Y1234567X" not in ct1
    assert cipher.decrypt(ct1, tenant_id=t1, employee_id=e1, field_name="nie_dni") == "Y1234567X"

    # Cross-tenant / cross-employee / cross-field AAD tampering MUST fail
    with pytest.raises(PiiEncryptionError):
        cipher.decrypt(ct1, tenant_id=t2, employee_id=e1, field_name="nie_dni")
    with pytest.raises(PiiEncryptionError):
        cipher.decrypt(ct1, tenant_id=t1, employee_id=e2, field_name="nie_dni")
    with pytest.raises(PiiEncryptionError):
        cipher.decrypt(ct1, tenant_id=t1, employee_id=e1, field_name="iban")

    # Tenant-isolated blind index
    b1 = cipher.blind_index("Y1234567X", tenant_id=t1)
    b2 = cipher.blind_index("Y1234567X", tenant_id=t2)
    assert b1 != b2
    assert b1 == cipher.blind_index("y1234567x", tenant_id=t1)


# --- End-to-End API, RBAC, PII Decryption & 4-Year Legal Hold Tests ---


@pytest.mark.asyncio
async def test_employee_lifecycle_pii_rbac_and_four_year_retention() -> None:
    client, _org, emp_repo, _cipher = _build_client()

    owner_id = uuid4()
    hr_id = uuid4()
    mgr_mad_id = uuid4()
    acc_id = uuid4()
    emp_mad_user_id = uuid4()
    emp_tfn_user_id = uuid4()

    # Onboard Tenant A + 2 stores
    tenant_id = client.post(
        "/api/v1/tenants",
        headers=_headers(owner_id, "owner@dragon.es"),
        json={
            "legal_name": "Restaurante Dragón S.L.",
            "trade_name": "金龙餐饮",
            "cif_nif": "B55556666",
        },
    ).json()["id"]

    store_mad_id = client.post(
        "/api/v1/stores",
        headers=_headers(owner_id, "owner@dragon.es", tenant_id),
        json={"name": "Madrid Sol", "code": "MAD-01", "timezone": "Europe/Madrid"},
    ).json()["id"]
    store_tfn_id = client.post(
        "/api/v1/stores",
        headers=_headers(owner_id, "owner@dragon.es", tenant_id),
        json={"name": "Tenerife", "code": "TFN-01", "timezone": "Atlantic/Canary"},
    ).json()["id"]

    # Add HR, Store Manager (MAD only), Accountant, Employee (MAD), Employee (TFN)
    for uid, email, name, role, stores in [
        (hr_id, "hr@dragon.es", "HR", "hr", []),
        (mgr_mad_id, "mgr@dragon.es", "Manager MAD", "store_manager", [store_mad_id]),
        (acc_id, "acc@asesoria.es", "Gestoría", "accountant", []),
        (emp_mad_user_id, "lin@dragon.es", "林小红", "employee", [store_mad_id]),
        (emp_tfn_user_id, "zhou@dragon.es", "周明", "employee", [store_tfn_id]),
    ]:
        r = client.post(
            "/api/v1/memberships",
            headers=_headers(owner_id, "owner@dragon.es", tenant_id),
            json={
                "user_id": str(uid),
                "email": email,
                "display_name": name,
                "role": role,
                "store_ids": stores,
            },
        )
        assert r.status_code == 201

    # 1. HR creates full-time employee in MAD with encrypted NIE/IBAN/NAF
    nie_plain = "Y1234567X"
    iban_plain = "ES9121000418450200051332"
    naf_plain = "281234567840"

    e1_res = client.post(
        "/api/v1/employees",
        headers=_headers(hr_id, "hr@dragon.es", tenant_id),
        json={
            "primary_store_id": store_mad_id,
            "user_id": str(emp_mad_user_id),
            "employee_code": "EMP-001",
            "full_name": "Xiaohong Lin",
            "preferred_name": "林小红",
            "email": "lin@dragon.es",
            "phone": "+34 600111222",
            "contract_type": "indefinido",
            "work_schedule_type": "full_time",
            "contracted_weekly_hours": "40.00",
            "annual_vacation_days": 30,
            "hire_date": "2025-01-15",
            "nie_dni": nie_plain,
            "iban": iban_plain,
            "naf": naf_plain,
        },
    )
    assert e1_res.status_code == 201
    e1 = e1_res.json()
    e1_id = e1["id"]

    # List & create response NEVER expose plaintext PII
    assert e1["nie_dni_masked"] == "*****567X"
    assert e1["iban_masked"] == "ES91 **** **** 1332"
    assert e1["naf_masked"] == "28******7840"
    assert nie_plain not in e1_res.text
    assert iban_plain not in e1_res.text
    assert naf_plain not in e1_res.text

    # Stored DB entity holds ONLY ciphertext (never plaintext)
    stored = emp_repo.employees[UUID(e1_id)]
    assert stored.nie_dni_enc is not None and nie_plain not in stored.nie_dni_enc
    assert stored.iban_enc is not None and iban_plain not in stored.iban_enc
    assert stored.naf_enc is not None and naf_plain not in stored.naf_enc

    # Duplicate NIE/DNI blocked via blind index -> 409
    dup_nie = client.post(
        "/api/v1/employees",
        headers=_headers(hr_id, "hr@dragon.es", tenant_id),
        json={
            "primary_store_id": store_tfn_id,
            "employee_code": "EMP-DUP",
            "full_name": "Dup Person",
            "contract_type": "temporal",
            "work_schedule_type": "part_time",
            "contracted_weekly_hours": "20.00",
            "annual_vacation_days": 30,
            "hire_date": "2026-01-01",
            "nie_dni": "y1234567x",
        },
    )
    assert dup_nie.status_code == 409

    # Create part-time employee in TFN (verifies is_overtime_prohibited == True)
    e2_res = client.post(
        "/api/v1/employees",
        headers=_headers(owner_id, "owner@dragon.es", tenant_id),
        json={
            "primary_store_id": store_tfn_id,
            "user_id": str(emp_tfn_user_id),
            "employee_code": "EMP-002",
            "full_name": "Ming Zhou",
            "preferred_name": "周明",
            "contract_type": "fijo_discontinuo",
            "work_schedule_type": "part_time",
            "contracted_weekly_hours": "24.00",
            "annual_vacation_days": 30,
            "hire_date": "2025-06-01",
        },
    )
    assert e2_res.status_code == 201
    assert e2_res.json()["is_overtime_prohibited"] is True
    e2_id = e2_res.json()["id"]

    # 2. Scope isolation on GET /employees
    # Store manager (MAD only) sees ONLY e1, gets 403 on e2 & 403 filtering TFN store
    mgr_headers = _headers(mgr_mad_id, "mgr@dragon.es", tenant_id)
    mgr_list = client.get("/api/v1/employees", headers=mgr_headers)
    assert mgr_list.status_code == 200
    assert [item["id"] for item in mgr_list.json()["items"]] == [e1_id]
    assert client.get(f"/api/v1/employees/{e2_id}", headers=mgr_headers).status_code == 403
    assert (
        client.get(f"/api/v1/employees?store_id={store_tfn_id}", headers=mgr_headers).status_code
        == 403
    )

    # Employee sees ONLY self
    emp1_headers = _headers(emp_mad_user_id, "lin@dragon.es", tenant_id)
    emp1_list = client.get("/api/v1/employees", headers=emp1_headers)
    assert emp1_list.status_code == 200
    assert [item["id"] for item in emp1_list.json()["items"]] == [e1_id]
    assert client.get(f"/api/v1/employees/{e2_id}", headers=emp1_headers).status_code == 403

    # 3. Sensitive PII decryption RBAC matrix
    # Owner, HR, Accountant, and self Employee -> 200 with Cache-Control: no-store
    for uid, email in [
        (owner_id, "owner@dragon.es"),
        (hr_id, "hr@dragon.es"),
        (acc_id, "acc@asesoria.es"),
        (emp_mad_user_id, "lin@dragon.es"),
    ]:
        sens = client.get(
            f"/api/v1/employees/{e1_id}/sensitive",
            headers=_headers(uid, email, tenant_id),
        )
        assert sens.status_code == 200
        assert "no-store" in sens.headers.get("cache-control", "")
        assert sens.json() == {
            "employee_id": e1_id,
            "nie_dni": nie_plain,
            "iban": iban_plain,
            "naf": naf_plain,
        }

    # CRITICAL P0: Store Manager NEVER decrypts PII (403 even for own store's employee!)
    assert (
        client.get(f"/api/v1/employees/{e1_id}/sensitive", headers=mgr_headers).status_code == 403
    )

    # Another employee NEVER decrypts e1's PII -> 403
    assert (
        client.get(
            f"/api/v1/employees/{e1_id}/sensitive",
            headers=_headers(emp_tfn_user_id, "zhou@dragon.es", tenant_id),
        ).status_code
        == 403
    )

    # Non-admins (store_manager, accountant, employee) denied writes -> 403
    for uid, email in [
        (mgr_mad_id, "mgr@dragon.es"),
        (acc_id, "acc@asesoria.es"),
        (emp_mad_user_id, "lin@dragon.es"),
    ]:
        h = _headers(uid, email, tenant_id)
        assert (
            client.post(
                f"/api/v1/employees/{e1_id}/terminate",
                headers=h,
                json={"termination_date": "2026-11-25"},
            ).status_code
            == 403
        )
        assert client.post(f"/api/v1/employees/{e1_id}/anonymize", headers=h).status_code == 403

    # 4. Terminate -> 4-year Legal Hold -> Frozen edit blocked -> Premature anonymize blocked (422)
    owner_headers = _headers(owner_id, "owner@dragon.es", tenant_id)
    term_res = client.post(
        f"/api/v1/employees/{e1_id}/terminate",
        headers=owner_headers,
        json={"termination_date": "2026-11-25"},
    )
    assert term_res.status_code == 200
    assert term_res.json()["employment_status"] == "legal_hold"
    assert term_res.json()["retention_until"] is not None

    # Frozen record cannot be edited -> 422
    edit_frozen = client.patch(
        f"/api/v1/employees/{e1_id}",
        headers=owner_headers,
        json={
            "primary_store_id": store_mad_id,
            "employee_code": "EMP-001",
            "full_name": "Changed",
            "contract_type": "indefinido",
            "work_schedule_type": "full_time",
            "contracted_weekly_hours": "40.00",
            "annual_vacation_days": 30,
            "hire_date": "2025-01-15",
            "employment_status": "active",
        },
    )
    assert edit_frozen.status_code == 422

    # Premature anonymization BEFORE 4-year retention expires -> 422
    premature_anon = client.post(f"/api/v1/employees/{e1_id}/anonymize", headers=owner_headers)
    assert premature_anon.status_code == 422

    # 5. Once 4-year retention expires, anonymization irreversibly wipes all PII (row preserved)
    owner_principal = AuthenticatedPrincipal(owner_id, UUID(tenant_id), UserRole.OWNER, frozenset())
    anon_uc = AnonymizeEmployeeUseCase(emp_repo)
    frozen_emp = emp_repo.employees[UUID(e1_id)]
    assert frozen_emp.retention_until is not None

    with pytest.raises(RetentionLockError):
        await anon_uc.execute(
            owner_principal,
            UUID(e1_id),
            now_utc=frozen_emp.retention_until - timedelta(seconds=1),
        )

    anonymized = await anon_uc.execute(
        owner_principal,
        UUID(e1_id),
        now_utc=frozen_emp.retention_until + timedelta(seconds=1),
    )
    assert anonymized.employment_status == EmploymentStatus.ANONYMIZED
    assert anonymized.full_name == "ANONYMIZED-EMP-001"
    assert anonymized.nie_dni_enc is None
    assert anonymized.nie_dni_masked is None
    assert anonymized.nie_dni_blind_index is None
    assert anonymized.iban_enc is None
    assert anonymized.naf_enc is None
    assert anonymized.deleted_at is not None
    # Row physically preserved (never SQL DELETE)
    assert UUID(e1_id) in emp_repo.employees


def test_cross_tenant_isolation_patch_pii_and_legal_hold_blocks() -> None:
    client, _org, emp_repo, _cipher = _build_client()

    owner_a = uuid4()
    owner_b = uuid4()
    mgr_a = uuid4()
    acc_a = uuid4()
    emp_a_user = uuid4()

    # Tenant A + Store A
    tenant_a = client.post(
        "/api/v1/tenants",
        headers=_headers(owner_a, "owner.a@dragon.es"),
        json={"legal_name": "Dragon A S.L.", "trade_name": "Dragon A", "cif_nif": "B77771111"},
    ).json()["id"]
    store_a = client.post(
        "/api/v1/stores",
        headers=_headers(owner_a, "owner.a@dragon.es", tenant_a),
        json={"name": "Store A", "code": "SA-01", "timezone": "Europe/Madrid"},
    ).json()["id"]

    # Tenant B + Store B
    tenant_b = client.post(
        "/api/v1/tenants",
        headers=_headers(owner_b, "owner.b@bazar.es"),
        json={"legal_name": "Bazar B S.L.", "trade_name": "Bazar B", "cif_nif": "B77772222"},
    ).json()["id"]
    store_b = client.post(
        "/api/v1/stores",
        headers=_headers(owner_b, "owner.b@bazar.es", tenant_b),
        json={"name": "Store B", "code": "SB-01", "timezone": "Europe/Madrid"},
    ).json()["id"]

    owner_a_h = _headers(owner_a, "owner.a@dragon.es", tenant_a)
    owner_b_h = _headers(owner_b, "owner.b@bazar.es", tenant_b)

    for uid, email, name, role, stores in [
        (mgr_a, "mgr.a@dragon.es", "Mgr A", "store_manager", [store_a]),
        (acc_a, "acc.a@asesoria.es", "Acc A", "accountant", []),
        (emp_a_user, "emp.a@dragon.es", "Emp A", "employee", [store_a]),
    ]:
        r = client.post(
            "/api/v1/memberships",
            headers=owner_a_h,
            json={
                "user_id": str(uid),
                "email": email,
                "display_name": name,
                "role": role,
                "store_ids": stores,
            },
        )
        assert r.status_code == 201

    # 1. Cross-tenant store assignment rejected on POST /employees (422)
    cross_store_create = client.post(
        "/api/v1/employees",
        headers=owner_a_h,
        json={
            "primary_store_id": store_b,
            "employee_code": "EMP-X",
            "full_name": "Cross Store",
            "hire_date": "2026-01-01",
        },
    )
    assert cross_store_create.status_code == 422

    # Owner A creates employee in Store A with initial PII
    emp_res = client.post(
        "/api/v1/employees",
        headers=owner_a_h,
        json={
            "primary_store_id": store_a,
            "user_id": str(emp_a_user),
            "employee_code": "EMP-A1",
            "full_name": "Ana Chen",
            "hire_date": "2025-02-01",
            "nie_dni": "X1234567L",
            "iban": "ES9121000418450200051332",
            "naf": "281234567840",
        },
    )
    assert emp_res.status_code == 201
    emp_id = emp_res.json()["id"]

    # 2. Cross-tenant isolation: Owner B (under Tenant B or forged Tenant A header) cannot access
    assert client.get("/api/v1/employees", headers=owner_b_h).json()["total"] == 0
    assert client.get(f"/api/v1/employees/{emp_id}", headers=owner_b_h).status_code == 404
    assert (
        client.get(f"/api/v1/employees/{emp_id}/sensitive", headers=owner_b_h).status_code == 404
    )
    assert (
        client.patch(
            f"/api/v1/employees/{emp_id}",
            headers=owner_b_h,
            json={
                "primary_store_id": store_b,
                "employee_code": "EMP-A1",
                "full_name": "Hijack",
                "contract_type": "indefinido",
                "work_schedule_type": "full_time",
                "contracted_weekly_hours": "40.00",
                "annual_vacation_days": 30,
                "hire_date": "2025-02-01",
                "employment_status": "active",
            },
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/employees/{emp_id}/terminate",
            headers=owner_b_h,
            json={"termination_date": "2026-11-25"},
        ).status_code
        == 404
    )
    assert (
        client.post(f"/api/v1/employees/{emp_id}/anonymize", headers=owner_b_h).status_code == 404
    )
    # Forged X-Tenant-Id = tenant_a -> 403
    forged_b_h = _headers(owner_b, "owner.b@bazar.es", tenant_a)
    assert client.get(f"/api/v1/employees/{emp_id}", headers=forged_b_h).status_code == 403
    assert (
        client.get(f"/api/v1/employees/{emp_id}/sensitive", headers=forged_b_h).status_code == 403
    )

    # 3. Non-admin roles denied POST & PATCH /employees (403)
    for uid, email in [
        (mgr_a, "mgr.a@dragon.es"),
        (acc_a, "acc.a@asesoria.es"),
        (emp_a_user, "emp.a@dragon.es"),
    ]:
        h = _headers(uid, email, tenant_a)
        assert (
            client.post(
                "/api/v1/employees",
                headers=h,
                json={
                    "primary_store_id": store_a,
                    "employee_code": "NO-1",
                    "full_name": "Denied",
                    "hire_date": "2026-01-01",
                },
            ).status_code
            == 403
        )
        assert (
            client.patch(
                f"/api/v1/employees/{emp_id}",
                headers=h,
                json={
                    "primary_store_id": store_a,
                    "employee_code": "EMP-A1",
                    "full_name": "Denied",
                    "contract_type": "indefinido",
                    "work_schedule_type": "full_time",
                    "contracted_weekly_hours": "40.00",
                    "annual_vacation_days": 30,
                    "hire_date": "2025-02-01",
                    "employment_status": "active",
                },
            ).status_code
            == 403
        )

    # 4. Active PATCH PII lifecycle: None preserves, new value rotates, "" clears
    patch_keep = client.patch(
        f"/api/v1/employees/{emp_id}",
        headers=owner_a_h,
        json={
            "primary_store_id": store_a,
            "employee_code": "EMP-A1",
            "full_name": "Ana Chen Updated",
            "contract_type": "indefinido",
            "work_schedule_type": "full_time",
            "contracted_weekly_hours": "40.00",
            "annual_vacation_days": 30,
            "hire_date": "2025-02-01",
            "employment_status": "active",
            "nie_dni": None,
            "iban": "ES7921000813610123456789",
            "naf": "",
        },
    )
    assert patch_keep.status_code == 200
    assert patch_keep.json()["nie_dni_masked"] == "*****567L"
    assert patch_keep.json()["iban_masked"] == "ES79 **** **** 6789"
    assert patch_keep.json()["naf_masked"] is None

    sens_after = client.get(f"/api/v1/employees/{emp_id}/sensitive", headers=owner_a_h).json()
    assert sens_after["nie_dni"] == "X1234567L"
    assert sens_after["iban"] == "ES7921000813610123456789"
    assert sens_after["naf"] is None

    # 5. Terminate -> Legal Hold: re-termination blocked (422) & retention_until immutable
    term1 = client.post(
        f"/api/v1/employees/{emp_id}/terminate",
        headers=owner_a_h,
        json={"termination_date": "2027-06-01"},
    )
    assert term1.status_code == 200
    original_retention = emp_repo.employees[UUID(emp_id)].retention_until
    assert original_retention is not None

    term2 = client.post(
        f"/api/v1/employees/{emp_id}/terminate",
        headers=owner_a_h,
        json={"termination_date": "2025-03-01"},
    )
    assert term2.status_code == 422
    assert emp_repo.employees[UUID(emp_id)].retention_until == original_retention

    # 6. Art. 32 LOPDGDD Bloqueo de datos: store_manager blocked from legal_hold records,
    # while owner & accountant retain statutory audit visibility
    mgr_h = _headers(mgr_a, "mgr.a@dragon.es", tenant_a)
    acc_h = _headers(acc_a, "acc.a@asesoria.es", tenant_a)

    assert client.get("/api/v1/employees", headers=mgr_h).json()["total"] == 0
    assert client.get("/api/v1/employees?status=legal_hold", headers=mgr_h).status_code == 403
    assert client.get(f"/api/v1/employees/{emp_id}", headers=mgr_h).status_code == 403

    assert client.get("/api/v1/employees", headers=acc_h).json()["total"] == 1
    assert client.get(f"/api/v1/employees/{emp_id}", headers=acc_h).status_code == 200
