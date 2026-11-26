"""FastAPI router for 3-tier custom break policies."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.application.break_policies import (
    DeleteBreakPolicyUseCase,
    GetEffectiveBreakPolicyUseCase,
    ListBreakPoliciesUseCase,
    UpsertBreakPolicyCommand,
    UpsertEmployeeBreakPolicyUseCase,
    UpsertStoreBreakPolicyUseCase,
    UpsertTenantBreakPolicyUseCase,
)
from app.domain.break_policies import (
    BreakMode,
    BreakPolicy,
    BreakScopeType,
    BreakTrackingMode,
    BreakWindow,
    EffectiveBreakPolicy,
)
from app.domain.repositories import BreakPolicyRepository, EmployeeRepository, StoreRepository
from app.domain.security import AuthenticatedPrincipal
from app.interfaces.api.deps import (
    CommitFn,
    get_break_policy_repository,
    get_commit_fn,
    get_current_principal,
    get_employee_repository,
    get_store_repository,
    get_utc_now,
)

router = APIRouter(prefix="/break-policies", tags=["break-policies"])


class BreakWindowSchema(BaseModel):
    start: str
    end: str


class UpsertBreakPolicyRequest(BaseModel):
    break_mode: BreakMode
    duration_minutes: int | None = Field(default=None, ge=5, le=240)
    windows: list[BreakWindowSchema] = Field(default_factory=list, max_length=4)
    is_paid: bool = False
    tracking_mode: BreakTrackingMode = BreakTrackingMode.MANUAL_PUNCH
    min_shift_minutes_for_auto_deduct: int = Field(default=360, ge=60, le=720)
    auto_end_manual_break: bool = True
    is_active: bool = True


class BreakPolicyResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    scope_type: BreakScopeType
    store_id: UUID | None
    employee_id: UUID | None
    break_mode: BreakMode
    duration_minutes: int | None
    windows: list[BreakWindowSchema]
    is_paid: bool
    tracking_mode: BreakTrackingMode
    min_shift_minutes_for_auto_deduct: int
    auto_end_manual_break: bool
    is_active: bool
    updated_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class BreakPolicyListResponse(BaseModel):
    items: list[BreakPolicyResponse]


class EffectiveBreakPolicyResponse(BaseModel):
    policy_id: UUID | None
    inherited_from: Literal["employee", "store", "tenant", "legacy_default", "none"]
    break_mode: BreakMode
    duration_minutes: int | None
    windows: list[BreakWindowSchema]
    is_paid: bool
    tracking_mode: BreakTrackingMode
    min_shift_minutes_for_auto_deduct: int
    auto_end_manual_break: bool
    effective_duration_minutes: int | None = None


def _to_policy_response(p: BreakPolicy) -> BreakPolicyResponse:
    return BreakPolicyResponse(
        id=p.id,
        tenant_id=p.tenant_id,
        scope_type=p.scope_type,
        store_id=p.store_id,
        employee_id=p.employee_id,
        break_mode=p.break_mode,
        duration_minutes=p.duration_minutes,
        windows=[BreakWindowSchema(start=w.start, end=w.end) for w in p.windows],
        is_paid=p.is_paid,
        tracking_mode=p.tracking_mode,
        min_shift_minutes_for_auto_deduct=p.min_shift_minutes_for_auto_deduct,
        auto_end_manual_break=p.auto_end_manual_break,
        is_active=p.is_active,
        updated_by_user_id=p.updated_by_user_id,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def to_effective_policy_response(p: EffectiveBreakPolicy) -> EffectiveBreakPolicyResponse:
    return EffectiveBreakPolicyResponse(
        policy_id=p.policy_id,
        inherited_from=p.inherited_from,
        break_mode=p.break_mode,
        duration_minutes=p.duration_minutes,
        windows=[BreakWindowSchema(start=w.start, end=w.end) for w in p.windows],
        is_paid=p.is_paid,
        tracking_mode=p.tracking_mode,
        min_shift_minutes_for_auto_deduct=p.min_shift_minutes_for_auto_deduct,
        auto_end_manual_break=p.auto_end_manual_break,
        effective_duration_minutes=p.effective_duration_minutes,
    )


def _to_cmd(body: UpsertBreakPolicyRequest) -> UpsertBreakPolicyCommand:
    return UpsertBreakPolicyCommand(
        break_mode=body.break_mode,
        duration_minutes=body.duration_minutes,
        windows=tuple(BreakWindow(start=w.start, end=w.end) for w in body.windows),
        is_paid=body.is_paid,
        tracking_mode=body.tracking_mode,
        min_shift_minutes_for_auto_deduct=body.min_shift_minutes_for_auto_deduct,
        auto_end_manual_break=body.auto_end_manual_break,
        is_active=body.is_active,
    )


@router.get(
    "",
    response_model=BreakPolicyListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="list_break_policies",
)
async def list_break_policies(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
) -> BreakPolicyListResponse:
    items = await ListBreakPoliciesUseCase(policy_repo, employee_repo).execute(principal)
    return BreakPolicyListResponse(items=[_to_policy_response(p) for p in items])


@router.put(
    "/tenant",
    response_model=BreakPolicyResponse,
    status_code=status.HTTP_200_OK,
    operation_id="upsert_tenant_break_policy",
)
async def upsert_tenant_break_policy(
    body: UpsertBreakPolicyRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> BreakPolicyResponse:
    saved = await UpsertTenantBreakPolicyUseCase(policy_repo).execute(
        principal, _to_cmd(body), now_utc=now_utc
    )
    await commit()
    return _to_policy_response(saved)


@router.put(
    "/store/{store_id}",
    response_model=BreakPolicyResponse,
    status_code=status.HTTP_200_OK,
    operation_id="upsert_store_break_policy",
)
async def upsert_store_break_policy(
    store_id: UUID,
    body: UpsertBreakPolicyRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> BreakPolicyResponse:
    saved = await UpsertStoreBreakPolicyUseCase(policy_repo, store_repo).execute(
        principal, store_id, _to_cmd(body), now_utc=now_utc
    )
    await commit()
    return _to_policy_response(saved)


@router.put(
    "/employee/{employee_id}",
    response_model=BreakPolicyResponse,
    status_code=status.HTTP_200_OK,
    operation_id="upsert_employee_break_policy",
)
async def upsert_employee_break_policy(
    employee_id: UUID,
    body: UpsertBreakPolicyRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> BreakPolicyResponse:
    saved = await UpsertEmployeeBreakPolicyUseCase(policy_repo, employee_repo).execute(
        principal, employee_id, _to_cmd(body), now_utc=now_utc
    )
    await commit()
    return _to_policy_response(saved)


@router.delete(
    "/{policy_id}",
    response_model=BreakPolicyResponse,
    status_code=status.HTTP_200_OK,
    operation_id="delete_break_policy",
)
async def delete_break_policy(
    policy_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> BreakPolicyResponse:
    deleted = await DeleteBreakPolicyUseCase(policy_repo, employee_repo).execute(
        principal, policy_id, now_utc=now_utc
    )
    await commit()
    return _to_policy_response(deleted)


@router.get(
    "/effective/{employee_id}",
    response_model=EffectiveBreakPolicyResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_effective_break_policy",
)
async def get_effective_break_policy(
    employee_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
) -> EffectiveBreakPolicyResponse:
    eff = await GetEffectiveBreakPolicyUseCase(policy_repo, employee_repo).execute(
        principal, employee_id
    )
    return to_effective_policy_response(eff)
