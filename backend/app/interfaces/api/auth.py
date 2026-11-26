from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.application.onboarding import GetMyProfileUseCase
from app.domain.repositories import MembershipRepository
from app.domain.security import MembershipStatus, UserRole, VerifiedIdentity
from app.infrastructure.config import Settings
from app.interfaces.api.deps import (
    get_app_settings,
    get_membership_repository,
    get_verified_identity,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class TenantMembershipSummaryResponse(BaseModel):
    tenant_id: UUID
    tenant_legal_name: str
    tenant_trade_name: str
    default_timezone: str
    default_locale: str
    role: UserRole
    status: MembershipStatus
    display_name: str
    store_ids: list[UUID]
    department_scope: str | None = None


class AuthMeResponse(BaseModel):
    user_id: UUID
    email: str | None
    dev_auth_enabled: bool
    tenants: list[TenantMembershipSummaryResponse]


@router.get(
    "/me",
    operation_id="getAuthMe",
    response_model=AuthMeResponse,
    summary="Get authenticated user identity and active tenant memberships",
)
async def get_auth_me(
    identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
    membership_repo: Annotated[MembershipRepository, Depends(get_membership_repository)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AuthMeResponse:
    use_case = GetMyProfileUseCase(membership_repo)
    summaries = await use_case.execute(identity)
    return AuthMeResponse(
        user_id=identity.user_id,
        email=identity.email,
        dev_auth_enabled=settings.is_dev_auth_active,
        tenants=[
            TenantMembershipSummaryResponse(
                tenant_id=item.tenant_id,
                tenant_legal_name=item.tenant_legal_name,
                tenant_trade_name=item.tenant_trade_name,
                default_timezone=item.default_timezone,
                default_locale=item.default_locale,
                role=item.role,
                status=item.status,
                display_name=item.display_name,
                store_ids=sorted(item.store_ids),
                department_scope=item.department_scope,
            )
            for item in summaries
        ],
    )
