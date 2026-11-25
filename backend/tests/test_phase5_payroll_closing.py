"""Phase 5 tests: Monthly Payroll Closing, RDL 8/2019 PDFs, Gestoria CSV, Month Locks & P2 fixes."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.application.payroll import CloseMonthlyPayrollUseCase
from app.domain.attendance import (
    AttendanceEvent,
    AttendanceEventType,
    AttendanceSource,
)
from app.domain.documents import (
    validate_file_magic,
)
from app.domain.employees import (
    ContractType,
    Employee,
    EmploymentStatus,
    WorkScheduleType,
    normalize_iban,
    normalize_nie_dni,
)
from app.domain.leaves import LeaveRequest, LeaveStatus, LeaveType
from app.domain.organization import (
    ConflictError,
    Membership,
    Store,
    Tenant,
    ValidationDomainError,
)
from app.domain.payroll import (
    AttendancePdfDayRow,
    AttendancePdfDocumentInput,
    ClosingBlockerCode,
    MonthlyClosing,
    MonthlyClosingItem,
    MonthlyClosingStatus,
    count_overlap_days,
    evaluate_employee_monthly_summary,
    month_date_bounds,
    spanned_period_months,
)
from app.domain.security import AuthenticatedPrincipal, MembershipStatus, UserRole
from app.infrastructure.config import Settings
from app.infrastructure.document_storage import LocalDocumentStorageAdapter
from app.infrastructure.pdf_generator import PurePythonAttendancePdfRenderer
from app.infrastructure.supabase_jwt import SupabaseJWTVerifier, sign_dev_hs256_token
from app.interfaces.api.deps import (
    get_attendance_repository,
    get_commit_fn,
    get_document_repository,
    get_document_storage,
    get_employee_repository,
    get_jwt_verifier,
    get_leave_repository,
    get_membership_repository,
    get_payroll_closing_repository,
    get_pdf_renderer,
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
from tests.test_phase3_attendance import InMemoryAttendanceRepo
from tests.test_phase4_scheduling_leaves_docs import (
    PDF_BYTES,
    InMemoryDocumentRepo,
    InMemoryLeaveRepo,
    InMemoryShiftRepo,
)


class InMemoryPayrollClosingRepo:
    def __init__(self) -> None:
        self.closings: list[MonthlyClosing] = []
        self.items: list[MonthlyClosingItem] = []

    async def is_store_month_closed(
        self,
        tenant_id: UUID,
        store_id: UUID,
        period_month: str,
    ) -> bool:
        return any(
            c.tenant_id == tenant_id
            and c.store_id == store_id
            and c.period_month == period_month
            and c.status == MonthlyClosingStatus.CLOSED
            and c.deleted_at is None
            for c in self.closings
        )

    async def list_closed_months_for_store(
        self,
        tenant_id: UUID,
        store_id: UUID,
        period_months: Sequence[str],
    ) -> set[str]:
        target = set(period_months)
        return {
            c.period_month
            for c in self.closings
            if c.tenant_id == tenant_id
            and c.store_id == store_id
            and c.period_month in target
            and c.status == MonthlyClosingStatus.CLOSED
            and c.deleted_at is None
        }

    async def get_by_id(
        self,
        tenant_id: UUID,
        closing_id: UUID,
    ) -> MonthlyClosing | None:
        for c in self.closings:
            if c.tenant_id == tenant_id and c.id == closing_id and c.deleted_at is None:
                return c
        return None

    async def get_by_store_month(
        self,
        tenant_id: UUID,
        store_id: UUID,
        period_month: str,
    ) -> MonthlyClosing | None:
        for c in self.closings:
            if (
                c.tenant_id == tenant_id
                and c.store_id == store_id
                and c.period_month == period_month
                and c.deleted_at is None
            ):
                return c
        return None

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        store_id: UUID | None = None,
        period_month: str | None = None,
    ) -> list[MonthlyClosing]:
        if allowed_store_ids is not None and not allowed_store_ids:
            return []
        out: list[MonthlyClosing] = []
        for c in self.closings:
            if c.tenant_id != tenant_id or c.deleted_at is not None:
                continue
            if allowed_store_ids is not None and c.store_id not in allowed_store_ids:
                continue
            if store_id is not None and c.store_id != store_id:
                continue
            if period_month is not None and c.period_month != period_month:
                continue
            out.append(c)
        return sorted(out, key=lambda x: (x.period_month, x.closed_at_utc), reverse=True)

    async def create_closing(self, closing: MonthlyClosing) -> MonthlyClosing:
        self.closings.append(closing)
        return closing

    async def update_closing(
        self,
        closing: MonthlyClosing,
        *,
        expected_status: MonthlyClosingStatus | None = None,
        expected_revision: int | None = None,
    ) -> MonthlyClosing | None:
        for idx, existing in enumerate(self.closings):
            if existing.tenant_id == closing.tenant_id and existing.id == closing.id:
                if expected_status is not None and existing.status != expected_status:
                    raise ConflictError(
                        "Monthly closing state or revision changed concurrently"
                    )
                if expected_revision is not None and existing.revision_number != expected_revision:
                    raise ConflictError(
                        "Monthly closing state or revision changed concurrently"
                    )
                self.closings[idx] = closing
                return closing
        if expected_status is not None or expected_revision is not None:
            raise ConflictError("Monthly closing state or revision changed concurrently")
        return None

    async def append_items(
        self,
        items: Sequence[MonthlyClosingItem],
    ) -> list[MonthlyClosingItem]:
        self.items.extend(items)
        return list(items)

    async def list_items(
        self,
        tenant_id: UUID,
        closing_id: UUID,
        *,
        revision_number: int | None = None,
    ) -> list[MonthlyClosingItem]:
        out = [
            it
            for it in self.items
            if it.tenant_id == tenant_id
            and it.closing_id == closing_id
            and (revision_number is None or it.revision_number == revision_number)
        ]
        return sorted(out, key=lambda x: (-x.revision_number, x.employee_code))

    async def list_items_for_closings(
        self,
        tenant_id: UUID,
        closing_ids: Sequence[UUID],
    ) -> list[MonthlyClosingItem]:
        ids = set(closing_ids)
        out = [
            it for it in self.items if it.tenant_id == tenant_id and it.closing_id in ids
        ]
        return sorted(out, key=lambda x: (-x.revision_number, x.employee_code))


# ---------------------------------------------------------------------------
# 1. Pure Domain Tests & P2 DNI/NIE/IBAN Checksum Tests
# ---------------------------------------------------------------------------


def test_domain_month_bounds_spanned_and_overlap() -> None:
    assert month_date_bounds("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))
    assert month_date_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    with pytest.raises(ValidationDomainError):
        month_date_bounds("2026-13")

    assert spanned_period_months(date(2026, 11, 28), date(2027, 2, 3)) == (
        "2026-11",
        "2026-12",
        "2027-01",
        "2027-02",
    )
    assert (
        count_overlap_days(
            date(2026, 5, 29), date(2026, 6, 3), date(2026, 5, 1), date(2026, 5, 31)
        )
        == 3
    )
    assert (
        count_overlap_days(
            date(2026, 4, 1), date(2026, 4, 30), date(2026, 5, 1), date(2026, 5, 31)
        )
        == 0
    )


def test_domain_dni_nie_mod23_and_iban_mod97() -> None:
    assert normalize_nie_dni("12345678z") == "12345678Z"
    assert normalize_nie_dni("X-1234567-L") == "X1234567L"
    assert normalize_nie_dni("Y1234567X") == "Y1234567X"
    with pytest.raises(ValidationDomainError, match="DNI checksum"):
        normalize_nie_dni("12345678A")
    with pytest.raises(ValidationDomainError, match="NIE checksum"):
        normalize_nie_dni("X1234567A")

    assert normalize_iban("ES91 2100 0418 4502 0005 1332") == "ES9121000418450200051332"
    with pytest.raises(ValidationDomainError, match="Mod-97"):
        normalize_iban("ES9121000418450200051333")


def test_domain_evaluate_employee_monthly_summary_blockers_and_leaves() -> None:
    now = datetime(2026, 5, 31, 18, 0, tzinfo=UTC)
    tid, sid, eid, uid = uuid4(), uuid4(), uuid4(), uuid4()
    emp = Employee(
        id=eid,
        tenant_id=tid,
        primary_store_id=sid,
        user_id=uid,
        employee_code="EMP-01",
        full_name="Lucia Gomez",
        preferred_name="Lucia",
        email="lucia@example.com",
        phone="",
        birth_date=date(1992, 3, 15),
        contract_type=ContractType.INDEFINIDO,
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
        annual_vacation_days=30,
        hire_date=date(2025, 1, 1),
        termination_date=None,
        nie_dni_enc=None,
        nie_dni_masked="*****567L",
        nie_dni_blind_index=None,
        iban_enc=None,
        iban_masked=None,
        naf_enc=None,
        naf_masked=None,
        employment_status=EmploymentStatus.ACTIVE,
        retention_until=None,
        anonymized_at=None,
        created_at=now,
        updated_at=now,
    )
    events = [
        AttendanceEvent(
            id=uuid4(),
            tenant_id=tid,
            store_id=sid,
            employee_id=eid,
            event_type=AttendanceEventType.CLOCK_IN,
            occurred_at_utc=datetime(2026, 5, 4, 7, 0, tzinfo=UTC),
            store_timezone="Europe/Madrid",
            source=AttendanceSource.WEB,
            recorded_by_user_id=uid,
            recorded_at_utc=now,
            retention_until=now,
        ),
    ]
    leaves = [
        LeaveRequest(
            id=uuid4(),
            tenant_id=tid,
            store_id=sid,
            employee_id=eid,
            leave_type=LeaveType.VACATION,
            start_date=date(2026, 5, 29),
            end_date=date(2026, 6, 2),
            natural_days=5,
            status=LeaveStatus.APPROVED,
            reason="",
            requested_by_user_id=uid,
            reviewed_by_user_id=uid,
            reviewed_at_utc=now,
            review_note="",
            medical_document_id=None,
            retention_until=now,
            created_at=now,
            updated_at=now,
        ),
        LeaveRequest(
            id=uuid4(),
            tenant_id=tid,
            store_id=sid,
            employee_id=eid,
            leave_type=LeaveType.PAID_LEAVE,
            start_date=date(2026, 5, 20),
            end_date=date(2026, 5, 20),
            natural_days=1,
            status=LeaveStatus.PENDING,
            reason="Medico",
            requested_by_user_id=uid,
            reviewed_by_user_id=None,
            reviewed_at_utc=None,
            review_note="",
            medical_document_id=None,
            retention_until=now,
            created_at=now,
            updated_at=now,
        ),
    ]

    summary, blockers = evaluate_employee_monthly_summary(
        employee=emp,
        events=events,
        leaves=leaves,
        month_start=date(2026, 5, 1),
        month_end=date(2026, 5, 31),
        store_timezone="Europe/Madrid",
    )
    assert summary.vacation_days == 3  # May 29, 30, 31
    assert {b.code for b in blockers} == {
        ClosingBlockerCode.OPEN_SHIFT,
        ClosingBlockerCode.PENDING_LEAVE,
    }


# ---------------------------------------------------------------------------
# 2. Pure Python A4 PDF Generator Tests
# ---------------------------------------------------------------------------


def test_pure_python_pdf_generator_multipage_accents_and_cjk_fallback() -> None:
    renderer = PurePythonAttendancePdfRenderer()
    rows = tuple(
        AttendancePdfDayRow(
            local_date=date(2026, 5, (i % 28) + 1),
            clock_in_local="09:00:00",
            clock_out_local="17:30:00",
            break_minutes=30,
            net_hours=Decimal("8.00"),
            has_corrections=(i % 5 == 0),
            notes="Corrección (Ñ)" if i % 5 == 0 else "",
        )
        for i in range(45)
    )
    pdf = renderer.render_monthly_attendance_pdf(
        AttendancePdfDocumentInput(
            tenant_legal_name="Restauración España S.L. (€)",
            tenant_cif="B12345678",
            store_code="MAD-01",
            store_name="Tienda Gran Vía 测试门店",
            store_timezone="Europe/Madrid",
            period_month="2026-05",
            revision_number=1,
            employee_full_name="José Núñez Peña",
            employee_code="EMP-01",
            nie_dni_masked="*****567L",
            contract_type=ContractType.INDEFINIDO,
            work_schedule_type=WorkScheduleType.FULL_TIME,
            contracted_weekly_hours=Decimal("40.00"),
            worked_days=22,
            total_net_hours=Decimal("176.00"),
            regular_hours=Decimal("176.00"),
            overtime_hours=Decimal("0.00"),
            annual_overtime_hours=Decimal("0.00"),
            total_break_minutes=660,
            vacation_days=0,
            sick_leave_days=0,
            paid_leave_days=0,
            unpaid_leave_days=0,
            warning_alerts_count=0,
            violation_alerts_count=0,
            daily_rows=rows,
            generated_at_utc=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
        )
    )
    assert pdf.startswith(b"%PDF-1.4\n")
    assert pdf.endswith(b"%%EOF\n")
    assert pdf.count(b"/Type /Page ") >= 2
    assert b"/WinAnsiEncoding" in pdf
    validate_file_magic("application/pdf", pdf)


# ---------------------------------------------------------------------------
# 3. API, RBAC, Month Locks, CSV & P2 Regression Tests
# ---------------------------------------------------------------------------


def _build_phase5_app(tmp_path: Path):
    now_ref = [datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)]
    tenant_id = uuid4()
    store_a = uuid4()
    store_b = uuid4()

    owner_uid = uuid4()
    hr_uid = uuid4()
    mgr_uid = uuid4()
    acc_uid = uuid4()
    emp_uid = uuid4()
    emp_hold_uid = uuid4()

    org_repo = InMemoryOrgRepos()
    org_repo.tenants[tenant_id] = Tenant(
        id=tenant_id,
        legal_name="=CMD|Grupo España S.L.",
        trade_name="Grupo España",
        cif_nif="B87654321",
        default_timezone="Europe/Madrid",
        default_locale="es-ES",
        created_at=now_ref[0],
        updated_at=now_ref[0],
    )
    store_repo = InMemoryStoreRepo(org_repo)
    org_repo.stores[store_a] = Store(
        id=store_a,
        tenant_id=tenant_id,
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
    org_repo.stores[store_b] = Store(
        id=store_b,
        tenant_id=tenant_id,
        name="Barcelona",
        code="BCN-01",
        timezone="Europe/Madrid",
        address="Diagonal 1",
        city="Barcelona",
        postal_code="08001",
        is_active=True,
        created_at=now_ref[0],
        updated_at=now_ref[0],
    )

    mem_repo = InMemoryMembershipRepo(org_repo)
    for uid, role, sids in [
        (owner_uid, UserRole.OWNER, frozenset()),
        (hr_uid, UserRole.HR, frozenset()),
        (mgr_uid, UserRole.STORE_MANAGER, frozenset({store_a})),
        (acc_uid, UserRole.ACCOUNTANT, frozenset()),
        (emp_uid, UserRole.EMPLOYEE, frozenset()),
    ]:
        mem = Membership(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=uid,
            email=f"{role.value}@example.com",
            display_name=role.value,
            role=role,
            status=MembershipStatus.ACTIVE,
            store_ids=sids,
            created_at=now_ref[0],
            updated_at=now_ref[0],
        )
        org_repo.memberships[mem.id] = mem

    emp_repo = InMemoryEmployeeRepo()
    emp_1 = Employee(
        id=uuid4(),
        tenant_id=tenant_id,
        primary_store_id=store_a,
        user_id=emp_uid,
        employee_code="EMP-01",
        full_name="=SUM(1+1) Maria",
        preferred_name="Maria",
        email="emp@example.com",
        phone="",
        birth_date=date(1994, 4, 10),
        contract_type=ContractType.INDEFINIDO,
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
        annual_vacation_days=30,
        hire_date=date(2025, 1, 1),
        termination_date=None,
        nie_dni_enc=None,
        nie_dni_masked="*****567L",
        nie_dni_blind_index=None,
        iban_enc=None,
        iban_masked=None,
        naf_enc=None,
        naf_masked=None,
        employment_status=EmploymentStatus.ACTIVE,
        retention_until=None,
        anonymized_at=None,
        created_at=now_ref[0],
        updated_at=now_ref[0],
    )
    emp_mgr = Employee(
        id=uuid4(),
        tenant_id=tenant_id,
        primary_store_id=store_a,
        user_id=mgr_uid,
        employee_code="MGR-01",
        full_name="Carlos Manager",
        preferred_name="Carlos",
        email="mgr@example.com",
        phone="",
        birth_date=date(1988, 2, 1),
        contract_type=ContractType.INDEFINIDO,
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
        annual_vacation_days=30,
        hire_date=date(2025, 1, 1),
        termination_date=None,
        nie_dni_enc=None,
        nie_dni_masked="*****678Z",
        nie_dni_blind_index=None,
        iban_enc=None,
        iban_masked=None,
        naf_enc=None,
        naf_masked=None,
        employment_status=EmploymentStatus.ACTIVE,
        retention_until=None,
        anonymized_at=None,
        created_at=now_ref[0],
        updated_at=now_ref[0],
    )
    emp_hold = Employee(
        id=uuid4(),
        tenant_id=tenant_id,
        primary_store_id=store_a,
        user_id=emp_hold_uid,
        employee_code="HOLD-01",
        full_name="Frozen Employee",
        preferred_name="",
        email="hold@example.com",
        phone="",
        birth_date=date(1990, 1, 1),
        contract_type=ContractType.INDEFINIDO,
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
        annual_vacation_days=30,
        hire_date=date(2025, 1, 1),
        termination_date=date(2026, 5, 15),
        nie_dni_enc=None,
        nie_dni_masked="*****999A",
        nie_dni_blind_index=None,
        iban_enc=None,
        iban_masked=None,
        naf_enc=None,
        naf_masked=None,
        employment_status=EmploymentStatus.LEGAL_HOLD,
        retention_until=datetime(2030, 5, 15, tzinfo=UTC),
        anonymized_at=None,
        created_at=now_ref[0],
        updated_at=now_ref[0],
    )
    for e in (emp_1, emp_mgr, emp_hold):
        emp_repo.employees[e.id] = e

    att_repo = InMemoryAttendanceRepo()
    # Closed shift on May 4, 2026 for emp_1
    ev_in = AttendanceEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=store_a,
        employee_id=emp_1.id,
        event_type=AttendanceEventType.CLOCK_IN,
        occurred_at_utc=datetime(2026, 5, 4, 7, 0, tzinfo=UTC),
        store_timezone="Europe/Madrid",
        source=AttendanceSource.WEB,
        recorded_by_user_id=emp_uid,
        recorded_at_utc=now_ref[0],
        retention_until=datetime(2030, 5, 4, tzinfo=UTC),
    )
    ev_out = AttendanceEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=store_a,
        employee_id=emp_1.id,
        event_type=AttendanceEventType.CLOCK_OUT,
        occurred_at_utc=datetime(2026, 5, 4, 15, 0, tzinfo=UTC),
        store_timezone="Europe/Madrid",
        source=AttendanceSource.WEB,
        recorded_by_user_id=emp_uid,
        recorded_at_utc=now_ref[0],
        retention_until=datetime(2030, 5, 4, tzinfo=UTC),
    )
    # Open shift on May 5 for emp_1 (will block close until corrected/clocked out)
    ev_open = AttendanceEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=store_a,
        employee_id=emp_1.id,
        event_type=AttendanceEventType.CLOCK_IN,
        occurred_at_utc=datetime(2026, 5, 5, 7, 0, tzinfo=UTC),
        store_timezone="Europe/Madrid",
        source=AttendanceSource.WEB,
        recorded_by_user_id=emp_uid,
        recorded_at_utc=now_ref[0],
        retention_until=datetime(2030, 5, 5, tzinfo=UTC),
    )
    att_repo.events.extend([ev_in, ev_out, ev_open])

    # Legal hold employee also has an event in May 2026 so they are included in May closing
    ev_hold_in = AttendanceEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=store_a,
        employee_id=emp_hold.id,
        event_type=AttendanceEventType.CLOCK_IN,
        occurred_at_utc=datetime(2026, 5, 6, 7, 0, tzinfo=UTC),
        store_timezone="Europe/Madrid",
        source=AttendanceSource.WEB,
        recorded_by_user_id=emp_hold_uid,
        recorded_at_utc=now_ref[0],
        retention_until=datetime(2030, 5, 6, tzinfo=UTC),
    )
    ev_hold_out = AttendanceEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=store_a,
        employee_id=emp_hold.id,
        event_type=AttendanceEventType.CLOCK_OUT,
        occurred_at_utc=datetime(2026, 5, 6, 15, 0, tzinfo=UTC),
        store_timezone="Europe/Madrid",
        source=AttendanceSource.WEB,
        recorded_by_user_id=emp_hold_uid,
        recorded_at_utc=now_ref[0],
        retention_until=datetime(2030, 5, 6, tzinfo=UTC),
    )
    att_repo.events.extend([ev_hold_in, ev_hold_out])

    shift_repo = InMemoryShiftRepo()
    leave_repo = InMemoryLeaveRepo()
    # Pending leave in May 2026 (blocks close)
    pending_leave = LeaveRequest(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=store_a,
        employee_id=emp_1.id,
        leave_type=LeaveType.PAID_LEAVE,
        start_date=date(2026, 5, 18),
        end_date=date(2026, 5, 18),
        natural_days=1,
        status=LeaveStatus.PENDING,
        reason="Cita medica",
        requested_by_user_id=emp_uid,
        reviewed_by_user_id=None,
        reviewed_at_utc=None,
        review_note="",
        medical_document_id=None,
        retention_until=datetime(2030, 5, 18, tzinfo=UTC),
        created_at=now_ref[0],
        updated_at=now_ref[0],
    )
    leave_repo.leaves.append(pending_leave)

    doc_repo = InMemoryDocumentRepo()
    payroll_repo = InMemoryPayrollClosingRepo()
    storage = LocalDocumentStorageAdapter(
        base_dir=tmp_path / "docs",
        signing_secret=DEV_SECRET,
        public_api_base_url="http://testserver",
        now_fn=lambda: now_ref[0],
    )

    settings = Settings(
        app_env="development",
        dev_auth_enabled=True,
        supabase_jwt_secret=DEV_SECRET,
        supabase_url="https://example.supabase.co",
        supabase_jwt_audience="authenticated",
        storage_backend="local",
        local_storage_dir=str(tmp_path / "docs"),
        storage_signing_secret=DEV_SECRET,
        trust_proxy_headers=True,
    )
    app = create_app(settings)
    verifier = SupabaseJWTVerifier(
        supabase_url=settings.supabase_url,
        audience=settings.supabase_jwt_audience,
        jwt_secret=DEV_SECRET,
    )

    async def _noop_commit() -> None:
        return None

    app.dependency_overrides[get_jwt_verifier] = lambda: verifier
    app.dependency_overrides[get_tenant_repository] = lambda: org_repo
    app.dependency_overrides[get_store_repository] = lambda: store_repo
    app.dependency_overrides[get_membership_repository] = lambda: mem_repo
    app.dependency_overrides[get_employee_repository] = lambda: emp_repo
    app.dependency_overrides[get_attendance_repository] = lambda: att_repo
    app.dependency_overrides[get_shift_repository] = lambda: shift_repo
    app.dependency_overrides[get_leave_repository] = lambda: leave_repo
    app.dependency_overrides[get_document_repository] = lambda: doc_repo
    app.dependency_overrides[get_document_storage] = lambda: storage
    app.dependency_overrides[get_payroll_closing_repository] = lambda: payroll_repo
    app.dependency_overrides[get_pdf_renderer] = lambda: PurePythonAttendancePdfRenderer()
    app.dependency_overrides[get_commit_fn] = lambda: _noop_commit
    app.dependency_overrides[get_utc_now] = lambda: now_ref[0]

    def _headers(uid: UUID) -> dict[str, str]:
        tok = sign_dev_hs256_token(user_id=uid, email="u@example.com", secret=DEV_SECRET)
        return {"Authorization": f"Bearer {tok}", "X-Tenant-Id": str(tenant_id)}

    return (
        TestClient(app),
        _headers,
        {
            "tenant_id": tenant_id,
            "store_a": store_a,
            "store_b": store_b,
            "owner": owner_uid,
            "hr": hr_uid,
            "mgr": mgr_uid,
            "acc": acc_uid,
            "emp": emp_uid,
            "emp_1_id": emp_1.id,
            "emp_mgr_id": emp_mgr.id,
            "emp_hold_id": emp_hold.id,
            "ev_in_id": ev_in.id,
            "pending_leave_id": pending_leave.id,
            "now_ref": now_ref,
            "repos": (
                org_repo,
                store_repo,
                emp_repo,
                att_repo,
                leave_repo,
                doc_repo,
                payroll_repo,
                storage,
            ),
        },
    )


def test_phase5_payroll_closing_lifecycle_rbac_locks_csv_and_receipts(tmp_path: Path) -> None:
    client, headers, ctx = _build_phase5_app(tmp_path)
    store_a = ctx["store_a"]
    store_b = ctx["store_b"]

    # 1. RBAC: employee -> 403 on all /payroll/*
    assert client.get("/api/v1/payroll/closings", headers=headers(ctx["emp"])).status_code == 403
    assert (
        client.get(
            f"/api/v1/payroll/preview?store_id={store_a}&period_month=2026-05",
            headers=headers(ctx["emp"]),
        ).status_code
        == 403
    )

    # 2. Preview shows OPEN_SHIFT + PENDING_LEAVE blockers; store_manager cannot see legal_hold
    prev_hr = client.get(
        f"/api/v1/payroll/preview?store_id={store_a}&period_month=2026-05",
        headers=headers(ctx["hr"]),
    )
    assert prev_hr.status_code == 200
    assert prev_hr.json()["can_close"] is False
    assert {b["code"] for b in prev_hr.json()["blockers"]} == {"OPEN_SHIFT", "PENDING_LEAVE"}
    assert len(prev_hr.json()["employees"]) == 3  # emp_1, emp_mgr, emp_hold

    prev_mgr = client.get(
        f"/api/v1/payroll/preview?store_id={store_a}&period_month=2026-05",
        headers=headers(ctx["mgr"]),
    )
    assert prev_mgr.status_code == 200
    assert len(prev_mgr.json()["employees"]) == 2  # legal_hold hidden (Art. 32 LOPDGDD)
    assert all(e["employee_id"] != str(ctx["emp_hold_id"]) for e in prev_mgr.json()["employees"])
    assert (
        client.get(
            f"/api/v1/payroll/preview?store_id={store_b}&period_month=2026-05",
            headers=headers(ctx["mgr"]),
        ).status_code
        == 403
    )

    # 3. Close blocked (409) while blockers exist; store_manager/accountant get 403
    assert (
        client.post(
            "/api/v1/payroll/closings",
            headers=headers(ctx["mgr"]),
            json={"store_id": str(store_a), "period_month": "2026-05"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/payroll/closings",
            headers=headers(ctx["acc"]),
            json={"store_id": str(store_a), "period_month": "2026-05"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/payroll/closings",
            headers=headers(ctx["hr"]),
            json={"store_id": str(store_a), "period_month": "2026-05"},
        ).status_code
        == 409
    )

    # 4. Resolve blockers: close open shift via correction & approve pending leave
    assert (
        client.post(
            "/api/v1/attendance/corrections",
            headers=headers(ctx["hr"]),
            json={
                "employee_id": str(ctx["emp_1_id"]),
                "corrected_event_type": "clock_out",
                "occurred_at_utc": "2026-05-05T15:00:00Z",
                "correction_reason": "Forgot to clock out",
            },
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/v1/leaves/{ctx['pending_leave_id']}/review",
            headers=headers(ctx["hr"]),
            json={"approve": True, "review_note": "OK"},
        ).status_code
        == 200
    )

    # 5. Close month successfully (201) -> generates 3 ATTENDANCE_REPORT PDFs
    close_res = client.post(
        "/api/v1/payroll/closings",
        headers=headers(ctx["hr"]),
        json={"store_id": str(store_a), "period_month": "2026-05", "notes": "Cierre mayo"},
    )
    assert close_res.status_code == 201
    closing_data = close_res.json()
    closing_id = closing_data["closing"]["id"]
    assert closing_data["closing"]["status"] == "closed"
    assert closing_data["closing"]["revision_number"] == 1
    assert len(closing_data["items"]) == 3

    emp_1_item = next(
        it for it in closing_data["items"] if it["employee_id"] == str(ctx["emp_1_id"])
    )
    doc_id = emp_1_item["attendance_report_document_id"]
    assert doc_id is not None

    # 6. Employee signs SHA-256 receipt -> closing signature count updates
    sign_res = client.post(
        f"/api/v1/documents/{doc_id}/receipts",
        headers=headers(ctx["emp"]),
    )
    assert sign_res.status_code == 201
    detail_after_sign = client.get(
        f"/api/v1/payroll/closings/{closing_id}",
        headers=headers(ctx["mgr"]),
    )
    assert detail_after_sign.status_code == 200
    # store_manager sees 2 items (legal_hold hidden)
    assert len(detail_after_sign.json()["items"]) == 2
    signed_item = next(
        it for it in detail_after_sign.json()["items"] if it["employee_id"] == str(ctx["emp_1_id"])
    )
    assert signed_item["is_signed"] is True
    assert signed_item["document_sha256_hex"] is not None

    list_res = client.get("/api/v1/payroll/closings", headers=headers(ctx["mgr"]))
    assert list_res.status_code == 200
    assert list_res.json()["items"][0]["signed_count"] == 1
    assert list_res.json()["items"][0]["total_reports"] == 2

    # 7. Closed-month 409 locks across Attendance & Leaves
    ctx["now_ref"][0] = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)
    assert (
        client.post(
            "/api/v1/attendance/clock",
            headers=headers(ctx["emp"]),
            json={"event_type": "clock_in", "source": "web"},
        ).status_code
        == 409
    )
    # Correction in closed month -> 409
    assert (
        client.post(
            "/api/v1/attendance/corrections",
            headers=headers(ctx["hr"]),
            json={
                "employee_id": str(ctx["emp_1_id"]),
                "corrected_event_type": "clock_in",
                "occurred_at_utc": "2026-05-10T07:00:00Z",
                "correction_reason": "Retroactive",
            },
        ).status_code
        == 409
    )
    # Superseding an event in closed month even with June occurred_at_utc -> 409
    assert (
        client.post(
            "/api/v1/attendance/corrections",
            headers=headers(ctx["hr"]),
            json={
                "employee_id": str(ctx["emp_1_id"]),
                "corrected_event_type": "void",
                "occurred_at_utc": "2026-06-02T07:00:00Z",
                "correction_reason": "Supersede closed month",
                "supersedes_event_id": str(ctx["ev_in_id"]),
            },
        ).status_code
        == 409
    )
    # Create/Cancel leave overlapping closed month -> 409
    assert (
        client.post(
            "/api/v1/leaves",
            headers=headers(ctx["emp"]),
            json={
                "leave_type": "vacation",
                "start_date": "2026-05-30",
                "end_date": "2026-06-02",
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/leaves/{ctx['pending_leave_id']}/cancel",
            headers=headers(ctx["emp"]),
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/leaves/{ctx['pending_leave_id']}/review",
            headers=headers(ctx["hr"]),
            json={"approve": False, "review_note": "Closed"},
        ).status_code
        == 409
    )
    ctx["now_ref"][0] = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)

    # 8. Gestoria CSV export: accountant allowed, store_manager 403, BOM + CWE-1236 sanitized
    assert (
        client.get(
            f"/api/v1/payroll/closings/{closing_id}/gestoria-export",
            headers=headers(ctx["mgr"]),
        ).status_code
        == 403
    )
    csv_res = client.get(
        f"/api/v1/payroll/closings/{closing_id}/gestoria-export",
        headers=headers(ctx["acc"]),
    )
    assert csv_res.status_code == 200
    assert csv_res.headers["cache-control"] == "no-store"
    assert csv_res.headers["x-content-type-options"] == "nosniff"
    csv_text = csv_res.content.decode("utf-8")
    assert csv_text.startswith("\ufeff")
    assert "'=CMD|Grupo España S.L." in csv_text
    assert "'=SUM(1+1) Maria" in csv_text
    assert "*****567L" in csv_text

    # 9. Reopen (owner/hr only; store_manager/accountant 403) -> CSV returns 409 -> re-close v2
    assert (
        client.post(
            f"/api/v1/payroll/closings/{closing_id}/reopen",
            headers=headers(ctx["acc"]),
            json={"reopen_reason": "Test"},
        ).status_code
        == 403
    )
    reopen_res = client.post(
        f"/api/v1/payroll/closings/{closing_id}/reopen",
        headers=headers(ctx["owner"]),
        json={"reopen_reason": "Ajuste horas"},
    )
    assert reopen_res.status_code == 200
    assert reopen_res.json()["closing"]["status"] == "reopened"

    assert (
        client.get(
            f"/api/v1/payroll/closings/{closing_id}/gestoria-export",
            headers=headers(ctx["acc"]),
        ).status_code
        == 409
    )

    reclose_res = client.post(
        "/api/v1/payroll/closings",
        headers=headers(ctx["owner"]),
        json={"store_id": str(store_a), "period_month": "2026-05", "notes": "v2"},
    )
    assert reclose_res.status_code == 201
    assert reclose_res.json()["closing"]["revision_number"] == 2
    # v1 items preserved in append-only store
    payroll_repo = ctx["repos"][6]
    assert len(payroll_repo.items) == 6


@pytest.mark.asyncio
async def test_close_monthly_payroll_cleans_up_storage_on_db_failure(tmp_path: Path) -> None:
    _, _, ctx = _build_phase5_app(tmp_path)
    org_repo, store_repo, emp_repo, att_repo, leave_repo, doc_repo, payroll_repo, storage = ctx[
        "repos"
    ]
    # Remove blockers
    att_repo.events.clear()
    leave_repo.leaves.clear()

    async def _fail_append(_items: Sequence[MonthlyClosingItem]) -> list[MonthlyClosingItem]:
        raise RuntimeError("Simulated DB failure")

    payroll_repo.append_items = _fail_append  # type: ignore[method-assign]

    uc = CloseMonthlyPayrollUseCase(
        payroll_repo,  # type: ignore[arg-type]
        org_repo,  # type: ignore[arg-type]
        store_repo,  # type: ignore[arg-type]
        emp_repo,  # type: ignore[arg-type]
        att_repo,  # type: ignore[arg-type]
        leave_repo,  # type: ignore[arg-type]
        doc_repo,  # type: ignore[arg-type]
        storage,
        PurePythonAttendancePdfRenderer(),
    )
    principal = AuthenticatedPrincipal(
        user_id=ctx["hr"],
        tenant_id=ctx["tenant_id"],
        role=UserRole.HR,
        store_ids=frozenset(),
        email="hr@example.com",
    )
    with pytest.raises(RuntimeError, match="Simulated DB failure"):
        await uc.execute(
            principal,
            store_id=ctx["store_a"],
            period_month="2026-05",
            now_utc=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        )
    # Storage directory must have 0 leftover PDF files
    leftover = list((tmp_path / "docs").rglob("*"))
    assert all(p.is_dir() for p in leftover)


def test_p2_regressions_non_ascii_filename_store_manager_own_docs_and_settings_override(
    tmp_path: Path,
) -> None:
    client, headers, ctx = _build_phase5_app(tmp_path)
    # 1. settings_override propagation in /auth/me (dev_auth_enabled=True)
    me_res = client.get("/api/v1/auth/me", headers=headers(ctx["mgr"]))
    assert me_res.status_code == 200
    assert me_res.json()["dev_auth_enabled"] is True

    # 2. Non-ASCII filename fallback -> "document.pdf"
    up_non_ascii = client.post(
        "/api/v1/documents",
        headers=headers(ctx["hr"]),
        data={
            "category": "payslip",
            "title": "Nomina Mayo",
            "employee_id": str(ctx["emp_mgr_id"]),
            "requires_signature": "true",
        },
        files={"file": ("工资单.pdf", PDF_BYTES, "application/pdf")},
    )
    assert up_non_ascii.status_code == 201
    mgr_payslip_id = up_non_ascii.json()["id"]

    # Also upload a payslip for another employee (emp_1)
    up_other = client.post(
        "/api/v1/documents",
        headers=headers(ctx["hr"]),
        data={
            "category": "payslip",
            "title": "Nomina Maria",
            "employee_id": str(ctx["emp_1_id"]),
            "requires_signature": "true",
        },
        files={"file": ("nomina.pdf", PDF_BYTES, "application/pdf")},
    )
    assert up_other.status_code == 201
    other_payslip_id = up_other.json()["id"]

    # 3. store_manager can list/view/signed-url/download/sign their OWN payslip,
    #    while other employee's payslip remains 403!
    list_all = client.get("/api/v1/documents", headers=headers(ctx["mgr"]))
    assert list_all.status_code == 200
    listed_ids = {it["document"]["id"] for it in list_all.json()["items"]}
    assert mgr_payslip_id in listed_ids
    assert other_payslip_id not in listed_ids

    assert (
        client.get(f"/api/v1/documents/{mgr_payslip_id}", headers=headers(ctx["mgr"])).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/documents/{other_payslip_id}", headers=headers(ctx["mgr"])
        ).status_code
        == 403
    )

    surl = client.post(
        f"/api/v1/documents/{mgr_payslip_id}/signed-url",
        headers=headers(ctx["mgr"]),
    )
    assert surl.status_code == 200
    dl_path = surl.json()["url"].replace("http://testserver", "")
    dl = client.get(dl_path)
    assert dl.status_code == 200
    assert dl.content == PDF_BYTES
    assert 'filename="document.pdf"' in dl.headers["content-disposition"]
    assert "filename*=UTF-8''" in dl.headers["content-disposition"]

    # Sign own payslip with X-Forwarded-For (verifying trust_proxy_headers=True)
    sign_own = client.post(
        f"/api/v1/documents/{mgr_payslip_id}/receipts",
        headers={**headers(ctx["mgr"]), "X-Forwarded-For": "203.0.113.42, 10.0.0.1"},
    )
    assert sign_own.status_code == 201
    assert sign_own.json()["ip_address"] == "203.0.113.42"
    assert sign_own.json()["document_sha256_hex"] == hashlib.sha256(PDF_BYTES).hexdigest()

    # 4. store_manager GET /documents?category=payslip (own only) & ?employee_id=<other> (403)
    own_payslips = client.get(
        "/api/v1/documents?category=payslip", headers=headers(ctx["mgr"])
    )
    assert own_payslips.status_code == 200
    assert [it["document"]["id"] for it in own_payslips.json()["items"]] == [mgr_payslip_id]

    assert (
        client.get(
            f"/api/v1/documents?category=payslip&employee_id={ctx['emp_1_id']}",
            headers=headers(ctx["mgr"]),
        ).status_code
        == 403
    )

    # Tenant-wide OTHER doc & legal_hold doc -> 403 for store_manager
    up_tenant_doc = client.post(
        "/api/v1/documents",
        headers=headers(ctx["hr"]),
        data={"category": "other", "title": "Global Policy"},
        files={"file": ("policy.pdf", PDF_BYTES, "application/pdf")},
    )
    assert up_tenant_doc.status_code == 201
    assert (
        client.get(
            f"/api/v1/documents/{up_tenant_doc.json()['id']}", headers=headers(ctx["mgr"])
        ).status_code
        == 403
    )

    up_hold_doc = client.post(
        "/api/v1/documents",
        headers=headers(ctx["hr"]),
        data={
            "category": "medical_leave",
            "title": "Hold Baja",
            "employee_id": str(ctx["emp_hold_id"]),
        },
        files={"file": ("baja.pdf", PDF_BYTES, "application/pdf")},
    )
    assert up_hold_doc.status_code == 201
    assert (
        client.get(
            f"/api/v1/documents/{up_hold_doc.json()['id']}", headers=headers(ctx["mgr"])
        ).status_code
        == 403
    )


def test_p1_negative_regressions_locks_unicode_pdf_and_concurrency(tmp_path: Path) -> None:
    # 1. Unicode \d bypass & Spanish IBAN BBAN letter rejection
    with pytest.raises(ValidationDomainError):
        month_date_bounds("٢٠٢٦-05")
    with pytest.raises(ValidationDomainError):
        normalize_nie_dni("١٢٣٤٥٦٧٨Z")
    with pytest.raises(ValidationDomainError):
        normalize_iban("ES912100041845020005133A")

    # 2. PDF control-char / CRLF stripping, literal escaping, no raw NIE
    renderer = PurePythonAttendancePdfRenderer()
    pdf = renderer.render_monthly_attendance_pdf(
        AttendancePdfDocumentInput(
            tenant_legal_name="Empresa\r\n(Test)\\Evil\x00S.L.",
            tenant_cif="B12345678",
            store_code="MAD-01",
            store_name="Centro\tMadrid\n",
            store_timezone="Europe/Madrid",
            period_month="2026-05",
            revision_number=1,
            employee_full_name="Ana\r\nLopez",
            employee_code="EMP-01",
            nie_dni_masked="*****567L",
            contract_type=ContractType.INDEFINIDO,
            work_schedule_type=WorkScheduleType.FULL_TIME,
            contracted_weekly_hours=Decimal("40.00"),
            worked_days=0,
            total_net_hours=Decimal("0.00"),
            regular_hours=Decimal("0.00"),
            overtime_hours=Decimal("0.00"),
            annual_overtime_hours=Decimal("0.00"),
            total_break_minutes=0,
            vacation_days=0,
            sick_leave_days=0,
            paid_leave_days=0,
            unpaid_leave_days=0,
            warning_alerts_count=0,
            violation_alerts_count=0,
            daily_rows=(),
            generated_at_utc=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
        )
    )
    validate_file_magic("application/pdf", pdf)
    assert b"Evil\x00" not in pdf
    assert b"Empresa  \\(Test\\)\\\\EvilS.L." in pdf
    assert b"X1234567L" not in pdf

    # 3. API-level P1 checks: overnight cross-month lock, cross-store lock, concurrency, isolation
    client, headers, ctx = _build_phase5_app(tmp_path)
    store_a = ctx["store_a"]
    store_b = ctx["store_b"]
    att_repo = ctx["repos"][3]
    leave_repo = ctx["repos"][4]
    payroll_repo = ctx["repos"][6]

    # Resolve May blockers and add an overnight shift: May 31 20:00 UTC -> Jun 1 04:00 UTC
    att_repo.events = [
        ev
        for ev in att_repo.events
        if ev.employee_id != ctx["emp_1_id"]
    ]
    leave_repo.leaves.clear()
    overnight_in = AttendanceEvent(
        id=uuid4(),
        tenant_id=ctx["tenant_id"],
        store_id=store_a,
        employee_id=ctx["emp_1_id"],
        event_type=AttendanceEventType.CLOCK_IN,
        occurred_at_utc=datetime(2026, 5, 31, 20, 0, tzinfo=UTC),
        store_timezone="Europe/Madrid",
        source=AttendanceSource.WEB,
        recorded_by_user_id=ctx["emp"],
        recorded_at_utc=datetime(2026, 5, 31, 20, 0, tzinfo=UTC),
        retention_until=datetime(2030, 5, 31, tzinfo=UTC),
    )
    overnight_out = AttendanceEvent(
        id=uuid4(),
        tenant_id=ctx["tenant_id"],
        store_id=store_a,
        employee_id=ctx["emp_1_id"],
        event_type=AttendanceEventType.CLOCK_OUT,
        occurred_at_utc=datetime(2026, 6, 1, 4, 0, tzinfo=UTC),
        store_timezone="Europe/Madrid",
        source=AttendanceSource.WEB,
        recorded_by_user_id=ctx["emp"],
        recorded_at_utc=datetime(2026, 6, 1, 4, 0, tzinfo=UTC),
        retention_until=datetime(2030, 6, 1, tzinfo=UTC),
    )
    att_repo.events.extend([overnight_in, overnight_out])

    # Unicode period_month rejected at API boundary (422)
    assert (
        client.get(
            f"/api/v1/payroll/preview?store_id={store_a}&period_month=٢٠٢٦-05",
            headers=headers(ctx["hr"]),
        ).status_code
        == 422
    )

    # Close May 2026 for store_a
    close_res = client.post(
        "/api/v1/payroll/closings",
        headers=headers(ctx["hr"]),
        json={"store_id": str(store_a), "period_month": "2026-05"},
    )
    assert close_res.status_code == 201
    closing_id = close_res.json()["closing"]["id"]

    # Cross-month overnight shift mutation via June correction (superseding June clock_out) -> 409!
    assert (
        client.post(
            "/api/v1/attendance/corrections",
            headers=headers(ctx["hr"]),
            json={
                "employee_id": str(ctx["emp_1_id"]),
                "corrected_event_type": "clock_out",
                "occurred_at_utc": "2026-06-01T05:00:00Z",
                "correction_reason": "Modify overnight shift",
                "supersedes_event_id": str(overnight_out.id),
            },
        ).status_code
        == 409
    )

    # Cross-store correction in store_b for emp_1 (primary_store_id=store_a) in closed May -> 409!
    assert (
        client.post(
            "/api/v1/attendance/corrections",
            headers=headers(ctx["hr"]),
            json={
                "employee_id": str(ctx["emp_1_id"]),
                "store_id": str(store_b),
                "corrected_event_type": "clock_in",
                "occurred_at_utc": "2026-05-12T08:00:00Z",
                "correction_reason": "Cross-store bypass attempt",
            },
        ).status_code
        == 409
    )

    # store_manager 403 on /reopen, employee 403 on all /payroll/*
    assert (
        client.post(
            f"/api/v1/payroll/closings/{closing_id}/reopen",
            headers=headers(ctx["mgr"]),
            json={"reopen_reason": "Not allowed"},
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/payroll/closings/{closing_id}", headers=headers(ctx["emp"])
        ).status_code
        == 403
    )

    # Cross-tenant closing isolation -> 404 (random closing_id / other tenant)
    assert (
        client.get(
            f"/api/v1/payroll/closings/{uuid4()}", headers=headers(ctx["owner"])
        ).status_code
        == 404
    )

    # Concurrent reopen mismatch -> 409 Conflict
    existing_closing = payroll_repo.closings[0]
    with pytest.raises(ConflictError):
        import asyncio

        asyncio.run(
            payroll_repo.update_closing(
                existing_closing,
                expected_status=MonthlyClosingStatus.REOPENED,
                expected_revision=existing_closing.revision_number,
            )
        )
