"""FastAPI router for Annual Vacation & Leave Management (Art. 38 ET)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

from app.application.leaves import (
    BulkUpsertLeaveEntitlementsCommand,
    BulkUpsertLeaveEntitlementsUseCase,
    CancelLeaveRequestUseCase,
    CreateJustificanteSignedUrlUseCase,
    CreateLeaveCommand,
    CreateLeaveRequestUseCase,
    DirectAssignLeaveCommand,
    DirectAssignLeaveUseCase,
    EmployeeEntitlementView,
    GetLeaveCalendarUseCase,
    GetTeamCalendarUseCase,
    GetVacationBalanceUseCase,
    ListLeaveEntitlementsUseCase,
    ListLeavesUseCase,
    ReviewLeaveCommand,
    ReviewLeaveRequestUseCase,
    UploadLeaveJustificanteUseCase,
    UpsertLeaveEntitlementCommand,
    UpsertLeaveEntitlementUseCase,
)
from app.domain.documents import MAX_DOCUMENT_SIZE_BYTES, DocumentStoragePort
from app.domain.holidays import HolidayScope
from app.domain.leaves import (
    EntitlementAdjustmentType,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
    VacationBalance,
)
from app.domain.repositories import (
    DocumentRepository,
    EmployeeRepository,
    LeaveEntitlementRepository,
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
    get_document_storage,
    get_employee_repository,
    get_leave_entitlement_repository,
    get_leave_repository,
    get_payroll_closing_repository,
    get_shift_repository,
    get_store_repository,
    get_utc_now,
)
from app.interfaces.api.documents import SignedUrlResponse

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
    manager_reviewed_by_user_id: UUID | None = None
    manager_reviewed_at_utc: datetime | None = None
    manager_review_note: str = ""
    hr_reviewed_by_user_id: UUID | None = None
    hr_reviewed_at_utc: datetime | None = None
    hr_review_note: str = ""
    assigned_directly: bool = False
    assigned_by_user_id: UUID | None = None
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


class CalendarHolidayResponse(BaseModel):
    date: date
    name_es: str
    name_zh: str
    scope: HolidayScope
    region_code: str | None


class LeaveCalendarResponse(BaseModel):
    year: int
    employee_id: UUID
    region_code: str
    vacation_balance: VacationBalanceResponse
    type_counts: dict[str, int]
    holidays: list[CalendarHolidayResponse]
    leaves: list[LeaveRequestResponse]


class TeamCalendarEntryResponse(BaseModel):
    leave_id: UUID
    display_type: str
    status: LeaveStatus
    start_date: date
    end_date: date
    natural_days: int


class TeamCalendarMemberResponse(BaseModel):
    employee_id: UUID
    full_name: str
    preferred_name: str
    job_title: str
    department: str
    store_id: UUID
    store_name: str
    avatar_url: str | None
    entries: list[TeamCalendarEntryResponse]


class TeamCalendarResponse(BaseModel):
    from_date: date
    to_date: date
    holidays: list[CalendarHolidayResponse]
    members: list[TeamCalendarMemberResponse]


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
        manager_reviewed_by_user_id=lv.manager_reviewed_by_user_id,
        manager_reviewed_at_utc=lv.manager_reviewed_at_utc,
        manager_review_note=lv.manager_review_note,
        hr_reviewed_by_user_id=lv.hr_reviewed_by_user_id,
        hr_reviewed_at_utc=lv.hr_reviewed_at_utc,
        hr_review_note=lv.hr_review_note,
        assigned_directly=lv.assigned_directly,
        assigned_by_user_id=lv.assigned_by_user_id,
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
    entitlement_repo: Annotated[
        LeaveEntitlementRepository, Depends(get_leave_entitlement_repository)
    ],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    employee_id: Annotated[UUID | None, Query()] = None,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> VacationBalanceResponse:
    target_year = year if year is not None else now_utc.year
    bal = await GetVacationBalanceUseCase(
        leave_repo, employee_repo, entitlement_repo=entitlement_repo
    ).execute(principal, employee_id=employee_id, year=target_year)
    return _to_balance_response(bal)


@router.get(
    "/calendar",
    response_model=LeaveCalendarResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_leave_calendar",
)
async def get_leave_calendar(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    entitlement_repo: Annotated[
        LeaveEntitlementRepository, Depends(get_leave_entitlement_repository)
    ],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    employee_id: Annotated[UUID | None, Query()] = None,
) -> LeaveCalendarResponse:
    target_year = year if year is not None else now_utc.year
    res = await GetLeaveCalendarUseCase(
        leave_repo, employee_repo, store_repo, entitlement_repo=entitlement_repo
    ).execute(principal, year=target_year, employee_id=employee_id)
    return LeaveCalendarResponse(
        year=res.year,
        employee_id=res.employee_id,
        region_code=res.region_code,
        vacation_balance=_to_balance_response(res.vacation_balance),
        type_counts={k.value: v for k, v in res.type_counts.items()},
        holidays=[
            CalendarHolidayResponse(
                date=h.date,
                name_es=h.name_es,
                name_zh=h.name_zh,
                scope=h.scope,
                region_code=h.region_code,
            )
            for h in res.holidays
        ],
        leaves=[_to_leave_response(lv) for lv in res.leaves],
    )


@router.get(
    "/team-calendar",
    response_model=TeamCalendarResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_team_leave_calendar",
    summary="GDPR Art. 9 safe team leave calendar (Gantt) within caller scope",
)
async def get_team_leave_calendar(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
    from_date: Annotated[date, Query()],
    to_date: Annotated[date, Query()],
    store_id: Annotated[UUID | None, Query()] = None,
    department: Annotated[str | None, Query()] = None,
) -> TeamCalendarResponse:
    res = await GetTeamCalendarUseCase(
        leave_repo=leave_repo,
        employee_repo=employee_repo,
        store_repo=store_repo,
        storage=storage,
    ).execute(
        principal,
        from_date=from_date,
        to_date=to_date,
        store_id=store_id,
        department=department,
    )
    return TeamCalendarResponse(
        from_date=res.from_date,
        to_date=res.to_date,
        holidays=[
            CalendarHolidayResponse(
                date=h.date,
                name_es=h.name_es,
                name_zh=h.name_zh,
                scope=h.scope,
                region_code=h.region_code,
            )
            for h in res.holidays
        ],
        members=[
            TeamCalendarMemberResponse(
                employee_id=m.employee_id,
                full_name=m.full_name,
                preferred_name=m.preferred_name,
                job_title=m.job_title,
                department=m.department,
                store_id=m.store_id,
                store_name=m.store_name,
                avatar_url=m.avatar_url,
                entries=[
                    TeamCalendarEntryResponse(
                        leave_id=e.leave_id,
                        display_type=e.display_type,
                        status=e.status,
                        start_date=e.start_date,
                        end_date=e.end_date,
                        natural_days=e.natural_days,
                    )
                    for e in m.entries
                ],
            )
            for m in res.members
        ],
    )


class LeaveEntitlementAdjustmentResponse(BaseModel):
    id: UUID
    employee_id: UUID
    year: int
    adjustment_type: EntitlementAdjustmentType
    delta_days: int
    previous_total_days: int
    new_total_days: int
    reason: str
    adjusted_by_user_id: UUID
    created_at: datetime


class EmployeeEntitlementResponse(BaseModel):
    employee_id: UUID
    employee_code: str
    full_name: str
    preferred_name: str
    store_id: UUID
    department: str
    year: int
    base_vacation_days: int
    carryover_days: int
    comp_off_days: int
    entitled_days: int
    approved_days: int
    pending_days: int
    remaining_days: int
    adjustments: list[LeaveEntitlementAdjustmentResponse]


class LeaveEntitlementListResponse(BaseModel):
    items: list[EmployeeEntitlementResponse]


class UpsertLeaveEntitlementBody(BaseModel):
    year: int = Field(ge=2020, le=2100)
    base_vacation_days: int = Field(ge=30, le=90)
    carryover_days: int = Field(default=0, ge=-30, le=60)
    comp_off_days: int = Field(default=0, ge=0, le=90)
    reason: str = Field(min_length=1, max_length=300)


class BulkUpsertLeaveEntitlementsBody(BaseModel):
    year: int = Field(ge=2020, le=2100)
    base_vacation_days: int = Field(ge=30, le=90)
    carryover_days: int = Field(default=0, ge=-30, le=60)
    comp_off_days: int = Field(default=0, ge=0, le=90)
    reason: str = Field(min_length=1, max_length=300)
    employee_ids: list[UUID] | None = None
    store_id: UUID | None = None


class DirectAssignLeaveBody(BaseModel):
    employee_id: UUID
    leave_type: LeaveType
    start_date: date
    end_date: date
    reason: str = Field(default="", max_length=500)


def _to_entitlement_response(v: EmployeeEntitlementView) -> EmployeeEntitlementResponse:
    return EmployeeEntitlementResponse(
        employee_id=v.employee_id,
        employee_code=v.employee_code,
        full_name=v.full_name,
        preferred_name=v.preferred_name,
        store_id=v.store_id,
        department=v.department,
        year=v.year,
        base_vacation_days=v.base_vacation_days,
        carryover_days=v.carryover_days,
        comp_off_days=v.comp_off_days,
        entitled_days=v.entitled_days,
        approved_days=v.approved_days,
        pending_days=v.pending_days,
        remaining_days=v.remaining_days,
        adjustments=[
            LeaveEntitlementAdjustmentResponse(
                id=a.id,
                employee_id=a.employee_id,
                year=a.year,
                adjustment_type=a.adjustment_type,
                delta_days=a.delta_days,
                previous_total_days=a.previous_total_days,
                new_total_days=a.new_total_days,
                reason=a.reason,
                adjusted_by_user_id=a.adjusted_by_user_id,
                created_at=a.created_at,
            )
            for a in v.adjustments
        ],
    )


@router.get(
    "/entitlements",
    response_model=LeaveEntitlementListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="list_leave_entitlements",
)
async def list_leave_entitlements(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    entitlement_repo: Annotated[
        LeaveEntitlementRepository, Depends(get_leave_entitlement_repository)
    ],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    employee_id: Annotated[UUID | None, Query()] = None,
    store_id: Annotated[UUID | None, Query()] = None,
) -> LeaveEntitlementListResponse:
    target_year = year if year is not None else now_utc.year
    views = await ListLeaveEntitlementsUseCase(
        entitlement_repo, leave_repo, employee_repo
    ).execute(principal, year=target_year, employee_id=employee_id, store_id=store_id)
    return LeaveEntitlementListResponse(items=[_to_entitlement_response(v) for v in views])


@router.post(
    "/entitlements/bulk",
    response_model=LeaveEntitlementListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="bulk_upsert_leave_entitlements",
)
async def bulk_upsert_leave_entitlements(
    body: BulkUpsertLeaveEntitlementsBody,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    entitlement_repo: Annotated[
        LeaveEntitlementRepository, Depends(get_leave_entitlement_repository)
    ],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> LeaveEntitlementListResponse:
    views = await BulkUpsertLeaveEntitlementsUseCase(
        entitlement_repo, leave_repo, employee_repo
    ).execute(
        principal,
        BulkUpsertLeaveEntitlementsCommand(
            year=body.year,
            base_vacation_days=body.base_vacation_days,
            carryover_days=body.carryover_days,
            comp_off_days=body.comp_off_days,
            reason=body.reason,
            employee_ids=tuple(body.employee_ids) if body.employee_ids is not None else None,
            store_id=body.store_id,
        ),
        now_utc=now_utc,
    )
    await commit()
    return LeaveEntitlementListResponse(items=[_to_entitlement_response(v) for v in views])


@router.put(
    "/entitlements/{employee_id}",
    response_model=EmployeeEntitlementResponse,
    status_code=status.HTTP_200_OK,
    operation_id="upsert_leave_entitlement",
)
async def upsert_leave_entitlement(
    employee_id: UUID,
    body: UpsertLeaveEntitlementBody,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    entitlement_repo: Annotated[
        LeaveEntitlementRepository, Depends(get_leave_entitlement_repository)
    ],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> EmployeeEntitlementResponse:
    view = await UpsertLeaveEntitlementUseCase(
        entitlement_repo, leave_repo, employee_repo
    ).execute(
        principal,
        employee_id,
        UpsertLeaveEntitlementCommand(
            year=body.year,
            base_vacation_days=body.base_vacation_days,
            carryover_days=body.carryover_days,
            comp_off_days=body.comp_off_days,
            reason=body.reason,
        ),
        now_utc=now_utc,
    )
    await commit()
    return _to_entitlement_response(view)


@router.post(
    "/assign",
    response_model=LeaveRequestResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="direct_assign_leave",
)
async def direct_assign_leave(
    body: DirectAssignLeaveBody,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    entitlement_repo: Annotated[
        LeaveEntitlementRepository, Depends(get_leave_entitlement_repository)
    ],
    shift_repo: Annotated[ShiftRepository, Depends(get_shift_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> LeaveRequestResponse:
    created = await DirectAssignLeaveUseCase(
        leave_repo,
        employee_repo,
        payroll_repo=payroll_repo,
        entitlement_repo=entitlement_repo,
        shift_repo=shift_repo,
        store_repo=store_repo,
    ).execute(
        principal,
        DirectAssignLeaveCommand(
            employee_id=body.employee_id,
            leave_type=body.leave_type,
            start_date=body.start_date,
            end_date=body.end_date,
            reason=body.reason,
        ),
        now_utc=now_utc,
    )
    await commit()
    return _to_leave_response(created)


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
    entitlement_repo: Annotated[
        LeaveEntitlementRepository, Depends(get_leave_entitlement_repository)
    ],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> LeaveRequestResponse:
    created = await CreateLeaveRequestUseCase(
        leave_repo,
        employee_repo,
        document_repo,
        payroll_repo=payroll_repo,
        entitlement_repo=entitlement_repo,
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
    entitlement_repo: Annotated[
        LeaveEntitlementRepository, Depends(get_leave_entitlement_repository)
    ],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> LeaveRequestResponse:
    reviewed = await ReviewLeaveRequestUseCase(
        leave_repo,
        employee_repo,
        shift_repo,
        store_repo,
        payroll_repo=payroll_repo,
        entitlement_repo=entitlement_repo,
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


@router.post(
    "/{leave_id}/justificante",
    response_model=LeaveRequestResponse,
    status_code=status.HTTP_200_OK,
    operation_id="upload_leave_justificante",
)
async def upload_leave_justificante(
    leave_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    file: Annotated[UploadFile, File()],
) -> LeaveRequestResponse:
    content = await file.read(MAX_DOCUMENT_SIZE_BYTES + 1)
    mime_type = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    updated = await UploadLeaveJustificanteUseCase(
        leave_repo, employee_repo, doc_repo, storage, payroll_repo=payroll_repo
    ).execute(
        principal,
        leave_id=leave_id,
        filename=file.filename or "justificante",
        mime_type=mime_type,
        content=content,
        now_utc=now_utc,
    )
    await commit()
    return _to_leave_response(updated)


@router.post(
    "/{leave_id}/justificante/signed-url",
    response_model=SignedUrlResponse,
    status_code=status.HTTP_200_OK,
    operation_id="create_justificante_signed_url",
)
async def create_justificante_signed_url(
    leave_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
) -> SignedUrlResponse:
    signed, sha256_hex = await CreateJustificanteSignedUrlUseCase(
        leave_repo, employee_repo, doc_repo, storage
    ).execute(principal, leave_id=leave_id, ttl_seconds=60)
    response.headers["Cache-Control"] = "no-store"
    return SignedUrlResponse(
        url=signed.url,
        expires_at_utc=signed.expires_at_utc,
        ttl_seconds=signed.ttl_seconds,
        sha256_hex=sha256_hex,
    )
