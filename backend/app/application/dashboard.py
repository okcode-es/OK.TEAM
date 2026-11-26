"""Application use cases for Admin & Employee Workspace Dashboards."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.application.announcements import ListAnnouncementsUseCase
from app.application.attendance import auto_complete_elapsed_break_if_due
from app.application.break_policies import build_break_policy_lookup, resolve_policy_for_employee
from app.application.documents import DocumentListItem, ListDocumentsUseCase
from app.application.employees import mint_avatar_url
from app.domain.announcements import is_employee_in_announcement_scope
from app.domain.attendance import (
    AttendanceEvent,
    AttendanceEventType,
    WorkMode,
    compute_auto_break_end_utc,
    evaluate_attendance_ledger,
    fold_attendance_events,
    resolve_timezone,
)
from app.domain.break_policies import EffectiveBreakPolicy, resolve_effective_break_policy
from app.domain.dashboard import (
    DashboardAnnouncementSummary,
    DashboardMemberItem,
    QuickReportEntry,
    TodayClockSegment,
    UpcomingAnniversaryItem,
    UpcomingBirthdayItem,
    WhoIsInGroup,
    WhoIsInStatus,
    build_today_clock_segments,
    compute_target_daily_minutes,
    days_until_anniversary,
    days_until_birthday,
)
from app.domain.documents import DocumentStoragePort
from app.domain.employees import Employee, EmploymentStatus
from app.domain.holidays import Holiday, list_upcoming_holidays
from app.domain.leaves import (
    PENDING_LEAVE_STATUSES,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
    VacationBalance,
    compute_vacation_balance,
)
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
from app.domain.scheduling import Shift, ShiftStatus
from app.domain.security import (
    AuthenticatedPrincipal,
    ForbiddenScopeError,
    MembershipStatus,
    UserRole,
)

_ADMIN_DASHBOARD_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.OWNER, UserRole.HR, UserRole.STORE_MANAGER, UserRole.ACCOUNTANT}
)
_APPROVER_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.OWNER, UserRole.HR, UserRole.STORE_MANAGER}
)


@dataclass(frozen=True, slots=True)
class AdminDashboardResult:
    who_is_in: tuple[WhoIsInGroup, ...]
    upcoming_birthdays: tuple[UpcomingBirthdayItem, ...]
    upcoming_holidays: tuple[Holiday, ...]
    quick_reports: tuple[QuickReportEntry, ...]
    recent_announcements: tuple[DashboardAnnouncementSummary, ...]
    pending_leaves_count: int
    upcoming_anniversaries: tuple[UpcomingAnniversaryItem, ...] = ()
    attendance_alerts_count: int = 0
    pending_justificantes_count: int = 0
    unacknowledged_announcements_count: int = 0
    available_departments: tuple[str, ...] = ()
    has_mutations: bool = False


@dataclass(frozen=True, slots=True)
class EmployeeClockState:
    status: str  # not_clocked_in | working | on_break
    work_mode: WorkMode
    active_clock_in_utc: datetime | None
    active_break_start_utc: datetime | None
    worked_minutes_today: int
    break_minutes_today: int
    target_daily_minutes: int
    today_shift_start_utc: datetime | None
    today_shift_end_utc: datetime | None
    saldo_minutes: int
    today_segments: tuple[TodayClockSegment, ...] = ()
    default_break_minutes: int | None = None
    auto_break_end_utc: datetime | None = None
    effective_break_policy: EffectiveBreakPolicy | None = None


@dataclass(frozen=True, slots=True)
class DashboardApproverItem:
    user_id: UUID
    display_name: str
    role: UserRole


@dataclass(frozen=True, slots=True)
class DashboardNotificationItem:
    kind: str
    local_date: date
    message: str
    severity: str


@dataclass(frozen=True, slots=True)
class MyDashboardEmployeeSummary:
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


@dataclass(frozen=True, slots=True)
class MyDashboardResult:
    has_employee_profile: bool
    employee: MyDashboardEmployeeSummary | None
    clock_state: EmployeeClockState | None
    teammates: tuple[DashboardMemberItem, ...]
    attendance_approvers: tuple[DashboardApproverItem, ...]
    leave_approvers: tuple[DashboardApproverItem, ...]
    notifications: tuple[DashboardNotificationItem, ...]
    vacation_balance: VacationBalance | None
    pending_announcements: tuple[DashboardAnnouncementSummary, ...]
    recent_announcements: tuple[DashboardAnnouncementSummary, ...]
    recent_documents: tuple[DocumentListItem, ...]
    upcoming_birthdays: tuple[UpcomingBirthdayItem, ...]
    upcoming_holidays: tuple[Holiday, ...]
    upcoming_anniversaries: tuple[UpcomingAnniversaryItem, ...] = ()
    has_mutations: bool = False


class GetAdminDashboardUseCase:
    def __init__(
        self,
        *,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        attendance_repo: AttendanceRepository,
        shift_repo: ShiftRepository,
        leave_repo: LeaveRepository,
        ann_repo: AnnouncementRepository,
        storage: DocumentStoragePort,
        payroll_repo: PayrollClosingRepository | None = None,
        policy_repo: BreakPolicyRepository | None = None,
    ) -> None:
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._attendance_repo = attendance_repo
        self._shift_repo = shift_repo
        self._leave_repo = leave_repo
        self._ann_repo = ann_repo
        self._storage = storage
        self._payroll_repo = payroll_repo
        self._policy_repo = policy_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        store_id: UUID | None = None,
        department: str | None = None,
        now_utc: datetime,
    ) -> AdminDashboardResult:
        if principal.role not in _ADMIN_DASHBOARD_ROLES:
            raise ForbiddenScopeError("Employees cannot access the admin dashboard")

        if principal.role == UserRole.STORE_MANAGER:
            if store_id is not None:
                principal.require_store_access(principal.tenant_id, store_id)
            if (
                principal.department_scope
                and principal.department_scope.strip()
                and department
                and department.strip()
                and department.strip().casefold() != principal.department_scope.strip().casefold()
            ):
                raise ForbiddenScopeError(
                    "Requested department is outside your authorized department_scope"
                )

        now = now_utc.astimezone(UTC)
        stores = await self._store_repo.list_by_tenant(principal.tenant_id, include_inactive=False)
        store_map = {s.id: s for s in stores}
        if store_id is not None and store_id not in store_map:
            # Check inactive too or raise
            all_stores = await self._store_repo.list_by_tenant(
                principal.tenant_id, include_inactive=True
            )
            store_map = {s.id: s for s in all_stores}

        all_emps = await self._employee_repo.list_by_tenant(
            principal.tenant_id,
            store_id=store_id,
        )
        dept_filter = department.strip().casefold() if department and department.strip() else None
        available_depts: set[str] = set()
        scoped_emps: list[Employee] = []
        for emp in all_emps:
            if emp.deleted_at is not None:
                continue
            if emp.employment_status not in (EmploymentStatus.ACTIVE, EmploymentStatus.ON_LEAVE):
                continue
            if principal.role == UserRole.STORE_MANAGER and not principal.can_manage_employee(emp):
                continue
            if emp.department.strip():
                available_depts.add(emp.department.strip())
            if dept_filter and emp.department.strip().casefold() != dept_filter:
                continue
            scoped_emps.append(emp)

        avatar_urls: dict[UUID, str | None] = {}
        for emp in scoped_emps:
            avatar_urls[emp.id] = await mint_avatar_url(self._storage, emp)

        events = await self._attendance_repo.list_by_tenant(
            principal.tenant_id,
            allowed_store_ids=(
                principal.store_ids if principal.role == UserRole.STORE_MANAGER else None
            ),
            store_id=store_id,
        )
        events_by_emp: dict[UUID, list[AttendanceEvent]] = defaultdict(list)
        for ev in events:
            events_by_emp[ev.employee_id].append(ev)

        leaves = await self._leave_repo.list_by_tenant(
            principal.tenant_id,
            allowed_store_ids=(
                principal.store_ids if principal.role == UserRole.STORE_MANAGER else None
            ),
            store_id=store_id,
        )
        leaves_by_emp: dict[UUID, list[LeaveRequest]] = defaultdict(list)
        scoped_emp_ids = {e.id for e in scoped_emps}
        pending_leaves_count = 0
        pending_justificantes_count = 0
        redact_health = principal.role == UserRole.ACCOUNTANT
        for lv in leaves:
            if lv.deleted_at is None and lv.employee_id in scoped_emp_ids:
                leaves_by_emp[lv.employee_id].append(lv)
                if lv.status in PENDING_LEAVE_STATUSES:
                    pending_leaves_count += 1
                if (
                    not redact_health
                    and lv.leave_type in (LeaveType.SICK_LEAVE, LeaveType.MEDICAL_APPOINTMENT)
                    and lv.status in (*PENDING_LEAVE_STATUSES, LeaveStatus.APPROVED)
                    and lv.medical_document_id is None
                ):
                    pending_justificantes_count += 1

        # Gather today's shifts per store
        shifts_by_emp: dict[UUID, list[Shift]] = defaultdict(list)
        target_store_ids = (
            [store_id]
            if store_id is not None
            else (
                sorted(principal.store_ids)
                if principal.role == UserRole.STORE_MANAGER
                else list(store_map.keys())
            )
        )
        win_start = now - timedelta(hours=24)
        win_end = now + timedelta(hours=24)
        for sid in target_store_ids:
            store_shifts = await self._shift_repo.list_by_store_window(
                principal.tenant_id, sid, window_start_utc=win_start, window_end_utc=win_end
            )
            for sh in store_shifts:
                if sh.deleted_at is None and sh.status == ShiftStatus.PUBLISHED:
                    shifts_by_emp[sh.employee_id].append(sh)

        buckets: dict[WhoIsInStatus, list[DashboardMemberItem]] = {
            st: [] for st in WhoIsInStatus
        }
        policy_lookup = (
            build_break_policy_lookup(
                await self._policy_repo.list_by_tenant(principal.tenant_id)
            )
            if self._policy_repo is not None
            else None
        )
        has_mutations = False
        attendance_alerts_count = 0

        for emp in scoped_emps:
            store = store_map.get(emp.primary_store_id)
            store_name = store.name if store is not None else ""
            tz = resolve_timezone(store.timezone if store is not None else "Europe/Madrid")
            local_today = now.astimezone(tz).date()

            emp_events = events_by_emp.get(emp.id, [])
            eff_policy = (
                resolve_policy_for_employee(emp, policy_lookup)
                if policy_lookup is not None
                else None
            )
            emp_events, did_mutate = await auto_complete_elapsed_break_if_due(
                tenant_id=principal.tenant_id,
                employee=emp,
                events=emp_events,
                now_utc=now,
                attendance_repo=self._attendance_repo,
                store_repo=self._store_repo,
                payroll_repo=self._payroll_repo,
                actor_user_id=principal.user_id,
                effective_policy=eff_policy,
            )
            if did_mutate:
                has_mutations = True
            emp_ledger_7d = evaluate_attendance_ledger(
                employee_id=emp.id,
                events=emp_events,
                birth_date=emp.birth_date,
                work_schedule_type=emp.work_schedule_type,
                contracted_weekly_hours=emp.contracted_weekly_hours,
                date_from=local_today - timedelta(days=6),
                date_to=local_today,
                break_policy=eff_policy,
            )
            attendance_alerts_count += len(emp_ledger_7d.alerts)
            sessions = fold_attendance_events(emp.id, emp_events, break_policy=eff_policy)
            open_session = next((s for s in sessions if s.is_open), None)

            if open_session is not None:
                # Determine work_mode & active break start from events
                clock_in_ev = next(
                    (e for e in emp_events if e.id in open_session.source_event_ids),
                    None,
                )
                work_mode = clock_in_ev.work_mode if clock_in_ev is not None else WorkMode.ONSITE
                if open_session.has_open_break:
                    break_ev = next(
                        (
                            e
                            for e in reversed(emp_events)
                            if e.id in open_session.source_event_ids
                            and e.event_type == AttendanceEventType.BREAK_START
                        ),
                        None,
                    )
                    buckets[WhoIsInStatus.ON_BREAK].append(
                        DashboardMemberItem(
                            employee_id=emp.id,
                            full_name=emp.full_name,
                            preferred_name=emp.preferred_name,
                            employee_code=emp.employee_code,
                            department=emp.department,
                            store_name=store_name,
                            avatar_url=avatar_urls.get(emp.id),
                            since_utc=(
                                break_ev.occurred_at_utc if break_ev else open_session.clock_in_utc
                            ),
                            job_title=emp.job_title,
                        )
                    )
                elif work_mode == WorkMode.REMOTE:
                    buckets[WhoIsInStatus.REMOTE].append(
                        DashboardMemberItem(
                            employee_id=emp.id,
                            full_name=emp.full_name,
                            preferred_name=emp.preferred_name,
                            employee_code=emp.employee_code,
                            department=emp.department,
                            store_name=store_name,
                            avatar_url=avatar_urls.get(emp.id),
                            since_utc=open_session.clock_in_utc,
                            job_title=emp.job_title,
                        )
                    )
                else:
                    buckets[WhoIsInStatus.WORKING].append(
                        DashboardMemberItem(
                            employee_id=emp.id,
                            full_name=emp.full_name,
                            preferred_name=emp.preferred_name,
                            employee_code=emp.employee_code,
                            department=emp.department,
                            store_name=store_name,
                            avatar_url=avatar_urls.get(emp.id),
                            since_utc=open_session.clock_in_utc,
                            job_title=emp.job_title,
                        )
                    )
                continue

            # Not currently clocked in -> check approved leaves today
            approved_today = [
                lv
                for lv in leaves_by_emp.get(emp.id, [])
                if lv.deleted_at is None
                and lv.status == LeaveStatus.APPROVED
                and lv.start_date <= local_today <= lv.end_date
            ]
            if approved_today:
                lv = approved_today[0]
                target_bucket = (
                    WhoIsInStatus.VACATION
                    if lv.leave_type == LeaveType.VACATION
                    else WhoIsInStatus.ABSENCE
                )
                buckets[target_bucket].append(
                    DashboardMemberItem(
                        employee_id=emp.id,
                        full_name=emp.full_name,
                        preferred_name=emp.preferred_name,
                        employee_code=emp.employee_code,
                        department=emp.department,
                        store_name=store_name,
                        avatar_url=avatar_urls.get(emp.id),
                        since_utc=None,
                        job_title=emp.job_title,
                    )
                )
                continue

            # Check absenteeism: published shift today with start_at_utc + 15m < now,
            # zero attendance events today, zero approved leaves today.
            events_today = [
                ev
                for ev in emp_events
                if ev.occurred_at_utc.astimezone(tz).date() == local_today
            ]
            if not events_today:
                late_shift = next(
                    (
                        sh
                        for sh in shifts_by_emp.get(emp.id, [])
                        if sh.start_at_utc.astimezone(tz).date() == local_today
                        and sh.start_at_utc + timedelta(minutes=15) < now
                    ),
                    None,
                )
                if late_shift is not None:
                    buckets[WhoIsInStatus.ABSENTEEISM].append(
                        DashboardMemberItem(
                            employee_id=emp.id,
                            full_name=emp.full_name,
                            preferred_name=emp.preferred_name,
                            employee_code=emp.employee_code,
                            department=emp.department,
                            store_name=store_name,
                            avatar_url=avatar_urls.get(emp.id),
                            since_utc=late_shift.start_at_utc,
                            job_title=emp.job_title,
                        )
                    )

        who_is_in = tuple(
            WhoIsInGroup(
                status=st,
                count=len(buckets[st]),
                preview_members=tuple(buckets[st][:4]),
                overflow_count=max(0, len(buckets[st]) - 4),
                members=tuple(buckets[st]),
            )
            for st in WhoIsInStatus
        )

        # Upcoming birthdays & anniversaries (next 30 days, store-local date, NO birth year!)
        ref_today = now.date()
        birthdays: list[UpcomingBirthdayItem] = []
        anniversaries: list[UpcomingAnniversaryItem] = []
        for emp in scoped_emps:
            store = store_map.get(emp.primary_store_id)
            store_name = store.name if store else ""
            tz = resolve_timezone(store.timezone if store is not None else "Europe/Madrid")
            emp_local_today = now.astimezone(tz).date()

            if emp.birth_date is not None:
                bday_delta = days_until_birthday(emp.birth_date, emp_local_today)
                if bday_delta is not None:
                    birthdays.append(
                        UpcomingBirthdayItem(
                            employee_id=emp.id,
                            full_name=emp.full_name,
                            preferred_name=emp.preferred_name,
                            department=emp.department,
                            store_name=store_name,
                            avatar_url=avatar_urls.get(emp.id),
                            birth_month=emp.birth_date.month,
                            birth_day=emp.birth_date.day,
                            days_until=bday_delta,
                        )
                    )

            ann_info = days_until_anniversary(emp.hire_date, emp_local_today)
            if ann_info is not None:
                delta, years_of_service = ann_info
                anniversaries.append(
                    UpcomingAnniversaryItem(
                        employee_id=emp.id,
                        full_name=emp.full_name,
                        preferred_name=emp.preferred_name,
                        job_title=emp.job_title,
                        department=emp.department,
                        store_name=store_name,
                        avatar_url=avatar_urls.get(emp.id),
                        anniversary_month=emp.hire_date.month,
                        anniversary_day=emp.hire_date.day,
                        years_of_service=years_of_service,
                        days_until=delta,
                    )
                )
        birthdays.sort(key=lambda b: (b.days_until, b.full_name))
        anniversaries.sort(key=lambda a: (a.days_until, a.full_name))

        region_codes = [
            store_map[sid].region_code
            for sid in target_store_ids
            if sid in store_map and store_map[sid].region_code
        ]
        holidays = list_upcoming_holidays(ref_today, 90, region_codes)

        quick_reports = (
            QuickReportEntry(
                key="executive_summary",
                title_es="Resumen Ejecutivo",
                title_zh="管理层总览报表",
                endpoint="/api/v1/reports/executive-summary",
                available=True,
            ),
            QuickReportEntry(
                key="attendance_summary",
                title_es="Resumen de Jornada y Horas",
                title_zh="考勤工时汇总表",
                endpoint="/api/v1/reports/attendance-summary",
                available=True,
            ),
            QuickReportEntry(
                key="leave_summary",
                title_es="Resumen de Vacaciones y Ausencias",
                title_zh="休假与缺勤统计表",
                endpoint="/api/v1/reports/leave-summary",
                available=True,
            ),
            QuickReportEntry(
                key="headcount_roster",
                title_es="Plantilla y Contratos",
                title_zh="在职员工花名册",
                endpoint="/api/v1/reports/headcount-roster",
                available=True,
            ),
        )

        ann_views = await ListAnnouncementsUseCase(
            self._ann_repo, self._employee_repo
        ).execute(principal)
        recent_anns = tuple(
            DashboardAnnouncementSummary(
                id=v.announcement.id,
                category=v.announcement.category.value,
                title=v.announcement.title,
                is_pinned=v.announcement.is_pinned,
                requires_acknowledgement=v.announcement.requires_acknowledgement,
                published_at_utc=v.announcement.published_at_utc,
                acknowledged_count=v.acknowledged_count,
                target_count=v.target_count,
                acknowledged_by_self=v.acknowledged_by_self,
            )
            for v in ann_views[:6]
        )

        req_anns = [v.announcement for v in ann_views if v.announcement.requires_acknowledgement]
        unacknowledged_announcements_count = 0
        if req_anns and scoped_emps:
            acks = await self._ann_repo.list_acknowledgements_for_announcements(
                principal.tenant_id, [a.id for a in req_anns]
            )
            acked_pairs = {(ack.announcement_id, ack.employee_id) for ack in acks}
            for ann in req_anns:
                for emp in scoped_emps:
                    if (
                        is_employee_in_announcement_scope(ann, emp)
                        and (ann.id, emp.id) not in acked_pairs
                    ):
                        unacknowledged_announcements_count += 1

        return AdminDashboardResult(
            who_is_in=who_is_in,
            upcoming_birthdays=tuple(birthdays),
            upcoming_holidays=tuple(holidays),
            quick_reports=quick_reports,
            recent_announcements=recent_anns,
            pending_leaves_count=pending_leaves_count,
            upcoming_anniversaries=tuple(anniversaries),
            attendance_alerts_count=attendance_alerts_count,
            pending_justificantes_count=pending_justificantes_count,
            unacknowledged_announcements_count=unacknowledged_announcements_count,
            available_departments=tuple(sorted(available_depts)),
            has_mutations=has_mutations,
        )


class GetMyDashboardUseCase:
    def __init__(
        self,
        *,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        membership_repo: MembershipRepository,
        attendance_repo: AttendanceRepository,
        shift_repo: ShiftRepository,
        leave_repo: LeaveRepository,
        doc_repo: DocumentRepository,
        ann_repo: AnnouncementRepository,
        storage: DocumentStoragePort,
        payroll_repo: PayrollClosingRepository | None = None,
        policy_repo: BreakPolicyRepository | None = None,
        entitlement_repo: LeaveEntitlementRepository | None = None,
    ) -> None:
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._membership_repo = membership_repo
        self._attendance_repo = attendance_repo
        self._shift_repo = shift_repo
        self._leave_repo = leave_repo
        self._doc_repo = doc_repo
        self._ann_repo = ann_repo
        self._storage = storage
        self._payroll_repo = payroll_repo
        self._policy_repo = policy_repo
        self._entitlement_repo = entitlement_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        now_utc: datetime,
    ) -> MyDashboardResult:
        now = now_utc.astimezone(UTC)
        ref_today = now.date()
        stores = await self._store_repo.list_by_tenant(principal.tenant_id, include_inactive=True)
        store_map = {s.id: s for s in stores}

        all_emps = await self._employee_repo.list_by_tenant(principal.tenant_id)
        self_emp = next(
            (e for e in all_emps if e.user_id == principal.user_id and e.deleted_at is None),
            None,
        )

        ann_views = await ListAnnouncementsUseCase(
            self._ann_repo, self._employee_repo
        ).execute(principal)
        all_ann_summaries = [
            DashboardAnnouncementSummary(
                id=v.announcement.id,
                category=v.announcement.category.value,
                title=v.announcement.title,
                is_pinned=v.announcement.is_pinned,
                requires_acknowledgement=v.announcement.requires_acknowledgement,
                published_at_utc=v.announcement.published_at_utc,
                acknowledged_count=v.acknowledged_count,
                target_count=v.target_count,
                acknowledged_by_self=v.acknowledged_by_self,
            )
            for v in ann_views
        ]
        pending_anns = tuple(
            a
            for a in all_ann_summaries
            if a.requires_acknowledgement and not a.acknowledged_by_self
        )
        recent_anns = tuple(all_ann_summaries[:6])

        if self_emp is None:
            region_codes = [s.region_code for s in stores if s.is_active and s.region_code]
            return MyDashboardResult(
                has_employee_profile=False,
                employee=None,
                clock_state=None,
                teammates=(),
                attendance_approvers=(),
                leave_approvers=(),
                notifications=(),
                vacation_balance=None,
                pending_announcements=pending_anns,
                recent_announcements=recent_anns,
                recent_documents=(),
                upcoming_birthdays=(),
                upcoming_holidays=tuple(list_upcoming_holidays(ref_today, 90, region_codes)),
            )

        store = store_map.get(self_emp.primary_store_id)
        store_name = store.name if store else ""
        tz = resolve_timezone(store.timezone if store else "Europe/Madrid")
        local_today = now.astimezone(tz).date()

        self_avatar = await mint_avatar_url(self._storage, self_emp)
        emp_summary = MyDashboardEmployeeSummary(
            id=self_emp.id,
            employee_code=self_emp.employee_code,
            full_name=self_emp.full_name,
            preferred_name=self_emp.preferred_name,
            department=self_emp.department,
            store_id=self_emp.primary_store_id,
            store_name=store_name,
            avatar_url=self_avatar,
            contracted_weekly_hours=self_emp.contracted_weekly_hours,
            job_title=self_emp.job_title,
        )

        # Clock state + today's shift + alerts
        if self._policy_repo is not None:
            lookup = build_break_policy_lookup(
                await self._policy_repo.list_by_tenant(principal.tenant_id)
            )
            eff_policy = resolve_policy_for_employee(self_emp, lookup)
        else:
            eff_policy = resolve_effective_break_policy(
                None, None, None, self_emp.default_break_minutes
            )

        emp_events = await self._attendance_repo.list_by_employee(principal.tenant_id, self_emp.id)
        emp_events, has_mutations = await auto_complete_elapsed_break_if_due(
            tenant_id=principal.tenant_id,
            employee=self_emp,
            events=emp_events,
            now_utc=now,
            attendance_repo=self._attendance_repo,
            store_repo=self._store_repo,
            payroll_repo=self._payroll_repo,
            actor_user_id=principal.user_id,
            effective_policy=eff_policy,
        )
        sessions = fold_attendance_events(self_emp.id, emp_events, break_policy=eff_policy)
        open_session = next((s for s in sessions if s.is_open), None)

        worked_mins_today = sum(
            s.net_minutes for s in sessions if not s.is_open and s.local_date == local_today
        )
        break_mins_today = sum(
            s.break_minutes for s in sessions if s.local_date == local_today
        )
        active_clock_in: datetime | None = None
        active_break_start: datetime | None = None
        clock_status = "not_clocked_in"
        work_mode = WorkMode.ONSITE

        if open_session is not None:
            active_clock_in = open_session.clock_in_utc
            clock_in_ev = next(
                (e for e in emp_events if e.id in open_session.source_event_ids), None
            )
            if clock_in_ev is not None:
                work_mode = clock_in_ev.work_mode
            if open_session.has_open_break:
                clock_status = "on_break"
                break_ev = next(
                    (
                        e
                        for e in reversed(emp_events)
                        if e.id in open_session.source_event_ids
                        and e.event_type == AttendanceEventType.BREAK_START
                    ),
                    None,
                )
                active_break_start = break_ev.occurred_at_utc if break_ev else None
                if active_break_start is not None:
                    break_mins_today += max(
                        0, int((now - active_break_start).total_seconds()) // 60
                    )
            else:
                clock_status = "working"
            elapsed_total = max(0, int((now - open_session.clock_in_utc).total_seconds()) // 60)
            if eff_policy.is_paid:
                worked_mins_today += elapsed_total
            else:
                open_break_total = open_session.break_minutes
                if active_break_start is not None:
                    open_break_total += max(
                        0, int((now - active_break_start).total_seconds()) // 60
                    )
                worked_mins_today += max(0, elapsed_total - open_break_total)

        shifts = await self._shift_repo.list_by_employee_window(
            principal.tenant_id,
            self_emp.id,
            window_start_utc=now - timedelta(hours=24),
            window_end_utc=now + timedelta(hours=24),
        )
        today_shift = next(
            (
                sh
                for sh in shifts
                if sh.deleted_at is None
                and sh.status == ShiftStatus.PUBLISHED
                and sh.start_at_utc.astimezone(tz).date() == local_today
            ),
            None,
        )
        target_daily_minutes = (
            max(
                0,
                int((today_shift.end_at_utc - today_shift.start_at_utc).total_seconds()) // 60
                - today_shift.planned_break_minutes,
            )
            if today_shift is not None
            else compute_target_daily_minutes(self_emp.contracted_weekly_hours)
        )
        today_segments = build_today_clock_segments(self_emp.id, emp_events, local_today, now)
        scheduled_break_end_utc, _ = compute_auto_break_end_utc(
            emp_events,
            self_emp.id,
            self_emp.default_break_minutes,
            now,
            effective_policy=eff_policy,
        )
        clock_state = EmployeeClockState(
            status=clock_status,
            work_mode=work_mode,
            active_clock_in_utc=active_clock_in,
            active_break_start_utc=active_break_start,
            worked_minutes_today=worked_mins_today,
            break_minutes_today=break_mins_today,
            target_daily_minutes=target_daily_minutes,
            today_shift_start_utc=today_shift.start_at_utc if today_shift else None,
            today_shift_end_utc=today_shift.end_at_utc if today_shift else None,
            saldo_minutes=worked_mins_today - target_daily_minutes,
            today_segments=today_segments,
            default_break_minutes=self_emp.default_break_minutes,
            auto_break_end_utc=scheduled_break_end_utc if clock_status == "on_break" else None,
            effective_break_policy=eff_policy,
        )

        # Teammates: same store & department (max 12)
        teammates_list: list[DashboardMemberItem] = []
        self_dept = self_emp.department.strip().casefold()
        for emp in all_emps:
            if emp.id == self_emp.id or emp.deleted_at is not None:
                continue
            if emp.employment_status not in (EmploymentStatus.ACTIVE, EmploymentStatus.ON_LEAVE):
                continue
            if emp.primary_store_id != self_emp.primary_store_id:
                continue
            if emp.department.strip().casefold() != self_dept:
                continue
            teammates_list.append(
                DashboardMemberItem(
                    employee_id=emp.id,
                    full_name=emp.full_name,
                    preferred_name=emp.preferred_name,
                    employee_code=emp.employee_code,
                    department=emp.department,
                    store_name=store_name,
                    avatar_url=await mint_avatar_url(self._storage, emp),
                    since_utc=None,
                    job_title=emp.job_title,
                )
            )
            if len(teammates_list) >= 12:
                break

        # Approvers
        memberships = await self._membership_repo.list_by_tenant(principal.tenant_id)
        approvers: list[DashboardApproverItem] = []
        for m in memberships:
            if (
                m.status != MembershipStatus.ACTIVE
                or m.role not in _APPROVER_ROLES
                or m.user_id == principal.user_id
            ):
                continue
            if m.role == UserRole.STORE_MANAGER:
                if self_emp.primary_store_id not in m.store_ids:
                    continue
                if (
                    m.department_scope
                    and m.department_scope.strip()
                    and m.department_scope.strip().casefold() != self_dept
                ):
                    continue
            approvers.append(
                DashboardApproverItem(user_id=m.user_id, display_name=m.display_name, role=m.role)
            )

        # Notifications: recent alerts & corrections
        ledger = evaluate_attendance_ledger(
            employee_id=self_emp.id,
            events=emp_events,
            birth_date=self_emp.birth_date,
            work_schedule_type=self_emp.work_schedule_type,
            contracted_weekly_hours=self_emp.contracted_weekly_hours,
            date_from=local_today - timedelta(days=30),
            date_to=local_today,
            break_policy=eff_policy,
        )
        notifs: list[DashboardNotificationItem] = [
            DashboardNotificationItem(
                kind="attendance_alert",
                local_date=a.local_date,
                message=a.message,
                severity=a.severity.value,
            )
            for a in ledger.alerts[-10:]
        ]
        for ev in reversed(emp_events[-10:]):
            if ev.event_type == AttendanceEventType.CORRECTION:
                notifs.append(
                    DashboardNotificationItem(
                        kind="attendance_correction",
                        local_date=ev.occurred_at_utc.astimezone(tz).date(),
                        message=f"Corrección de fichaje: {ev.correction_reason}",
                        severity="info",
                    )
                )

        # Vacation balance
        leaves = await self._leave_repo.list_by_employee(principal.tenant_id, self_emp.id)
        entitlement = (
            await self._entitlement_repo.get_by_employee_year(
                principal.tenant_id, self_emp.id, local_today.year
            )
            if self._entitlement_repo is not None
            else None
        )
        vac_bal = compute_vacation_balance(
            self_emp, leaves, local_today.year, entitlement=entitlement
        )

        # Recent personal docs (latest 8)
        docs = await ListDocumentsUseCase(
            self._doc_repo, self._employee_repo, ann_repo=self._ann_repo
        ).execute(
            principal, employee_id=self_emp.id
        )
        recent_docs = tuple(docs[:8])

        # Upcoming birthdays in same store
        birthdays: list[UpcomingBirthdayItem] = []
        for emp in all_emps:
            if emp.deleted_at is not None or emp.birth_date is None:
                continue
            if emp.employment_status not in (EmploymentStatus.ACTIVE, EmploymentStatus.ON_LEAVE):
                continue
            if emp.primary_store_id != self_emp.primary_store_id:
                continue
            delta = days_until_birthday(emp.birth_date, local_today)
            if delta is not None:
                birthdays.append(
                    UpcomingBirthdayItem(
                        employee_id=emp.id,
                        full_name=emp.full_name,
                        preferred_name=emp.preferred_name,
                        department=emp.department,
                        store_name=store_name,
                        avatar_url=await mint_avatar_url(self._storage, emp),
                        birth_month=emp.birth_date.month,
                        birth_day=emp.birth_date.day,
                        days_until=delta,
                    )
                )
        birthdays.sort(key=lambda b: (b.days_until, b.full_name))

        anniversaries: list[UpcomingAnniversaryItem] = []
        for emp in all_emps:
            if emp.deleted_at is not None:
                continue
            if emp.employment_status not in (EmploymentStatus.ACTIVE, EmploymentStatus.ON_LEAVE):
                continue
            if emp.primary_store_id != self_emp.primary_store_id:
                continue
            ann_info = days_until_anniversary(emp.hire_date, local_today)
            if ann_info is not None:
                delta, years_of_service = ann_info
                anniversaries.append(
                    UpcomingAnniversaryItem(
                        employee_id=emp.id,
                        full_name=emp.full_name,
                        preferred_name=emp.preferred_name,
                        job_title=emp.job_title,
                        department=emp.department,
                        store_name=store_name,
                        avatar_url=await mint_avatar_url(self._storage, emp),
                        anniversary_month=emp.hire_date.month,
                        anniversary_day=emp.hire_date.day,
                        years_of_service=years_of_service,
                        days_until=delta,
                    )
                )
        anniversaries.sort(key=lambda a: (a.days_until, a.full_name))

        region_codes = [store.region_code] if store and store.region_code else []
        holidays = list_upcoming_holidays(local_today, 90, region_codes)

        return MyDashboardResult(
            has_employee_profile=True,
            employee=emp_summary,
            clock_state=clock_state,
            teammates=tuple(teammates_list),
            attendance_approvers=tuple(approvers),
            leave_approvers=tuple(approvers),
            notifications=tuple(notifs[:10]),
            vacation_balance=vac_bal,
            pending_announcements=pending_anns,
            recent_announcements=recent_anns,
            recent_documents=recent_docs,
            upcoming_birthdays=tuple(birthdays),
            upcoming_holidays=tuple(holidays),
            upcoming_anniversaries=tuple(anniversaries),
            has_mutations=has_mutations,
        )
