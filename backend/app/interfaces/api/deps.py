"""FastAPI dependencies for Hybrid Auth (Supabase JWT identity + live DB RBAC principal)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.documents import DocumentStoragePort
from app.domain.employees import PiiCipher
from app.domain.payroll import AttendancePdfRendererPort
from app.domain.repositories import (
    AttendanceRepository,
    DocumentRepository,
    EmployeeRepository,
    LeaveRepository,
    MembershipRepository,
    PayrollClosingRepository,
    ShiftRepository,
    StoreRepository,
    TenantRepository,
)
from app.domain.security import (
    AuthenticatedPrincipal,
    ForbiddenScopeError,
    TokenVerificationError,
    VerifiedIdentity,
)
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.database import get_db_session
from app.infrastructure.document_storage import (
    LocalDocumentStorageAdapter,
    SupabaseDocumentStorageAdapter,
)
from app.infrastructure.pdf_generator import PurePythonAttendancePdfRenderer
from app.infrastructure.pii_crypto import AesGcmPiiCipher
from app.infrastructure.repositories import (
    SqlAlchemyAttendanceRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyEmployeeRepository,
    SqlAlchemyLeaveRepository,
    SqlAlchemyMembershipRepository,
    SqlAlchemyPayrollClosingRepository,
    SqlAlchemyShiftRepository,
    SqlAlchemyStoreRepository,
    SqlAlchemyTenantRepository,
)
from app.infrastructure.supabase_jwt import SupabaseJWTVerifier

bearer_scheme = HTTPBearer(auto_error=False)
CommitFn = Callable[[], Awaitable[None]]


def get_app_settings(request: Request) -> Settings:
    app_settings = getattr(request.app.state, "settings", None)
    if isinstance(app_settings, Settings):
        return app_settings
    return get_settings()


@lru_cache(maxsize=4)
def _build_jwt_verifier(
    supabase_url: str,
    audience: str,
    jwt_secret: str,
) -> SupabaseJWTVerifier:
    return SupabaseJWTVerifier(
        supabase_url=supabase_url,
        audience=audience,
        jwt_secret=jwt_secret,
    )


def get_jwt_verifier(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> SupabaseJWTVerifier:
    return _build_jwt_verifier(
        settings.supabase_url,
        settings.supabase_jwt_audience,
        settings.supabase_jwt_secret,
    )


def get_tenant_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TenantRepository:
    return SqlAlchemyTenantRepository(session)


def get_store_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StoreRepository:
    return SqlAlchemyStoreRepository(session)


def get_membership_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MembershipRepository:
    return SqlAlchemyMembershipRepository(session)


def get_employee_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> EmployeeRepository:
    return SqlAlchemyEmployeeRepository(session)


def get_attendance_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AttendanceRepository:
    return SqlAlchemyAttendanceRepository(session)


def get_shift_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ShiftRepository:
    return SqlAlchemyShiftRepository(session)


def get_leave_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> LeaveRepository:
    return SqlAlchemyLeaveRepository(session)


def get_document_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DocumentRepository:
    return SqlAlchemyDocumentRepository(session)


def get_payroll_closing_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PayrollClosingRepository:
    return SqlAlchemyPayrollClosingRepository(session)


def get_pdf_renderer() -> AttendancePdfRendererPort:
    return PurePythonAttendancePdfRenderer()


@lru_cache(maxsize=4)
def _build_document_storage(
    storage_backend: str,
    supabase_url: str,
    supabase_service_role_key: str,
    supabase_storage_bucket: str,
    local_storage_dir: str,
    storage_signing_secret: str,
    public_api_base_url: str,
    api_v1_prefix: str,
) -> DocumentStoragePort:
    if storage_backend.strip().lower() == "supabase":
        return SupabaseDocumentStorageAdapter(
            supabase_url=supabase_url,
            service_role_key=supabase_service_role_key,
            bucket=supabase_storage_bucket,
        )
    return LocalDocumentStorageAdapter(
        base_dir=local_storage_dir,
        signing_secret=storage_signing_secret,
        public_api_base_url=public_api_base_url,
        api_v1_prefix=api_v1_prefix,
    )


def get_document_storage(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> DocumentStoragePort:
    return _build_document_storage(
        settings.storage_backend,
        settings.supabase_url,
        settings.supabase_service_role_key,
        settings.supabase_storage_bucket,
        settings.local_storage_dir,
        settings.storage_signing_secret,
        settings.public_api_base_url,
        settings.api_v1_prefix,
    )


@lru_cache(maxsize=4)
def _build_pii_cipher(pii_encryption_key: str) -> PiiCipher:
    return AesGcmPiiCipher(pii_encryption_key)


def get_pii_cipher(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> PiiCipher:
    return _build_pii_cipher(settings.pii_encryption_key)


def get_commit_fn(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CommitFn:
    async def _commit() -> None:
        await session.commit()

    return _commit


def get_utc_now() -> datetime:
    return datetime.now(UTC)


def get_verified_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    verifier: Annotated[SupabaseJWTVerifier, Depends(get_jwt_verifier)],
) -> VerifiedIdentity:
    """Validate Supabase Auth Bearer JWT and return user identity (no tenant bound yet)."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return verifier.verify_identity(credentials.credentials)
    except TokenVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_principal(
    identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
    membership_repo: Annotated[MembershipRepository, Depends(get_membership_repository)],
    x_tenant_id: Annotated[UUID | None, Header(alias="X-Tenant-Id")] = None,
) -> AuthenticatedPrincipal:
    """Resolve live DB-backed tenant membership and store scope for the authenticated user."""
    if x_tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required X-Tenant-Id header",
        )

    membership = await membership_repo.get_active_for_user_in_tenant(
        x_tenant_id,
        identity.user_id,
    )
    if membership is None:
        raise ForbiddenScopeError("Access denied for the requested tenant")

    return AuthenticatedPrincipal(
        user_id=identity.user_id,
        tenant_id=membership.tenant_id,
        role=membership.role,
        store_ids=membership.store_ids,
        email=identity.email or membership.email,
    )
