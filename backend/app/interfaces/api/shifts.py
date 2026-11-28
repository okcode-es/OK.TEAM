"""FastAPI router for Store Shift Scheduling Calendar."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.application.scheduling import (
    ApprovedScheduleLeave,
    CancelShiftUseCase,
    CreateShiftCommand,
    CreateShiftUseCase,
    EmployeeWeeklyScheduleRow,
    GetWeeklyScheduleUseCase,
    PublishWeeklyShiftsUseCase,
    ScheduleLeaveType,
    ShiftMutationResult,
    UpdateShiftCommand,
    UpdateShiftUseCase,
)
from app.domain.attendance import AlertSeverity
from app.domain.employees import WorkScheduleType
from app.domain.leaves import LeaveStatus
from app.domain.repositories import (
    EmployeeRepository,
    LeaveRepository,
    ShiftRepository,
    StoreRepository,
)
from app.domain.scheduling import ScheduleAlert, ScheduleAlertCode, Shift, ShiftStatus
from app.domain.security import AuthenticatedPrincipal
from app.interfaces.api.deps import (
    CommitFn,
    get_commit_fn,
    get_current_principal,
    get_employee_repository,
    get_leave_repository,
    get_shift_repository,
    get_store_repository,
    get_utc_now,
)

router = APIRouter(prefix="/shifts", tags=["shifts"])


class ShiftResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    store_id: UUID
    employee_id: UUID
    start_at_utc: datetime
    end_at_utc: datetime
    planned_break_minutes: int
    net_hours: Decimal
    status: ShiftStatus
    notes: str
    created_by_user_id: UUID
    published_by_user_id: UUID | None
    published_at_utc: datetime | None
    retention_until: datetime
    created_at: datetime
    updated_at: datetime


class ScheduleAlertResponse(BaseModel):
    code: ScheduleAlertCode
    severity: AlertSeverity
    employee_id: UUID
    local_date: date
    message: str
    actual_value: Decimal | None
    limit_value: Decimal | None


class ApprovedLeaveSummaryResponse(BaseModel):
    id: UUID
    leave_type: ScheduleLeaveType
    start_date: date
    end_date: date
    natural_days: int
    status: LeaveStatus


class EmployeeScheduleRowResponse(BaseModel):
    employee_id: UUID
    employee_code: str
    full_name: str
    work_schedule_type: WorkScheduleType
    contracted_weekly_hours: Decimal
    weekly_net_hours: Decimal
    shifts: list[ShiftResponse]
    approved_leaves: list[ApprovedLeaveSummaryResponse]
    alerts: list[ScheduleAlertResponse]


class WeeklyScheduleResponse(BaseModel):
    store_id: UUID
    store_code: str
    store_name: str
    store_timezone: str
    week_start: date
    week_end: date
    rows: list[EmployeeScheduleRowResponse]


class CreateShiftRequest(BaseModel):
    employee_id: UUID
    start_at_utc: datetime
    end_at_utc: datetime
    planned_break_minutes: int = Field(default=0, ge=0)
    status: ShiftStatus = ShiftStatus.DRAFT
    notes: str = Field(default="", max_length=500)
    # Optional cross-store support target; defaults to the employee's primary store.
    store_id: UUID | None = None


class UpdateShiftRequest(BaseModel):
    start_at_utc: datetime | None = None
    end_at_utc: datetime | None = None
    planned_break_minutes: int | None = Field(default=None, ge=0)
    status: ShiftStatus | None = None
    notes: str | None = Field(default=None, max_length=500)


class ShiftMutationResponse(BaseModel):
    shift: ShiftResponse
    alerts: list[ScheduleAlertResponse]


class PublishWeekRequest(BaseModel):
    store_id: UUID
    week_start: date


class PublishWeekResponse(BaseModel):
    published_count: int
    shifts: list[ShiftResponse]


def _to_shift_response(shift: Shift) -> ShiftResponse:
    return ShiftResponse(
        id=shift.id,
        tenant_id=shift.tenant_id,
        store_id=shift.store_id,
        employee_id=shift.employee_id,
        start_at_utc=shift.start_at_utc,
        end_at_utc=shift.end_at_utc,
        planned_break_minutes=shift.planned_break_minutes,
        net_hours=shift.net_hours,
        status=shift.status,
        notes=shift.notes,
        created_by_user_id=shift.created_by_user_id,
        published_by_user_id=shift.published_by_user_id,
        published_at_utc=shift.published_at_utc,
        retention_until=shift.retention_until,
        created_at=shift.created_at,
        updated_at=shift.updated_at,
    )


def _to_alert_response(alert: ScheduleAlert) -> ScheduleAlertResponse:
    return ScheduleAlertResponse(
        code=alert.code,
        severity=alert.severity,
        employee_id=alert.employee_id,
        local_date=alert.local_date,
        message=alert.message,
        actual_value=alert.actual_value,
        limit_value=alert.limit_value,
    )


def _to_leave_summary(lv: ApprovedScheduleLeave) -> ApprovedLeaveSummaryResponse:
    return ApprovedLeaveSummaryResponse(
        id=lv.id,
        leave_type=lv.leave_type,
        start_date=lv.start_date,
        end_date=lv.end_date,
        natural_days=lv.natural_days,
        status=lv.status,
    )


def _to_row_response(row: EmployeeWeeklyScheduleRow) -> EmployeeScheduleRowResponse:
    return EmployeeScheduleRowResponse(
        employee_id=row.employee.id,
        employee_code=row.employee.employee_code,
        full_name=row.employee.full_name,
        work_schedule_type=row.employee.work_schedule_type,
        contracted_weekly_hours=row.employee.contracted_weekly_hours,
        weekly_net_hours=row.weekly_net_hours,
        shifts=[_to_shift_response(s) for s in row.shifts],
        approved_leaves=[_to_leave_summary(lv) for lv in row.approved_leaves],
        alerts=[_to_alert_response(a) for a in row.alerts],
    )


def _to_mutation_response(result: ShiftMutationResult) -> ShiftMutationResponse:
    return ShiftMutationResponse(
        shift=_to_shift_response(result.shift),
        alerts=[_to_alert_response(a) for a in result.alerts],
    )


@router.get(
    "/weekly",
    response_model=WeeklyScheduleResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_weekly_schedule",
)
async def get_weekly_schedule(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    shift_repo: Annotated[ShiftRepository, Depends(get_shift_repository)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    week_start: Annotated[date, Query()],
    store_id: Annotated[UUID | None, Query()] = None,
) -> WeeklyScheduleResponse:
    result = await GetWeeklyScheduleUseCase(
        shift_repo, leave_repo, employee_repo, store_repo
    ).execute(principal, store_id=store_id, week_start=week_start)
    return WeeklyScheduleResponse(
        store_id=result.store_id,
        store_code=result.store_code,
        store_name=result.store_name,
        store_timezone=result.store_timezone,
        week_start=result.week_start,
        week_end=result.week_end,
        rows=[_to_row_response(row) for row in result.rows],
    )


@router.post(
    "",
    response_model=ShiftMutationResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_shift",
)
async def create_shift(
    body: CreateShiftRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    shift_repo: Annotated[ShiftRepository, Depends(get_shift_repository)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> ShiftMutationResponse:
    result = await CreateShiftUseCase(
        shift_repo, leave_repo, employee_repo, store_repo
    ).execute(
        principal,
        CreateShiftCommand(
            employee_id=body.employee_id,
            start_at_utc=body.start_at_utc,
            end_at_utc=body.end_at_utc,
            planned_break_minutes=body.planned_break_minutes,
            status=body.status,
            notes=body.notes,
            store_id=body.store_id,
        ),
        now_utc=now_utc,
    )
    await commit()
    return _to_mutation_response(result)


@router.patch(
    "/{shift_id}",
    response_model=ShiftMutationResponse,
    status_code=status.HTTP_200_OK,
    operation_id="update_shift",
)
async def update_shift(
    shift_id: UUID,
    body: UpdateShiftRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    shift_repo: Annotated[ShiftRepository, Depends(get_shift_repository)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> ShiftMutationResponse:
    result = await UpdateShiftUseCase(
        shift_repo, leave_repo, employee_repo, store_repo
    ).execute(
        principal,
        UpdateShiftCommand(
            shift_id=shift_id,
            start_at_utc=body.start_at_utc,
            end_at_utc=body.end_at_utc,
            planned_break_minutes=body.planned_break_minutes,
            status=body.status,
            notes=body.notes,
        ),
        now_utc=now_utc,
    )
    await commit()
    return _to_mutation_response(result)


@router.post(
    "/publish-week",
    response_model=PublishWeekResponse,
    status_code=status.HTTP_200_OK,
    operation_id="publish_weekly_shifts",
)
async def publish_weekly_shifts(
    body: PublishWeekRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    shift_repo: Annotated[ShiftRepository, Depends(get_shift_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> PublishWeekResponse:
    shifts = await PublishWeeklyShiftsUseCase(
        shift_repo, store_repo, employee_repo, leave_repo
    ).execute(
        principal,
        store_id=body.store_id,
        week_start=body.week_start,
        now_utc=now_utc,
    )
    await commit()
    return PublishWeekResponse(
        published_count=len(shifts),
        shifts=[_to_shift_response(s) for s in shifts],
    )


@router.delete(
    "/{shift_id}",
    response_model=ShiftResponse,
    status_code=status.HTTP_200_OK,
    operation_id="cancel_shift",
)
async def cancel_shift(
    shift_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    shift_repo: Annotated[ShiftRepository, Depends(get_shift_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> ShiftResponse:
    shift = await CancelShiftUseCase(shift_repo, employee_repo).execute(
        principal, shift_id=shift_id, now_utc=now_utc
    )
    await commit()
    return _to_shift_response(shift)
