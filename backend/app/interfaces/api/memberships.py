from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from app.application.memberships import (
    ArchiveMembershipUseCase,
    CreateMembershipCommand,
    CreateMembershipUseCase,
    ListMembershipsUseCase,
    UpdateMembershipCommand,
    UpdateMembershipUseCase,
)
from app.domain.organization import Membership
from app.domain.repositories import EmployeeRepository, MembershipRepository, StoreRepository
from app.domain.security import AuthenticatedPrincipal, MembershipStatus, UserRole
from app.interfaces.api.deps import (
    CommitFn,
    get_commit_fn,
    get_current_principal,
    get_employee_repository,
    get_membership_repository,
    get_store_repository,
)

router = APIRouter(prefix="/memberships", tags=["memberships"])


class MembershipCreateRequest(BaseModel):
    user_id: UUID
    email: str = Field(min_length=3, max_length=255)
    display_name: str = Field(min_length=1, max_length=120)
    role: UserRole
    status: MembershipStatus = MembershipStatus.ACTIVE
    store_ids: list[UUID] = Field(default_factory=list)
    department_scope: str | None = Field(default=None, max_length=100)


class MembershipUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    role: UserRole
    status: MembershipStatus
    store_ids: list[UUID] = Field(default_factory=list)
    department_scope: str | None = Field(default=None, max_length=100)


class MembershipResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    user_id: UUID
    email: str
    display_name: str
    role: UserRole
    status: MembershipStatus
    store_ids: list[UUID]
    department_scope: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, membership: Membership) -> MembershipResponse:
        return cls(
            id=membership.id,
            tenant_id=membership.tenant_id,
            user_id=membership.user_id,
            email=membership.email,
            display_name=membership.display_name,
            role=membership.role,
            status=membership.status,
            store_ids=sorted(membership.store_ids),
            department_scope=membership.department_scope,
            created_at=membership.created_at,
            updated_at=membership.updated_at,
        )


class MembershipListResponse(BaseModel):
    items: list[MembershipResponse]
    total: int


@router.get(
    "",
    operation_id="listMemberships",
    response_model=MembershipListResponse,
    summary="List tenant memberships scoped by caller role and authorized stores",
)
async def list_memberships(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    membership_repo: Annotated[MembershipRepository, Depends(get_membership_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
) -> MembershipListResponse:
    use_case = ListMembershipsUseCase(membership_repo, employee_repo)
    items = await use_case.execute(principal)
    return MembershipListResponse(
        items=[MembershipResponse.from_domain(item) for item in items],
        total=len(items),
    )


@router.post(
    "",
    operation_id="createMembership",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a member with role and store scope (owner / hr only)",
)
async def create_membership(
    payload: MembershipCreateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    membership_repo: Annotated[MembershipRepository, Depends(get_membership_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
) -> MembershipResponse:
    use_case = CreateMembershipUseCase(membership_repo, store_repo)
    created = await use_case.execute(
        principal,
        CreateMembershipCommand(
            user_id=payload.user_id,
            email=payload.email,
            display_name=payload.display_name,
            role=payload.role,
            status=payload.status,
            store_ids=frozenset(payload.store_ids),
            department_scope=payload.department_scope,
        ),
    )
    await commit()
    return MembershipResponse.from_domain(created)


@router.patch(
    "/{membership_id}",
    operation_id="updateMembership",
    response_model=MembershipResponse,
    summary="Update member role, status, or assigned stores (owner / hr only)",
)
async def update_membership(
    membership_id: UUID,
    payload: MembershipUpdateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    membership_repo: Annotated[MembershipRepository, Depends(get_membership_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
) -> MembershipResponse:
    use_case = UpdateMembershipUseCase(membership_repo, store_repo)
    updated = await use_case.execute(
        principal,
        membership_id,
        UpdateMembershipCommand(
            display_name=payload.display_name,
            role=payload.role,
            status=payload.status,
            store_ids=frozenset(payload.store_ids),
            department_scope=payload.department_scope,
        ),
    )
    await commit()
    return MembershipResponse.from_domain(updated)


@router.delete(
    "/{membership_id}",
    operation_id="archiveMembership",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete (disable) a member — never physically deleted (owner / hr only)",
)
async def archive_membership(
    membership_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    membership_repo: Annotated[MembershipRepository, Depends(get_membership_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
) -> Response:
    use_case = ArchiveMembershipUseCase(membership_repo)
    await use_case.execute(principal, membership_id)
    await commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
