"""Phase 5A tests: Workspace Ops, Scopes, Avatars, Dashboards, Holidays, Justificantes & Import."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.application.employees import AnonymizeEmployeeUseCase
from app.domain.announcements import Announcement, AnnouncementAcknowledgement, AnnouncementCategory
from app.domain.employees import ContractType, Employee, EmploymentStatus, WorkScheduleType
from app.domain.holidays import (
    SUPPORTED_REGION_CODES,
    compute_easter_sunday,
    list_holidays_for_year,
)
from app.domain.leaves import LeaveRequest, LeaveStatus, LeaveType
from app.domain.organization import Membership, Store, Tenant
from app.domain.payroll import evaluate_employee_monthly_summary
from app.domain.scheduling import Shift, ShiftStatus
from app.domain.security import AuthenticatedPrincipal, MembershipStatus, UserRole
from app.infrastructure.config import Settings
from app.infrastructure.document_storage import LocalDocumentStorageAdapter
from app.infrastructure.pii_crypto import AesGcmPiiCipher
from app.infrastructure.supabase_jwt import SupabaseJWTVerifier, sign_dev_hs256_token
from app.infrastructure.xlsx_codec import generate_xlsx, parse_xlsx
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
    PDF_BYTES,
    InMemoryDocumentRepo,
    InMemoryLeaveRepo,
    InMemoryShiftRepo,
)
from tests.test_phase5_payroll_closing import InMemoryPayrollClosingRepo

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde"
)
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
WEBP_BYTES = b"RIFF\x1a\x00\x00\x00WEBPVP8 \x0e\x00\x00\x00\x00\x00\x00\x00\x00\x00"


class InMemoryAnnouncementRepo:
    def __init__(self) -> None:
        self.announcements: list[Announcement] = []
        self.acks: list[AnnouncementAcknowledgement] = []

    async def get_by_id(self, tenant_id: UUID, announcement_id: UUID) -> Announcement | None:
        return next(
            (
                a
                for a in self.announcements
                if a.tenant_id == tenant_id and a.id == announcement_id and a.deleted_at is None
            ),
            None,
        )

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        category: AnnouncementCategory | None = None,
    ) -> list[Announcement]:
        items = [
            a
            for a in self.announcements
            if a.tenant_id == tenant_id
            and a.deleted_at is None
            and (category is None or a.category == category)
        ]
        items.sort(key=lambda a: (not a.is_pinned, -a.published_at_utc.timestamp(), str(a.id)))
        return items

    async def create(self, announcement: Announcement) -> Announcement:
        self.announcements.append(announcement)
        return announcement

    async def list_acknowledgements_for_announcement(
        self, tenant_id: UUID, announcement_id: UUID
    ) -> list[AnnouncementAcknowledgement]:
        return [
            a
            for a in self.acks
            if a.tenant_id == tenant_id and a.announcement_id == announcement_id
        ]

    async def list_acknowledgements_for_announcements(
        self, tenant_id: UUID, announcement_ids: Sequence[UUID]
    ) -> list[AnnouncementAcknowledgement]:
        id_set = set(announcement_ids)
        return [a for a in self.acks if a.tenant_id == tenant_id and a.announcement_id in id_set]

    async def get_acknowledgement(
        self, tenant_id: UUID, announcement_id: UUID, employee_id: UUID
    ) -> AnnouncementAcknowledgement | None:
        return next(
            (
                a
                for a in self.acks
                if a.tenant_id == tenant_id
                and a.announcement_id == announcement_id
                and a.employee_id == employee_id
            ),
            None,
        )

    async def append_acknowledgement(
        self, ack: AnnouncementAcknowledgement
    ) -> AnnouncementAcknowledgement:
        self.acks.append(ack)
        return ack


class _State:
    def __init__(self, tmp_path: Path, now_ref: list[datetime]) -> None:
        self.org = InMemoryOrgRepos()
        self.tenant_repo = self.org
        self.store_repo = InMemoryStoreRepo(self.org)
        self.membership_repo = InMemoryMembershipRepo(self.org)
        self.emp_repo = InMemoryEmployeeRepo()
        self.att_repo = InMemoryAttendanceRepo()
        self.shift_repo = InMemoryShiftRepo()
        self.leave_repo = InMemoryLeaveRepo()
        self.doc_repo = InMemoryDocumentRepo()
        self.payroll_repo = InMemoryPayrollClosingRepo()
        self.ann_repo = InMemoryAnnouncementRepo()
        self.cipher = AesGcmPiiCipher(TEST_PII_KEY)
        self.storage = LocalDocumentStorageAdapter(
            base_dir=tmp_path / "storage",
            signing_secret="phase5a-test-signing-secret-32bytes!",
            public_api_base_url="http://testserver",
            now_fn=lambda: now_ref[0],
        )


def _auth(user_id: UUID, email: str, tenant_id: UUID) -> dict[str, str]:
    tok = sign_dev_hs256_token(user_id=user_id, email=email, secret=DEV_SECRET)
    return {"Authorization": f"Bearer {tok}", "X-Tenant-Id": str(tenant_id)}


def _build_env(
    tmp_path: Path, initial_now: datetime | None = None
) -> tuple[TestClient, _State, list[datetime]]:
    now_ref = [initial_now or datetime(2026, 11, 25, 10, 0, tzinfo=UTC)]
    state = _State(tmp_path, now_ref)
    settings = Settings(
        app_env="development",
        dev_auth_enabled=True,
        supabase_jwt_secret=DEV_SECRET,
        pii_encryption_key=TEST_PII_KEY,
        storage_backend="local",
        local_storage_dir=str(tmp_path / "storage"),
        storage_signing_secret="phase5a-test-signing-secret-32bytes!",
    )
    app = create_app(settings_override=settings)
    verifier = SupabaseJWTVerifier(
        supabase_url="https://placeholder.supabase.co",
        audience="authenticated",
        jwt_secret=DEV_SECRET,
    )

    async def _noop_commit() -> None:
        return None

    app.dependency_overrides[get_jwt_verifier] = lambda: verifier
    app.dependency_overrides[get_tenant_repository] = lambda: state.tenant_repo
    app.dependency_overrides[get_store_repository] = lambda: state.store_repo
    app.dependency_overrides[get_membership_repository] = lambda: state.membership_repo
    app.dependency_overrides[get_employee_repository] = lambda: state.emp_repo
    app.dependency_overrides[get_attendance_repository] = lambda: state.att_repo
    app.dependency_overrides[get_shift_repository] = lambda: state.shift_repo
    app.dependency_overrides[get_leave_repository] = lambda: state.leave_repo
    app.dependency_overrides[get_document_repository] = lambda: state.doc_repo
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

    app.dependency_overrides[get_payroll_closing_repository] = lambda: state.payroll_repo
    app.dependency_overrides[get_break_policy_repository] = lambda: _EmptyBreakPolicyRepo()
    app.dependency_overrides[get_leave_entitlement_repository] = (
        lambda: _EmptyLeaveEntitlementRepo()
    )
    app.dependency_overrides[get_announcement_repository] = lambda: state.ann_repo
    app.dependency_overrides[get_pii_cipher] = lambda: state.cipher
    app.dependency_overrides[get_document_storage] = lambda: state.storage
    app.dependency_overrides[get_commit_fn] = lambda: _noop_commit
    app.dependency_overrides[get_utc_now] = lambda: now_ref[0]

    return TestClient(app), state, now_ref


def _seed_org(state: _State) -> dict[str, UUID]:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    tenant_id = uuid4()
    state.org.tenants[tenant_id] = Tenant(
        id=tenant_id,
        legal_name="Restaurante Dragón S.L.",
        trade_name="Dragón",
        cif_nif="B12345678",
        default_timezone="Europe/Madrid",
        default_locale="es-ES",
        created_at=now,
        updated_at=now,
    )
    store_mad = uuid4()
    store_bcn = uuid4()
    state.org.stores[store_mad] = Store(
        id=store_mad,
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
    state.org.stores[store_bcn] = Store(
        id=store_bcn,
        tenant_id=tenant_id,
        name="Barcelona Diagonal",
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
    users = {
        "owner": uuid4(),
        "hr": uuid4(),
        "mgr_cocina": uuid4(),
        "acc": uuid4(),
        "emp_cocina": uuid4(),
        "emp_sala": uuid4(),
        "emp_bcn": uuid4(),
    }
    specs = [
        ("owner", "owner@dragon.es", "Owner", UserRole.OWNER, frozenset(), None),
        ("hr", "hr@dragon.es", "HR", UserRole.HR, frozenset(), None),
        (
            "mgr_cocina",
            "mgr@dragon.es",
            "Jefe Cocina",
            UserRole.STORE_MANAGER,
            frozenset({store_mad}),
            "Cocina",
        ),
        ("acc", "acc@dragon.es", "Gestoría", UserRole.ACCOUNTANT, frozenset(), None),
        (
            "emp_cocina",
            "cocina@dragon.es",
            "Chef 1",
            UserRole.EMPLOYEE,
            frozenset({store_mad}),
            None,
        ),
        (
            "emp_sala",
            "sala@dragon.es",
            "Camarero 1",
            UserRole.EMPLOYEE,
            frozenset({store_mad}),
            None,
        ),
        ("emp_bcn", "bcn@dragon.es", "Chef BCN", UserRole.EMPLOYEE, frozenset({store_bcn}), None),
    ]
    for key, email, name, role, sids, dept_scope in specs:
        mid = uuid4()
        state.org.memberships[mid] = Membership(
            id=mid,
            tenant_id=tenant_id,
            user_id=users[key],
            email=email,
            display_name=name,
            role=role,
            status=MembershipStatus.ACTIVE,
            store_ids=sids,
            department_scope=dept_scope,
            created_at=now,
            updated_at=now,
        )

    emp_cocina_id = uuid4()
    emp_sala_id = uuid4()
    emp_bcn_id = uuid4()
    emp_hold_id = uuid4()
    for eid, uid, sid, code, name, dept, status_val, bday in [
        (
            emp_cocina_id,
            users["emp_cocina"],
            store_mad,
            "E01",
            "Ana Cocina",
            "cocina",
            EmploymentStatus.ACTIVE,
            date(1992, 12, 1),
        ),
        (
            emp_sala_id,
            users["emp_sala"],
            store_mad,
            "E02",
            "Luis Sala",
            "Sala",
            EmploymentStatus.ACTIVE,
            date(1990, 12, 10),
        ),
        (
            emp_bcn_id,
            users["emp_bcn"],
            store_bcn,
            "E03",
            "Marc BCN",
            "Cocina",
            EmploymentStatus.ACTIVE,
            date(1988, 6, 15),
        ),
        (
            emp_hold_id,
            None,
            store_mad,
            "E04",
            "Ex Cocina",
            "Cocina",
            EmploymentStatus.LEGAL_HOLD,
            None,
        ),
    ]:
        state.emp_repo.employees[eid] = Employee(
            id=eid,
            tenant_id=tenant_id,
            primary_store_id=sid,
            user_id=uid,
            employee_code=code,
            full_name=name,
            preferred_name=name.split()[0],
            department=dept,
            email=f"{code.lower()}@dragon.es",
            phone="",
            birth_date=bday,
            contract_type=ContractType.INDEFINIDO,
            work_schedule_type=WorkScheduleType.FULL_TIME,
            contracted_weekly_hours=Decimal("40.00"),
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
            employment_status=status_val,
            retention_until=None,
            anonymized_at=None,
            created_at=now,
            updated_at=now,
        )

    return {
        "tenant_id": tenant_id,
        "store_mad": store_mad,
        "store_bcn": store_bcn,
        "emp_cocina_id": emp_cocina_id,
        "emp_sala_id": emp_sala_id,
        "emp_bcn_id": emp_bcn_id,
        "emp_hold_id": emp_hold_id,
        **users,
    }


def test_department_scope_hardening_and_auth_me(tmp_path: Path) -> None:
    client, state, _ = _build_env(tmp_path)
    ids = _seed_org(state)
    tid = ids["tenant_id"]

    # /auth/me exposes department_scope
    mgr_tok = sign_dev_hs256_token(
        user_id=ids["mgr_cocina"], email="mgr@dragon.es", secret=DEV_SECRET
    )
    me_res = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {mgr_tok}"},
    )
    assert me_res.status_code == 200
    assert me_res.json()["tenants"][0]["department_scope"] == "Cocina"

    # Store manager with department_scope="Cocina" sees ONLY E01 (case-insensitive match)
    list_res = client.get(
        "/api/v1/employees", headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid)
    )
    assert list_res.status_code == 200
    codes = [e["employee_code"] for e in list_res.json()["items"]]
    assert codes == ["E01"]

    # Cross-department employee GET -> 403
    assert (
        client.get(
            f"/api/v1/employees/{ids['emp_sala_id']}",
            headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
        ).status_code
        == 403
    )

    # Cross-store employee GET -> 403
    assert (
        client.get(
            f"/api/v1/employees/{ids['emp_bcn_id']}",
            headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
        ).status_code
        == 403
    )

    # Cross-department attendance correction -> 403
    assert (
        client.post(
            "/api/v1/attendance/corrections",
            headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
            json={
                "employee_id": str(ids["emp_sala_id"]),
                "corrected_event_type": "clock_in",
                "occurred_at_utc": "2026-11-25T09:00:00Z",
                "correction_reason": "Olvido",
            },
        ).status_code
        == 403
    )

    # Cross-department leave balance -> 403
    assert (
        client.get(
            f"/api/v1/leaves/balance?employee_id={ids['emp_sala_id']}&year=2026",
            headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
        ).status_code
        == 403
    )


def test_employee_avatars_upload_serve_security_and_anonymize(tmp_path: Path) -> None:
    client, state, _ = _build_env(tmp_path)
    ids = _seed_org(state)
    tid = ids["tenant_id"]

    # SVG rejected (422)
    svg_res = client.post(
        f"/api/v1/employees/{ids['emp_cocina_id']}/avatar",
        headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
        files={"file": ("evil.svg", b"<svg onload=alert(1)/>", "image/svg+xml")},
    )
    assert svg_res.status_code == 422

    # Spoofed PNG (PDF bytes declared as image/png) rejected (422)
    spoof_res = client.post(
        f"/api/v1/employees/{ids['emp_cocina_id']}/avatar",
        headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
        files={"file": ("fake.png", PDF_BYTES, "image/png")},
    )
    assert spoof_res.status_code == 422

    # Accountant blocked (403)
    assert (
        client.post(
            f"/api/v1/employees/{ids['emp_cocina_id']}/avatar",
            headers=_auth(ids["acc"], "acc@dragon.es", tid),
            files={"file": ("a.png", PNG_BYTES, "image/png")},
        ).status_code
        == 403
    )

    # Out-of-scope manager blocked (403)
    assert (
        client.post(
            f"/api/v1/employees/{ids['emp_sala_id']}/avatar",
            headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
            files={"file": ("a.png", PNG_BYTES, "image/png")},
        ).status_code
        == 403
    )

    # Valid PNG upload by self
    up_res = client.post(
        f"/api/v1/employees/{ids['emp_cocina_id']}/avatar",
        headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
        files={"file": ("avatar.png", PNG_BYTES, "image/png")},
    )
    assert up_res.status_code == 200
    avatar_url = up_res.json()["avatar_url"]
    assert avatar_url is not None
    assert "/api/v1/employees/avatars/local/" in avatar_url
    token_path = avatar_url.split("http://testserver", 1)[1]

    # Fetch inline with strict security headers
    img_res = client.get(token_path)
    assert img_res.status_code == 200
    assert img_res.content == PNG_BYTES
    assert img_res.headers["x-content-type-options"] == "nosniff"
    assert img_res.headers["content-security-policy"] == "default-src 'none'"
    assert img_res.headers["cache-control"] == "no-store"

    # Replace with JPEG -> old key deleted
    old_key = state.emp_repo.employees[ids["emp_cocina_id"]].avatar_storage_key
    assert old_key is not None
    up2_res = client.post(
        f"/api/v1/employees/{ids['emp_cocina_id']}/avatar",
        headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
        files={"file": ("avatar.jpg", JPEG_BYTES, "image/jpeg")},
    )
    assert up2_res.status_code == 200
    new_key = state.emp_repo.employees[ids["emp_cocina_id"]].avatar_storage_key
    assert new_key != old_key
    assert not (tmp_path / "storage" / old_key).exists()
    assert (tmp_path / "storage" / str(new_key)).exists()

    # Old token now fails (403/404)
    assert client.get(token_path).status_code in (403, 404)

    # Delete avatar
    del_res = client.delete(
        f"/api/v1/employees/{ids['emp_cocina_id']}/avatar",
        headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
    )
    assert del_res.status_code == 204
    assert state.emp_repo.employees[ids["emp_cocina_id"]].avatar_storage_key is None
    assert not (tmp_path / "storage" / str(new_key)).exists()

    # AnonymizeEmployeeUseCase also purges avatar
    import asyncio

    async def _test_anon() -> None:
        up3 = client.post(
            f"/api/v1/employees/{ids['emp_cocina_id']}/avatar",
            headers=_auth(ids["owner"], "owner@dragon.es", tid),
            files={"file": ("avatar.webp", WEBP_BYTES, "image/webp")},
        )
        assert up3.status_code == 200
        webp_key = state.emp_repo.employees[ids["emp_cocina_id"]].avatar_storage_key
        assert webp_key is not None
        state.emp_repo.employees[ids["emp_cocina_id"]] = replace(
            state.emp_repo.employees[ids["emp_cocina_id"]],
            employment_status=EmploymentStatus.LEGAL_HOLD,
            retention_until=datetime(2025, 1, 1, tzinfo=UTC),
        )
        owner_p = AuthenticatedPrincipal(
            user_id=ids["owner"], tenant_id=tid, role=UserRole.OWNER, store_ids=frozenset()
        )
        await AnonymizeEmployeeUseCase(state.emp_repo, storage=state.storage).execute(
            owner_p, ids["emp_cocina_id"], now_utc=datetime(2026, 11, 25, tzinfo=UTC)
        )
        assert not (tmp_path / "storage" / webp_key).exists()

    asyncio.run(_test_anon())


def test_holidays_and_dashboards_admin_and_me(tmp_path: Path) -> None:
    # Verify Computus Easter & all 9 autonomous communities
    assert compute_easter_sunday(2026) == date(2026, 4, 5)
    for reg in SUPPORTED_REGION_CODES:
        hols = list_holidays_for_year(2026, reg)
        assert any(h.name_es == "Viernes Santo" and h.date == date(2026, 4, 3) for h in hols)
        assert any(h.scope.value == "regional" and h.region_code == reg for h in hols)

    now = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
    client, state, now_ref = _build_env(tmp_path, initial_now=now)
    ids = _seed_org(state)
    tid = ids["tenant_id"]

    # Emp Cocina clocks in remotely
    clk = client.post(
        "/api/v1/attendance/clock",
        headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
        json={"event_type": "clock_in", "work_mode": "remote"},
    )
    assert clk.status_code == 201
    assert clk.json()["event"]["work_mode"] == "remote"

    # Emp Sala has a published shift starting at 09:00 local (08:00 UTC) and zero punches
    sh_id = uuid4()
    state.shift_repo.shifts.append(
        Shift(
            id=sh_id,
            tenant_id=tid,
            store_id=ids["store_mad"],
            employee_id=ids["emp_sala_id"],
            start_at_utc=datetime(2026, 11, 25, 8, 0, tzinfo=UTC),
            end_at_utc=datetime(2026, 11, 25, 16, 0, tzinfo=UTC),
            planned_break_minutes=30,
            status=ShiftStatus.PUBLISHED,
            notes="",
            created_by_user_id=ids["owner"],
            published_by_user_id=ids["owner"],
            published_at_utc=now - timedelta(days=1),
            retention_until=now + timedelta(days=1460),
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
    )

    # Employee blocked from /dashboard/admin (403)
    assert (
        client.get(
            "/api/v1/dashboard/admin",
            headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
        ).status_code
        == 403
    )

    # Scoped manager blocked from out-of-scope store/dept (403)
    assert (
        client.get(
            f"/api/v1/dashboard/admin?store_id={ids['store_bcn']}",
            headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/dashboard/admin?department=Sala",
            headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
        ).status_code
        == 403
    )

    # Owner views admin dashboard
    adm = client.get("/api/v1/dashboard/admin", headers=_auth(ids["owner"], "owner@dragon.es", tid))
    assert adm.status_code == 200
    body = adm.json()
    groups = {g["status"]: g for g in body["who_is_in"]}
    assert groups["remote"]["count"] == 1
    assert groups["remote"]["members"][0]["employee_code"] == "E01"
    assert groups["absenteeism"]["count"] == 1
    assert groups["absenteeism"]["members"][0]["employee_code"] == "E02"
    # GDPR: upcoming_birthdays NEVER exposes birth_year
    assert len(body["upcoming_birthdays"]) == 2
    assert "birth_year" not in body["upcoming_birthdays"][0]
    assert body["upcoming_birthdays"][0]["birth_month"] == 12
    assert len(body["quick_reports"]) == 4
    assert len(body["upcoming_holidays"]) >= 1

    # /dashboard/me for employee with profile
    me = client.get(
        "/api/v1/dashboard/me",
        headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
    )
    assert me.status_code == 200
    me_data = me.json()
    assert me_data["has_employee_profile"] is True
    assert me_data["clock_state"]["status"] == "working"
    assert me_data["clock_state"]["work_mode"] == "remote"

    # /dashboard/me for owner WITHOUT linked employee profile -> graceful 200
    owner_me = client.get(
        "/api/v1/dashboard/me",
        headers=_auth(ids["owner"], "owner@dragon.es", tid),
    )
    assert owner_me.status_code == 200
    assert owner_me.json()["has_employee_profile"] is False
    assert owner_me.json()["clock_state"] is None


def test_justificantes_security_red_line_and_leave_calendar(tmp_path: Path) -> None:
    client, state, _ = _build_env(tmp_path)
    ids = _seed_org(state)
    tid = ids["tenant_id"]

    # Create medical_appointment leave
    lv_res = client.post(
        "/api/v1/leaves",
        headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
        json={
            "leave_type": "medical_appointment",
            "start_date": "2026-11-26",
            "end_date": "2026-11-26",
            "reason": "Consulta médica",
        },
    )
    assert lv_res.status_code == 201
    leave_id = lv_res.json()["id"]

    # Upload justificante
    just_res = client.post(
        f"/api/v1/leaves/{leave_id}/justificante",
        headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
        files={"file": ("justificante.pdf", PDF_BYTES, "application/pdf")},
    )
    assert just_res.status_code == 200
    doc_id = just_res.json()["medical_document_id"]
    assert doc_id is not None

    # Self & scoped manager can mint signed-url
    assert (
        client.post(
            f"/api/v1/leaves/{leave_id}/justificante/signed-url",
            headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/leaves/{leave_id}/justificante/signed-url",
            headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
        ).status_code
        == 200
    )

    # SECURITY RED LINE: Accountant MUST get 403 on justificante & medical_leave docs
    assert (
        client.post(
            f"/api/v1/leaves/{leave_id}/justificante/signed-url",
            headers=_auth(ids["acc"], "acc@dragon.es", tid),
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/documents/{doc_id}",
            headers=_auth(ids["acc"], "acc@dragon.es", tid),
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/documents?category=medical_leave",
            headers=_auth(ids["acc"], "acc@dragon.es", tid),
        ).status_code
        == 403
    )
    acc_docs = client.get("/api/v1/documents", headers=_auth(ids["acc"], "acc@dragon.es", tid))
    assert acc_docs.status_code == 200
    assert all(it["document"]["category"] != "medical_leave" for it in acc_docs.json()["items"])

    # Leave calendar
    cal = client.get(
        "/api/v1/leaves/calendar?year=2026",
        headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
    )
    assert cal.status_code == 200
    cal_json = cal.json()
    assert cal_json["region_code"] == "ES-MD"
    assert cal_json["type_counts"]["medical_appointment"] == 1
    assert len(cal_json["holidays"]) >= 11

    # Closed payroll month blocks justificante upload -> 409
    from app.domain.payroll import MonthlyClosing, MonthlyClosingStatus

    state.payroll_repo.closings.append(
        MonthlyClosing(
            id=uuid4(),
            tenant_id=tid,
            store_id=ids["store_mad"],
            period_month="2026-11",
            status=MonthlyClosingStatus.CLOSED,
            revision_number=1,
            closed_by_user_id=ids["owner"],
            closed_at_utc=datetime(2026, 12, 1, tzinfo=UTC),
            reopened_by_user_id=None,
            reopened_at_utc=None,
            reopen_reason="",
            notes="",
            retention_until=datetime(2030, 12, 1, tzinfo=UTC),
            created_at=datetime(2026, 12, 1, tzinfo=UTC),
            updated_at=datetime(2026, 12, 1, tzinfo=UTC),
        )
    )
    assert (
        client.post(
            f"/api/v1/leaves/{leave_id}/justificante",
            headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
            files={"file": ("justificante2.pdf", PDF_BYTES, "application/pdf")},
        ).status_code
        == 409
    )

    # Payroll summary handles medical_appointment & day_off cleanly
    emp_obj = state.emp_repo.employees[ids["emp_cocina_id"]]
    lv_med = state.leave_repo.leaves[0]
    lv_dayoff = LeaveRequest(
        id=uuid4(),
        tenant_id=tid,
        store_id=ids["store_mad"],
        employee_id=emp_obj.id,
        leave_type=LeaveType.DAY_OFF,
        start_date=date(2026, 11, 27),
        end_date=date(2026, 11, 27),
        natural_days=1,
        status=LeaveStatus.APPROVED,
        reason="",
        requested_by_user_id=ids["emp_cocina"],
        reviewed_by_user_id=ids["owner"],
        reviewed_at_utc=datetime(2026, 11, 25, tzinfo=UTC),
        review_note="",
        medical_document_id=None,
        retention_until=datetime(2030, 11, 27, tzinfo=UTC),
        created_at=datetime(2026, 11, 25, tzinfo=UTC),
        updated_at=datetime(2026, 11, 25, tzinfo=UTC),
    )
    summary, _ = evaluate_employee_monthly_summary(
        employee=emp_obj,
        events=[],
        leaves=[lv_med, lv_dayoff],
        month_start=date(2026, 11, 1),
        month_end=date(2026, 11, 30),
        store_timezone="Europe/Madrid",
    )
    assert summary.paid_leave_days == 1


def test_accountant_leave_details_are_personal(tmp_path: Path) -> None:
    client, state, _ = _build_env(tmp_path)
    ids = _seed_org(state)
    tenant_id = ids["tenant_id"]
    accountant = _auth(ids["acc"], "acc@dragon.es", tenant_id)
    other_employee = _auth(ids["emp_cocina"], "cocina@dragon.es", tenant_id)
    manager = _auth(ids["mgr_cocina"], "mgr@dragon.es", tenant_id)
    hr = _auth(ids["hr"], "hr@dragon.es", tenant_id)
    leaves = "/api/v1/leaves"
    calendar = f"{leaves}/calendar"
    own_employee_id = ids["emp_sala_id"]
    other_employee_id = ids["emp_cocina_id"]

    leave = client.post(
        leaves,
        headers=other_employee,
        json={
            "leave_type": "medical_appointment",
            "start_date": "2026-11-26",
            "end_date": "2026-11-26",
            "reason": "Consulta médica",
        },
    )
    assert leave.status_code == 201
    own_leave = client.post(
        leaves,
        headers=hr,
        json={
            "employee_id": str(own_employee_id),
            "leave_type": "sick_leave",
            "start_date": "2026-11-27",
            "end_date": "2026-11-28",
            "reason": "Justificado",
        },
    )
    assert own_leave.status_code == 201

    unlinked_list = client.get(leaves, headers=accountant)
    assert unlinked_list.status_code == 200
    assert unlinked_list.json()["items"] == []
    assert client.get(calendar, headers=accountant).status_code == 403

    state.emp_repo.employees[own_employee_id] = replace(
        state.emp_repo.employees[own_employee_id], user_id=ids["acc"]
    )
    own_items = client.get(leaves, headers=accountant, params={"year": 2026})
    assert own_items.status_code == 200
    assert own_items.json()["items"] == [own_leave.json()]
    personal = client.get(calendar, headers=accountant, params={"year": 2026})
    assert personal.status_code == 200
    assert personal.json()["employee_id"] == str(own_employee_id)
    assert personal.json()["leaves"] == [own_leave.json()]
    assert personal.json()["type_counts"]["sick_leave"] == 2
    assert personal.json()["type_counts"]["medical_appointment"] == 0
    own_params = {"employee_id": str(own_employee_id), "year": 2026}
    for path in (leaves, calendar):
        assert client.get(path, headers=accountant, params=own_params).status_code == 200

    other_params = {"employee_id": str(other_employee_id), "year": 2026}
    for path in (leaves, calendar):
        assert client.get(path, headers=accountant, params=other_params).status_code == 403
    for allowed in (other_employee, manager, hr):
        result = client.get(calendar, headers=allowed, params=other_params)
        assert result.status_code == 200
        assert result.json()["leaves"] == [leave.json()]
        assert result.json()["type_counts"]["medical_appointment"] == 1

    approved_other, approved_own = (
        replace(request, status=LeaveStatus.APPROVED) for request in state.leave_repo.leaves
    )
    other_day_off = replace(
        approved_other,
        id=uuid4(),
        leave_type=LeaveType.DAY_OFF,
        start_date=date(2026, 11, 25),
        end_date=date(2026, 11, 25),
    )
    other_vacation = replace(
        approved_other,
        id=uuid4(),
        leave_type=LeaveType.VACATION,
        start_date=date(2026, 11, 27),
        end_date=date(2026, 11, 27),
    )
    state.leave_repo.leaves = [approved_other, approved_own, other_day_off, other_vacation]
    schedule_url = "/api/v1/shifts/weekly"
    schedule_params = {"store_id": str(ids["store_mad"]), "week_start": "2026-11-23"}
    expected_types = {
        "unauthorized": ["absence_redacted", "absence_redacted", "vacation"],
        "authorized": ["medical_appointment", "day_off", "vacation"],
    }
    for headers, visibility in ((accountant, "unauthorized"), (hr, "authorized")):
        schedule = client.get(schedule_url, headers=headers, params=schedule_params)
        assert schedule.status_code == 200
        rows = {row["employee_id"]: row["approved_leaves"] for row in schedule.json()["rows"]}
        assert [entry["leave_type"] for entry in rows[str(other_employee_id)]] == (
            expected_types[visibility]
        )
        assert [entry["leave_type"] for entry in rows[str(own_employee_id)]] == ["sick_leave"]

    state.emp_repo.employees[own_employee_id] = replace(
        state.emp_repo.employees[own_employee_id],
        employment_status=EmploymentStatus.LEGAL_HOLD,
        termination_date=date(2026, 11, 28),
    )
    held_list = client.get(leaves, headers=accountant, params={"year": 2026})
    assert held_list.status_code == 200
    assert held_list.json()["items"] == []
    assert client.get(calendar, headers=accountant).status_code == 403
    for path in (leaves, calendar):
        assert client.get(path, headers=accountant, params=own_params).status_code == 403

    team = client.get(
        f"{leaves}/team-calendar",
        headers=accountant,
        params={"from_date": "2026-11-23", "to_date": "2026-11-29"},
    )
    assert team.status_code == 200
    team_entries = {member["employee_id"]: member["entries"] for member in team.json()["members"]}
    assert str(own_employee_id) not in team_entries
    assert [entry["display_type"] for entry in team_entries[str(other_employee_id)]] == (
        expected_types["unauthorized"]
    )

    schedule = client.get(schedule_url, headers=accountant, params=schedule_params)
    assert schedule.status_code == 200
    rows = {row["employee_id"]: row["approved_leaves"] for row in schedule.json()["rows"]}
    assert [entry["leave_type"] for entry in rows[str(own_employee_id)]] == ["absence_redacted"]


def test_announcements_payslips_and_bulk_import(tmp_path: Path) -> None:
    client, state, _ = _build_env(tmp_path)
    ids = _seed_org(state)
    tid = ids["tenant_id"]

    # 1. Announcements
    ann_res = client.post(
        "/api/v1/announcements",
        headers=_auth(ids["owner"], "owner@dragon.es", tid),
        data={
            "category": "policy",
            "title": "Normativa PRL 2026",
            "body": "Uso obligatorio de calzado antideslizante en cocina.",
            "store_id": str(ids["store_mad"]),
            "department_scope": "Cocina",
            "requires_acknowledgement": "true",
            "is_pinned": "true",
        },
        files={"file": ("prl.pdf", PDF_BYTES, "application/pdf")},
    )
    assert ann_res.status_code == 201
    ann_id = ann_res.json()["id"]
    assert len(ann_res.json()["content_sha256_hex"]) == 64

    # Acknowledge by emp_cocina
    ack_res = client.post(
        f"/api/v1/announcements/{ann_id}/acknowledge",
        headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
    )
    assert ack_res.status_code == 201
    # Duplicate -> 409
    assert (
        client.post(
            f"/api/v1/announcements/{ann_id}/acknowledge",
            headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
        ).status_code
        == 409
    )

    # Out-of-scope emp_sala -> 403
    assert (
        client.post(
            f"/api/v1/announcements/{ann_id}/acknowledge",
            headers=_auth(ids["emp_sala"], "sala@dragon.es", tid),
        ).status_code
        == 403
    )

    # Acknowledgements report: employee -> 403; scoped manager -> 200
    assert (
        client.get(
            f"/api/v1/announcements/{ann_id}/acknowledgements",
            headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
        ).status_code
        == 403
    )
    rep = client.get(
        f"/api/v1/announcements/{ann_id}/acknowledgements",
        headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
    )
    assert rep.status_code == 200
    assert rep.json()["acknowledged_count"] == 1
    assert rep.json()["total_target_count"] == 1

    # Attachment signed URL
    assert (
        client.post(
            f"/api/v1/announcements/{ann_id}/attachment/signed-url",
            headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
        ).status_code
        == 200
    )

    # 2. Payslip distribution by Accountant
    pay_res = client.post(
        "/api/v1/documents",
        headers=_auth(ids["acc"], "acc@dragon.es", tid),
        data={
            "category": "payslip",
            "title": "Nómina Noviembre 2026",
            "employee_id": str(ids["emp_cocina_id"]),
            "period_month": "2026-11",
        },
        files={"file": ("nomina.pdf", PDF_BYTES, "application/pdf")},
    )
    assert pay_res.status_code == 201
    # Accountant blocked from uploading non-payslip (403)
    assert (
        client.post(
            "/api/v1/documents",
            headers=_auth(ids["acc"], "acc@dragon.es", tid),
            data={
                "category": "contract",
                "title": "Contrato",
                "employee_id": str(ids["emp_cocina_id"]),
            },
            files={"file": ("c.pdf", PDF_BYTES, "application/pdf")},
        ).status_code
        == 403
    )
    # Store manager blocked from uploading or reading other employee's payslip (403)
    assert (
        client.get(
            f"/api/v1/documents/{pay_res.json()['id']}",
            headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
        ).status_code
        == 403
    )
    # Filter by period_month
    emp_payslips = client.get(
        "/api/v1/documents?category=payslip&period_month=2026-11",
        headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
    )
    assert emp_payslips.status_code == 200
    assert len(emp_payslips.json()["items"]) == 1

    # 3. Bulk Employee Import (CSV & XLSX + Zip-Bomb + XXE + Atomic Rollback)
    tpl_xlsx = client.get(
        "/api/v1/employees/import/template?format=xlsx",
        headers=_auth(ids["hr"], "hr@dragon.es", tid),
    )
    assert tpl_xlsx.status_code == 200
    headers, sample_rows = parse_xlsx(tpl_xlsx.content)
    assert headers[0] == "employee_code"
    assert len(sample_rows) == 1

    # Store manager blocked from template & import (403)
    assert (
        client.get(
            "/api/v1/employees/import/template?format=csv",
            headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
        ).status_code
        == 403
    )

    # Atomic rollback on invalid row (bad NIE & bad IBAN — never echo raw PII!)
    bad_nie = "12345678A"
    bad_iban = "ES0012345678901234567890"
    bad_xlsx = generate_xlsx(
        "Employees",
        headers,
        [
            [
                "IMP01",
                "Carlos Nuevo",
                "Carlos",
                "MAD-01",
                "Cocina",
                "carlos@dragon.es",
                "",
                "1995-01-01",
                "indefinido",
                "full_time",
                "40.00",
                "30",
                "2026-11-01",
                "",
                "",
                "",
            ],
            [
                "IMP02",
                "Bad Row",
                "",
                "MAD-01",
                "Sala",
                "bad@dragon.es",
                "",
                "",
                "indefinido",
                "full_time",
                "40.00",
                "30",
                "2026-11-01",
                bad_nie,
                bad_iban,
                "123",
            ],
        ],
    )
    bad_res = client.post(
        "/api/v1/employees/import?dry_run=false",
        headers=_auth(ids["owner"], "owner@dragon.es", tid),
        files={
            "file": (
                "import.xlsx",
                bad_xlsx,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert bad_res.status_code == 200
    bad_json = bad_res.json()
    assert bad_json["committed"] is False
    assert bad_json["error_rows"] == 1
    assert bad_json["created_count"] == 0
    err_text = str(bad_json["errors"])
    assert bad_nie not in err_text
    assert bad_iban not in err_text
    # Verify IMP01 was NOT committed
    assert all(e.employee_code != "IMP01" for e in state.emp_repo.employees.values())

    # Valid XLSX import commits atomically
    good_xlsx = generate_xlsx(
        "Employees",
        headers,
        [
            [
                "IMP01",
                "Carlos Nuevo",
                "Carlos",
                "MAD-01",
                "Cocina",
                "carlos@dragon.es",
                "",
                "1995-01-01",
                "indefinido",
                "full_time",
                "40.00",
                "30",
                "2026-11-01",
                "",
                "",
                "",
            ],
        ],
    )
    good_res = client.post(
        "/api/v1/employees/import?dry_run=false",
        headers=_auth(ids["owner"], "owner@dragon.es", tid),
        files={
            "file": (
                "good.xlsx",
                good_xlsx,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert good_res.status_code == 200
    assert good_res.json()["committed"] is True
    assert good_res.json()["created_count"] == 1

    # XXE rejection
    xxe_buf = io.BytesIO()
    with zipfile.ZipFile(xxe_buf, "w") as zf:
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><worksheet/>',
        )
    assert (
        client.post(
            "/api/v1/employees/import",
            headers=_auth(ids["owner"], "owner@dragon.es", tid),
            files={
                "file": (
                    "xxe.xlsx",
                    xxe_buf.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        ).status_code
        == 422
    )

    # Zip-Bomb (> 20 MiB uncompressed) rejection
    bomb_buf = io.BytesIO()
    with zipfile.ZipFile(bomb_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/worksheets/sheet1.xml", b"A" * (21 * 1024 * 1024))
    assert (
        client.post(
            "/api/v1/employees/import",
            headers=_auth(ids["owner"], "owner@dragon.es", tid),
            files={
                "file": (
                    "bomb.xlsx",
                    bomb_buf.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        ).status_code
        == 422
    )


def test_p1_regressions_leave_repo_attachments_receipts_xlsx_and_shifts(tmp_path: Path) -> None:
    """Regression tests for all 5 Red-Team P1s and P2s."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from app.infrastructure.models import LeaveRequestModel
    from app.infrastructure.repositories import SqlAlchemyLeaveRepository

    # P1-1: SqlAlchemyLeaveRepository.update persists medical_document_id
    doc_uuid = uuid4()
    leave_uuid = uuid4()
    fake_model = LeaveRequestModel(
        id=leave_uuid,
        tenant_id=uuid4(),
        store_id=uuid4(),
        employee_id=uuid4(),
        leave_type="sick_leave",
        start_date=date(2026, 11, 26),
        end_date=date(2026, 11, 26),
        natural_days=1,
        status="pending_manager",
        reason="",
        requested_by_user_id=uuid4(),
        reviewed_by_user_id=None,
        reviewed_at_utc=None,
        review_note="",
        medical_document_id=None,
        retention_until=datetime(2030, 11, 26, tzinfo=UTC),
        created_at=datetime(2026, 11, 25, tzinfo=UTC),
        updated_at=datetime(2026, 11, 25, tzinfo=UTC),
    )
    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = fake_model
    fake_session = AsyncMock()
    fake_session.execute.return_value = fake_result
    updated_leave_domain = LeaveRequest(
        id=leave_uuid,
        tenant_id=fake_model.tenant_id,
        store_id=fake_model.store_id,
        employee_id=fake_model.employee_id,
        leave_type=LeaveType.SICK_LEAVE,
        start_date=fake_model.start_date,
        end_date=fake_model.end_date,
        natural_days=1,
        status=LeaveStatus.PENDING_MANAGER,
        reason="",
        requested_by_user_id=fake_model.requested_by_user_id,
        reviewed_by_user_id=None,
        reviewed_at_utc=None,
        review_note="",
        medical_document_id=doc_uuid,
        retention_until=fake_model.retention_until,
        created_at=fake_model.created_at,
        updated_at=datetime(2026, 11, 25, 12, 0, tzinfo=UTC),
    )
    saved_lv = asyncio.run(SqlAlchemyLeaveRepository(fake_session).update(updated_leave_domain))
    assert saved_lv is not None
    assert fake_model.medical_document_id == doc_uuid
    assert saved_lv.medical_document_id == doc_uuid

    client, state, _ = _build_env(tmp_path)
    ids = _seed_org(state)
    tid = ids["tenant_id"]

    # P1-2: Department-scoped announcement attachment cannot be bypassed via /documents
    ann_sala = client.post(
        "/api/v1/announcements",
        headers=_auth(ids["owner"], "owner@dragon.es", tid),
        data={
            "category": "policy",
            "title": "Solo Sala",
            "body": "Protocolo exclusivo de Sala.",
            "store_id": str(ids["store_mad"]),
            "department_scope": "Sala",
            "requires_acknowledgement": "true",
        },
        files={"file": ("sala_policy.pdf", PDF_BYTES, "application/pdf")},
    )
    assert ann_sala.status_code == 201
    att_doc_id = ann_sala.json()["attachment_document_id"]
    assert att_doc_id is not None

    # Out-of-department employee & manager -> 403 on /documents/{id} and /signed-url
    for uid, email in [
        (ids["emp_cocina"], "cocina@dragon.es"),
        (ids["mgr_cocina"], "mgr@dragon.es"),
    ]:
        assert (
            client.get(
                f"/api/v1/documents/{att_doc_id}",
                headers=_auth(uid, email, tid),
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/documents/{att_doc_id}/signed-url",
                headers=_auth(uid, email, tid),
            ).status_code
            == 403
        )
        doc_list = client.get("/api/v1/documents", headers=_auth(uid, email, tid))
        assert doc_list.status_code == 200
        assert all(it["document"]["id"] != att_doc_id for it in doc_list.json()["items"])
        me_dash = client.get("/api/v1/dashboard/me", headers=_auth(uid, email, tid))
        assert me_dash.status_code == 200
        assert all(it["document"]["id"] != att_doc_id for it in me_dash.json()["recent_documents"])

    # P1-3: Store-wide document receipt leakage to department-scoped store_manager
    store_doc = client.post(
        "/api/v1/documents",
        headers=_auth(ids["owner"], "owner@dragon.es", tid),
        data={
            "category": "other",
            "title": "Normas Generales Tienda",
            "store_id": str(ids["store_mad"]),
            "requires_signature": "true",
        },
        files={"file": ("normas.pdf", PDF_BYTES, "application/pdf")},
    )
    assert store_doc.status_code == 201
    store_doc_id = store_doc.json()["id"]
    assert (
        client.post(
            f"/api/v1/documents/{store_doc_id}/receipts",
            headers=_auth(ids["emp_cocina"], "cocina@dragon.es", tid),
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/v1/documents/{store_doc_id}/receipts",
            headers=_auth(ids["emp_sala"], "sala@dragon.es", tid),
        ).status_code
        == 201
    )
    mgr_detail = client.get(
        f"/api/v1/documents/{store_doc_id}",
        headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
    )
    assert mgr_detail.status_code == 200
    assert len(mgr_detail.json()["receipts"]) == 1
    assert mgr_detail.json()["receipts"][0]["employee_id"] == str(ids["emp_cocina_id"])
    mgr_list = client.get(
        "/api/v1/documents", headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid)
    )
    matched_item = next(
        it for it in mgr_list.json()["items"] if it["document"]["id"] == store_doc_id
    )
    assert matched_item["receipt_count"] == 1

    # P1-4: XLSX/CSV hardening (UTF-16 XXE, huge col, >128 entries, bad shared-string) -> 422
    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    # UTF-16 XXE
    utf16_xxe = io.BytesIO()
    with zipfile.ZipFile(utf16_xxe, "w") as zf:
        utf16_payload = (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<!DOCTYPE foo [<!ENTITY xxe "1">]><worksheet/>'
        ).encode("utf-16")
        zf.writestr("xl/worksheets/sheet1.xml", utf16_payload)
    assert (
        client.post(
            "/api/v1/employees/import",
            headers=_auth(ids["owner"], "owner@dragon.es", tid),
            files={"file": ("utf16.xlsx", utf16_xxe.getvalue(), xlsx_mime)},
        ).status_code
        == 422
    )

    # Huge column ref AAAAAA1
    huge_col_buf = io.BytesIO()
    with zipfile.ZipFile(huge_col_buf, "w") as zf:
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            b'<?xml version="1.0"?><worksheet '
            b'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            b'<sheetData><row r="1"><c r="AAAAAA1" t="inlineStr">'
            b"<is><t>x</t></is></c></row></sheetData></worksheet>",
        )
    assert (
        client.post(
            "/api/v1/employees/import",
            headers=_auth(ids["owner"], "owner@dragon.es", tid),
            files={"file": ("hugecol.xlsx", huge_col_buf.getvalue(), xlsx_mime)},
        ).status_code
        == 422
    )

    # >128 ZIP entries
    many_entries_buf = io.BytesIO()
    with zipfile.ZipFile(many_entries_buf, "w") as zf:
        for i in range(130):
            zf.writestr(f"xl/dummy_{i}.txt", b"x")
    assert (
        client.post(
            "/api/v1/employees/import",
            headers=_auth(ids["owner"], "owner@dragon.es", tid),
            files={"file": ("many.xlsx", many_entries_buf.getvalue(), xlsx_mime)},
        ).status_code
        == 422
    )

    # Invalid shared-string index ("notanint")
    bad_ss_buf = io.BytesIO()
    with zipfile.ZipFile(bad_ss_buf, "w") as zf:
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            b'<?xml version="1.0"?><worksheet '
            b'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            b'<sheetData><row r="1"><c r="A1" t="s"><v>notanint</v></c>'
            b"</row></sheetData></worksheet>",
        )
    assert (
        client.post(
            "/api/v1/employees/import",
            headers=_auth(ids["owner"], "owner@dragon.es", tid),
            files={"file": ("badss.xlsx", bad_ss_buf.getvalue(), xlsx_mime)},
        ).status_code
        == 422
    )

    # Oversized CSV field (>128 KiB)
    huge_csv = ("employee_code,full_name\nE99," + ("A" * 140000)).encode("utf-8")
    assert (
        client.post(
            "/api/v1/employees/import",
            headers=_auth(ids["owner"], "owner@dragon.es", tid),
            files={"file": ("huge.csv", huge_csv, "text/csv")},
        ).status_code
        == 422
    )

    # Excel serial date & float-formatted int ("30.0") succeed
    serial_xlsx = generate_xlsx(
        "Employees",
        [
            "employee_code",
            "full_name",
            "preferred_name",
            "store_code",
            "department",
            "email",
            "phone",
            "birth_date",
            "contract_type",
            "work_schedule_type",
            "contracted_weekly_hours",
            "annual_vacation_days",
            "hire_date",
            "nie_dni",
            "iban",
            "naf",
        ],
        [
            [
                "IMP99",
                "Excel Serial",
                "Excel",
                "MAD-01",
                "Cocina",
                "excel@dragon.es",
                "",
                "34000",
                "indefinido",
                "full_time",
                "40.00",
                "30.0",
                "46037",
                "",
                "",
                "",
            ]
        ],
    )
    serial_res = client.post(
        "/api/v1/employees/import?dry_run=false",
        headers=_auth(ids["owner"], "owner@dragon.es", tid),
        files={"file": ("serial.xlsx", serial_xlsx, xlsx_mime)},
    )
    assert serial_res.status_code == 200
    assert serial_res.json()["committed"] is True

    # P1-5: Shift scheduling department_scope enforcement
    # Owner creates a draft shift for emp_sala
    sala_shift = client.post(
        "/api/v1/shifts",
        headers=_auth(ids["owner"], "owner@dragon.es", tid),
        json={
            "employee_id": str(ids["emp_sala_id"]),
            "start_at_utc": "2026-11-30T09:00:00Z",
            "end_at_utc": "2026-11-30T17:00:00Z",
            "planned_break_minutes": 30,
            "status": "draft",
        },
    )
    assert sala_shift.status_code == 201
    sala_shift_id = sala_shift.json()["shift"]["id"]

    # mgr_cocina cannot create, update, or cancel shift for emp_sala (403)
    assert (
        client.post(
            "/api/v1/shifts",
            headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
            json={
                "employee_id": str(ids["emp_sala_id"]),
                "start_at_utc": "2026-12-01T09:00:00Z",
                "end_at_utc": "2026-12-01T17:00:00Z",
                "planned_break_minutes": 30,
                "status": "draft",
            },
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/shifts/{sala_shift_id}",
            headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
            json={"notes": "hijack"},
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/shifts/{sala_shift_id}",
            headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
        ).status_code
        == 403
    )
    # Weekly schedule for mgr_cocina excludes emp_sala
    wk = client.get(
        f"/api/v1/shifts/weekly?store_id={ids['store_mad']}&week_start=2026-11-30",
        headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
    )
    assert wk.status_code == 200
    assert all(r["employee_id"] != str(ids["emp_sala_id"]) for r in wk.json()["rows"])
    # Publish by mgr_cocina does NOT publish emp_sala's draft shift
    pub = client.post(
        "/api/v1/shifts/publish-week",
        headers=_auth(ids["mgr_cocina"], "mgr@dragon.es", tid),
        json={"store_id": str(ids["store_mad"]), "week_start": "2026-11-30"},
    )
    assert pub.status_code == 200
    assert all(s["id"] != sala_shift_id for s in pub.json()["shifts"])

    # P2 regression: Infinity / 1e999999 in CSV/XLSX dates & vacation days -> structured row errors
    inf_csv = (
        b"employee_code,full_name,store_code,birth_date,contract_type,"
        b"work_schedule_type,contracted_weekly_hours,annual_vacation_days,hire_date\n"
        b"E98,Inf Test,MAD-01,1e999999,indefinido,full_time,40.00,Infinity,Infinity\n"
    )
    inf_res = client.post(
        "/api/v1/employees/import?dry_run=false",
        headers=_auth(ids["owner"], "owner@dragon.es", tid),
        files={"file": ("inf.csv", inf_csv, "text/csv")},
    )
    assert inf_res.status_code == 200
    inf_json = inf_res.json()
    assert inf_json["committed"] is False
    assert inf_json["error_rows"] == 1
    assert {e["field"] for e in inf_json["errors"]} == {
        "birth_date",
        "annual_vacation_days",
        "hire_date",
    }
