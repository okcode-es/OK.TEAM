"""Unit & API integration tests for structured backend error handling."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.domain.employees import (
    ContractType,
    Employee,
    EmploymentStatus,
    FrozenRecordError,
    RetentionLockError,
    WorkScheduleType,
)
from app.domain.organization import (
    ConflictError,
    EntityNotFoundError,
    LastOwnerProtectionError,
    Membership,
    Store,
    Tenant,
)
from app.domain.security import (
    ForbiddenScopeError,
    MembershipStatus,
    TokenVerificationError,
    UserRole,
)
from app.infrastructure.config import Settings
from app.infrastructure.document_storage import SignedTokenError, StorageBackendError
from app.infrastructure.pii_crypto import PiiEncryptionError
from app.infrastructure.supabase_jwt import SupabaseJWTVerifier, sign_dev_hs256_token
from app.interfaces.api.deps import (
    get_attendance_repository,
    get_break_policy_repository,
    get_commit_fn,
    get_employee_repository,
    get_jwt_verifier,
    get_membership_repository,
    get_payroll_closing_repository,
    get_store_repository,
    get_tenant_repository,
    get_utc_now,
)
from app.interfaces.api.errors import (
    _classify_conflict_error,
    _classify_validation_error,
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


def _build_test_client() -> tuple[TestClient, dict[str, object]]:
    """Build a FastAPI TestClient with in-memory repos and synthetic error test routes."""
    now = datetime(2026, 5, 4, 8, 0, 0, tzinfo=UTC)
    tenant_id = uuid4()
    store_id = uuid4()
    emp_uid = uuid4()
    emp_id = uuid4()

    org = InMemoryOrgRepos()
    org.tenants[tenant_id] = Tenant(
        id=tenant_id,
        legal_name="Error Test SL",
        trade_name="Error Test",
        cif_nif="B12345678",
        default_timezone="Europe/Madrid",
        default_locale="es-ES",
        created_at=now,
        updated_at=now,
    )
    org.stores[store_id] = Store(
        id=store_id,
        tenant_id=tenant_id,
        name="Madrid Sol",
        code="MAD-01",
        timezone="Europe/Madrid",
        address="Puerta del Sol 1",
        city="Madrid",
        postal_code="28013",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    membership_id = uuid4()
    org.memberships[membership_id] = Membership(
        id=membership_id,
        tenant_id=tenant_id,
        user_id=emp_uid,
        email="emp@okteam.es",
        display_name="Ana García",
        role=UserRole.EMPLOYEE,
        status=MembershipStatus.ACTIVE,
        store_ids=frozenset({store_id}),
        created_at=now,
        updated_at=now,
    )

    emp_repo = InMemoryEmployeeRepo()
    emp_repo.employees[emp_id] = Employee(
        id=emp_id,
        tenant_id=tenant_id,
        primary_store_id=store_id,
        user_id=emp_uid,
        employee_code="EMP-ERR-1",
        full_name="Ana García",
        preferred_name="Ana",
        email="emp@okteam.es",
        phone=None,
        birth_date=date(1995, 3, 15),
        hire_date=date(2024, 1, 10),
        termination_date=None,
        contract_type=ContractType.INDEFINIDO,
        work_schedule_type=WorkScheduleType.FULL_TIME,
        contracted_weekly_hours=Decimal("40.00"),
        annual_vacation_days=30,
        nie_dni_enc=None,
        nie_dni_masked=None,
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

    attendance_repo = InMemoryAttendanceRepo()
    clock_state = {"now": now}

    class _OpenPayrollRepo:
        async def is_store_month_closed(
            self, _tenant_id: object, _store_id: object, _period_month: str
        ) -> bool:
            return False

    class _EmptyBreakPolicyRepo:
        async def list_by_tenant(self, _tenant_id: object) -> list[object]:
            return []

        async def list_closed_months_for_store(
            self, _tenant_id: object, _store_id: object, _period_months: object
        ) -> set[str]:
            return set()

    async def _noop_commit() -> None:
        return None

    settings = Settings(
        app_env="local",
        enable_dev_auth=True,
        dev_jwt_secret=DEV_SECRET,
        supabase_jwt_audience="authenticated",
    )
    app = create_app(settings)
    verifier = SupabaseJWTVerifier(
        supabase_url="https://example.supabase.co",
        audience="authenticated",
        jwt_secret=DEV_SECRET,
    )

    app.dependency_overrides[get_jwt_verifier] = lambda: verifier
    app.dependency_overrides[get_tenant_repository] = lambda: org
    app.dependency_overrides[get_store_repository] = lambda: InMemoryStoreRepo(org)
    app.dependency_overrides[get_membership_repository] = lambda: InMemoryMembershipRepo(org)
    app.dependency_overrides[get_employee_repository] = lambda: emp_repo
    app.dependency_overrides[get_attendance_repository] = lambda: attendance_repo
    app.dependency_overrides[get_payroll_closing_repository] = lambda: _OpenPayrollRepo()
    app.dependency_overrides[get_break_policy_repository] = lambda: _EmptyBreakPolicyRepo()
    app.dependency_overrides[get_commit_fn] = lambda: _noop_commit
    app.dependency_overrides[get_utc_now] = lambda: clock_state["now"]

    # Synthetic probe router for exception handler verification
    probe = APIRouter(prefix="/api/v1/_error_probe")

    class _Payload(BaseModel):
        count: int

    @probe.post("/validate-body")
    async def _validate_body(body: _Payload) -> dict[str, int]:
        return {"count": body.count}

    @probe.get("/boom")
    async def _boom() -> None:
        raise RuntimeError("SELECT * FROM secrets WHERE key='super-secret-sql-leak'")

    @probe.get("/forbidden")
    async def _forbidden() -> None:
        raise ForbiddenScopeError("Store access denied")

    @probe.get("/not-found")
    async def _not_found() -> None:
        raise EntityNotFoundError("Employee not found")

    @probe.get("/conflict-dup")
    async def _conflict_dup() -> None:
        raise ConflictError("Employee code already exists")

    @probe.get("/conflict-generic")
    async def _conflict_generic() -> None:
        raise ConflictError("State mismatch")

    @probe.get("/token-error")
    async def _token_error() -> None:
        raise TokenVerificationError("Invalid token")

    @probe.get("/signed-error")
    async def _signed_error() -> None:
        raise SignedTokenError("Expired signature")

    @probe.get("/last-owner")
    async def _last_owner() -> None:
        raise LastOwnerProtectionError("Cannot remove last owner")

    @probe.get("/retention-lock")
    async def _retention_lock() -> None:
        raise RetentionLockError("Record under 4-year retention")

    @probe.get("/frozen")
    async def _frozen() -> None:
        raise FrozenRecordError("Record is frozen")

    @probe.get("/pii-error")
    async def _pii_error() -> None:
        raise PiiEncryptionError("AES-GCM key=0xDEADBEEF failed")

    @probe.get("/storage-error")
    async def _storage_error() -> None:
        raise StorageBackendError("S3 bucket secret-bucket-123 unreachable")

    @probe.get("/http-404")
    async def _http_404() -> None:
        raise HTTPException(status_code=404, detail="Custom missing")

    app.include_router(probe)

    token = sign_dev_hs256_token(user_id=emp_uid, email="emp@okteam.es", secret=DEV_SECRET)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": str(tenant_id),
    }
    return TestClient(app, raise_server_exceptions=False), {
        "headers": headers,
        "store_id": str(store_id),
        "clock_state": clock_state,
    }


def test_attendance_double_clock_in_returns_structured_422() -> None:
    client, ctx = _build_test_client()
    headers = ctx["headers"]
    assert isinstance(headers, dict)

    first = client.post(
        "/api/v1/attendance/clock",
        headers=headers,
        json={"event_type": "clock_in", "source": "web"},
    )
    assert first.status_code == 201, first.text

    second = client.post(
        "/api/v1/attendance/clock",
        headers=headers,
        json={"event_type": "clock_in", "source": "web"},
    )
    assert second.status_code == 422
    body = second.json()
    assert body["error_code"] == "ATTENDANCE_SHIFT_ALREADY_OPEN"
    assert isinstance(body["detail"], str) and body["detail"]


def test_attendance_clock_out_without_open_shift_returns_structured_422() -> None:
    client, ctx = _build_test_client()
    headers = ctx["headers"]
    assert isinstance(headers, dict)

    resp = client.post(
        "/api/v1/attendance/clock",
        headers=headers,
        json={"event_type": "clock_out", "source": "web"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error_code"] == "ATTENDANCE_NO_OPEN_SHIFT"
    assert isinstance(body["detail"], str) and body["detail"]


def test_request_validation_error_returns_422_without_leaking_internals() -> None:
    client, _ = _build_test_client()

    resp = client.post(
        "/api/v1/_error_probe/validate-body",
        json={"count": "not-an-integer"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body == {
        "error_code": "REQUEST_VALIDATION_ERROR",
        "detail": "Invalid request parameters",
    }


def test_unhandled_exception_returns_500_without_leaking_details() -> None:
    client, _ = _build_test_client()

    resp = client.get("/api/v1/_error_probe/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body == {
        "error_code": "INTERNAL_ERROR",
        "detail": "Internal server error",
    }
    assert "super-secret-sql-leak" not in resp.text
    assert "RuntimeError" not in resp.text
    assert "Traceback" not in resp.text


def test_domain_and_http_exception_handlers_return_expected_codes() -> None:
    client, _ = _build_test_client()

    r_forbidden = client.get("/api/v1/_error_probe/forbidden")
    assert r_forbidden.status_code == 403
    assert r_forbidden.json() == {"error_code": "FORBIDDEN", "detail": "Store access denied"}

    r_not_found = client.get("/api/v1/_error_probe/not-found")
    assert r_not_found.status_code == 404
    assert r_not_found.json() == {"error_code": "NOT_FOUND", "detail": "Employee not found"}

    r_dup = client.get("/api/v1/_error_probe/conflict-dup")
    assert r_dup.status_code == 409
    assert r_dup.json() == {
        "error_code": "DUPLICATE_RECORD",
        "detail": "Employee code already exists",
    }

    r_conflict = client.get("/api/v1/_error_probe/conflict-generic")
    assert r_conflict.status_code == 409
    assert r_conflict.json() == {"error_code": "CONFLICT", "detail": "State mismatch"}

    r_token = client.get("/api/v1/_error_probe/token-error")
    assert r_token.status_code == 401
    assert r_token.headers.get("www-authenticate") == "Bearer"
    assert r_token.json() == {"error_code": "AUTH_INVALID_TOKEN", "detail": "Invalid token"}

    r_signed = client.get("/api/v1/_error_probe/signed-error")
    assert r_signed.status_code == 403
    assert r_signed.json() == {"error_code": "SIGNED_URL_INVALID", "detail": "Expired signature"}

    r_owner = client.get("/api/v1/_error_probe/last-owner")
    assert r_owner.status_code == 422
    assert r_owner.json() == {
        "error_code": "LAST_OWNER_PROTECTED",
        "detail": "Cannot remove last owner",
    }

    r_retention = client.get("/api/v1/_error_probe/retention-lock")
    assert r_retention.status_code == 422
    assert r_retention.json() == {
        "error_code": "RETENTION_LOCK",
        "detail": "Record under 4-year retention",
    }

    r_frozen = client.get("/api/v1/_error_probe/frozen")
    assert r_frozen.status_code == 422
    assert r_frozen.json() == {"error_code": "FROZEN_RECORD", "detail": "Record is frozen"}

    r_pii = client.get("/api/v1/_error_probe/pii-error")
    assert r_pii.status_code == 500
    assert r_pii.json() == {
        "error_code": "PII_SERVICE_ERROR",
        "detail": "PII encryption service error",
    }
    assert "DEADBEEF" not in r_pii.text

    r_storage = client.get("/api/v1/_error_probe/storage-error")
    assert r_storage.status_code == 500
    assert r_storage.json() == {
        "error_code": "STORAGE_SERVICE_ERROR",
        "detail": "Document storage service error",
    }
    assert "secret-bucket-123" not in r_storage.text

    r_http_404 = client.get("/api/v1/_error_probe/http-404")
    assert r_http_404.status_code == 404
    assert r_http_404.json() == {"error_code": "HTTP_404", "detail": "Custom missing"}

    r_unknown_route = client.get("/api/v1/does-not-exist-route")
    assert r_unknown_route.status_code == 404
    assert r_unknown_route.json()["error_code"] == "HTTP_404"


def test_error_classifiers_cover_all_domain_categories() -> None:
    assert (
        _classify_validation_error("Cannot clock_in while shift is already open")
        == "ATTENDANCE_SHIFT_ALREADY_OPEN"
    )
    assert (
        _classify_validation_error("Cannot clock_out without an open shift")
        == "ATTENDANCE_NO_OPEN_SHIFT"
    )
    assert (
        _classify_validation_error("Cannot start break: break is already open")
        == "ATTENDANCE_BREAK_ALREADY_OPEN"
    )
    assert (
        _classify_validation_error("Cannot end break: no open break") == "ATTENDANCE_NO_OPEN_BREAK"
    )
    assert (
        _classify_validation_error("Cannot clock_out while a break is still open")
        == "ATTENDANCE_BREAK_NOT_ENDED"
    )
    assert (
        _classify_validation_error("Event occurred_at_utc cannot be in the future")
        == "ATTENDANCE_CHRONOLOGY_ERROR"
    )
    assert (
        _classify_validation_error("Cannot modify closed payroll month")
        == "PAYROLL_MONTH_CLOSED"
    )
    assert _classify_validation_error("Overlaps existing leave request") == "LEAVE_OVERLAP"
    assert (
        _classify_validation_error("Insufficient vacation balance remaining")
        == "LEAVE_INSUFFICIENT_BALANCE"
    )
    assert _classify_validation_error("Shift overlap detected") == "SHIFT_CONFLICT"
    assert _classify_validation_error("Unsupported file mime_type") == "FILE_INVALID_TYPE"
    assert _classify_validation_error("File exceeds maximum size") == "FILE_TOO_LARGE"
    assert _classify_validation_error("Invalid xlsx workbook structure") == "IMPORT_INVALID_FILE"
    assert _classify_validation_error("XML DTD is not allowed") == "IMPORT_INVALID_FILE"
    assert _classify_validation_error("Invalid NIE/DNI checksum") == "INVALID_NIE_DNI"
    assert _classify_validation_error("Invalid IBAN format") == "INVALID_IBAN"
    assert _classify_validation_error("Invalid NAF number") == "INVALID_NAF"
    assert _classify_validation_error("Some other domain rule") == "VALIDATION_ERROR"

    assert _classify_conflict_error("Closed payroll month") == "PAYROLL_MONTH_CLOSED"
    assert _classify_conflict_error("Month closing in progress") == "PAYROLL_CONFLICT"
    assert _classify_conflict_error("Leave overlaps existing") == "LEAVE_OVERLAP"
    assert _classify_conflict_error("Shift conflict with assigned shift") == "SHIFT_CONFLICT"
    assert _classify_conflict_error("Record already signed") == "DUPLICATE_RECORD"
    assert _classify_conflict_error("Generic conflict") == "CONFLICT"
