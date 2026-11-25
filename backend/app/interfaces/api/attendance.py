from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from app.application.attendance import (
    AppendCorrectionCommand,
    AppendCorrectionUseCase,
    ClockAttendanceCommand,
    ClockAttendanceUseCase,
    EmployeeAttendanceLedgerItem,
    ExportAttendanceCsvUseCase,
    GetAttendanceLedgerUseCase,
)
from app.domain.attendance import (
    AlertCode,
    AlertSeverity,
    AttendanceEvent,
    AttendanceEventType,
    AttendanceSource,
    ComplianceAlert,
    CorrectedEventType,
    DailyAttendanceSummary,
    WorkSession,
)
from app.domain.repositories import (
    AttendanceRepository,
    EmployeeRepository,
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
    get_employee_repository,
    get_payroll_closing_repository,
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


class EmployeeAttendanceLedgerResponse(BaseModel):
    employee_id: UUID
    employee_code: str
    full_name: str
    store_id: UUID
    store_code: str
    store_name: str
    store_timezone: str
    total_net_hours: Decimal
    regular_hours: Decimal
    overtime_hours: Decimal
    annual_overtime_hours: Decimal
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
        total_net_hours=item.report.total_net_hours,
        regular_hours=item.report.regular_hours,
        overtime_hours=item.report.overtime_hours,
        annual_overtime_hours=item.report.annual_overtime_hours,
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
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> ClockAttendanceResponse:
    result = await ClockAttendanceUseCase(
        attendance_repo,
        employee_repo,
        store_repo,
        utc_now=lambda: now_utc,
        payroll_repo=payroll_repo,
    ).execute(
        principal,
        ClockAttendanceCommand(
            event_type=payload.event_type,
            source=payload.source,
            store_id=payload.store_id,
            employee_code=payload.employee_code,
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
    store_id: Annotated[UUID | None, Query()] = None,
    employee_id: Annotated[UUID | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> AttendanceLedgerResponse:
    result = await GetAttendanceLedgerUseCase(attendance_repo, employee_repo, store_repo).execute(
        principal,
        store_id=store_id,
        employee_id=employee_id,
        date_from=date_from,
        date_to=date_to,
    )
    return AttendanceLedgerResponse(
        items=[_to_ledger_item_response(item) for item in result.items]
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
    store_id: Annotated[UUID | None, Query()] = None,
    employee_id: Annotated[UUID | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> Response:
    ledger_uc = GetAttendanceLedgerUseCase(attendance_repo, employee_repo, store_repo)
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
