"""FastAPI router for Admin & Employee Workspace Dashboards."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel

from app.application.dashboard import GetAdminDashboardUseCase, GetMyDashboardUseCase
from app.domain.attendance import WorkMode
from app.domain.dashboard import (
    DashboardAnnouncementSummary,
    DashboardMemberItem,
    TodayClockSegment,
    UpcomingAnniversaryItem,
    UpcomingBirthdayItem,
    WhoIsInStatus,
)
from app.domain.documents import DocumentStoragePort
from app.domain.holidays import Holiday, HolidayScope
from app.domain.repositories import (
    AnnouncementRepository,
    AttendanceRepository,
    BreakPolicyRepository,
    DocumentRepository,
    EmployeeRepository,
    LeaveEntitlementRepository,
    LeaveRepository,
    MembershipRepository,
    PayrollClosingRepository,
    ShiftRepository,
    StoreRepository,
)
from app.domain.security import AuthenticatedPrincipal, UserRole
from app.interfaces.api.break_policies import (
    EffectiveBreakPolicyResponse,
    to_effective_policy_response,
)
from app.interfaces.api.deps import (
    CommitFn,
    get_announcement_repository,
    get_attendance_repository,
    get_break_policy_repository,
    get_commit_fn,
    get_current_principal,
    get_document_repository,
    get_document_storage,
    get_employee_repository,
    get_leave_entitlement_repository,
    get_leave_repository,
    get_membership_repository,
    get_payroll_closing_repository,
    get_shift_repository,
    get_store_repository,
    get_utc_now,
)
from app.interfaces.api.documents import DocumentListItemResponse, _to_doc_response
from app.interfaces.api.leaves import VacationBalanceResponse

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardMemberItemResponse(BaseModel):
    employee_id: UUID
    full_name: str
    preferred_name: str
    employee_code: str
    department: str
    store_name: str
    avatar_url: str | None
    since_utc: datetime | None = None
    job_title: str = ""


class WhoIsInGroupResponse(BaseModel):
    status: WhoIsInStatus
    count: int
    preview_members: list[DashboardMemberItemResponse]
    overflow_count: int
    members: list[DashboardMemberItemResponse]


class UpcomingBirthdayResponse(BaseModel):
    employee_id: UUID
    full_name: str
    preferred_name: str
    department: str
    store_name: str
    avatar_url: str | None
    birth_month: int
    birth_day: int
    days_until: int


class UpcomingAnniversaryResponse(BaseModel):
    employee_id: UUID
    full_name: str
    preferred_name: str
    job_title: str
    department: str
    store_name: str
    avatar_url: str | None
    anniversary_month: int
    anniversary_day: int
    years_of_service: int
    days_until: int


class TodayClockSegmentResponse(BaseModel):
    kind: Literal["work", "break"]
    start_utc: datetime
    end_utc: datetime | None
    duration_minutes: int
    work_mode: WorkMode


class UpcomingHolidayResponse(BaseModel):
    date: date
    name_es: str
    name_zh: str
    scope: HolidayScope
    region_code: str | None


class QuickReportEntryResponse(BaseModel):
    key: str
    title_es: str
    title_zh: str
    endpoint: str
    available: bool


class DashboardAnnouncementSummaryResponse(BaseModel):
    id: UUID
    category: str
    title: str
    is_pinned: bool
    requires_acknowledgement: bool
    published_at_utc: datetime
    acknowledged_count: int
    target_count: int
    acknowledged_by_self: bool = False


class AdminDashboardResponse(BaseModel):
    who_is_in: list[WhoIsInGroupResponse]
    upcoming_birthdays: list[UpcomingBirthdayResponse]
    upcoming_holidays: list[UpcomingHolidayResponse]
    quick_reports: list[QuickReportEntryResponse]
    recent_announcements: list[DashboardAnnouncementSummaryResponse]
    pending_leaves_count: int
    upcoming_anniversaries: list[UpcomingAnniversaryResponse] = []
    attendance_alerts_count: int = 0
    pending_justificantes_count: int = 0
    unacknowledged_announcements_count: int = 0
    available_departments: list[str] = []


class MyDashboardEmployeeSummaryResponse(BaseModel):
    id: UUID
    employee_code: str
    full_name: str
    preferred_name: str
    department: str
    store_id: UUID
    store_name: str
    avatar_url: str | None
    contracted_weekly_hours: Decimal
    job_title: str = ""


class EmployeeClockStateResponse(BaseModel):
    status: str
    work_mode: WorkMode
    active_clock_in_utc: datetime | None
    active_break_start_utc: datetime | None
    worked_minutes_today: int
    break_minutes_today: int
    target_daily_minutes: int
    today_shift_start_utc: datetime | None
    today_shift_end_utc: datetime | None
    saldo_minutes: int
    today_segments: list[TodayClockSegmentResponse] = []
    default_break_minutes: int | None = None
    auto_break_end_utc: datetime | None = None
    effective_break_policy: EffectiveBreakPolicyResponse | None = None


class DashboardApproverResponse(BaseModel):
    user_id: UUID
    display_name: str
    role: UserRole


class DashboardNotificationResponse(BaseModel):
    kind: str
    local_date: date
    message: str
    severity: str


class MyDashboardResponse(BaseModel):
    has_employee_profile: bool
    employee: MyDashboardEmployeeSummaryResponse | None
    clock_state: EmployeeClockStateResponse | None
    teammates: list[DashboardMemberItemResponse]
    attendance_approvers: list[DashboardApproverResponse]
    leave_approvers: list[DashboardApproverResponse]
    notifications: list[DashboardNotificationResponse]
    vacation_balance: VacationBalanceResponse | None
    pending_announcements: list[DashboardAnnouncementSummaryResponse]
    recent_announcements: list[DashboardAnnouncementSummaryResponse]
    recent_documents: list[DocumentListItemResponse]
    upcoming_birthdays: list[UpcomingBirthdayResponse]
    upcoming_holidays: list[UpcomingHolidayResponse]
    upcoming_anniversaries: list[UpcomingAnniversaryResponse] = []


def _to_member_resp(m: DashboardMemberItem) -> DashboardMemberItemResponse:
    return DashboardMemberItemResponse(
        employee_id=m.employee_id,
        full_name=m.full_name,
        preferred_name=m.preferred_name,
        employee_code=m.employee_code,
        department=m.department,
        store_name=m.store_name,
        avatar_url=m.avatar_url,
        since_utc=m.since_utc,
        job_title=m.job_title,
    )


def _to_anniversary_resp(a: UpcomingAnniversaryItem) -> UpcomingAnniversaryResponse:
    return UpcomingAnniversaryResponse(
        employee_id=a.employee_id,
        full_name=a.full_name,
        preferred_name=a.preferred_name,
        job_title=a.job_title,
        department=a.department,
        store_name=a.store_name,
        avatar_url=a.avatar_url,
        anniversary_month=a.anniversary_month,
        anniversary_day=a.anniversary_day,
        years_of_service=a.years_of_service,
        days_until=a.days_until,
    )


def _to_segment_resp(s: TodayClockSegment) -> TodayClockSegmentResponse:
    return TodayClockSegmentResponse(
        kind=s.kind,
        start_utc=s.start_utc,
        end_utc=s.end_utc,
        duration_minutes=s.duration_minutes,
        work_mode=s.work_mode,
    )


def _to_bday_resp(b: UpcomingBirthdayItem) -> UpcomingBirthdayResponse:
    return UpcomingBirthdayResponse(
        employee_id=b.employee_id,
        full_name=b.full_name,
        preferred_name=b.preferred_name,
        department=b.department,
        store_name=b.store_name,
        avatar_url=b.avatar_url,
        birth_month=b.birth_month,
        birth_day=b.birth_day,
        days_until=b.days_until,
    )


def _to_holiday_resp(h: Holiday) -> UpcomingHolidayResponse:
    return UpcomingHolidayResponse(
        date=h.date,
        name_es=h.name_es,
        name_zh=h.name_zh,
        scope=h.scope,
        region_code=h.region_code,
    )


def _to_ann_summary_resp(
    a: DashboardAnnouncementSummary,
) -> DashboardAnnouncementSummaryResponse:
    return DashboardAnnouncementSummaryResponse(
        id=a.id,
        category=a.category,
        title=a.title,
        is_pinned=a.is_pinned,
        requires_acknowledgement=a.requires_acknowledgement,
        published_at_utc=a.published_at_utc,
        acknowledged_count=a.acknowledged_count,
        target_count=a.target_count,
        acknowledged_by_self=a.acknowledged_by_self,
    )


@router.get(
    "/admin",
    response_model=AdminDashboardResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_admin_dashboard",
)
async def get_admin_dashboard(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    attendance_repo: Annotated[AttendanceRepository, Depends(get_attendance_repository)],
    shift_repo: Annotated[ShiftRepository, Depends(get_shift_repository)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    ann_repo: Annotated[AnnouncementRepository, Depends(get_announcement_repository)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    store_id: Annotated[UUID | None, Query()] = None,
    department: Annotated[str | None, Query()] = None,
) -> AdminDashboardResponse:
    res = await GetAdminDashboardUseCase(
        employee_repo=employee_repo,
        store_repo=store_repo,
        attendance_repo=attendance_repo,
        shift_repo=shift_repo,
        leave_repo=leave_repo,
        ann_repo=ann_repo,
        storage=storage,
        payroll_repo=payroll_repo,
        policy_repo=policy_repo,
    ).execute(principal, store_id=store_id, department=department, now_utc=now_utc)
    if res.has_mutations:
        await commit()
    return AdminDashboardResponse(
        who_is_in=[
            WhoIsInGroupResponse(
                status=g.status,
                count=g.count,
                preview_members=[_to_member_resp(m) for m in g.preview_members],
                overflow_count=g.overflow_count,
                members=[_to_member_resp(m) for m in g.members],
            )
            for g in res.who_is_in
        ],
        upcoming_birthdays=[_to_bday_resp(b) for b in res.upcoming_birthdays],
        upcoming_holidays=[_to_holiday_resp(h) for h in res.upcoming_holidays],
        quick_reports=[
            QuickReportEntryResponse(
                key=r.key,
                title_es=r.title_es,
                title_zh=r.title_zh,
                endpoint=r.endpoint,
                available=r.available,
            )
            for r in res.quick_reports
        ],
        recent_announcements=[_to_ann_summary_resp(a) for a in res.recent_announcements],
        pending_leaves_count=res.pending_leaves_count,
        upcoming_anniversaries=[_to_anniversary_resp(a) for a in res.upcoming_anniversaries],
        attendance_alerts_count=res.attendance_alerts_count,
        pending_justificantes_count=res.pending_justificantes_count,
        unacknowledged_announcements_count=res.unacknowledged_announcements_count,
        available_departments=list(res.available_departments),
    )


@router.get(
    "/me",
    response_model=MyDashboardResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_my_dashboard",
)
async def get_my_dashboard(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    membership_repo: Annotated[MembershipRepository, Depends(get_membership_repository)],
    attendance_repo: Annotated[AttendanceRepository, Depends(get_attendance_repository)],
    shift_repo: Annotated[ShiftRepository, Depends(get_shift_repository)],
    leave_repo: Annotated[LeaveRepository, Depends(get_leave_repository)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    ann_repo: Annotated[AnnouncementRepository, Depends(get_announcement_repository)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
    payroll_repo: Annotated[PayrollClosingRepository, Depends(get_payroll_closing_repository)],
    policy_repo: Annotated[BreakPolicyRepository, Depends(get_break_policy_repository)],
    entitlement_repo: Annotated[
        LeaveEntitlementRepository, Depends(get_leave_entitlement_repository)
    ],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> MyDashboardResponse:
    res = await GetMyDashboardUseCase(
        employee_repo=employee_repo,
        store_repo=store_repo,
        membership_repo=membership_repo,
        attendance_repo=attendance_repo,
        shift_repo=shift_repo,
        leave_repo=leave_repo,
        doc_repo=doc_repo,
        ann_repo=ann_repo,
        storage=storage,
        payroll_repo=payroll_repo,
        policy_repo=policy_repo,
        entitlement_repo=entitlement_repo,
    ).execute(principal, now_utc=now_utc)
    if res.has_mutations:
        await commit()
    return MyDashboardResponse(
        has_employee_profile=res.has_employee_profile,
        employee=(
            MyDashboardEmployeeSummaryResponse(
                id=res.employee.id,
                employee_code=res.employee.employee_code,
                full_name=res.employee.full_name,
                preferred_name=res.employee.preferred_name,
                department=res.employee.department,
                store_id=res.employee.store_id,
                store_name=res.employee.store_name,
                avatar_url=res.employee.avatar_url,
                contracted_weekly_hours=res.employee.contracted_weekly_hours,
                job_title=res.employee.job_title,
            )
            if res.employee
            else None
        ),
        clock_state=(
            EmployeeClockStateResponse(
                status=res.clock_state.status,
                work_mode=res.clock_state.work_mode,
                active_clock_in_utc=res.clock_state.active_clock_in_utc,
                active_break_start_utc=res.clock_state.active_break_start_utc,
                worked_minutes_today=res.clock_state.worked_minutes_today,
                break_minutes_today=res.clock_state.break_minutes_today,
                target_daily_minutes=res.clock_state.target_daily_minutes,
                today_shift_start_utc=res.clock_state.today_shift_start_utc,
                today_shift_end_utc=res.clock_state.today_shift_end_utc,
                saldo_minutes=res.clock_state.saldo_minutes,
                today_segments=[_to_segment_resp(s) for s in res.clock_state.today_segments],
                default_break_minutes=res.clock_state.default_break_minutes,
                auto_break_end_utc=res.clock_state.auto_break_end_utc,
                effective_break_policy=(
                    to_effective_policy_response(res.clock_state.effective_break_policy)
                    if res.clock_state.effective_break_policy is not None
                    else None
                ),
            )
            if res.clock_state
            else None
        ),
        teammates=[_to_member_resp(t) for t in res.teammates],
        attendance_approvers=[
            DashboardApproverResponse(
                user_id=a.user_id, display_name=a.display_name, role=a.role
            )
            for a in res.attendance_approvers
        ],
        leave_approvers=[
            DashboardApproverResponse(
                user_id=a.user_id, display_name=a.display_name, role=a.role
            )
            for a in res.leave_approvers
        ],
        notifications=[
            DashboardNotificationResponse(
                kind=n.kind,
                local_date=n.local_date,
                message=n.message,
                severity=n.severity,
            )
            for n in res.notifications
        ],
        vacation_balance=(
            VacationBalanceResponse(
                employee_id=res.vacation_balance.employee_id,
                year=res.vacation_balance.year,
                entitled_days=res.vacation_balance.entitled_days,
                approved_days=res.vacation_balance.approved_days,
                pending_days=res.vacation_balance.pending_days,
                remaining_days=res.vacation_balance.remaining_days,
            )
            if res.vacation_balance
            else None
        ),
        pending_announcements=[_to_ann_summary_resp(a) for a in res.pending_announcements],
        recent_announcements=[_to_ann_summary_resp(a) for a in res.recent_announcements],
        recent_documents=[
            DocumentListItemResponse(
                document=_to_doc_response(d.document),
                receipt_count=d.receipt_count,
                signed_by_self=d.signed_by_self,
                latest_signed_at_utc=d.latest_signed_at_utc,
            )
            for d in res.recent_documents
        ],
        upcoming_birthdays=[_to_bday_resp(b) for b in res.upcoming_birthdays],
        upcoming_holidays=[_to_holiday_resp(h) for h in res.upcoming_holidays],
        upcoming_anniversaries=[_to_anniversary_resp(a) for a in res.upcoming_anniversaries],
    )
