from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.application.onboarding import CreateTenantCommand, CreateTenantUseCase
from app.application.tenants import (
    GetCurrentTenantUseCase,
    UpdateCurrentTenantUseCase,
    UpdateTenantCommand,
)
from app.domain.organization import Tenant
from app.domain.repositories import MembershipRepository, TenantRepository
from app.domain.security import AuthenticatedPrincipal, VerifiedIdentity
from app.interfaces.api.deps import (
    CommitFn,
    get_commit_fn,
    get_current_principal,
    get_membership_repository,
    get_tenant_repository,
    get_verified_identity,
)

router = APIRouter(prefix="/tenants", tags=["tenants"])


class TenantCreateRequest(BaseModel):
    legal_name: str = Field(min_length=1, max_length=200)
    trade_name: str = Field(min_length=1, max_length=200)
    cif_nif: str = Field(min_length=6, max_length=32)
    default_timezone: str = Field(default="Europe/Madrid", max_length=64)
    default_locale: Literal["zh-CN", "es-ES"] = "zh-CN"
    owner_display_name: str = Field(default="", max_length=120)


class TenantUpdateRequest(BaseModel):
    legal_name: str = Field(min_length=1, max_length=200)
    trade_name: str = Field(min_length=1, max_length=200)
    cif_nif: str = Field(min_length=6, max_length=32)
    default_timezone: str = Field(min_length=1, max_length=64)
    default_locale: Literal["zh-CN", "es-ES"]


class TenantResponse(BaseModel):
    id: UUID
    legal_name: str
    trade_name: str
    cif_nif: str
    default_timezone: str
    default_locale: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, tenant: Tenant) -> TenantResponse:
        return cls(
            id=tenant.id,
            legal_name=tenant.legal_name,
            trade_name=tenant.trade_name,
            cif_nif=tenant.cif_nif,
            default_timezone=tenant.default_timezone,
            default_locale=tenant.default_locale,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )


@router.post(
    "",
    operation_id="createTenant",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Onboard a new company tenant and assign current user as owner",
)
async def create_tenant(
    payload: TenantCreateRequest,
    identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
    tenant_repo: Annotated[TenantRepository, Depends(get_tenant_repository)],
    membership_repo: Annotated[MembershipRepository, Depends(get_membership_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
) -> TenantResponse:
    use_case = CreateTenantUseCase(tenant_repo, membership_repo)
    result = await use_case.execute(
        identity,
        CreateTenantCommand(
            legal_name=payload.legal_name,
            trade_name=payload.trade_name,
            cif_nif=payload.cif_nif,
            default_timezone=payload.default_timezone,
            default_locale=payload.default_locale,
            owner_display_name=payload.owner_display_name,
        ),
    )
    await commit()
    return TenantResponse.from_domain(result.tenant)


@router.get(
    "/current",
    operation_id="getCurrentTenant",
    response_model=TenantResponse,
    summary="Get current tenant company profile",
)
async def get_current_tenant(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    tenant_repo: Annotated[TenantRepository, Depends(get_tenant_repository)],
) -> TenantResponse:
    use_case = GetCurrentTenantUseCase(tenant_repo)
    tenant = await use_case.execute(principal)
    return TenantResponse.from_domain(tenant)


@router.patch(
    "/current",
    operation_id="updateCurrentTenant",
    response_model=TenantResponse,
    summary="Update current tenant company profile (owner only)",
)
async def update_current_tenant(
    payload: TenantUpdateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    tenant_repo: Annotated[TenantRepository, Depends(get_tenant_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
) -> TenantResponse:
    use_case = UpdateCurrentTenantUseCase(tenant_repo)
    tenant = await use_case.execute(
        principal,
        UpdateTenantCommand(
            legal_name=payload.legal_name,
            trade_name=payload.trade_name,
            cif_nif=payload.cif_nif,
            default_timezone=payload.default_timezone,
            default_locale=payload.default_locale,
        ),
    )
    await commit()
    return TenantResponse.from_domain(tenant)
