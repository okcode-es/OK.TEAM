"""Application use cases for Management Reports Center (CSV/PDF + Executive Summary)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from app.application.break_policies import build_break_policy_lookup, resolve_policy_for_employee
from app.domain.attendance import evaluate_attendance_ledger
from app.domain.employees import Employee, EmploymentStatus, WorkScheduleType
from app.domain.leaves import (
    LeaveStatus,
    LeaveType,
    compute_vacation_balance,
    split_days_by_year,
)
from app.domain.organization import ValidationDomainError
from app.domain.reports import (
    AttendanceSummaryRow,
    ExecutiveSummary,
    HeadcountRosterRow,
    LeaveSummaryRow,
    StoreReportSummary,
    render_csv_utf8_bom,
    render_table_pdf,
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
from app.domain.security import AuthenticatedPrincipal, ForbiddenScopeError, UserRole

_REPORT_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.OWNER, UserRole.HR, UserRole.STORE_MANAGER, UserRole.ACCOUNTANT}
)
_MAX_SPAN_DAYS = 366


def _require_report_access(
    principal: AuthenticatedPrincipal,
    *,
    store_id: UUID | None,
    department: str | None,
) -> None:
    if principal.role not in _REPORT_ROLES:
        raise ForbiddenScopeError("Employees cannot access management reports")
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


def _validate_date_span(from_date: date, to_date: date) -> int:
    if to_date < from_date:
        raise ValidationDomainError("to_date must be greater than or equal to from_date")
    span_days = (to_date - from_date).days + 1
    if span_days > _MAX_SPAN_DAYS:
        raise ValidationDomainError(f"Report date span cannot exceed {_MAX_SPAN_DAYS} days")
    return span_days


async def _load_scoped_employees(
    employee_repo: EmployeeRepository,
    principal: AuthenticatedPrincipal,
    *,
    store_id: UUID | None,
    department: str | None,
) -> list[Employee]:
    all_emps = await employee_repo.list_by_tenant(principal.tenant_id, store_id=store_id)
    dept_filter = department.strip().casefold() if department and department.strip() else None
    scoped: list[Employee] = []
    for emp in all_emps:
        if emp.deleted_at is not None:
            continue
        if emp.employment_status not in (EmploymentStatus.ACTIVE, EmploymentStatus.ON_LEAVE):
            continue
        if principal.role == UserRole.STORE_MANAGER and not principal.can_manage_employee(emp):
            continue
        if dept_filter is not None and emp.department.strip().casefold() != dept_filter:
            continue
        scoped.append(emp)
    scoped.sort(key=lambda e: (e.employee_code, str(e.id)))
    return scoped


def _overlap_days(start_a: date, end_a: date, start_b: date, end_b: date) -> int:
    s = max(start_a, start_b)
    e = min(end_a, end_b)
    if e < s:
        return 0
    return (e - s).days + 1


class GetExecutiveSummaryUseCase:
    def __init__(
        self,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        attendance_repo: AttendanceRepository,
        leave_repo: LeaveRepository,
        announcement_repo: AnnouncementRepository,
        policy_repo: BreakPolicyRepository | None = None,
    ) -> None:
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._attendance_repo = attendance_repo
        self._leave_repo = leave_repo
        self._announcement_repo = announcement_repo
        self._policy_repo = policy_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        from_date: date,
        to_date: date,
        store_id: UUID | None = None,
        department: str | None = None,
    ) -> ExecutiveSummary:
        _require_report_access(principal, store_id=store_id, department=department)
        _validate_date_span(from_date, to_date)

        stores = await self._store_repo.list_by_tenant(principal.tenant_id, include_inactive=False)
        store_map = {s.id: s for s in stores}
        employees = await _load_scoped_employees(
            self._employee_repo, principal, store_id=store_id, department=department
        )
        emp_ids = {e.id for e in employees}

        events = await self._attendance_repo.list_by_tenant(
            principal.tenant_id,
            allowed_store_ids=(
                principal.store_ids if principal.role == UserRole.STORE_MANAGER else None
            ),
            store_id=store_id,
        )
        events_by_emp = defaultdict(list)
        for ev in events:
            if ev.employee_id in emp_ids:
                events_by_emp[ev.employee_id].append(ev)

        leaves = await self._leave_repo.list_by_tenant(
            principal.tenant_id,
            allowed_store_ids=(
                principal.store_ids if principal.role == UserRole.STORE_MANAGER else None
            ),
            store_id=store_id,
        )

        contract_counts: Counter[str] = Counter()
        full_time = 0
        part_time = 0
        total_worked = Decimal("0.00")
        total_regular = Decimal("0.00")
        total_overtime = Decimal("0.00")
        total_alerts = 0

        store_emp_count: Counter[UUID] = Counter()
        store_worked: defaultdict[UUID, Decimal] = defaultdict(lambda: Decimal("0.00"))
        store_overtime: defaultdict[UUID, Decimal] = defaultdict(lambda: Decimal("0.00"))
        store_alerts: Counter[UUID] = Counter()
        store_pending_leaves: Counter[UUID] = Counter()

        policy_lookup = (
            build_break_policy_lookup(
                await self._policy_repo.list_by_tenant(principal.tenant_id)
            )
            if self._policy_repo is not None
            else None
        )

        for emp in employees:
            contract_counts[emp.contract_type.value] += 1
            if emp.work_schedule_type == WorkScheduleType.FULL_TIME:
                full_time += 1
            else:
                part_time += 1
            store_emp_count[emp.primary_store_id] += 1

            ledger = evaluate_attendance_ledger(
                employee_id=emp.id,
                events=events_by_emp[emp.id],
                birth_date=emp.birth_date,
                work_schedule_type=emp.work_schedule_type,
                contracted_weekly_hours=emp.contracted_weekly_hours,
                date_from=from_date,
                date_to=to_date,
                break_policy=(
                    resolve_policy_for_employee(emp, policy_lookup)
                    if policy_lookup is not None
                    else None
                ),
            )
            total_worked += ledger.total_net_hours
            total_regular += ledger.regular_hours
            total_overtime += ledger.overtime_hours
            alert_cnt = len(ledger.alerts)
            total_alerts += alert_cnt

            store_worked[emp.primary_store_id] += ledger.total_net_hours
            store_overtime[emp.primary_store_id] += ledger.overtime_hours
            store_alerts[emp.primary_store_id] += alert_cnt

        pending_mgr = 0
        pending_hr = 0
        approved_vac_days = 0
        approved_sick_days = 0
        for lv in leaves:
            if lv.deleted_at is not None or lv.employee_id not in emp_ids:
                continue
            if lv.status == LeaveStatus.PENDING_MANAGER:
                pending_mgr += 1
                store_pending_leaves[lv.store_id] += 1
            elif lv.status == LeaveStatus.PENDING_HR:
                pending_hr += 1
                store_pending_leaves[lv.store_id] += 1
            elif lv.status == LeaveStatus.APPROVED:
                days = _overlap_days(lv.start_date, lv.end_date, from_date, to_date)
                if days > 0:
                    if lv.leave_type == LeaveType.VACATION:
                        approved_vac_days += days
                    elif lv.leave_type in (LeaveType.SICK_LEAVE, LeaveType.MEDICAL_APPOINTMENT):
                        approved_sick_days += days

        # Announcements pending acknowledgement count
        anns = await self._announcement_repo.list_by_tenant(principal.tenant_id)
        pending_ack_count = 0
        for ann in anns:
            if ann.deleted_at is not None or not ann.requires_acknowledgement:
                continue
            if store_id is not None and ann.store_id is not None and ann.store_id != store_id:
                continue
            acks = await self._announcement_repo.list_acknowledgements_for_announcement(
                principal.tenant_id, ann.id
            )
            acked_emp_ids = {a.employee_id for a in acks}
            target_emps = [
                e for e in employees if ann.store_id is None or e.primary_store_id == ann.store_id
            ]
            if any(e.id not in acked_emp_ids for e in target_emps):
                pending_ack_count += 1

        by_store: list[StoreReportSummary] = []
        for sid, s in sorted(store_map.items(), key=lambda kv: kv[1].name):
            if store_id is not None and sid != store_id:
                continue
            if principal.role == UserRole.STORE_MANAGER and sid not in principal.store_ids:
                continue
            by_store.append(
                StoreReportSummary(
                    store_id=sid,
                    store_name=s.name,
                    active_employees=store_emp_count[sid],
                    worked_hours=store_worked[sid].quantize(Decimal("0.01")),
                    overtime_hours=store_overtime[sid].quantize(Decimal("0.01")),
                    compliance_alerts=store_alerts[sid],
                    pending_leaves=store_pending_leaves[sid],
                )
            )

        return ExecutiveSummary(
            from_date=from_date,
            to_date=to_date,
            total_active_employees=len(employees),
            full_time_count=full_time,
            part_time_count=part_time,
            contract_breakdown=dict(contract_counts),
            total_worked_hours=total_worked.quantize(Decimal("0.01")),
            total_regular_hours=total_regular.quantize(Decimal("0.01")),
            total_overtime_hours=total_overtime.quantize(Decimal("0.01")),
            total_compliance_alerts=total_alerts,
            pending_manager_leaves=pending_mgr,
            pending_hr_leaves=pending_hr,
            approved_vacation_days=approved_vac_days,
            approved_sick_days=(
                None if principal.role == UserRole.ACCOUNTANT else approved_sick_days
            ),
            announcements_pending_ack_count=pending_ack_count,
            by_store=tuple(by_store),
        )


class GetAttendanceSummaryReportUseCase:
    def __init__(
        self,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        attendance_repo: AttendanceRepository,
        policy_repo: BreakPolicyRepository | None = None,
    ) -> None:
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._attendance_repo = attendance_repo
        self._policy_repo = policy_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        from_date: date,
        to_date: date,
        store_id: UUID | None = None,
        department: str | None = None,
    ) -> list[AttendanceSummaryRow]:
        _require_report_access(principal, store_id=store_id, department=department)
        span_days = _validate_date_span(from_date, to_date)

        stores = await self._store_repo.list_by_tenant(principal.tenant_id, include_inactive=True)
        store_map = {s.id: s for s in stores}
        employees = await _load_scoped_employees(
            self._employee_repo, principal, store_id=store_id, department=department
        )
        emp_ids = {e.id for e in employees}
        events = await self._attendance_repo.list_by_tenant(
            principal.tenant_id,
            allowed_store_ids=(
                principal.store_ids if principal.role == UserRole.STORE_MANAGER else None
            ),
            store_id=store_id,
        )
        events_by_emp = defaultdict(list)
        for ev in events:
            if ev.employee_id in emp_ids:
                events_by_emp[ev.employee_id].append(ev)

        policy_lookup = (
            build_break_policy_lookup(
                await self._policy_repo.list_by_tenant(principal.tenant_id)
            )
            if self._policy_repo is not None
            else None
        )
        rows: list[AttendanceSummaryRow] = []
        for emp in employees:
            ledger = evaluate_attendance_ledger(
                employee_id=emp.id,
                events=events_by_emp[emp.id],
                birth_date=emp.birth_date,
                work_schedule_type=emp.work_schedule_type,
                contracted_weekly_hours=emp.contracted_weekly_hours,
                date_from=from_date,
                date_to=to_date,
                break_policy=(
                    resolve_policy_for_employee(emp, policy_lookup)
                    if policy_lookup is not None
                    else None
                ),
            )
            worked_days = len({s.local_date for s in ledger.sessions if not s.is_open})
            break_mins = sum(s.break_minutes for s in ledger.sessions)
            target_hours = (
                (emp.contracted_weekly_hours / Decimal("7")) * Decimal(span_days)
            ).quantize(Decimal("0.01"))
            saldo_hours = (ledger.total_net_hours - target_hours).quantize(Decimal("0.01"))
            store = store_map.get(emp.primary_store_id)
            rows.append(
                AttendanceSummaryRow(
                    employee_code=emp.employee_code,
                    full_name=emp.full_name,
                    department=emp.department,
                    store_name=store.name if store else "",
                    worked_days=worked_days,
                    regular_hours=ledger.regular_hours,
                    overtime_hours=ledger.overtime_hours,
                    break_minutes=break_mins,
                    target_hours=target_hours,
                    saldo_hours=saldo_hours,
                    compliance_alerts=len(ledger.alerts),
                )
            )
        return rows

    @staticmethod
    def to_csv(rows: list[AttendanceSummaryRow]) -> bytes:
        headers = [
            "employee_code",
            "full_name",
            "department",
            "store_name",
            "worked_days",
            "regular_hours",
            "overtime_hours",
            "break_minutes",
            "target_hours",
            "saldo_hours",
            "compliance_alerts",
        ]
        data = [
            [
                r.employee_code,
                r.full_name,
                r.department,
                r.store_name,
                r.worked_days,
                r.regular_hours,
                r.overtime_hours,
                r.break_minutes,
                r.target_hours,
                r.saldo_hours,
                r.compliance_alerts,
            ]
            for r in rows
        ]
        return render_csv_utf8_bom(headers, data)

    @staticmethod
    def to_pdf(
        rows: list[AttendanceSummaryRow], *, from_date: date, to_date: date
    ) -> bytes:
        columns = [
            ("Codigo", 36.0, 10),
            ("Empleado", 90.0, 22),
            ("Tienda", 195.0, 16),
            ("Dias", 275.0, 5),
            ("Reg(h)", 305.0, 7),
            ("Extra(h)", 350.0, 7),
            ("Pausa(m)", 395.0, 7),
            ("Obj(h)", 440.0, 7),
            ("Saldo", 485.0, 7),
            ("Alertas", 525.0, 6),
        ]
        data = [
            [
                r.employee_code,
                r.full_name,
                r.store_name,
                r.worked_days,
                r.regular_hours,
                r.overtime_hours,
                r.break_minutes,
                r.target_hours,
                r.saldo_hours,
                r.compliance_alerts,
            ]
            for r in rows
        ]
        return render_table_pdf(
            title="okTeam - Resumen de Jornada y Horas (RDL 8/2019)",
            subtitle=f"Periodo: {from_date.isoformat()} a {to_date.isoformat()}",
            columns=columns,
            rows=data,
        )


class GetLeaveSummaryReportUseCase:
    def __init__(
        self,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        leave_repo: LeaveRepository,
        entitlement_repo: LeaveEntitlementRepository | None = None,
    ) -> None:
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._leave_repo = leave_repo
        self._entitlement_repo = entitlement_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        year: int,
        store_id: UUID | None = None,
        department: str | None = None,
    ) -> list[LeaveSummaryRow]:
        _require_report_access(principal, store_id=store_id, department=department)
        stores = await self._store_repo.list_by_tenant(principal.tenant_id, include_inactive=True)
        store_map = {s.id: s for s in stores}
        employees = await _load_scoped_employees(
            self._employee_repo, principal, store_id=store_id, department=department
        )
        leaves = await self._leave_repo.list_by_tenant(
            principal.tenant_id,
            allowed_store_ids=(
                principal.store_ids if principal.role == UserRole.STORE_MANAGER else None
            ),
            store_id=store_id,
            year=year,
        )
        leaves_by_emp = defaultdict(list)
        for lv in leaves:
            leaves_by_emp[lv.employee_id].append(lv)

        ent_by_emp = (
            {
                e.employee_id: e
                for e in await self._entitlement_repo.list_by_tenant(
                    principal.tenant_id, year=year
                )
            }
            if self._entitlement_repo is not None
            else {}
        )
        redact_health = principal.role == UserRole.ACCOUNTANT
        rows: list[LeaveSummaryRow] = []
        for emp in employees:
            emp_leaves = leaves_by_emp[emp.id]
            bal = compute_vacation_balance(
                emp, emp_leaves, year, entitlement=ent_by_emp.get(emp.id)
            )
            sick_days = 0
            med_days = 0
            other_days = 0
            for lv in emp_leaves:
                if lv.deleted_at is not None or lv.status != LeaveStatus.APPROVED:
                    continue
                days_in_year = split_days_by_year(lv.start_date, lv.end_date).get(year, 0)
                if lv.leave_type == LeaveType.SICK_LEAVE:
                    sick_days += days_in_year
                elif lv.leave_type == LeaveType.MEDICAL_APPOINTMENT:
                    med_days += days_in_year
                elif lv.leave_type in (
                    LeaveType.DAY_OFF,
                    LeaveType.PAID_LEAVE,
                    LeaveType.UNPAID_LEAVE,
                ):
                    other_days += days_in_year

            store = store_map.get(emp.primary_store_id)
            if redact_health:
                collapsed_other = other_days + sick_days + med_days
                rows.append(
                    LeaveSummaryRow(
                        employee_code=emp.employee_code,
                        full_name=emp.full_name,
                        department=emp.department,
                        store_name=store.name if store else "",
                        entitled_vacation_days=bal.entitled_days,
                        used_vacation_days=bal.approved_days,
                        pending_vacation_days=bal.pending_days,
                        remaining_vacation_days=bal.remaining_days,
                        sick_days=None,
                        medical_appointment_days=None,
                        other_absence_days=collapsed_other,
                    )
                )
            else:
                rows.append(
                    LeaveSummaryRow(
                        employee_code=emp.employee_code,
                        full_name=emp.full_name,
                        department=emp.department,
                        store_name=store.name if store else "",
                        entitled_vacation_days=bal.entitled_days,
                        used_vacation_days=bal.approved_days,
                        pending_vacation_days=bal.pending_days,
                        remaining_vacation_days=bal.remaining_days,
                        sick_days=sick_days,
                        medical_appointment_days=med_days,
                        other_absence_days=other_days,
                    )
                )
        return rows

    @staticmethod
    def to_csv(rows: list[LeaveSummaryRow], *, redact_health: bool) -> bytes:
        headers = [
            "employee_code",
            "full_name",
            "department",
            "store_name",
            "entitled_vacation_days",
            "used_vacation_days",
            "pending_vacation_days",
            "remaining_vacation_days",
        ]
        if not redact_health:
            headers.extend(["sick_days", "medical_appointment_days"])
        headers.append("other_absence_days")

        data: list[list[object]] = []
        for r in rows:
            row: list[object] = [
                r.employee_code,
                r.full_name,
                r.department,
                r.store_name,
                r.entitled_vacation_days,
                r.used_vacation_days,
                r.pending_vacation_days,
                r.remaining_vacation_days,
            ]
            if not redact_health:
                row.extend([r.sick_days or 0, r.medical_appointment_days or 0])
            row.append(r.other_absence_days)
            data.append(row)
        return render_csv_utf8_bom(headers, data)

    @staticmethod
    def to_pdf(rows: list[LeaveSummaryRow], *, year: int, redact_health: bool) -> bytes:
        columns: list[tuple[str, float, int]] = [
            ("Codigo", 36.0, 10),
            ("Empleado", 90.0, 22),
            ("Tienda", 195.0, 16),
            ("Der.", 275.0, 5),
            ("Usad.", 308.0, 5),
            ("Pend.", 341.0, 5),
            ("Rest.", 374.0, 5),
        ]
        if not redact_health:
            columns.extend([("Baja", 410.0, 5), ("Cita", 445.0, 5), ("Otras", 480.0, 6)])
        else:
            columns.append(("Ausencias", 415.0, 9))

        data: list[list[object]] = []
        for r in rows:
            row: list[object] = [
                r.employee_code,
                r.full_name,
                r.store_name,
                r.entitled_vacation_days,
                r.used_vacation_days,
                r.pending_vacation_days,
                r.remaining_vacation_days,
            ]
            if not redact_health:
                row.extend([r.sick_days or 0, r.medical_appointment_days or 0])
            row.append(r.other_absence_days)
            data.append(row)

        return render_table_pdf(
            title="okTeam - Resumen de Vacaciones y Ausencias (Art. 38 ET)",
            subtitle=f"Ejercicio: {year}",
            columns=columns,
            rows=data,
        )


class GetHeadcountRosterReportUseCase:
    def __init__(
        self,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
    ) -> None:
        self._employee_repo = employee_repo
        self._store_repo = store_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        store_id: UUID | None = None,
        department: str | None = None,
        as_of_date: date | None = None,
    ) -> list[HeadcountRosterRow]:
        _require_report_access(principal, store_id=store_id, department=department)
        ref_date = as_of_date or datetime.now(UTC).date()
        stores = await self._store_repo.list_by_tenant(principal.tenant_id, include_inactive=True)
        store_map = {s.id: s for s in stores}
        employees = await _load_scoped_employees(
            self._employee_repo, principal, store_id=store_id, department=department
        )
        rows: list[HeadcountRosterRow] = []
        for emp in employees:
            store = store_map.get(emp.primary_store_id)
            tenure_months = max(
                0,
                (ref_date.year - emp.hire_date.year) * 12 + (ref_date.month - emp.hire_date.month),
            )
            rows.append(
                HeadcountRosterRow(
                    employee_code=emp.employee_code,
                    full_name=emp.full_name,
                    job_title=emp.job_title,
                    department=emp.department,
                    store_name=store.name if store else "",
                    hire_date=emp.hire_date,
                    tenure_months=tenure_months,
                    contract_type=emp.contract_type.value,
                    schedule_type=emp.work_schedule_type.value,
                    weekly_hours=emp.contracted_weekly_hours,
                    status=emp.employment_status.value,
                )
            )
        return rows

    @staticmethod
    def to_csv(rows: list[HeadcountRosterRow]) -> bytes:
        headers = [
            "employee_code",
            "full_name",
            "job_title",
            "department",
            "store_name",
            "hire_date",
            "tenure_months",
            "contract_type",
            "schedule_type",
            "weekly_hours",
            "status",
        ]
        data = [
            [
                r.employee_code,
                r.full_name,
                r.job_title,
                r.department,
                r.store_name,
                r.hire_date.isoformat(),
                r.tenure_months,
                r.contract_type,
                r.schedule_type,
                r.weekly_hours,
                r.status,
            ]
            for r in rows
        ]
        return render_csv_utf8_bom(headers, data)

    @staticmethod
    def to_pdf(rows: list[HeadcountRosterRow]) -> bytes:
        columns = [
            ("Codigo", 36.0, 9),
            ("Empleado", 85.0, 20),
            ("Puesto", 180.0, 14),
            ("Depto", 250.0, 12),
            ("Tienda", 310.0, 14),
            ("Alta", 380.0, 10),
            ("Meses", 435.0, 5),
            ("Contrato", 465.0, 12),
            ("Horas", 525.0, 6),
        ]
        data = [
            [
                r.employee_code,
                r.full_name,
                r.job_title,
                r.department,
                r.store_name,
                r.hire_date.isoformat(),
                r.tenure_months,
                r.contract_type,
                r.weekly_hours,
            ]
            for r in rows
        ]
        return render_table_pdf(
            title="okTeam - Plantilla y Contratos",
            subtitle=f"Total empleados activos: {len(rows)}",
            columns=columns,
            rows=data,
        )
