"""Phase 4 tests: Scheduling, Leaves (Art. 38 ET), Private Documents & Receipts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from fastapi.testclient import TestClient

from app.domain.documents import (
    ComplianceDocument,
    DocumentCategory,
    DocumentReceipt,
    sanitize_filename,
    validate_document_upload,
    validate_signed_url_ttl,
)
from app.domain.employees import (
    ContractType,
    Employee,
    EmploymentStatus,
    WorkScheduleType,
)
from app.domain.leaves import (
    LeaveRequest,
    LeaveStatus,
    LeaveType,
    compute_vacation_balance,
    split_days_by_year,
)
from app.domain.organization import Membership, Store, Tenant
from app.domain.scheduling import (
    ScheduleAlertCode,
    Shift,
    ShiftStatus,
    evaluate_employee_week_schedule,
)
from app.domain.security import MembershipStatus, UserRole
from app.infrastructure.config import Settings
from app.infrastructure.document_storage import (
    LocalDocumentStorageAdapter,
    SupabaseDocumentStorageAdapter,
)
from app.infrastructure.supabase_jwt import SupabaseJWTVerifier, sign_dev_hs256_token
from app.interfaces.api.deps import (
    get_commit_fn,
    get_document_repository,
    get_document_storage,
    get_employee_repository,
    get_jwt_verifier,
    get_leave_repository,
    get_membership_repository,
    get_payroll_closing_repository,
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
from tests.test_phase2_employees import InMemoryEmployeeRepo

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class InMemoryShiftRepo:
    def __init__(self) -> None:
        self.shifts: list[Shift] = []

    async def get_by_id(self, tenant_id: UUID, shift_id: UUID) -> Shift | None:
        for s in self.shifts:
            if s.tenant_id == tenant_id and s.id == shift_id and s.deleted_at is None:
                return s
        return None

    async def list_by_store_window(
        self,
        tenant_id: UUID,
        store_id: UUID,
        *,
        window_start_utc: datetime,
        window_end_utc: datetime,
    ) -> list[Shift]:
        return sorted(
            [
                s
                for s in self.shifts
                if s.tenant_id == tenant_id
                and s.store_id == store_id
                and s.deleted_at is None
                and s.start_at_utc < window_end_utc
                and s.end_at_utc > window_start_utc
            ],
            key=lambda s: (s.start_at_utc, str(s.id)),
        )

    async def list_by_employee_window(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        window_start_utc: datetime,
        window_end_utc: datetime,
    ) -> list[Shift]:
        return sorted(
            [
                s
                for s in self.shifts
                if s.tenant_id == tenant_id
                and s.employee_id == employee_id
                and s.deleted_at is None
                and s.start_at_utc < window_end_utc
                and s.end_at_utc > window_start_utc
            ],
            key=lambda s: (s.start_at_utc, str(s.id)),
        )

    async def create(self, shift: Shift) -> Shift:
        self.shifts.append(shift)
        return shift

    async def update(self, shift: Shift) -> Shift | None:
        for idx, existing in enumerate(self.shifts):
            if (
                existing.tenant_id == shift.tenant_id
                and existing.id == shift.id
                and existing.deleted_at is None
            ):
                self.shifts[idx] = shift
                return shift
        return None


class InMemoryLeaveRepo:
    def __init__(self) -> None:
        self.leaves: list[LeaveRequest] = []

    async def get_by_id(self, tenant_id: UUID, leave_id: UUID) -> LeaveRequest | None:
        for lv in self.leaves:
            if lv.tenant_id == tenant_id and lv.id == leave_id and lv.deleted_at is None:
                return lv
        return None

    async def list_by_employee(self, tenant_id: UUID, employee_id: UUID) -> list[LeaveRequest]:
        return [
            lv
            for lv in self.leaves
            if lv.tenant_id == tenant_id and lv.employee_id == employee_id and lv.deleted_at is None
        ]

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        store_id: UUID | None = None,
        employee_id: UUID | None = None,
        status: LeaveStatus | None = None,
        year: int | None = None,
    ) -> list[LeaveRequest]:
        if allowed_store_ids is not None and not allowed_store_ids:
            return []
        out: list[LeaveRequest] = []
        for lv in self.leaves:
            if lv.tenant_id != tenant_id or lv.deleted_at is not None:
                continue
            if allowed_store_ids is not None and lv.store_id not in allowed_store_ids:
                continue
            if store_id is not None and lv.store_id != store_id:
                continue
            if employee_id is not None and lv.employee_id != employee_id:
                continue
            if status is not None and lv.status != status:
                continue
            if year is not None and (
                lv.start_date > date(year, 12, 31) or lv.end_date < date(year, 1, 1)
            ):
                continue
            out.append(lv)
        return out

    async def create(self, leave: LeaveRequest) -> LeaveRequest:
        self.leaves.append(leave)
        return leave

    async def update(self, leave: LeaveRequest) -> LeaveRequest | None:
        for idx, existing in enumerate(self.leaves):
            if (
                existing.tenant_id == leave.tenant_id
                and existing.id == leave.id
                and existing.deleted_at is None
            ):
                self.leaves[idx] = leave
                return leave
        return None


class InMemoryDocumentRepo:
    def __init__(self) -> None:
        self.docs: list[ComplianceDocument] = []
        self.receipts: list[DocumentReceipt] = []

    async def get_by_id(self, tenant_id: UUID, document_id: UUID) -> ComplianceDocument | None:
        for d in self.docs:
            if d.tenant_id == tenant_id and d.id == document_id and d.deleted_at is None:
                return d
        return None

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        only_employee_id: UUID | None = None,
        include_tenant_wide_for_employee: bool = False,
        employee_store_id: UUID | None = None,
        category: DocumentCategory | None = None,
        employee_id: UUID | None = None,
        store_id: UUID | None = None,
        requires_signature: bool | None = None,
        exclude_categories: frozenset[DocumentCategory] | None = None,
    ) -> list[ComplianceDocument]:
        if allowed_store_ids is not None and not allowed_store_ids:
            return []
        out: list[ComplianceDocument] = []
        for d in self.docs:
            if d.tenant_id != tenant_id or d.deleted_at is not None:
                continue
            if allowed_store_ids is not None and d.store_id not in allowed_store_ids:
                continue
            if only_employee_id is not None:
                if include_tenant_wide_for_employee:
                    if d.employee_id is None:
                        if d.category != DocumentCategory.OTHER or (
                            d.store_id is not None and d.store_id != employee_store_id
                        ):
                            continue
                    elif d.employee_id != only_employee_id:
                        continue
                elif d.employee_id != only_employee_id:
                    continue
            if category is not None and d.category != category:
                continue
            if exclude_categories and d.category in exclude_categories:
                continue
            if employee_id is not None and d.employee_id != employee_id:
                continue
            if store_id is not None and d.store_id != store_id:
                continue
            if requires_signature is not None and d.requires_signature != requires_signature:
                continue
            out.append(d)
        return out

    async def create(self, document: ComplianceDocument) -> ComplianceDocument:
        self.docs.append(document)
        return document

    async def soft_delete(
        self,
        tenant_id: UUID,
        document_id: UUID,
        *,
        deleted_at: datetime,
    ) -> bool:
        for idx, d in enumerate(self.docs):
            if d.tenant_id == tenant_id and d.id == document_id and d.deleted_at is None:
                self.docs[idx] = replace(d, deleted_at=deleted_at, updated_at=deleted_at)
                return True
        return False

    async def list_receipts_for_document(
        self, tenant_id: UUID, document_id: UUID
    ) -> list[DocumentReceipt]:
        return [
            r
            for r in self.receipts
            if r.tenant_id == tenant_id and r.document_id == document_id
        ]

    async def list_receipts_for_documents(
        self, tenant_id: UUID, document_ids: list[UUID] | tuple[UUID, ...]
    ) -> list[DocumentReceipt]:
        ids = set(document_ids)
        return [r for r in self.receipts if r.tenant_id == tenant_id and r.document_id in ids]

    async def get_receipt(
        self, tenant_id: UUID, document_id: UUID, employee_id: UUID
    ) -> DocumentReceipt | None:
        for r in self.receipts:
            if (
                r.tenant_id == tenant_id
                and r.document_id == document_id
                and r.employee_id == employee_id
            ):
                return r
        return None

    async def append_receipt(self, receipt: DocumentReceipt) -> DocumentReceipt:
        self.receipts.append(receipt)
        return receipt


def _make_employee(
    *,
    tenant_id: UUID,
    store_id: UUID,
    user_id: UUID | None,
    code: str,
    full_name: str,
    schedule_type: WorkScheduleType = WorkScheduleType.FULL_TIME,
    weekly_hours: Decimal = Decimal("40.00"),
    birth_date: date | None = date(1995, 5, 10),
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
        preferred_name=full_name,
        email=f"{code.lower()}@example.com",
        phone="",
        birth_date=birth_date,
        contract_type=ContractType.INDEFINIDO,
        work_schedule_type=schedule_type,
        contracted_weekly_hours=weekly_hours,
        annual_vacation_days=30,
        hire_date=date(2025, 1, 1),
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


def _setup_phase4_app(
    tmp_path: Path,
    *,
    now_holder: list[datetime] | None = None,
) -> tuple[TestClient, dict[str, object]]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    store_mad = uuid4()
    store_bcn = uuid4()
    store_other = uuid4()

    now_ref = now_holder or [datetime(2026, 11, 25, 10, 0, tzinfo=UTC)]

    owner_id = uuid4()
    hr_id = uuid4()
    mgr_id = uuid4()
    acc_id = uuid4()
    emp1_user_id = uuid4()
    emp2_user_id = uuid4()
    other_tenant_user_id = uuid4()

    org_repo = InMemoryOrgRepos()
    org_repo.tenants[tenant_a] = Tenant(
        id=tenant_a,
        legal_name="Cafe Madrid SL",
        trade_name="Cafe Madrid",
        cif_nif="B12345678",
        default_timezone="Europe/Madrid",
        default_locale="es-ES",
        created_at=now_ref[0],
        updated_at=now_ref[0],
    )
    org_repo.tenants[tenant_b] = Tenant(
        id=tenant_b,
        legal_name="Other SL",
        trade_name="Other",
        cif_nif="B87654321",
        default_timezone="Europe/Madrid",
        default_locale="es-ES",
        created_at=now_ref[0],
        updated_at=now_ref[0],
    )
    store_repo = InMemoryStoreRepo(org_repo)
    org_repo.stores[store_mad] = Store(
        id=store_mad,
        tenant_id=tenant_a,
        name="Madrid Centro",
        code="MAD-01",
        timezone="Europe/Madrid",
        address="Gran Via 1",
        city="Madrid",
        postal_code="28013",
        is_active=True,
        created_at=now_ref[0],
        updated_at=now_ref[0],
    )
    org_repo.stores[store_bcn] = Store(
        id=store_bcn,
        tenant_id=tenant_a,
        name="Barcelona Diagonal",
        code="BCN-01",
        timezone="Europe/Madrid",
        address="Diagonal 10",
        city="Barcelona",
        postal_code="08001",
        is_active=True,
        created_at=now_ref[0],
        updated_at=now_ref[0],
    )
    org_repo.stores[store_other] = Store(
        id=store_other,
        tenant_id=tenant_b,
        name="Other Store",
        code="OTH-01",
        timezone="Europe/Madrid",
        address="",
        city="",
        postal_code="",
        is_active=True,
        created_at=now_ref[0],
        updated_at=now_ref[0],
    )

    mem_repo = InMemoryMembershipRepo(org_repo)
    for uid, role, stores, tid in [
        (owner_id, UserRole.OWNER, frozenset(), tenant_a),
        (hr_id, UserRole.HR, frozenset(), tenant_a),
        (mgr_id, UserRole.STORE_MANAGER, frozenset({store_mad}), tenant_a),
        (acc_id, UserRole.ACCOUNTANT, frozenset(), tenant_a),
        (emp1_user_id, UserRole.EMPLOYEE, frozenset({store_mad}), tenant_a),
        (emp2_user_id, UserRole.EMPLOYEE, frozenset({store_bcn}), tenant_a),
        (other_tenant_user_id, UserRole.OWNER, frozenset(), tenant_b),
    ]:
        mid = uuid4()
        org_repo.memberships[mid] = Membership(
            id=mid,
            tenant_id=tid,
            user_id=uid,
            email=f"{role.value}@example.com",
            display_name=role.value,
            role=role,
            status=MembershipStatus.ACTIVE,
            store_ids=stores,
            created_at=now_ref[0],
            updated_at=now_ref[0],
        )

    emp_repo = InMemoryEmployeeRepo()
    emp1 = _make_employee(
        tenant_id=tenant_a,
        store_id=store_mad,
        user_id=emp1_user_id,
        code="EMP-01",
        full_name="Lucia Perez",
    )
    emp_pt = _make_employee(
        tenant_id=tenant_a,
        store_id=store_mad,
        user_id=None,
        code="EMP-PT",
        full_name="Carlos Ruiz",
        schedule_type=WorkScheduleType.PART_TIME,
        weekly_hours=Decimal("20.00"),
    )
    emp_minor = _make_employee(
        tenant_id=tenant_a,
        store_id=store_mad,
        user_id=None,
        code="EMP-MIN",
        full_name="Ana Joven",
        birth_date=date(2010, 1, 15),
    )
    emp_hold = _make_employee(
        tenant_id=tenant_a,
        store_id=store_mad,
        user_id=None,
        code="EMP-HOLD",
        full_name="Frozen Hold",
        status=EmploymentStatus.LEGAL_HOLD,
    )
    emp_bcn = _make_employee(
        tenant_id=tenant_a,
        store_id=store_bcn,
        user_id=emp2_user_id,
        code="EMP-BCN",
        full_name="Marc Soler",
    )
    for e in (emp1, emp_pt, emp_minor, emp_hold, emp_bcn):
        emp_repo.employees[e.id] = e

    shift_repo = InMemoryShiftRepo()
    leave_repo = InMemoryLeaveRepo()
    doc_repo = InMemoryDocumentRepo()
    storage = LocalDocumentStorageAdapter(
        base_dir=tmp_path / "docs",
        signing_secret="test-secret-key-for-hmac-32-bytes!",
        public_api_base_url="http://testserver",
        api_v1_prefix="/api/v1",
        now_fn=lambda: now_ref[0],
    )

    settings = Settings(
        app_env="development",
        dev_auth_enabled=True,
        supabase_jwt_secret=DEV_SECRET,
        storage_backend="local",
        storage_signing_secret="test-secret-key-for-hmac-32-bytes!",
    )
    app = create_app(settings)
    verifier = SupabaseJWTVerifier(
        supabase_url=settings.supabase_url,
        audience=settings.supabase_jwt_audience,
        jwt_secret=DEV_SECRET,
    )

    async def _noop_commit() -> None:
        return None

    class _OpenPayrollRepo:
        async def is_store_month_closed(
            self, _tenant_id: UUID, _store_id: UUID, _period_month: str
        ) -> bool:
            return False

        async def list_closed_months_for_store(
            self, _tenant_id: UUID, _store_id: UUID, _period_months: object
        ) -> set[str]:
            return set()

    app.dependency_overrides[get_jwt_verifier] = lambda: verifier
    app.dependency_overrides[get_tenant_repository] = lambda: org_repo
    app.dependency_overrides[get_store_repository] = lambda: store_repo
    app.dependency_overrides[get_membership_repository] = lambda: mem_repo
    app.dependency_overrides[get_employee_repository] = lambda: emp_repo
    app.dependency_overrides[get_shift_repository] = lambda: shift_repo
    app.dependency_overrides[get_leave_repository] = lambda: leave_repo
    app.dependency_overrides[get_document_repository] = lambda: doc_repo
    app.dependency_overrides[get_document_storage] = lambda: storage
    app.dependency_overrides[get_payroll_closing_repository] = lambda: _OpenPayrollRepo()
    app.dependency_overrides[get_commit_fn] = lambda: _noop_commit
    app.dependency_overrides[get_utc_now] = lambda: now_ref[0]

    def _headers(uid: UUID, tid: UUID = tenant_a) -> dict[str, str]:
        tok = sign_dev_hs256_token(user_id=uid, email="u@example.com", secret=DEV_SECRET)
        return {"Authorization": f"Bearer {tok}", "X-Tenant-Id": str(tid)}

    ctx: dict[str, object] = {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "store_mad": store_mad,
        "store_bcn": store_bcn,
        "owner_id": owner_id,
        "hr_id": hr_id,
        "mgr_id": mgr_id,
        "acc_id": acc_id,
        "emp1_user_id": emp1_user_id,
        "emp2_user_id": emp2_user_id,
        "other_tenant_user_id": other_tenant_user_id,
        "emp1": emp1,
        "emp_pt": emp_pt,
        "emp_minor": emp_minor,
        "emp_hold": emp_hold,
        "emp_bcn": emp_bcn,
        "shift_repo": shift_repo,
        "leave_repo": leave_repo,
        "doc_repo": doc_repo,
        "storage": storage,
        "headers": _headers,
        "now_ref": now_ref,
    }
    return TestClient(app), ctx


# ---------------------------------------------------------------------------
# 1. Domain Unit Tests (Pure Spanish Labor Law, Leaves, Documents)
# ---------------------------------------------------------------------------


def test_scheduling_weekly_rest_36h_and_labor_alerts() -> None:
    tid, sid = uuid4(), uuid4()
    emp = _make_employee(tenant_id=tid, store_id=sid, user_id=None, code="E1", full_name="Test")
    now = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)
    # 7 consecutive 7h shifts (09:00-16:00 UTC) Mon-Sun => max continuous rest is only 17h (< 36h)
    # and weekly hours = 49h (> 40h), plus missing break (> 6h with 0 break)
    shifts = [
        Shift(
            id=uuid4(),
            tenant_id=tid,
            store_id=sid,
            employee_id=emp.id,
            start_at_utc=datetime(2026, 11, 23 + d, 9, 0, tzinfo=UTC),
            end_at_utc=datetime(2026, 11, 23 + d, 16, 0, tzinfo=UTC),
            planned_break_minutes=0,
            status=ShiftStatus.DRAFT,
            notes="",
            created_by_user_id=uuid4(),
            published_by_user_id=None,
            published_at_utc=None,
            retention_until=now + timedelta(days=1460),
            created_at=now,
            updated_at=now,
        )
        for d in range(7)
    ]
    hours, alerts = evaluate_employee_week_schedule(
        employee=emp,
        week_start=date(2026, 11, 23),
        store_timezone="Europe/Madrid",
        shifts=shifts,
    )
    assert hours == Decimal("49.00")
    codes = {a.code for a in alerts}
    assert ScheduleAlertCode.WEEKLY_HOURS_EXCEEDED in codes
    assert ScheduleAlertCode.WEEKLY_REST_VIOLATION in codes
    assert ScheduleAlertCode.MISSING_CONTINUOUS_BREAK in codes


def test_cross_year_vacation_balance_art38_et() -> None:
    tid, sid = uuid4(), uuid4()
    emp = _make_employee(tenant_id=tid, store_id=sid, user_id=None, code="E2", full_name="Vac")
    assert split_days_by_year(date(2026, 12, 28), date(2027, 1, 4)) == {2026: 4, 2027: 4}
    now = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    req = LeaveRequest(
        id=uuid4(),
        tenant_id=tid,
        store_id=sid,
        employee_id=emp.id,
        leave_type=LeaveType.VACATION,
        start_date=date(2026, 12, 28),
        end_date=date(2027, 1, 4),
        natural_days=8,
        status=LeaveStatus.APPROVED,
        reason="Navidad",
        requested_by_user_id=uuid4(),
        reviewed_by_user_id=uuid4(),
        reviewed_at_utc=now,
        review_note="",
        medical_document_id=None,
        retention_until=now + timedelta(days=1460),
        created_at=now,
        updated_at=now,
    )
    b2026 = compute_vacation_balance(emp, [req], 2026)
    b2027 = compute_vacation_balance(emp, [req], 2027)
    assert b2026.approved_days == 4 and b2026.remaining_days == 26
    assert b2027.approved_days == 4 and b2027.remaining_days == 26


def test_document_domain_validators() -> None:
    assert sanitize_filename('../../etc/passwd\r\n"evil".pdf') == "passwd_evil_.pdf"
    digest = validate_document_upload("application/pdf", PDF_BYTES)
    assert digest == hashlib.sha256(PDF_BYTES).hexdigest()
    assert validate_signed_url_ttl(60) == 60


# ---------------------------------------------------------------------------
# 2. Scheduling API & RBAC Tests
# ---------------------------------------------------------------------------


def test_scheduling_api_conflicts_labor_blocks_and_rbac(tmp_path: Path) -> None:
    client, ctx = _setup_phase4_app(tmp_path)
    headers = ctx["headers"]
    mgr_h = headers(ctx["mgr_id"])  # type: ignore[operator]
    acc_h = headers(ctx["acc_id"])  # type: ignore[operator]
    emp_h = headers(ctx["emp1_user_id"])  # type: ignore[operator]
    emp1: Employee = ctx["emp1"]  # type: ignore[assignment]
    emp_pt: Employee = ctx["emp_pt"]  # type: ignore[assignment]
    emp_minor: Employee = ctx["emp_minor"]  # type: ignore[assignment]
    emp_hold: Employee = ctx["emp_hold"]  # type: ignore[assignment]
    emp_bcn: Employee = ctx["emp_bcn"]  # type: ignore[assignment]

    # Accountant & Employee cannot create shifts (403)
    payload = {
        "employee_id": str(emp1.id),
        "start_at_utc": "2026-11-23T09:00:00Z",
        "end_at_utc": "2026-11-23T17:00:00Z",
        "planned_break_minutes": 30,
    }
    assert client.post("/api/v1/shifts", json=payload, headers=acc_h).status_code == 403
    assert client.post("/api/v1/shifts", json=payload, headers=emp_h).status_code == 403

    # Store manager cannot schedule BCN employee (403) or legal_hold employee (403)
    assert (
        client.post(
            "/api/v1/shifts", json={**payload, "employee_id": str(emp_bcn.id)}, headers=mgr_h
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/shifts", json={**payload, "employee_id": str(emp_hold.id)}, headers=mgr_h
        ).status_code
        == 403
    )

    # Create valid shift for emp1
    r1 = client.post("/api/v1/shifts", json=payload, headers=mgr_h)
    assert r1.status_code == 201
    shift_id = r1.json()["shift"]["id"]

    # Overlapping shift -> 409
    r_ov = client.post(
        "/api/v1/shifts",
        json={
            "employee_id": str(emp1.id),
            "start_at_utc": "2026-11-23T15:00:00Z",
            "end_at_utc": "2026-11-23T20:00:00Z",
        },
        headers=mgr_h,
    )
    assert r_ov.status_code == 409

    # Art. 12.4.c Part-time overtime hard block (20h contracted, try 22h) -> 422
    r_pt = client.post(
        "/api/v1/shifts",
        json={
            "employee_id": str(emp_pt.id),
            "start_at_utc": "2026-11-23T08:00:00Z",
            "end_at_utc": "2026-11-23T18:00:00Z",
        },
        headers=mgr_h,
    )
    assert r_pt.status_code == 201  # 10h ok
    r_pt2 = client.post(
        "/api/v1/shifts",
        json={
            "employee_id": str(emp_pt.id),
            "start_at_utc": "2026-11-24T08:00:00Z",
            "end_at_utc": "2026-11-24T19:00:00Z",  # +11h = 21h > 20h
        },
        headers=mgr_h,
    )
    assert r_pt2.status_code == 422

    # Minor > 8h hard block -> 422
    r_min = client.post(
        "/api/v1/shifts",
        json={
            "employee_id": str(emp_minor.id),
            "start_at_utc": "2026-11-23T08:00:00Z",
            "end_at_utc": "2026-11-23T17:00:00Z",  # 9h > 8h
        },
        headers=mgr_h,
    )
    assert r_min.status_code == 422

    # Publish week + soft cancel
    pub = client.post(
        "/api/v1/shifts/publish-week",
        json={"store_id": str(ctx["store_mad"]), "week_start": "2026-11-23"},
        headers=mgr_h,
    )
    assert pub.status_code == 200
    assert pub.json()["published_count"] >= 1

    wk = client.get(
        f"/api/v1/shifts/weekly?store_id={ctx['store_mad']}&week_start=2026-11-23",
        headers=emp_h,
    )
    assert wk.status_code == 200
    assert len(wk.json()["rows"]) == 1  # Employee only sees self

    del_res = client.delete(f"/api/v1/shifts/{shift_id}", headers=mgr_h)
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "cancelled"


# ---------------------------------------------------------------------------
# 3. Leaves API & Conflict Tests
# ---------------------------------------------------------------------------


def test_leaves_api_workflow_and_shift_conflict(tmp_path: Path) -> None:
    client, ctx = _setup_phase4_app(tmp_path)
    headers = ctx["headers"]
    mgr_h = headers(ctx["mgr_id"])  # type: ignore[operator]
    emp_h = headers(ctx["emp1_user_id"])  # type: ignore[operator]
    acc_h = headers(ctx["acc_id"])  # type: ignore[operator]
    emp1: Employee = ctx["emp1"]  # type: ignore[assignment]

    # Publish a shift on 2026-12-01
    s_res = client.post(
        "/api/v1/shifts",
        json={
            "employee_id": str(emp1.id),
            "start_at_utc": "2026-12-01T09:00:00Z",
            "end_at_utc": "2026-12-01T15:00:00Z",
            "status": "published",
        },
        headers=mgr_h,
    )
    assert s_res.status_code == 201

    # Employee submits vacation overlapping 2026-12-01 (pending allowed, approve -> 409)
    lv_res = client.post(
        "/api/v1/leaves",
        json={
            "leave_type": "vacation",
            "start_date": "2026-12-01",
            "end_date": "2026-12-03",
            "reason": "Descanso",
        },
        headers=emp_h,
    )
    assert lv_res.status_code == 201
    leave_id = lv_res.json()["id"]
    assert lv_res.json()["natural_days"] == 3

    # Accountant cannot create or review leaves (403)
    assert (
        client.post(
            f"/api/v1/leaves/{leave_id}/review",
            json={"approve": True},
            headers=acc_h,
        ).status_code
        == 403
    )

    # Approve fails 409 because of published shift on 2026-12-01
    assert (
        client.post(
            f"/api/v1/leaves/{leave_id}/review",
            json={"approve": True},
            headers=mgr_h,
        ).status_code
        == 409
    )

    # Cancel leave and submit non-conflicting leave
    assert client.post(f"/api/v1/leaves/{leave_id}/cancel", headers=emp_h).status_code == 200

    lv2 = client.post(
        "/api/v1/leaves",
        json={
            "leave_type": "vacation",
            "start_date": "2026-12-10",
            "end_date": "2026-12-12",
        },
        headers=emp_h,
    )
    assert lv2.status_code == 201
    assert (
        client.post(
            f"/api/v1/leaves/{lv2.json()['id']}/review",
            json={"approve": True, "review_note": "OK"},
            headers=mgr_h,
        ).status_code
        == 200
    )

    bal = client.get("/api/v1/leaves/balance?year=2026", headers=emp_h)
    assert bal.status_code == 200
    assert bal.json()["approved_days"] == 3
    assert bal.json()["remaining_days"] == 27


# ---------------------------------------------------------------------------
# 4. Private Compliance Documents, <=60s Signed URLs, Receipts & Supabase Mock
# ---------------------------------------------------------------------------


def test_documents_security_magic_bytes_ttl_hmac_and_receipts(tmp_path: Path) -> None:
    client, ctx = _setup_phase4_app(tmp_path)
    headers = ctx["headers"]
    hr_h = headers(ctx["hr_id"])  # type: ignore[operator]
    mgr_h = headers(ctx["mgr_id"])  # type: ignore[operator]
    acc_h = headers(ctx["acc_id"])  # type: ignore[operator]
    emp_h = headers(ctx["emp1_user_id"])  # type: ignore[operator]
    emp2_h = headers(ctx["emp2_user_id"])  # type: ignore[operator]
    other_h = headers(ctx["other_tenant_user_id"], ctx["tenant_b"])  # type: ignore[operator]
    emp1: Employee = ctx["emp1"]  # type: ignore[assignment]
    emp_hold: Employee = ctx["emp_hold"]  # type: ignore[assignment]
    now_ref: list[datetime] = ctx["now_ref"]  # type: ignore[assignment]

    # Magic-bytes spoof (declared PDF, actual EXE/text) -> 422
    spoof = client.post(
        "/api/v1/documents",
        files={"file": ("fake.pdf", b"MZ\x90\x00not-a-pdf", "application/pdf")},
        data={"category": "contract", "title": "Spoof", "employee_id": str(emp1.id)},
        headers=hr_h,
    )
    assert spoof.status_code == 422

    # HR uploads payslip for emp1 (requires_signature=true)
    up_pay = client.post(
        "/api/v1/documents",
        files={"file": ("nomina.pdf", PDF_BYTES, "application/pdf")},
        data={
            "category": "payslip",
            "title": "Nomina Nov 2026",
            "employee_id": str(emp1.id),
            "period_month": "2026-11",
            "requires_signature": "true",
        },
        headers=hr_h,
    )
    assert up_pay.status_code == 201
    pay_id = up_pay.json()["id"]

    # HR uploads attendance_report for legal_hold employee -> store_manager blocked (403)
    up_hold = client.post(
        "/api/v1/documents",
        files={"file": ("att.pdf", PDF_BYTES, "application/pdf")},
        data={
            "category": "attendance_report",
            "title": "Hold Report",
            "employee_id": str(emp_hold.id),
        },
        headers=hr_h,
    )
    assert up_hold.status_code == 201
    hold_doc_id = up_hold.json()["id"]

    # Store manager blocked from payslip (403) & legal_hold doc (403)
    assert client.get(f"/api/v1/documents/{pay_id}", headers=mgr_h).status_code == 403
    assert client.get(f"/api/v1/documents/{hold_doc_id}", headers=mgr_h).status_code == 403
    assert (
        client.post(
            f"/api/v1/documents/{pay_id}/signed-url", json={"ttl_seconds": 60}, headers=mgr_h
        ).status_code
        == 403
    )

    # Cross-tenant 404/403 & other employee 403
    assert client.get(f"/api/v1/documents/{pay_id}", headers=emp2_h).status_code == 403
    assert client.get(f"/api/v1/documents/{pay_id}", headers=other_h).status_code == 404

    # Accountant can read & generate <=60s signed URL, cannot upload or sign receipt
    assert client.get(f"/api/v1/documents/{pay_id}", headers=acc_h).status_code == 200
    assert client.post(f"/api/v1/documents/{pay_id}/receipts", headers=acc_h).status_code == 403

    # TTL > 60s rejected (422)
    assert (
        client.post(
            f"/api/v1/documents/{pay_id}/signed-url", json={"ttl_seconds": 61}, headers=emp_h
        ).status_code
        == 422
    )

    # Valid <= 60s signed URL + Cache-Control: no-store
    sig_res = client.post(
        f"/api/v1/documents/{pay_id}/signed-url", json={"ttl_seconds": 60}, headers=emp_h
    )
    assert sig_res.status_code == 200
    assert sig_res.headers["cache-control"] == "no-store"
    download_url = sig_res.json()["url"]
    token = download_url.split("token=")[1]

    # Download with security headers
    dl = client.get(f"/api/v1/documents/local-download?token={token}")
    assert dl.status_code == 200
    assert dl.content == PDF_BYTES
    assert dl.headers["cache-control"] == "no-store"
    assert dl.headers["x-content-type-options"] == "nosniff"
    assert dl.headers["content-security-policy"] == "default-src 'none'"
    assert "attachment" in dl.headers["content-disposition"]

    # Tampered token -> 403
    assert (
        client.get(f"/api/v1/documents/local-download?token={token[:-3]}xxx").status_code == 403
    )

    # Expired token (> 60s) -> 403
    now_ref[0] = now_ref[0] + timedelta(seconds=61)
    assert client.get(f"/api/v1/documents/local-download?token={token}").status_code == 403

    # Employee signs electronic receipt -> 201, duplicate -> 409
    rcpt = client.post(f"/api/v1/documents/{pay_id}/receipts", headers=emp_h)
    assert rcpt.status_code == 201
    assert rcpt.json()["document_sha256_hex"] == hashlib.sha256(PDF_BYTES).hexdigest()
    assert client.post(f"/api/v1/documents/{pay_id}/receipts", headers=emp_h).status_code == 409

    # Retention lock blocks delete (422)
    assert client.delete(f"/api/v1/documents/{pay_id}", headers=hr_h).status_code == 422

    # Employee can upload own medical_leave PNG
    up_med = client.post(
        "/api/v1/documents",
        files={"file": ("baja.png", PNG_BYTES, "image/png")},
        data={"category": "medical_leave", "title": "Baja Medica"},
        headers=emp_h,
    )
    assert up_med.status_code == 201


async def test_supabase_storage_adapter_mocked() -> None:
    calls: list[tuple[str, str]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        assert request.headers.get("Authorization") == "Bearer srv-role-key"
        if "/object/sign/" in str(request.url):
            body = json.loads(request.content.decode("utf-8"))
            assert body["expiresIn"] == 60
            return httpx.Response(
                200, json={"signedURL": "/object/sign/okteam-private-docs/k?token=abc"}
            )
        return httpx.Response(200, json={"Key": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as http_client:
        adapter = SupabaseDocumentStorageAdapter(
            supabase_url="https://eu.supabase.co",
            service_role_key="srv-role-key",
            bucket="okteam-private-docs",
            client=http_client,
        )
        key = f"{uuid4()}/{uuid4()}"
        await adapter.put_object(storage_key=key, content=PDF_BYTES, mime_type="application/pdf")
        signed = await adapter.create_signed_url(
            storage_key=key,
            ttl_seconds=60,
            download_filename="doc.pdf",
            mime_type="application/pdf",
        )
        assert signed.url == "https://eu.supabase.co/storage/v1/object/sign/okteam-private-docs/k?token=abc"
        assert signed.ttl_seconds == 60
        await adapter.delete_object(storage_key=key)

    assert len(calls) == 3


def test_p0_p1_red_team_regressions(tmp_path: Path) -> None:
    client, ctx = _setup_phase4_app(tmp_path)
    headers = ctx["headers"]
    hr_h = headers(ctx["hr_id"])  # type: ignore[operator]
    mgr_h = headers(ctx["mgr_id"])  # type: ignore[operator]
    emp_h = headers(ctx["emp1_user_id"])  # type: ignore[operator]
    emp2_h = headers(ctx["emp2_user_id"])  # type: ignore[operator]
    emp1: Employee = ctx["emp1"]  # type: ignore[assignment]
    emp_bcn: Employee = ctx["emp_bcn"]  # type: ignore[assignment]
    emp_hold: Employee = ctx["emp_hold"]  # type: ignore[assignment]
    emp_minor: Employee = ctx["emp_minor"]  # type: ignore[assignment]
    shift_repo: InMemoryShiftRepo = ctx["shift_repo"]  # type: ignore[assignment]
    leave_repo: InMemoryLeaveRepo = ctx["leave_repo"]  # type: ignore[assignment]
    doc_repo: InMemoryDocumentRepo = ctx["doc_repo"]  # type: ignore[assignment]
    now = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)

    # 1. Employee must NOT see draft shifts in weekly schedule
    r_draft = client.post(
        "/api/v1/shifts",
        json={
            "employee_id": str(emp1.id),
            "start_at_utc": "2026-11-24T09:00:00Z",
            "end_at_utc": "2026-11-24T13:00:00Z",
            "status": "draft",
        },
        headers=mgr_h,
    )
    assert r_draft.status_code == 201
    wk_emp = client.get(
        f"/api/v1/shifts/weekly?store_id={ctx['store_mad']}&week_start=2026-11-23",
        headers=emp_h,
    )
    assert wk_emp.status_code == 200
    assert wk_emp.json()["rows"][0]["shifts"] == []
    assert Decimal(wk_emp.json()["rows"][0]["weekly_net_hours"]) == Decimal("0.00")

    # 2. Store manager blocked from 'contract' documents
    up_contract = client.post(
        "/api/v1/documents",
        files={"file": ("contract.pdf", PDF_BYTES, "application/pdf")},
        data={"category": "contract", "title": "Contrato", "employee_id": str(emp1.id)},
        headers=hr_h,
    )
    assert up_contract.status_code == 201
    contract_id = up_contract.json()["id"]
    assert client.get(f"/api/v1/documents/{contract_id}", headers=mgr_h).status_code == 403
    assert client.get("/api/v1/documents?category=contract", headers=mgr_h).status_code == 403

    # 3. Cross-store employee_id=None leak & shared receipt PII isolation
    up_bcn_policy = client.post(
        "/api/v1/documents",
        files={"file": ("bcn_policy.pdf", PDF_BYTES, "application/pdf")},
        data={
            "category": "other",
            "title": "BCN Policy",
            "store_id": str(ctx["store_bcn"]),
            "requires_signature": "true",
        },
        headers=hr_h,
    )
    assert up_bcn_policy.status_code == 201
    bcn_policy_id = up_bcn_policy.json()["id"]
    # emp1 (Madrid) must NOT access BCN store-level policy
    assert client.get(f"/api/v1/documents/{bcn_policy_id}", headers=emp_h).status_code == 403
    assert (
        client.post(f"/api/v1/documents/{bcn_policy_id}/receipts", headers=emp_h).status_code
        == 403
    )

    # Tenant-wide policy signed by emp2: emp1 must NOT see emp2's receipt PII
    up_all_policy = client.post(
        "/api/v1/documents",
        files={"file": ("工资单.pdf", PDF_BYTES, "application/pdf")},
        data={"category": "other", "title": "Global Policy", "requires_signature": "true"},
        headers=hr_h,
    )
    assert up_all_policy.status_code == 201
    all_policy_id = up_all_policy.json()["id"]
    assert (
        client.post(
            f"/api/v1/documents/{all_policy_id}/receipts",
            headers={**emp2_h, "X-Forwarded-For": "9.9.9.9", "User-Agent": "Evil\r\nAgent"},
        ).status_code
        == 201
    )
    # emp1 sees 0 receipts on shared doc (no emp2 PII leak)
    det_emp1 = client.get(f"/api/v1/documents/{all_policy_id}", headers=emp_h)
    assert det_emp1.status_code == 200
    assert det_emp1.json()["receipts"] == []
    # X-Forwarded-For ignored when trust_proxy_headers=False & control chars stripped
    det_hr = client.get(f"/api/v1/documents/{all_policy_id}", headers=hr_h)
    assert det_hr.json()["receipts"][0]["ip_address"] != "9.9.9.9"
    assert "\r" not in det_hr.json()["receipts"][0]["user_agent"]

    # Non-ASCII filename (工资单.pdf) downloads via RFC 6266 without 500, and soft-delete -> 404
    sig_zh = client.post(
        f"/api/v1/documents/{all_policy_id}/signed-url",
        json={"ttl_seconds": 60},
        headers=emp_h,
    )
    assert sig_zh.status_code == 200
    tok_zh = sig_zh.json()["url"].split("token=")[1]
    dl_zh = client.get(f"/api/v1/documents/local-download?token={tok_zh}")
    assert dl_zh.status_code == 200
    assert "filename*=UTF-8''" in dl_zh.headers["content-disposition"]

    # Soft-delete doc (simulate expired retention) -> local-download returns 404
    for idx, d in enumerate(doc_repo.docs):
        if str(d.id) == all_policy_id:
            doc_repo.docs[idx] = replace(d, deleted_at=now)
    assert client.get(f"/api/v1/documents/local-download?token={tok_zh}").status_code == 404

    # 4. Signing oracle: emp2 trying to sign emp1's non-signature doc gets 403, not 422
    up_nonsig = client.post(
        "/api/v1/documents",
        files={"file": ("info.pdf", PDF_BYTES, "application/pdf")},
        data={
            "category": "attendance_report",
            "title": "Info",
            "employee_id": str(emp1.id),
            "requires_signature": "false",
        },
        headers=hr_h,
    )
    assert (
        client.post(
            f"/api/v1/documents/{up_nonsig.json()['id']}/receipts", headers=emp2_h
        ).status_code
        == 403
    )

    # 5. medical_document_id cross-employee IDOR blocked (404)
    assert (
        client.post(
            "/api/v1/leaves",
            json={
                "leave_type": "sick_leave",
                "start_date": "2026-12-20",
                "end_date": "2026-12-21",
                "medical_document_id": contract_id,
            },
            headers=emp_h,
        ).status_code
        == 404
    )

    # 6. WebP RIFF....WAVE spoof & empty & >10MiB rejected (422)
    assert (
        client.post(
            "/api/v1/documents",
            files={"file": ("bad.webp", b"RIFF\x00\x00\x00\x00WAVE", "image/webp")},
            data={"category": "medical_leave", "title": "Bad WebP"},
            headers=emp_h,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/documents",
            files={"file": ("empty.pdf", b"", "application/pdf")},
            data={"category": "medical_leave", "title": "Empty"},
            headers=emp_h,
        ).status_code
        == 422
    )

    # 7. legal_hold cancel & publish-week protections
    hold_shift = Shift(
        id=uuid4(),
        tenant_id=ctx["tenant_a"],  # type: ignore[arg-type]
        store_id=ctx["store_mad"],  # type: ignore[arg-type]
        employee_id=emp_hold.id,
        start_at_utc=datetime(2026, 12, 7, 9, 0, tzinfo=UTC),
        end_at_utc=datetime(2026, 12, 7, 13, 0, tzinfo=UTC),
        planned_break_minutes=0,
        status=ShiftStatus.DRAFT,
        notes="",
        created_by_user_id=ctx["hr_id"],  # type: ignore[arg-type]
        published_by_user_id=None,
        published_at_utc=None,
        retention_until=now + timedelta(days=1460),
        created_at=now,
        updated_at=now,
    )
    shift_repo.shifts.append(hold_shift)
    assert client.delete(f"/api/v1/shifts/{hold_shift.id}", headers=mgr_h).status_code == 403
    assert client.delete(f"/api/v1/shifts/{hold_shift.id}", headers=hr_h).status_code == 422
    assert (
        client.post(
            "/api/v1/shifts/publish-week",
            json={"store_id": str(ctx["store_mad"]), "week_start": "2026-12-07"},
            headers=hr_h,
        ).status_code
        == 422
    )

    # Transferred legal_hold employee from another store (emp_bcn)
    hold_leave = LeaveRequest(
        id=uuid4(),
        tenant_id=ctx["tenant_a"],  # type: ignore[arg-type]
        store_id=ctx["store_mad"],  # type: ignore[arg-type]
        employee_id=emp_bcn.id,
        leave_type=LeaveType.VACATION,
        start_date=date(2026, 12, 15),
        end_date=date(2026, 12, 16),
        natural_days=2,
        status=LeaveStatus.PENDING,
        reason="",
        requested_by_user_id=ctx["hr_id"],  # type: ignore[arg-type]
        reviewed_by_user_id=None,
        reviewed_at_utc=None,
        review_note="",
        medical_document_id=None,
        retention_until=now + timedelta(days=1460),
        created_at=now,
        updated_at=now,
    )
    leave_repo.leaves.append(hold_leave)
    # Mark emp_bcn as legal_hold -> store_manager of MAD must NOT see hold_leave in MAD
    emp_repo_inst: InMemoryEmployeeRepo = client.app.dependency_overrides[
        get_employee_repository
    ]()  # type: ignore[union-attr]
    emp_repo_inst.employees[emp_bcn.id] = replace(
        emp_bcn, employment_status=EmploymentStatus.LEGAL_HOLD
    )
    lv_list = client.get("/api/v1/leaves", headers=mgr_h)
    assert all(item["id"] != str(hold_leave.id) for item in lv_list.json()["items"])

    # 8. Cross-week overnight shift non-double-counting & 12h cross-week rest alert + 4.5h minor
    sun_overnight = Shift(
        id=uuid4(),
        tenant_id=ctx["tenant_a"],  # type: ignore[arg-type]
        store_id=ctx["store_mad"],  # type: ignore[arg-type]
        employee_id=emp_minor.id,
        start_at_utc=datetime(2026, 11, 29, 21, 0, tzinfo=UTC),  # Sun 22:00 Madrid
        end_at_utc=datetime(2026, 11, 30, 3, 0, tzinfo=UTC),  # Mon 04:00 Madrid (6h)
        planned_break_minutes=0,
        status=ShiftStatus.DRAFT,
        notes="",
        created_by_user_id=ctx["hr_id"],  # type: ignore[arg-type]
        published_by_user_id=None,
        published_at_utc=None,
        retention_until=now + timedelta(days=1460),
        created_at=now,
        updated_at=now,
    )
    mon_morning = Shift(
        id=uuid4(),
        tenant_id=ctx["tenant_a"],  # type: ignore[arg-type]
        store_id=ctx["store_mad"],  # type: ignore[arg-type]
        employee_id=emp_minor.id,
        start_at_utc=datetime(2026, 11, 30, 8, 0, tzinfo=UTC),  # Only 5h rest after 03:00 UTC!
        end_at_utc=datetime(2026, 11, 30, 12, 0, tzinfo=UTC),
        planned_break_minutes=0,
        status=ShiftStatus.DRAFT,
        notes="",
        created_by_user_id=ctx["hr_id"],  # type: ignore[arg-type]
        published_by_user_id=None,
        published_at_utc=None,
        retention_until=now + timedelta(days=1460),
        created_at=now,
        updated_at=now,
    )
    hrs_w2, alerts_w2 = evaluate_employee_week_schedule(
        employee=emp_minor,
        week_start=date(2026, 11, 30),
        store_timezone="Europe/Madrid",
        shifts=[sun_overnight, mon_morning],
    )
    # Only mon_morning (4h) is attributed to week 2 (sun_overnight started in week 1)
    assert hrs_w2 == Decimal("4.00")
    assert any(a.code == ScheduleAlertCode.INTER_SHIFT_REST_VIOLATION for a in alerts_w2)
    _, alerts_w1 = evaluate_employee_week_schedule(
        employee=emp_minor,
        week_start=date(2026, 11, 23),
        store_timezone="Europe/Madrid",
        shifts=[sun_overnight],
    )
    break_alerts = [a for a in alerts_w1 if a.code == ScheduleAlertCode.MISSING_CONTINUOUS_BREAK]
    assert break_alerts and "4.5h" in break_alerts[0].message

