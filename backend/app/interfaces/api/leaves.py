"""FastAPI router for Annual Vacation & Leave Management (Art. 38 ET)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.application.leaves import (
    CancelLeaveRequestUseCase,
    CreateLeaveCommand,
    CreateLeaveRequestUseCase,
    GetVacationBalanceUseCase,
    ListLeavesUseCase,
    ReviewLeaveCommand,
    ReviewLeaveRequestUseCase,
)
from app.domain.leaves import LeaveRequest, LeaveStatus, LeaveType, VacationBalance
from app.domain.repositories import (
    DocumentRepository,
    EmployeeRepository,
    LeaveRepository,
    PayrollClosingRepository,
    ShiftRepository,
    StoreRepository,
)
from app.domain.security import AuthenticatedPrincipal
from app.interfaces.api.deps import (
    CommitFn,
    get_commit_fn,
    get_current_principal,
    get_document_repository,
    get_employee_repository,
    get_leave_repository,
    get_payroll_closing_repository,
    get_shift_repository,
    get_store_repository,
    get_utc_now,
)

router = APIRouter(prefix="/leaves", tags=["leaves"])


class LeaveRequestResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    store_id: UUID
    employee_id: UUID
    leave_type: LeaveType
    start_date: date
    end_date: date
    natural_days: int
    status: LeaveStatus
    reason: str
    requested_by_user_id: UUID
    reviewed_by_user_id: UUID | None
    reviewed_at_utc: datetime | None
    review_note: str
    medical_document_id: UUID | None
    retention_until: datetime
    created_at: datetime
    updated_at: datetime


class LeaveListResponse(BaseModel):
    items: list[LeaveRequestResponse]


class VacationBalanceResponse(BaseModel):
    employee_id: UUID
    year: int
    entitled_days: int
    approved_days: int
    pending_days: int
    remaining_days: int


class CreateLeaveRequestBody(BaseModel):
    employee_id: UUID | None = None
    leave_type: LeaveType
    start_date: date
    end_date: date
    reason: str = Field(default="", max_length=500)
    medical_document_id: UUID | None = None


class ReviewLeaveRequestBody(BaseModel):
    approve: bool
    review_note: str = Field(default="", max_length=500)


def _to_leave_response(lv: LeaveRequest) -> LeaveRequestResponse:
    return LeaveRequestResponse(
        id=lv.id,
        tenant_id=lv.tenant_id,
        store_id=lv.store_id,
        employee_id=lv.employee_id,
        leave_type=lv.leave_type,
        start_date=lv.start_date,
        end_date=lv.end_date,
        natural_days=lv.natural_days,
        status=lv.status,
        reason=lv.reason,
        requested_by_user_id=lv.requested_by_user_id,
        reviewed_by_user_id=lv.reviewed_by_user_id,
        reviewed_at_utc=lv.reviewed_at_utc,
        review_note=lv.review_note,
        medical_document_id=lv.medical_document_id,
        retention_until=lv.retention_until,
        created_at=lv.created_at,
        updated_at=lv.updated_at,
    )


def _to_balance_response(bal: VacationBalance) -> VacationBalanceResponse:
    return VacationBalanceResponse(
        employee_id=bal.employee_id,
        year=bal.year,
        entitled_days=bal.entitled_days,
        approved_days=bal.approved_days,
        pending_days=bal.pending_days,
        remaining_days=bal.remaining_days,
    )


@router.get(
    "",
    response_model=LeaveListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="list_leave_requests",
)
async def list_leave_requests(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_id: Annotated[UUID | None, Query()] = None,
    employee_id: Annotated[UUID | None, Query()] = None,
    leave_status: Annotated[LeaveStatus | None, Query(alias="status")] = None,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> LeaveListResponse:
    items = await ListLeavesUseCase(leave_repo, employee_repo).execute(
        principal,
        store_id=store_id,
        employee_id=employee_id,
        status=leave_status,
        year=year,
    )
    return LeaveListResponse(items=[_to_leave_response(lv) for lv in items])


@router.get(
    "/balance",
    response_model=VacationBalanceResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_vacation_balance",
)
async def get_vacation_balance(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    employee_id: Annotated[UUID | None, Query()] = None,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> VacationBalanceResponse:
    target_year = year if year is not None else now_utc.year
    bal = await GetVacationBalanceUseCase(leave_repo, employee_repo).execute(
        principal, employee_id=employee_id, year=target_year
    )
    return _to_balance_response(bal)


@router.post(
    "",
    response_model=LeaveRequestResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_leave_request",
)
async def create_leave_request(
    body: CreateLeaveRequestBody,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    document_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> LeaveRequestResponse:
    created = await CreateLeaveRequestUseCase(
        leave_repo, employee_repo, document_repo, payroll_repo=payroll_repo
    ).execute(
        principal,
        CreateLeaveCommand(
            employee_id=body.employee_id,
            leave_type=body.leave_type,
            start_date=body.start_date,
            end_date=body.end_date,
            reason=body.reason,
            medical_document_id=body.medical_document_id,
        ),
        now_utc=now_utc,
    )
    await commit()
    return _to_leave_response(created)


@router.post(
    "/{leave_id}/review",
    response_model=LeaveRequestResponse,
    status_code=status.HTTP_200_OK,
    operation_id="review_leave_request",
)
async def review_leave_request(
    leave_id: UUID,
    body: ReviewLeaveRequestBody,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    shift_repo: Annotated[ShiftRepository, Depends(get_shift_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> LeaveRequestResponse:
    reviewed = await ReviewLeaveRequestUseCase(
        leave_repo, employee_repo, shift_repo, store_repo, payroll_repo=payroll_repo
    ).execute(
        principal,
        ReviewLeaveCommand(
            leave_id=leave_id,
            approve=body.approve,
            review_note=body.review_note,
        ),
        now_utc=now_utc,
    )
    await commit()
    return _to_leave_response(reviewed)


@router.post(
    "/{leave_id}/cancel",
    response_model=LeaveRequestResponse,
    status_code=status.HTTP_200_OK,
    operation_id="cancel_leave_request",
)
async def cancel_leave_request(
    leave_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> LeaveRequestResponse:
    cancelled = await CancelLeaveRequestUseCase(
        leave_repo, employee_repo, payroll_repo=payroll_repo
    ).execute(
        principal, leave_id=leave_id, now_utc=now_utc
    )
    await commit()
    return _to_leave_response(cancelled)
