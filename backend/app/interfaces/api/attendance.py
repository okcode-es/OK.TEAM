from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from app.application.attendance import (
    AppendCorrectionCommand,
    AppendCorrectionUseCase,
    BatchReviewAttendanceDaysCommand,
    BatchReviewAttendanceDaysUseCase,
    ClockAttendanceCommand,
    ClockAttendanceUseCase,
    EmployeeAttendanceLedgerItem,
    ExportAttendanceCsvUseCase,
    ExportAttendancePdfUseCase,
    GetAttendanceLedgerUseCase,
    RecordManualDayCommand,
    RecordManualDayUseCase,
)
from app.domain.attendance import (
    AlertCode,
    AlertSeverity,
    AttendanceDayReview,
    AttendanceDayReviewStatus,
    AttendanceEvent,
    AttendanceEventType,
    AttendanceSegment,
    AttendanceSource,
    ComplianceAlert,
    CorrectedEventType,
    DailyAttendanceSummary,
    SegmentType,
    WorkMode,
    WorkSession,
)
from app.domain.payroll import AttendancePdfRendererPort
from app.domain.repositories import (
    AttendanceDayReviewRepository,
    AttendanceRepository,
    BreakPolicyRepository,
    EmployeeRepository,
    LeaveRepository,
    PayrollClosingRepository,
    ShiftRepository,
    StoreRepository,
    TenantRepository,
)
from app.domain.security import AuthenticatedPrincipal
from app.interfaces.api.break_policies import (
    EffectiveBreakPolicyResponse,
    to_effective_policy_response,
)
from app.interfaces.api.deps import (
    CommitFn,
    get_attendance_day_review_repository,
    get_attendance_repository,
    get_break_policy_repository,
    get_commit_fn,
    get_current_principal,
    get_employee_repository,
    get_leave_repository,
    get_payroll_closing_repository,
    get_pdf_renderer,
    get_shift_repository,
    get_store_repository,
    get_tenant_repository,
    get_utc_now,
)

router = APIRouter(prefix="/attendance", tags=["attendance"])


class ClockAttendanceRequest(BaseModel):
    event_type: AttendanceEventType
    source: AttendanceSource = AttendanceSource.WEB
    store_id: UUID | None = None
    employee_code: str | None = Field(default=None, max_length=32)
    work_mode: WorkMode = WorkMode.ONSITE
    occurred_at_utc: datetime | None = None


class RecordManualDayRequest(BaseModel):
    employee_id: UUID | None = None
    local_date: date
    clock_in_time: str = Field(min_length=5, max_length=5)
    break_start_time: str | None = Field(default=None, min_length=5, max_length=5)
    break_end_time: str | None = Field(default=None, min_length=5, max_length=5)
    clock_out_time: str | None = Field(default=None, min_length=5, max_length=5)
    work_mode: WorkMode = WorkMode.ONSITE
    note: str = Field(default="", max_length=300)


class RecordManualDayResponse(BaseModel):
    events: list[AttendanceEventResponse]
    employee_id: UUID
    employee_code: str
    employee_name: str
    store_id: UUID
    store_name: str


class AppendCorrectionRequest(BaseModel):
    employee_id: UUID
    corrected_event_type: CorrectedEventType
    occurred_at_utc: datetime
    correction_reason: str = Field(min_length=1, max_length=300)
    store_id: UUID | None = None
    supersedes_event_id: UUID | None = None


class AttendanceEventResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    store_id: UUID
    employee_id: UUID
    event_type: AttendanceEventType
    occurred_at_utc: datetime
    store_timezone: str
    source: AttendanceSource
    recorded_by_user_id: UUID
    recorded_at_utc: datetime
    retention_until: datetime
    supersedes_event_id: UUID | None
    corrected_event_type: CorrectedEventType | None
    correction_reason: str
    work_mode: WorkMode = WorkMode.ONSITE


class ClockAttendanceResponse(BaseModel):
    event: AttendanceEventResponse
    employee_id: UUID
    employee_code: str
    employee_name: str
    store_id: UUID
    store_name: str


class ComplianceAlertResponse(BaseModel):
    code: AlertCode
    severity: AlertSeverity
    employee_id: UUID
    local_date: date
    message: str
    actual_value: Decimal | None
    limit_value: Decimal | None


class WorkSessionResponse(BaseModel):
    employee_id: UUID
    store_id: UUID
    store_timezone: str
    local_date: date
    clock_in_utc: datetime
    clock_out_utc: datetime | None
    break_minutes: int
    net_minutes: int
    net_hours: Decimal
    has_corrections: bool
    is_open: bool
    has_open_break: bool
    source_event_ids: list[UUID]


class AttendanceSegmentResponse(BaseModel):
    type: SegmentType
    start_at: datetime
    end_at: datetime | None
    duration_minutes: int
    is_paid: bool


class AttendanceDayReviewResponse(BaseModel):
    id: UUID
    employee_id: UUID
    store_id: UUID
    local_date: date
    status: AttendanceDayReviewStatus
    reviewed_by_user_id: UUID
    reviewed_at_utc: datetime
    note: str


class BatchReviewAttendanceDaysRequest(BaseModel):
    employee_id: UUID
    dates: list[date] = Field(min_length=1, max_length=31)
    action: Literal["submit", "approve"]
    note: str = Field(default="", max_length=300)


class BatchReviewAttendanceDaysResponse(BaseModel):
    items: list[AttendanceDayReviewResponse]


class DailyAttendanceSummaryResponse(BaseModel):
    employee_id: UUID
    store_id: UUID
    store_timezone: str
    local_date: date
    sessions: list[WorkSessionResponse]
    total_break_minutes: int
    total_net_minutes: int
    total_net_hours: Decimal
    has_corrections: bool
    alerts: list[ComplianceAlertResponse]
    segments: list[AttendanceSegmentResponse]
    planned_minutes: int
    is_locked: bool
    daily_limit_minutes: int
    total_paid_break_minutes: int
    total_auto_deducted_break_minutes: int
    review_status: AttendanceDayReviewStatus | None


class EmployeeAttendanceLedgerResponse(BaseModel):
    employee_id: UUID
    employee_code: str
    full_name: str
    store_id: UUID
    store_code: str
    store_name: str
    store_timezone: str
    contracted_weekly_hours: Decimal
    total_net_hours: Decimal
    regular_hours: Decimal
    overtime_hours: Decimal
    annual_overtime_hours: Decimal
    closed_months: list[str]
    effective_break_policy: EffectiveBreakPolicyResponse | None = None
    day_reviews: list[AttendanceDayReviewResponse]
    daily_summaries: list[DailyAttendanceSummaryResponse]
    sessions: list[WorkSessionResponse]
    alerts: list[ComplianceAlertResponse]
    events: list[AttendanceEventResponse]


class AttendanceLedgerResponse(BaseModel):
    items: list[EmployeeAttendanceLedgerResponse]


def _to_event_response(ev: AttendanceEvent) -> AttendanceEventResponse:
    return AttendanceEventResponse(
        id=ev.id,
        tenant_id=ev.tenant_id,
        store_id=ev.store_id,
        employee_id=ev.employee_id,
        event_type=ev.event_type,
        occurred_at_utc=ev.occurred_at_utc,
        store_timezone=ev.store_timezone,
        source=ev.source,
        recorded_by_user_id=ev.recorded_by_user_id,
        recorded_at_utc=ev.recorded_at_utc,
        retention_until=ev.retention_until,
        supersedes_event_id=ev.supersedes_event_id,
        corrected_event_type=ev.corrected_event_type,
        correction_reason=ev.correction_reason,
        work_mode=ev.work_mode,
    )


def _to_alert_response(alert: ComplianceAlert) -> ComplianceAlertResponse:
    return ComplianceAlertResponse(
        code=alert.code,
        severity=alert.severity,
        employee_id=alert.employee_id,
        local_date=alert.local_date,
        message=alert.message,
        actual_value=alert.actual_value,
        limit_value=alert.limit_value,
    )


def _to_session_response(session: WorkSession) -> WorkSessionResponse:
    return WorkSessionResponse(
        employee_id=session.employee_id,
        store_id=session.store_id,
        store_timezone=session.store_timezone,
        local_date=session.local_date,
        clock_in_utc=session.clock_in_utc,
        clock_out_utc=session.clock_out_utc,
        break_minutes=session.break_minutes,
        net_minutes=session.net_minutes,
        net_hours=session.net_hours,
        has_corrections=session.has_corrections,
        is_open=session.is_open,
        has_open_break=session.has_open_break,
        source_event_ids=list(session.source_event_ids),
    )


def _to_segment_response(seg: AttendanceSegment) -> AttendanceSegmentResponse:
    return AttendanceSegmentResponse(
        type=seg.type,
        start_at=seg.start_at,
        end_at=seg.end_at,
        duration_minutes=seg.duration_minutes,
        is_paid=seg.is_paid,
    )


def _to_day_review_response(rev: AttendanceDayReview) -> AttendanceDayReviewResponse:
    return AttendanceDayReviewResponse(
        id=rev.id,
        employee_id=rev.employee_id,
        store_id=rev.store_id,
        local_date=rev.local_date,
        status=rev.status,
        reviewed_by_user_id=rev.reviewed_by_user_id,
        reviewed_at_utc=rev.reviewed_at_utc,
        note=rev.note,
    )


def _to_daily_summary_response(summary: DailyAttendanceSummary) -> DailyAttendanceSummaryResponse:
    return DailyAttendanceSummaryResponse(
        employee_id=summary.employee_id,
        store_id=summary.store_id,
        store_timezone=summary.store_timezone,
        local_date=summary.local_date,
        sessions=[_to_session_response(s) for s in summary.sessions],
        total_break_minutes=summary.total_break_minutes,
        total_net_minutes=summary.total_net_minutes,
        total_net_hours=summary.total_net_hours,
        has_corrections=summary.has_corrections,
        alerts=[_to_alert_response(a) for a in summary.alerts],
        segments=[_to_segment_response(seg) for seg in summary.segments],
        planned_minutes=summary.planned_minutes,
        is_locked=summary.is_locked,
        daily_limit_minutes=summary.daily_limit_minutes,
        total_paid_break_minutes=summary.total_paid_break_minutes,
        total_auto_deducted_break_minutes=summary.total_auto_deducted_break_minutes,
        review_status=summary.review_status,
    )


def _to_ledger_item_response(
    item: EmployeeAttendanceLedgerItem,
) -> EmployeeAttendanceLedgerResponse:
    return EmployeeAttendanceLedgerResponse(
        employee_id=item.employee.id,
        employee_code=item.employee.employee_code,
        full_name=item.employee.full_name,
        store_id=item.store.id,
        store_code=item.store.code,
        store_name=item.store.name,
        store_timezone=item.store.timezone,
        contracted_weekly_hours=item.employee.contracted_weekly_hours,
        total_net_hours=item.report.total_net_hours,
        regular_hours=item.report.regular_hours,
        overtime_hours=item.report.overtime_hours,
        annual_overtime_hours=item.report.annual_overtime_hours,
        closed_months=list(item.closed_months),
        effective_break_policy=(
            to_effective_policy_response(item.effective_break_policy)
            if item.effective_break_policy is not None
            else None
        ),
        day_reviews=[_to_day_review_response(r) for r in item.day_reviews],
        daily_summaries=[
            _to_daily_summary_response(d) for d in item.report.daily_summaries
        ],
        sessions=[_to_session_response(s) for s in item.report.sessions],
        alerts=[_to_alert_response(a) for a in item.report.alerts],
        events=[_to_event_response(e) for e in item.events],
    )


@router.post(
    "/clock",
    response_model=ClockAttendanceResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="clockAttendance",
)
async def clock_attendance(
    payload: ClockAttendanceRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    attendance_repo: Annotated[AttendanceRepository, Depends(get_attendance_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> ClockAttendanceResponse:
    result = await ClockAttendanceUseCase(
        attendance_repo,
        employee_repo,
        store_repo,
        utc_now=lambda: now_utc,
        payroll_repo=payroll_repo,
        policy_repo=policy_repo,
    ).execute(
        principal,
        ClockAttendanceCommand(
            event_type=payload.event_type,
            source=payload.source,
            store_id=payload.store_id,
            employee_code=payload.employee_code,
            work_mode=payload.work_mode,
            occurred_at_utc=payload.occurred_at_utc,
        ),
    )
    await commit()
    return ClockAttendanceResponse(
        event=_to_event_response(result.event),
        employee_id=result.employee.id,
        employee_code=result.employee.employee_code,
        employee_name=result.employee.preferred_name or result.employee.full_name,
        store_id=result.store.id,
        store_name=result.store.name,
    )


@router.post(
    "/manual-day",
    response_model=RecordManualDayResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="recordManualAttendanceDay",
)
async def record_manual_attendance_day(
    payload: RecordManualDayRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    attendance_repo: Annotated[AttendanceRepository, Depends(get_attendance_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> RecordManualDayResponse:
    result = await RecordManualDayUseCase(
        attendance_repo,
        employee_repo,
        store_repo,
        utc_now=lambda: now_utc,
        payroll_repo=payroll_repo,
        policy_repo=policy_repo,
    ).execute(
        principal,
        RecordManualDayCommand(
            employee_id=payload.employee_id,
            local_date=payload.local_date,
            clock_in_time=payload.clock_in_time,
            break_start_time=payload.break_start_time,
            break_end_time=payload.break_end_time,
            clock_out_time=payload.clock_out_time,
            work_mode=payload.work_mode,
            note=payload.note,
        ),
    )
    await commit()
    return RecordManualDayResponse(
        events=[_to_event_response(ev) for ev in result.events],
        employee_id=result.employee.id,
        employee_code=result.employee.employee_code,
        employee_name=result.employee.preferred_name or result.employee.full_name,
        store_id=result.store.id,
        store_name=result.store.name,
    )


@router.post(
    "/corrections",
    response_model=AttendanceEventResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="appendAttendanceCorrection",
)
async def append_attendance_correction(
    payload: AppendCorrectionRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    attendance_repo: Annotated[AttendanceRepository, Depends(get_attendance_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
) -> AttendanceEventResponse:
    event = await AppendCorrectionUseCase(
        attendance_repo, employee_repo, store_repo, payroll_repo=payroll_repo
    ).execute(
        principal,
        AppendCorrectionCommand(
            employee_id=payload.employee_id,
            corrected_event_type=payload.corrected_event_type,
            occurred_at_utc=payload.occurred_at_utc,
            correction_reason=payload.correction_reason,
            store_id=payload.store_id,
            supersedes_event_id=payload.supersedes_event_id,
        ),
    )
    await commit()
    return _to_event_response(event)


@router.get(
    "/ledger",
    response_model=AttendanceLedgerResponse,
    status_code=status.HTTP_200_OK,
    operation_id="getAttendanceLedger",
)
async def get_attendance_ledger(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    attendance_repo: Annotated[AttendanceRepository, Depends(get_attendance_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    shift_repo: Annotated[ShiftRepository, Depends(get_shift_repository)],
    day_review_repo: Annotated[
        AttendanceDayReviewRepository, Depends(get_attendance_day_review_repository)
    ],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    store_id: Annotated[UUID | None, Query()] = None,
    employee_id: Annotated[UUID | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> AttendanceLedgerResponse:
    result = await GetAttendanceLedgerUseCase(
        attendance_repo,
        employee_repo,
        store_repo,
        utc_now=lambda: now_utc,
        payroll_repo=payroll_repo,
        policy_repo=policy_repo,
        shift_repo=shift_repo,
        day_review_repo=day_review_repo,
    ).execute(
        principal,
        store_id=store_id,
        employee_id=employee_id,
        date_from=date_from,
        date_to=date_to,
    )
    if result.has_mutations:
        await commit()
    return AttendanceLedgerResponse(
        items=[_to_ledger_item_response(item) for item in result.items]
    )


@router.post(
    "/day-reviews",
    response_model=BatchReviewAttendanceDaysResponse,
    status_code=status.HTTP_200_OK,
    operation_id="batchReviewAttendanceDays",
)
async def batch_review_attendance_days(
    payload: BatchReviewAttendanceDaysRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    attendance_repo: Annotated[AttendanceRepository, Depends(get_attendance_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    day_review_repo: Annotated[
        AttendanceDayReviewRepository, Depends(get_attendance_day_review_repository)
    ],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> BatchReviewAttendanceDaysResponse:
    reviews = await BatchReviewAttendanceDaysUseCase(
        attendance_repo,
        employee_repo,
        store_repo,
        day_review_repo,
        utc_now=lambda: now_utc,
        payroll_repo=payroll_repo,
        policy_repo=policy_repo,
    ).execute(
        principal,
        BatchReviewAttendanceDaysCommand(
            employee_id=payload.employee_id,
            dates=tuple(payload.dates),
            action=payload.action,
            note=payload.note,
        ),
    )
    await commit()
    return BatchReviewAttendanceDaysResponse(
        items=[_to_day_review_response(r) for r in reviews]
    )


@router.get(
    "/export.csv",
    status_code=status.HTTP_200_OK,
    operation_id="exportAttendanceCsv",
    responses={
        200: {
            "content": {"text/csv": {}},
            "description": "ITSS-compliant Spanish (es-ES) attendance CSV export",
        }
    },
)
async def export_attendance_csv(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    attendance_repo: Annotated[AttendanceRepository, Depends(get_attendance_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    tenant_repo: Annotated[TenantRepository, Depends(get_tenant_repository)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    store_id: Annotated[UUID | None, Query()] = None,
    employee_id: Annotated[UUID | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> Response:
    ledger_uc = GetAttendanceLedgerUseCase(
        attendance_repo,
        employee_repo,
        store_repo,
        utc_now=lambda: now_utc,
        payroll_repo=payroll_repo,
        policy_repo=policy_repo,
    )
    csv_text = await ExportAttendanceCsvUseCase(ledger_uc, tenant_repo).execute(
        principal,
        store_id=store_id,
        employee_id=employee_id,
        date_from=date_from,
        date_to=date_to,
    )
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="registro_jornada_itss.csv"',
        },
    )


@router.get(
    "/export.pdf",
    status_code=status.HTTP_200_OK,
    operation_id="exportAttendancePdf",
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "ITSS-compliant Spanish (es-ES) A4 attendance PDF export",
        }
    },
)
async def export_attendance_pdf(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    attendance_repo: Annotated[AttendanceRepository, Depends(get_attendance_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    tenant_repo: Annotated[TenantRepository, Depends(get_tenant_repository)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    shift_repo: Annotated[ShiftRepository, Depends(get_shift_repository)],
    day_review_repo: Annotated[
        AttendanceDayReviewRepository, Depends(get_attendance_day_review_repository)
    ],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    pdf_renderer: Annotated[AttendancePdfRendererPort, Depends(get_pdf_renderer)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    store_id: Annotated[UUID | None, Query()] = None,
    employee_id: Annotated[UUID | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> Response:
    ledger_uc = GetAttendanceLedgerUseCase(
        attendance_repo,
        employee_repo,
        store_repo,
        utc_now=lambda: now_utc,
        payroll_repo=payroll_repo,
        policy_repo=policy_repo,
        shift_repo=shift_repo,
        day_review_repo=day_review_repo,
    )
    pdf_bytes, filename, has_mutations = await ExportAttendancePdfUseCase(
        ledger_uc,
        tenant_repo,
        pdf_renderer,
        leave_repo=leave_repo,
        utc_now=lambda: now_utc,
    ).execute(
        principal,
        store_id=store_id,
        employee_id=employee_id,
        date_from=date_from,
        date_to=date_to,
    )
    if has_mutations:
        await commit()
    encoded_filename = quote(filename)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": (
                f'attachment; filename="{filename}"; filename*=UTF-8\'\'{encoded_filename}'
            ),
        },
    )
