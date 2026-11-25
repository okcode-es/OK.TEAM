"""FastAPI dependencies for Hybrid Auth (Supabase JWT identity + live DB RBAC principal)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.employees import PiiCipher
from app.domain.repositories import (
    EmployeeRepository,
    MembershipRepository,
    StoreRepository,
    TenantRepository,
)
from app.domain.security import (
    AuthenticatedPrincipal,
    ForbiddenScopeError,
    TokenVerificationError,
    VerifiedIdentity,
)
from app.infrastructure.config import get_settings
from app.infrastructure.database import get_db_session
from app.infrastructure.pii_crypto import AesGcmPiiCipher
from app.infrastructure.repositories import (
    SqlAlchemyEmployeeRepository,
    SqlAlchemyMembershipRepository,
    SqlAlchemyStoreRepository,
    SqlAlchemyTenantRepository,
)
from app.infrastructure.supabase_jwt import SupabaseJWTVerifier

bearer_scheme = HTTPBearer(auto_error=False)
CommitFn = Callable[[], Awaitable[None]]


@lru_cache(maxsize=1)
def get_jwt_verifier() -> SupabaseJWTVerifier:
    settings = get_settings()
    return SupabaseJWTVerifier(
        supabase_url=settings.supabase_url,
        audience=settings.supabase_jwt_audience,
        jwt_secret=settings.supabase_jwt_secret,
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


@lru_cache(maxsize=1)
def get_pii_cipher() -> PiiCipher:
    settings = get_settings()
    return AesGcmPiiCipher(settings.pii_encryption_key)


def get_commit_fn(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CommitFn:
    async def _commit() -> None:
        await session.commit()

    return _commit


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
