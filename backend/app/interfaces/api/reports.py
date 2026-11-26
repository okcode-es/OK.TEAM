"""API router for Management Reports Center (/api/v1/reports/*)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel

from app.application.reports import (
    GetAttendanceSummaryReportUseCase,
    GetExecutiveSummaryUseCase,
    GetHeadcountRosterReportUseCase,
    GetLeaveSummaryReportUseCase,
)
from app.domain.repositories import (
    AnnouncementRepository,
    AttendanceRepository,
    BreakPolicyRepository,
    EmployeeRepository,
    LeaveEntitlementRepository,
    LeaveRepository,
    StoreRepository,
)
from app.domain.security import AuthenticatedPrincipal, UserRole
from app.interfaces.api.deps import (
    get_announcement_repository,
    get_attendance_repository,
    get_break_policy_repository,
    get_current_principal,
    get_employee_repository,
    get_leave_entitlement_repository,
    get_leave_repository,
    get_store_repository,
    get_utc_now,
)

router = APIRouter(prefix="/reports", tags=["reports"])


class StoreReportSummaryResponse(BaseModel):
    store_id: UUID
    store_name: str
    active_employees: int
    worked_hours: Decimal
    overtime_hours: Decimal
    compliance_alerts: int
    pending_leaves: int


class ExecutiveSummaryResponse(BaseModel):
    from_date: date
    to_date: date
    total_active_employees: int
    full_time_count: int
    part_time_count: int
    contract_breakdown: dict[str, int]
    total_worked_hours: Decimal
    total_regular_hours: Decimal
    total_overtime_hours: Decimal
    total_compliance_alerts: int
    pending_manager_leaves: int
    pending_hr_leaves: int
    approved_vacation_days: int
    approved_sick_days: int | None
    announcements_pending_ack_count: int
    by_store: list[StoreReportSummaryResponse]


@router.get(
    "/executive-summary",
    response_model=ExecutiveSummaryResponse,
    status_code=status.HTTP_200_OK,
    operation_id="getExecutiveSummaryReport",
)
async def get_executive_summary(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    attendance_repo: Annotated[AttendanceRepository, Depends(get_attendance_repository)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    ann_repo: Annotated[AnnouncementRepository, Depends(get_announcement_repository)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    from_date: Annotated[date, Query()],
    to_date: Annotated[date, Query()],
    store_id: Annotated[UUID | None, Query()] = None,
    department: Annotated[str | None, Query()] = None,
) -> ExecutiveSummaryResponse:
    summary = await GetExecutiveSummaryUseCase(
        employee_repo, store_repo, attendance_repo, leave_repo, ann_repo, policy_repo=policy_repo
    ).execute(
        principal,
        from_date=from_date,
        to_date=to_date,
        store_id=store_id,
        department=department,
    )
    return ExecutiveSummaryResponse(
        from_date=summary.from_date,
        to_date=summary.to_date,
        total_active_employees=summary.total_active_employees,
        full_time_count=summary.full_time_count,
        part_time_count=summary.part_time_count,
        contract_breakdown=summary.contract_breakdown,
        total_worked_hours=summary.total_worked_hours,
        total_regular_hours=summary.total_regular_hours,
        total_overtime_hours=summary.total_overtime_hours,
        total_compliance_alerts=summary.total_compliance_alerts,
        pending_manager_leaves=summary.pending_manager_leaves,
        pending_hr_leaves=summary.pending_hr_leaves,
        approved_vacation_days=summary.approved_vacation_days,
        approved_sick_days=summary.approved_sick_days,
        announcements_pending_ack_count=summary.announcements_pending_ack_count,
        by_store=[
            StoreReportSummaryResponse(
                store_id=s.store_id,
                store_name=s.store_name,
                active_employees=s.active_employees,
                worked_hours=s.worked_hours,
                overtime_hours=s.overtime_hours,
                compliance_alerts=s.compliance_alerts,
                pending_leaves=s.pending_leaves,
            )
            for s in summary.by_store
        ],
    )


@router.get(
    "/attendance-summary",
    status_code=status.HTTP_200_OK,
    operation_id="exportAttendanceSummaryReport",
    responses={
        200: {
            "content": {"text/csv": {}, "application/pdf": {}},
            "description": "Attendance summary report in CSV (UTF-8 BOM) or A4 PDF",
        }
    },
)
async def get_attendance_summary_report(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    attendance_repo: Annotated[AttendanceRepository, Depends(get_attendance_repository)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    from_date: Annotated[date, Query()],
    to_date: Annotated[date, Query()],
    store_id: Annotated[UUID | None, Query()] = None,
    department: Annotated[str | None, Query()] = None,
    export_format: Annotated[Literal["csv", "pdf"], Query(alias="format")] = "csv",
) -> Response:
    uc = GetAttendanceSummaryReportUseCase(
        employee_repo, store_repo, attendance_repo, policy_repo=policy_repo
    )
    rows = await uc.execute(
        principal,
        from_date=from_date,
        to_date=to_date,
        store_id=store_id,
        department=department,
    )
    if export_format == "pdf":
        pdf_bytes = uc.to_pdf(rows, from_date=from_date, to_date=to_date)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="resumen_jornada_{from_date}_{to_date}.pdf"'
                )
            },
        )
    csv_bytes = uc.to_csv(rows)
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="resumen_jornada_{from_date}_{to_date}.csv"'
            )
        },
    )


@router.get(
    "/leave-summary",
    status_code=status.HTTP_200_OK,
    operation_id="exportLeaveSummaryReport",
    responses={
        200: {
            "content": {"text/csv": {}, "application/pdf": {}},
            "description": "Leave summary report in CSV (UTF-8 BOM) or A4 PDF",
        }
    },
)
async def get_leave_summary_report(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    entitlement_repo: Annotated[
        LeaveEntitlementRepository, Depends(get_leave_entitlement_repository)
    ],
    year: Annotated[int, Query(ge=2020, le=2100)],
    store_id: Annotated[UUID | None, Query()] = None,
    department: Annotated[str | None, Query()] = None,
    export_format: Annotated[Literal["csv", "pdf"], Query(alias="format")] = "csv",
) -> Response:
    uc = GetLeaveSummaryReportUseCase(
        employee_repo, store_repo, leave_repo, entitlement_repo=entitlement_repo
    )
    rows = await uc.execute(
        principal,
        year=year,
        store_id=store_id,
        department=department,
    )
    redact_health = principal.role == UserRole.ACCOUNTANT
    if export_format == "pdf":
        pdf_bytes = uc.to_pdf(rows, year=year, redact_health=redact_health)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="resumen_ausencias_{year}.pdf"'
            },
        )
    csv_bytes = uc.to_csv(rows, redact_health=redact_health)
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="resumen_ausencias_{year}.csv"'},
    )


@router.get(
    "/headcount-roster",
    status_code=status.HTTP_200_OK,
    operation_id="exportHeadcountRosterReport",
    responses={
        200: {
            "content": {"text/csv": {}, "application/pdf": {}},
            "description": "Headcount roster report in CSV (UTF-8 BOM) or A4 PDF",
        }
    },
)
async def get_headcount_roster_report(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    store_id: Annotated[UUID | None, Query()] = None,
    department: Annotated[str | None, Query()] = None,
    export_format: Annotated[Literal["csv", "pdf"], Query(alias="format")] = "csv",
) -> Response:
    uc = GetHeadcountRosterReportUseCase(employee_repo, store_repo)
    rows = await uc.execute(
        principal,
        store_id=store_id,
        department=department,
        as_of_date=now_utc.date(),
    )
    if export_format == "pdf":
        pdf_bytes = uc.to_pdf(rows)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="plantilla_empleados.pdf"'},
        )
    csv_bytes = uc.to_csv(rows)
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="plantilla_empleados.csv"'},
    )
