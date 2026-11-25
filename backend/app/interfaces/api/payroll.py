"""FastAPI router for Phase 5 Monthly Payroll Closing, RDL 8/2019 PDFs & Gestoria CSV."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from app.application.payroll import (
    CloseMonthlyPayrollUseCase,
    ClosingDetailResult,
    ClosingListSummary,
    ExportGestoriaPayrollCsvUseCase,
    GetMonthlyClosingDetailUseCase,
    ListMonthlyClosingsUseCase,
    PreviewMonthlyClosingUseCase,
    ReopenMonthlyClosingUseCase,
)
from app.domain.documents import DocumentStoragePort
from app.domain.employees import ContractType, WorkScheduleType
from app.domain.payroll import (
    AttendancePdfRendererPort,
    ClosingBlocker,
    ClosingBlockerCode,
    MonthlyClosing,
    MonthlyClosingStatus,
    MonthlyEmployeeSummary,
)
from app.domain.repositories import (
    AttendanceRepository,
    DocumentRepository,
    EmployeeRepository,
    LeaveRepository,
    PayrollClosingRepository,
    StoreRepository,
    TenantRepository,
)
from app.domain.security import AuthenticatedPrincipal
from app.interfaces.api.deps import (
    CommitFn,
    get_attendance_repository,
    get_commit_fn,
    get_current_principal,
    get_document_repository,
    get_document_storage,
    get_employee_repository,
    get_leave_repository,
    get_payroll_closing_repository,
    get_pdf_renderer,
    get_store_repository,
    get_tenant_repository,
    get_utc_now,
)

router = APIRouter(prefix="/payroll", tags=["payroll"])


class MonthlyClosingResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    store_id: UUID
    period_month: str
    status: MonthlyClosingStatus
    revision_number: int
    closed_by_user_id: UUID
    closed_at_utc: datetime
    reopened_by_user_id: UUID | None
    reopened_at_utc: datetime | None
    reopen_reason: str
    notes: str
    retention_until: datetime
    created_at: datetime
    updated_at: datetime


class MonthlyClosingListItemResponse(BaseModel):
    closing: MonthlyClosingResponse
    store_code: str
    store_name: str
    employee_count: int
    total_net_hours: Decimal
    total_overtime_hours: Decimal
    signed_count: int
    total_reports: int


class MonthlyClosingListResponse(BaseModel):
    items: list[MonthlyClosingListItemResponse]


class ClosingBlockerResponse(BaseModel):
    code: ClosingBlockerCode
    employee_id: UUID | None
    employee_code: str | None
    employee_full_name: str | None
    reference_id: UUID | None
    local_date: date | None
    message: str


class MonthlyEmployeePreviewResponse(BaseModel):
    employee_id: UUID
    employee_code: str
    employee_full_name: str
    nie_dni_masked: str | None
    contract_type: ContractType
    work_schedule_type: WorkScheduleType
    contracted_weekly_hours: Decimal
    worked_days: int
    total_net_hours: Decimal
    regular_hours: Decimal
    overtime_hours: Decimal
    annual_overtime_hours: Decimal
    total_break_minutes: int
    vacation_days: int
    sick_leave_days: int
    paid_leave_days: int
    unpaid_leave_days: int
    warning_alerts_count: int
    violation_alerts_count: int


class MonthlyReadinessPreviewResponse(BaseModel):
    store_id: UUID
    period_month: str
    month_start: date
    month_end: date
    existing_closing: MonthlyClosingResponse | None
    can_close: bool
    blockers: list[ClosingBlockerResponse]
    employees: list[MonthlyEmployeePreviewResponse]


class MonthlyClosingItemResponse(BaseModel):
    id: UUID
    closing_id: UUID
    revision_number: int
    employee_id: UUID
    employee_code: str
    employee_full_name: str
    nie_dni_masked: str | None
    contract_type: ContractType
    work_schedule_type: WorkScheduleType
    contracted_weekly_hours: Decimal
    worked_days: int
    total_net_hours: Decimal
    regular_hours: Decimal
    overtime_hours: Decimal
    annual_overtime_hours: Decimal
    total_break_minutes: int
    vacation_days: int
    sick_leave_days: int
    paid_leave_days: int
    unpaid_leave_days: int
    warning_alerts_count: int
    violation_alerts_count: int
    attendance_report_document_id: UUID | None
    is_signed: bool
    signed_at_utc: datetime | None
    document_sha256_hex: str | None
    retention_until: datetime
    created_at: datetime


class MonthlyClosingDetailResponse(BaseModel):
    closing: MonthlyClosingResponse
    store_code: str
    store_name: str
    items: list[MonthlyClosingItemResponse]


class CloseMonthlyPayrollRequest(BaseModel):
    store_id: UUID
    period_month: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
    notes: str = Field(default="", max_length=500)


class ReopenMonthlyClosingRequest(BaseModel):
    reopen_reason: str = Field(min_length=1, max_length=500)


def _to_closing_response(c: MonthlyClosing) -> MonthlyClosingResponse:
    return MonthlyClosingResponse(
        id=c.id,
        tenant_id=c.tenant_id,
        store_id=c.store_id,
        period_month=c.period_month,
        status=c.status,
        revision_number=c.revision_number,
        closed_by_user_id=c.closed_by_user_id,
        closed_at_utc=c.closed_at_utc,
        reopened_by_user_id=c.reopened_by_user_id,
        reopened_at_utc=c.reopened_at_utc,
        reopen_reason=c.reopen_reason,
        notes=c.notes,
        retention_until=c.retention_until,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _to_blocker_response(b: ClosingBlocker) -> ClosingBlockerResponse:
    return ClosingBlockerResponse(
        code=b.code,
        employee_id=b.employee_id,
        employee_code=b.employee_code,
        employee_full_name=b.employee_full_name,
        reference_id=b.reference_id,
        local_date=b.local_date,
        message=b.message,
    )


def _to_preview_emp_response(e: MonthlyEmployeeSummary) -> MonthlyEmployeePreviewResponse:
    return MonthlyEmployeePreviewResponse(
        employee_id=e.employee_id,
        employee_code=e.employee_code,
        employee_full_name=e.employee_full_name,
        nie_dni_masked=e.nie_dni_masked,
        contract_type=e.contract_type,
        work_schedule_type=e.work_schedule_type,
        contracted_weekly_hours=e.contracted_weekly_hours,
        worked_days=e.worked_days,
        total_net_hours=e.total_net_hours,
        regular_hours=e.regular_hours,
        overtime_hours=e.overtime_hours,
        annual_overtime_hours=e.annual_overtime_hours,
        total_break_minutes=e.total_break_minutes,
        vacation_days=e.vacation_days,
        sick_leave_days=e.sick_leave_days,
        paid_leave_days=e.paid_leave_days,
        unpaid_leave_days=e.unpaid_leave_days,
        warning_alerts_count=e.warning_alerts_count,
        violation_alerts_count=e.violation_alerts_count,
    )


def _to_list_item_response(s: ClosingListSummary) -> MonthlyClosingListItemResponse:
    return MonthlyClosingListItemResponse(
        closing=_to_closing_response(s.closing),
        store_code=s.store_code,
        store_name=s.store_name,
        employee_count=s.employee_count,
        total_net_hours=s.total_net_hours,
        total_overtime_hours=s.total_overtime_hours,
        signed_count=s.signed_count,
        total_reports=s.total_reports,
    )


def _to_detail_response(detail: ClosingDetailResult) -> MonthlyClosingDetailResponse:
    return MonthlyClosingDetailResponse(
        closing=_to_closing_response(detail.closing),
        store_code=detail.store_code,
        store_name=detail.store_name,
        items=[
            MonthlyClosingItemResponse(
                id=row.item.id,
                closing_id=row.item.closing_id,
                revision_number=row.item.revision_number,
                employee_id=row.item.employee_id,
                employee_code=row.item.employee_code,
                employee_full_name=row.item.employee_full_name,
                nie_dni_masked=row.item.nie_dni_masked,
                contract_type=row.item.contract_type,
                work_schedule_type=row.item.work_schedule_type,
                contracted_weekly_hours=row.item.contracted_weekly_hours,
                worked_days=row.item.worked_days,
                total_net_hours=row.item.total_net_hours,
                regular_hours=row.item.regular_hours,
                overtime_hours=row.item.overtime_hours,
                annual_overtime_hours=row.item.annual_overtime_hours,
                total_break_minutes=row.item.total_break_minutes,
                vacation_days=row.item.vacation_days,
                sick_leave_days=row.item.sick_leave_days,
                paid_leave_days=row.item.paid_leave_days,
                unpaid_leave_days=row.item.unpaid_leave_days,
                warning_alerts_count=row.item.warning_alerts_count,
                violation_alerts_count=row.item.violation_alerts_count,
                attendance_report_document_id=row.item.attendance_report_document_id,
                is_signed=row.is_signed,
                signed_at_utc=row.signed_at_utc,
                document_sha256_hex=row.document_sha256_hex,
                retention_until=row.item.retention_until,
                created_at=row.item.created_at,
            )
            for row in detail.items
        ],
    )


@router.get(
    "/closings",
    response_model=MonthlyClosingListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="list_monthly_closings",
)
async def list_monthly_closings(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    store_id: Annotated[UUID | None, Query()] = None,
    period_month: Annotated[str | None, Query()] = None,
) -> MonthlyClosingListResponse:
    items = await ListMonthlyClosingsUseCase(
        payroll_repo, store_repo, employee_repo, doc_repo
    ).execute(principal, store_id=store_id, period_month=period_month)
    return MonthlyClosingListResponse(items=[_to_list_item_response(it) for it in items])


@router.get(
    "/preview",
    response_model=MonthlyReadinessPreviewResponse,
    status_code=status.HTTP_200_OK,
    operation_id="preview_monthly_closing",
)
async def preview_monthly_closing(
    store_id: Annotated[UUID, Query()],
    period_month: Annotated[str, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")],
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    attendance_repo: Annotated[AttendanceRepository, Depends(get_attendance_repository)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
) -> MonthlyReadinessPreviewResponse:
    report = await PreviewMonthlyClosingUseCase(
        payroll_repo, store_repo, employee_repo, attendance_repo, leave_repo
    ).execute(principal, store_id=store_id, period_month=period_month)
    return MonthlyReadinessPreviewResponse(
        store_id=report.store_id,
        period_month=report.period_month,
        month_start=report.month_start,
        month_end=report.month_end,
        existing_closing=(
            _to_closing_response(report.existing_closing)
            if report.existing_closing is not None
            else None
        ),
        can_close=report.can_close,
        blockers=[_to_blocker_response(b) for b in report.blockers],
        employees=[_to_preview_emp_response(e) for e in report.employees],
    )


@router.post(
    "/closings",
    response_model=MonthlyClosingDetailResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="close_monthly_payroll",
)
async def close_monthly_payroll(
    body: CloseMonthlyPayrollRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    tenant_repo: Annotated[TenantRepository, Depends(get_tenant_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    attendance_repo: Annotated[AttendanceRepository, Depends(get_attendance_repository)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
    pdf_renderer: Annotated[AttendancePdfRendererPort, Depends(get_pdf_renderer)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> MonthlyClosingDetailResponse:
    detail = await CloseMonthlyPayrollUseCase(
        payroll_repo,
        tenant_repo,
        store_repo,
        employee_repo,
        attendance_repo,
        leave_repo,
        doc_repo,
        storage,
        pdf_renderer,
    ).execute(
        principal,
        store_id=body.store_id,
        period_month=body.period_month,
        notes=body.notes,
        now_utc=now_utc,
    )
    await commit()
    return _to_detail_response(detail)


@router.get(
    "/closings/{closing_id}",
    response_model=MonthlyClosingDetailResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_monthly_closing",
)
async def get_monthly_closing(
    closing_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> MonthlyClosingDetailResponse:
    detail = await GetMonthlyClosingDetailUseCase(
        payroll_repo, store_repo, employee_repo, doc_repo
    ).execute(principal, closing_id=closing_id)
    return _to_detail_response(detail)


@router.post(
    "/closings/{closing_id}/reopen",
    response_model=MonthlyClosingDetailResponse,
    status_code=status.HTTP_200_OK,
    operation_id="reopen_monthly_closing",
)
async def reopen_monthly_closing(
    closing_id: UUID,
    body: ReopenMonthlyClosingRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> MonthlyClosingDetailResponse:
    detail = await ReopenMonthlyClosingUseCase(
        payroll_repo, store_repo, employee_repo, doc_repo
    ).execute(
        principal,
        closing_id=closing_id,
        reopen_reason=body.reopen_reason,
        now_utc=now_utc,
    )
    await commit()
    return _to_detail_response(detail)


@router.get(
    "/closings/{closing_id}/gestoria-export",
    response_model=None,
    status_code=status.HTTP_200_OK,
    operation_id="export_gestoria_payroll_csv",
    responses={
        200: {
            "content": {"text/csv": {}},
            "description": "Gestoria monthly payroll pack CSV (es-ES UTF-8 BOM)",
        }
    },
)
async def export_gestoria_payroll_csv(
    closing_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    tenant_repo: Annotated[TenantRepository, Depends(get_tenant_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> Response:
    export = await ExportGestoriaPayrollCsvUseCase(
        payroll_repo, tenant_repo, store_repo, doc_repo
    ).execute(principal, closing_id=closing_id)
    encoded_fn = quote(export.filename, safe="")
    content_disp = f'attachment; filename="{export.filename}"; filename*=UTF-8\'\'{encoded_fn}'
    return Response(
        content=export.csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": content_disp,
        },
    )
