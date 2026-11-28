"""Application use cases for Phase 5 Monthly Payroll Closing, RDL 8/2019 PDFs & Gestoria CSV."""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from decimal import Decimal
from uuid import UUID, uuid4

from app.application.attendance import _sanitize_csv_cell
from app.application.break_policies import build_break_policy_lookup, resolve_policy_for_employee
from app.domain.attendance import AttendanceEvent, resolve_timezone
from app.domain.documents import (
    ComplianceDocument,
    DocumentCategory,
    DocumentReceipt,
    DocumentStoragePort,
    build_storage_key,
    compute_document_retention_until,
)
from app.domain.employees import EmploymentStatus, PiiCipher
from app.domain.leaves import LeaveRequest
from app.domain.organization import (
    ConflictError,
    EntityNotFoundError,
    Store,
    validate_non_empty_text,
)
from app.domain.payroll import (
    AttendancePdfDocumentInput,
    AttendancePdfRendererPort,
    ClosingBlocker,
    ClosingBlockerCode,
    MonthlyClosing,
    MonthlyClosingItem,
    MonthlyClosingStatus,
    MonthlyEmployeeSummary,
    MonthlyReadinessReport,
    compute_closing_retention_until,
    count_overlap_days,
    evaluate_employee_monthly_summary,
    month_date_bounds,
    should_include_employee_in_month,
)
from app.domain.repositories import (
    AttendanceRepository,
    BreakPolicyRepository,
    DocumentRepository,
    EmployeeRepository,
    LeaveRepository,
    PayrollClosingRepository,
    StoreRepository,
    TenantRepository,
)
from app.domain.security import AuthenticatedPrincipal, ForbiddenScopeError, UserRole

_PAYROLL_READ_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.OWNER, UserRole.HR, UserRole.ACCOUNTANT, UserRole.STORE_MANAGER}
)
_PAYROLL_WRITE_ROLES: frozenset[UserRole] = frozenset({UserRole.OWNER, UserRole.HR})
_GESTORIA_EXPORT_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.OWNER, UserRole.HR, UserRole.ACCOUNTANT}
)
_HIDDEN_MANAGER_STATUSES: frozenset[EmploymentStatus] = frozenset(
    {
        EmploymentStatus.TERMINATED,
        EmploymentStatus.LEGAL_HOLD,
        EmploymentStatus.ANONYMIZED,
    }
)


@dataclass(frozen=True, slots=True)
class ClosingListSummary:
    closing: MonthlyClosing
    store_code: str
    store_name: str
    employee_count: int
    total_net_hours: Decimal
    total_overtime_hours: Decimal
    signed_count: int
    total_reports: int


@dataclass(frozen=True, slots=True)
class ClosingItemWithReceipt:
    item: MonthlyClosingItem
    is_signed: bool
    signed_at_utc: datetime | None
    document_sha256_hex: str | None


@dataclass(frozen=True, slots=True)
class ClosingDetailResult:
    closing: MonthlyClosing
    store_code: str
    store_name: str
    items: tuple[ClosingItemWithReceipt, ...]


@dataclass(frozen=True, slots=True)
class GestoriaExportResult:
    csv_text: str
    filename: str


async def _blocked_employee_ids_for_manager(
    employee_repo: EmployeeRepository,
    tenant_id: UUID,
) -> set[UUID]:
    emps = await employee_repo.list_by_tenant(tenant_id)
    return {
        e.id
        for e in emps
        if e.deleted_at is not None or e.employment_status in _HIDDEN_MANAGER_STATUSES
    }


async def _evaluate_store_month_readiness(
    *,
    tenant_id: UUID,
    store: Store,
    period_month: str,
    employee_repo: EmployeeRepository,
    attendance_repo: AttendanceRepository,
    leave_repo: LeaveRepository,
    payroll_repo: PayrollClosingRepository,
    hide_legal_hold_for_manager: bool,
    policy_repo: BreakPolicyRepository | None = None,
) -> tuple[MonthlyReadinessReport, list[ClosingBlocker]]:
    month_start, month_end = month_date_bounds(period_month)
    existing = await payroll_repo.get_by_store_month(tenant_id, store.id, period_month)

    store_employees = await employee_repo.list_by_tenant(tenant_id, store_id=store.id)
    store_emp_ids = {emp.id for emp in store_employees}
    tenant_events = await attendance_repo.list_by_tenant(tenant_id) if store_emp_ids else []
    tenant_leaves = await leave_repo.list_by_tenant(tenant_id) if store_emp_ids else []
    policy_lookup = (
        build_break_policy_lookup(await policy_repo.list_by_tenant(tenant_id))
        if policy_repo is not None
        else None
    )

    events_by_emp: dict[UUID, list[AttendanceEvent]] = defaultdict(list)
    has_events_in_month: set[UUID] = set()
    tz_cache: dict[str, tzinfo] = {store.timezone: resolve_timezone(store.timezone)}
    for ev in tenant_events:
        if ev.employee_id not in store_emp_ids:
            continue
        events_by_emp[ev.employee_id].append(ev)
        ev_tz_name = ev.store_timezone or store.timezone
        ev_tz = tz_cache.get(ev_tz_name)
        if ev_tz is None:
            ev_tz = resolve_timezone(ev_tz_name)
            tz_cache[ev_tz_name] = ev_tz
        ev_local_date = ev.occurred_at_utc.astimezone(ev_tz).date()
        if month_start <= ev_local_date <= month_end:
            has_events_in_month.add(ev.employee_id)

    leaves_by_emp: dict[UUID, list[LeaveRequest]] = defaultdict(list)
    has_leaves_in_month: set[UUID] = set()
    for lv in tenant_leaves:
        if lv.employee_id not in store_emp_ids:
            continue
        leaves_by_emp[lv.employee_id].append(lv)
        if count_overlap_days(lv.start_date, lv.end_date, month_start, month_end) > 0:
            has_leaves_in_month.add(lv.employee_id)

    all_blockers: list[ClosingBlocker] = []
    if existing is not None and existing.status == MonthlyClosingStatus.CLOSED:
        all_blockers.append(
            ClosingBlocker(
                code=ClosingBlockerCode.ALREADY_CLOSED,
                employee_id=None,
                employee_code=None,
                employee_full_name=None,
                reference_id=existing.id,
                local_date=None,
                message=(
                    f"Month {period_month} for store {store.code} is already closed "
                    f"(revision v{existing.revision_number})"
                ),
            )
        )

    visible_summaries: list[MonthlyEmployeeSummary] = []
    visible_blockers: list[ClosingBlocker] = list(all_blockers)

    for emp in sorted(store_employees, key=lambda e: e.employee_code):
        if not should_include_employee_in_month(
            emp,
            month_start=month_start,
            month_end=month_end,
            has_events_in_month=emp.id in has_events_in_month,
            has_leaves_in_month=emp.id in has_leaves_in_month,
        ):
            continue

        summary, emp_blockers = evaluate_employee_monthly_summary(
            employee=emp,
            events=events_by_emp.get(emp.id, []),
            leaves=leaves_by_emp.get(emp.id, []),
            month_start=month_start,
            month_end=month_end,
            store_timezone=store.timezone,
            break_policy=(
                resolve_policy_for_employee(emp, policy_lookup)
                if policy_lookup is not None
                else None
            ),
        )
        all_blockers.extend(emp_blockers)

        if hide_legal_hold_for_manager and (
            emp.deleted_at is not None or emp.employment_status in _HIDDEN_MANAGER_STATUSES
        ):
            continue
        visible_summaries.append(summary)
        visible_blockers.extend(emp_blockers)

    report = MonthlyReadinessReport(
        store_id=store.id,
        period_month=period_month,
        month_start=month_start,
        month_end=month_end,
        existing_closing=existing,
        can_close=len(all_blockers) == 0,
        blockers=tuple(visible_blockers),
        employees=tuple(visible_summaries),
    )
    return report, all_blockers


class ListMonthlyClosingsUseCase:
    def __init__(
        self,
        payroll_repo: PayrollClosingRepository,
        store_repo: StoreRepository,
        employee_repo: EmployeeRepository,
        doc_repo: DocumentRepository,
    ) -> None:
        self._payroll_repo = payroll_repo
        self._store_repo = store_repo
        self._employee_repo = employee_repo
        self._doc_repo = doc_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        store_id: UUID | None = None,
        period_month: str | None = None,
    ) -> list[ClosingListSummary]:
        if principal.role not in _PAYROLL_READ_ROLES:
            raise ForbiddenScopeError("Employees cannot access payroll closing records")

        if period_month is not None:
            month_date_bounds(period_month)

        allowed_stores: frozenset[UUID] | None = None
        blocked_emp_ids: set[UUID] = set()
        if principal.role == UserRole.STORE_MANAGER:
            if store_id is not None:
                principal.require_store_access(principal.tenant_id, store_id)
            allowed_stores = principal.store_ids
            blocked_emp_ids = await _blocked_employee_ids_for_manager(
                self._employee_repo, principal.tenant_id
            )

        closings = await self._payroll_repo.list_by_tenant(
            principal.tenant_id,
            allowed_store_ids=allowed_stores,
            store_id=store_id,
            period_month=period_month,
        )
        if not closings:
            return []

        stores = await self._store_repo.list_by_tenant(
            principal.tenant_id, include_inactive=True
        )
        store_map = {s.id: s for s in stores}
        all_items = await self._payroll_repo.list_items_for_closings(
            principal.tenant_id, [c.id for c in closings]
        )

        items_by_closing: dict[UUID, list[MonthlyClosingItem]] = defaultdict(list)
        doc_ids: list[UUID] = []
        for item in all_items:
            if item.employee_id in blocked_emp_ids:
                continue
            items_by_closing[item.closing_id].append(item)

        for c in closings:
            latest = [
                it
                for it in items_by_closing.get(c.id, [])
                if it.revision_number == c.revision_number
            ]
            for it in latest:
                if it.attendance_report_document_id is not None:
                    doc_ids.append(it.attendance_report_document_id)

        receipts = await self._doc_repo.list_receipts_for_documents(principal.tenant_id, doc_ids)
        signed_doc_ids = {r.document_id for r in receipts}

        summaries: list[ClosingListSummary] = []
        for c in closings:
            latest_items = [
                it
                for it in items_by_closing.get(c.id, [])
                if it.revision_number == c.revision_number
            ]
            store = store_map.get(c.store_id)
            report_doc_ids = [
                it.attendance_report_document_id
                for it in latest_items
                if it.attendance_report_document_id is not None
            ]
            summaries.append(
                ClosingListSummary(
                    closing=c,
                    store_code=store.code if store is not None else "",
                    store_name=store.name if store is not None else "",
                    employee_count=len(latest_items),
                    total_net_hours=sum(
                        (it.total_net_hours for it in latest_items), Decimal("0.00")
                    ).quantize(Decimal("0.01")),
                    total_overtime_hours=sum(
                        (it.overtime_hours for it in latest_items), Decimal("0.00")
                    ).quantize(Decimal("0.01")),
                    signed_count=sum(1 for did in report_doc_ids if did in signed_doc_ids),
                    total_reports=len(report_doc_ids),
                )
            )
        return summaries


class PreviewMonthlyClosingUseCase:
    def __init__(
        self,
        payroll_repo: PayrollClosingRepository,
        store_repo: StoreRepository,
        employee_repo: EmployeeRepository,
        attendance_repo: AttendanceRepository,
        leave_repo: LeaveRepository,
        policy_repo: BreakPolicyRepository | None = None,
    ) -> None:
        self._payroll_repo = payroll_repo
        self._store_repo = store_repo
        self._employee_repo = employee_repo
        self._attendance_repo = attendance_repo
        self._leave_repo = leave_repo
        self._policy_repo = policy_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        store_id: UUID,
        period_month: str,
    ) -> MonthlyReadinessReport:
        if principal.role not in _PAYROLL_READ_ROLES:
            raise ForbiddenScopeError("Employees cannot preview payroll closings")

        if principal.role == UserRole.STORE_MANAGER:
            principal.require_store_access(principal.tenant_id, store_id)

        store = await self._store_repo.get_by_id(principal.tenant_id, store_id)
        if store is None:
            raise EntityNotFoundError(f"Store {store_id} not found")

        report, _ = await _evaluate_store_month_readiness(
            tenant_id=principal.tenant_id,
            store=store,
            period_month=period_month,
            employee_repo=self._employee_repo,
            attendance_repo=self._attendance_repo,
            leave_repo=self._leave_repo,
            payroll_repo=self._payroll_repo,
            hide_legal_hold_for_manager=principal.role == UserRole.STORE_MANAGER,
            policy_repo=self._policy_repo,
        )
        return report


class CloseMonthlyPayrollUseCase:
    def __init__(
        self,
        payroll_repo: PayrollClosingRepository,
        tenant_repo: TenantRepository,
        store_repo: StoreRepository,
        employee_repo: EmployeeRepository,
        attendance_repo: AttendanceRepository,
        leave_repo: LeaveRepository,
        doc_repo: DocumentRepository,
        storage: DocumentStoragePort,
        pdf_renderer: AttendancePdfRendererPort,
        policy_repo: BreakPolicyRepository | None = None,
    ) -> None:
        self._payroll_repo = payroll_repo
        self._tenant_repo = tenant_repo
        self._store_repo = store_repo
        self._employee_repo = employee_repo
        self._attendance_repo = attendance_repo
        self._leave_repo = leave_repo
        self._doc_repo = doc_repo
        self._storage = storage
        self._pdf_renderer = pdf_renderer
        self._policy_repo = policy_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        store_id: UUID,
        period_month: str,
        notes: str = "",
        now_utc: datetime,
    ) -> ClosingDetailResult:
        if principal.role not in _PAYROLL_WRITE_ROLES:
            raise ForbiddenScopeError("Only owners or HR may close monthly payroll")

        tenant = await self._tenant_repo.get_by_id(principal.tenant_id)
        if tenant is None:
            raise EntityNotFoundError("Tenant not found")

        store = await self._store_repo.get_by_id(principal.tenant_id, store_id)
        if store is None:
            raise EntityNotFoundError(f"Store {store_id} not found")

        report, all_blockers = await _evaluate_store_month_readiness(
            tenant_id=principal.tenant_id,
            store=store,
            period_month=period_month,
            employee_repo=self._employee_repo,
            attendance_repo=self._attendance_repo,
            leave_repo=self._leave_repo,
            payroll_repo=self._payroll_repo,
            hide_legal_hold_for_manager=False,
            policy_repo=self._policy_repo,
        )
        if all_blockers:
            raise ConflictError(
                f"Cannot close {period_month} for store {store.code}: {all_blockers[0].message}"
            )

        clean_notes = (
            validate_non_empty_text(notes, field="notes", max_length=500)
            if notes.strip()
            else ""
        )
        now = now_utc.astimezone(UTC)
        existing = report.existing_closing
        closing_id = existing.id if existing is not None else uuid4()
        revision_number = (existing.revision_number + 1) if existing is not None else 1
        retention_until = compute_closing_retention_until(period_month, now)

        uploaded_keys: list[str] = []
        items_to_append: list[MonthlyClosingItem] = []

        if existing is None:
            closing = MonthlyClosing(
                id=closing_id,
                tenant_id=principal.tenant_id,
                store_id=store.id,
                period_month=period_month,
                status=MonthlyClosingStatus.CLOSED,
                revision_number=revision_number,
                closed_by_user_id=principal.user_id,
                closed_at_utc=now,
                reopened_by_user_id=None,
                reopened_at_utc=None,
                reopen_reason="",
                notes=clean_notes,
                retention_until=retention_until,
                created_at=now,
                updated_at=now,
            )
            saved_closing = await self._payroll_repo.create_closing(closing)
        else:
            closing = MonthlyClosing(
                id=existing.id,
                tenant_id=existing.tenant_id,
                store_id=existing.store_id,
                period_month=existing.period_month,
                status=MonthlyClosingStatus.CLOSED,
                revision_number=revision_number,
                closed_by_user_id=principal.user_id,
                closed_at_utc=now,
                reopened_by_user_id=existing.reopened_by_user_id,
                reopened_at_utc=existing.reopened_at_utc,
                reopen_reason=existing.reopen_reason,
                notes=clean_notes,
                retention_until=retention_until,
                created_at=existing.created_at,
                updated_at=now,
            )
            updated = await self._payroll_repo.update_closing(
                closing,
                expected_status=MonthlyClosingStatus.REOPENED,
                expected_revision=existing.revision_number,
            )
            if updated is None:
                raise EntityNotFoundError(f"Monthly closing {existing.id} not found")
            saved_closing = updated

        try:
            for emp_summary in report.employees:
                pdf_bytes = self._pdf_renderer.render_monthly_attendance_pdf(
                    AttendancePdfDocumentInput(
                        tenant_legal_name=tenant.legal_name,
                        tenant_cif=tenant.cif_nif,
                        store_code=store.code,
                        store_name=store.name,
                        store_timezone=store.timezone,
                        period_month=period_month,
                        revision_number=revision_number,
                        employee_full_name=emp_summary.employee_full_name,
                        employee_code=emp_summary.employee_code,
                        nie_dni_masked=emp_summary.nie_dni_masked,
                        contract_type=emp_summary.contract_type,
                        work_schedule_type=emp_summary.work_schedule_type,
                        contracted_weekly_hours=emp_summary.contracted_weekly_hours,
                        worked_days=emp_summary.worked_days,
                        total_net_hours=emp_summary.total_net_hours,
                        regular_hours=emp_summary.regular_hours,
                        overtime_hours=emp_summary.overtime_hours,
                        annual_overtime_hours=emp_summary.annual_overtime_hours,
                        total_break_minutes=emp_summary.total_break_minutes,
                        vacation_days=emp_summary.vacation_days,
                        sick_leave_days=emp_summary.sick_leave_days,
                        paid_leave_days=emp_summary.paid_leave_days,
                        unpaid_leave_days=emp_summary.unpaid_leave_days,
                        warning_alerts_count=emp_summary.warning_alerts_count,
                        violation_alerts_count=emp_summary.violation_alerts_count,
                        daily_rows=emp_summary.daily_rows,
                        generated_at_utc=now,
                    )
                )
                sha256_hex = hashlib.sha256(pdf_bytes).hexdigest()
                doc_id = uuid4()
                storage_key = build_storage_key(principal.tenant_id, doc_id)
                await self._storage.put_object(
                    storage_key=storage_key,
                    content=pdf_bytes,
                    mime_type="application/pdf",
                )
                uploaded_keys.append(storage_key)

                doc = ComplianceDocument(
                    id=doc_id,
                    tenant_id=principal.tenant_id,
                    store_id=store.id,
                    employee_id=emp_summary.employee_id,
                    category=DocumentCategory.ATTENDANCE_REPORT,
                    title=(
                        f"Registro Jornada {period_month} - "
                        f"{emp_summary.employee_code} (v{revision_number})"
                    ),
                    period_month=period_month,
                    storage_backend=self._storage.backend_name,
                    storage_key=storage_key,
                    original_filename=(
                        f"registro_jornada_{period_month}_{emp_summary.employee_code}"
                        f"_v{revision_number}.pdf"
                    ),
                    mime_type="application/pdf",
                    size_bytes=len(pdf_bytes),
                    sha256_hex=sha256_hex,
                    requires_signature=True,
                    uploaded_by_user_id=principal.user_id,
                    retention_until=compute_document_retention_until(now),
                    created_at=now,
                    updated_at=now,
                )
                saved_doc = await self._doc_repo.create(doc)

                items_to_append.append(
                    MonthlyClosingItem(
                        id=uuid4(),
                        tenant_id=principal.tenant_id,
                        closing_id=closing_id,
                        store_id=store.id,
                        period_month=period_month,
                        revision_number=revision_number,
                        employee_id=emp_summary.employee_id,
                        employee_code=emp_summary.employee_code,
                        employee_full_name=emp_summary.employee_full_name,
                        nie_dni_masked=emp_summary.nie_dni_masked,
                        contract_type=emp_summary.contract_type,
                        work_schedule_type=emp_summary.work_schedule_type,
                        contracted_weekly_hours=emp_summary.contracted_weekly_hours,
                        worked_days=emp_summary.worked_days,
                        total_net_hours=emp_summary.total_net_hours,
                        regular_hours=emp_summary.regular_hours,
                        overtime_hours=emp_summary.overtime_hours,
                        annual_overtime_hours=emp_summary.annual_overtime_hours,
                        total_break_minutes=emp_summary.total_break_minutes,
                        vacation_days=emp_summary.vacation_days,
                        sick_leave_days=emp_summary.sick_leave_days,
                        paid_leave_days=emp_summary.paid_leave_days,
                        unpaid_leave_days=emp_summary.unpaid_leave_days,
                        warning_alerts_count=emp_summary.warning_alerts_count,
                        violation_alerts_count=emp_summary.violation_alerts_count,
                        attendance_report_document_id=saved_doc.id,
                        retention_until=retention_until,
                        created_at=now,
                    )
                )

            saved_items = (
                await self._payroll_repo.append_items(items_to_append)
                if items_to_append
                else []
            )
        except Exception:
            for key in uploaded_keys:
                with contextlib.suppress(Exception):
                    await self._storage.delete_object(storage_key=key)
            raise

        return ClosingDetailResult(
            closing=saved_closing,
            store_code=store.code,
            store_name=store.name,
            items=tuple(
                ClosingItemWithReceipt(
                    item=it,
                    is_signed=False,
                    signed_at_utc=None,
                    document_sha256_hex=None,
                )
                for it in saved_items
            ),
        )


class GetMonthlyClosingDetailUseCase:
    def __init__(
        self,
        payroll_repo: PayrollClosingRepository,
        store_repo: StoreRepository,
        employee_repo: EmployeeRepository,
        doc_repo: DocumentRepository,
    ) -> None:
        self._payroll_repo = payroll_repo
        self._store_repo = store_repo
        self._employee_repo = employee_repo
        self._doc_repo = doc_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        closing_id: UUID,
    ) -> ClosingDetailResult:
        if principal.role not in _PAYROLL_READ_ROLES:
            raise ForbiddenScopeError("Employees cannot access payroll closing details")

        closing = await self._payroll_repo.get_by_id(principal.tenant_id, closing_id)
        if closing is None:
            raise EntityNotFoundError(f"Monthly closing {closing_id} not found")

        if principal.role == UserRole.STORE_MANAGER:
            principal.require_store_access(principal.tenant_id, closing.store_id)

        store = await self._store_repo.get_by_id(principal.tenant_id, closing.store_id)
        items = await self._payroll_repo.list_items(
            principal.tenant_id,
            closing.id,
            revision_number=closing.revision_number,
        )
        if principal.role == UserRole.STORE_MANAGER:
            blocked_ids = await _blocked_employee_ids_for_manager(
                self._employee_repo, principal.tenant_id
            )
            items = [it for it in items if it.employee_id not in blocked_ids]

        doc_ids = [
            it.attendance_report_document_id
            for it in items
            if it.attendance_report_document_id is not None
        ]
        receipts = await self._doc_repo.list_receipts_for_documents(principal.tenant_id, doc_ids)
        receipt_by_doc: dict[UUID, DocumentReceipt] = {r.document_id: r for r in receipts}

        enriched: list[ClosingItemWithReceipt] = []
        for it in items:
            receipt = (
                receipt_by_doc.get(it.attendance_report_document_id)
                if it.attendance_report_document_id is not None
                else None
            )
            enriched.append(
                ClosingItemWithReceipt(
                    item=it,
                    is_signed=receipt is not None,
                    signed_at_utc=receipt.signed_at_utc if receipt is not None else None,
                    document_sha256_hex=(
                        receipt.document_sha256_hex if receipt is not None else None
                    ),
                )
            )

        return ClosingDetailResult(
            closing=closing,
            store_code=store.code if store is not None else "",
            store_name=store.name if store is not None else "",
            items=tuple(enriched),
        )


class ReopenMonthlyClosingUseCase:
    def __init__(
        self,
        payroll_repo: PayrollClosingRepository,
        store_repo: StoreRepository,
        employee_repo: EmployeeRepository,
        doc_repo: DocumentRepository,
    ) -> None:
        self._payroll_repo = payroll_repo
        self._store_repo = store_repo
        self._employee_repo = employee_repo
        self._doc_repo = doc_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        closing_id: UUID,
        reopen_reason: str,
        now_utc: datetime,
    ) -> ClosingDetailResult:
        if principal.role not in _PAYROLL_WRITE_ROLES:
            raise ForbiddenScopeError("Only owners or HR may reopen a closed payroll month")

        reason = validate_non_empty_text(
            reopen_reason, field="reopen_reason", max_length=500
        )
        closing = await self._payroll_repo.get_by_id(principal.tenant_id, closing_id)
        if closing is None:
            raise EntityNotFoundError(f"Monthly closing {closing_id} not found")

        if closing.status != MonthlyClosingStatus.CLOSED:
            raise ConflictError("Only closed monthly closings can be reopened")

        now = now_utc.astimezone(UTC)
        updated = await self._payroll_repo.update_closing(
            MonthlyClosing(
                id=closing.id,
                tenant_id=closing.tenant_id,
                store_id=closing.store_id,
                period_month=closing.period_month,
                status=MonthlyClosingStatus.REOPENED,
                revision_number=closing.revision_number,
                closed_by_user_id=closing.closed_by_user_id,
                closed_at_utc=closing.closed_at_utc,
                reopened_by_user_id=principal.user_id,
                reopened_at_utc=now,
                reopen_reason=reason,
                notes=closing.notes,
                retention_until=closing.retention_until,
                created_at=closing.created_at,
                updated_at=now,
            ),
            expected_status=MonthlyClosingStatus.CLOSED,
            expected_revision=closing.revision_number,
        )
        if updated is None:
            raise EntityNotFoundError(f"Monthly closing {closing_id} not found")

        return await GetMonthlyClosingDetailUseCase(
            self._payroll_repo, self._store_repo, self._employee_repo, self._doc_repo
        ).execute(principal, closing_id=updated.id)


class ExportGestoriaPayrollCsvUseCase:
    def __init__(
        self,
        payroll_repo: PayrollClosingRepository,
        tenant_repo: TenantRepository,
        store_repo: StoreRepository,
        doc_repo: DocumentRepository,
        employee_repo: EmployeeRepository | None = None,
        pii_cipher: PiiCipher | None = None,
    ) -> None:
        self._payroll_repo = payroll_repo
        self._tenant_repo = tenant_repo
        self._store_repo = store_repo
        self._doc_repo = doc_repo
        self._employee_repo = employee_repo
        self._pii_cipher = pii_cipher

    async def _decrypt_identifiers(
        self,
        principal: AuthenticatedPrincipal,
        employee_ids: list[UUID],
    ) -> dict[UUID, tuple[str, str]]:
        """Return {employee_id: (nie_dni, naf)}; empty strings when erased/missing."""
        if self._employee_repo is None or self._pii_cipher is None:
            raise RuntimeError("Full identifier export requires employee repository and PII cipher")
        result: dict[UUID, tuple[str, str]] = {}
        for employee_id in dict.fromkeys(employee_ids):
            emp = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
            if emp is None or emp.employment_status == EmploymentStatus.ANONYMIZED:
                result[employee_id] = ("", "")
                continue
            nie_dni = (
                self._pii_cipher.decrypt(
                    emp.nie_dni_enc,
                    tenant_id=principal.tenant_id,
                    employee_id=emp.id,
                    field_name="nie_dni",
                )
                if emp.nie_dni_enc
                else ""
            )
            naf = (
                self._pii_cipher.decrypt(
                    emp.naf_enc,
                    tenant_id=principal.tenant_id,
                    employee_id=emp.id,
                    field_name="naf",
                )
                if emp.naf_enc
                else ""
            )
            result[employee_id] = (nie_dni, naf)
        return result

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        closing_id: UUID,
        include_full_identifiers: bool = False,
    ) -> GestoriaExportResult:
        if principal.role not in _GESTORIA_EXPORT_ROLES:
            raise ForbiddenScopeError(
                "Only owners, HR, or accountants may export Gestoria payroll CSV"
            )

        closing = await self._payroll_repo.get_by_id(principal.tenant_id, closing_id)
        if closing is None:
            raise EntityNotFoundError(f"Monthly closing {closing_id} not found")

        if closing.status != MonthlyClosingStatus.CLOSED:
            raise ConflictError(
                "Gestoria payroll export requires the monthly closing to be in 'closed' status"
            )

        tenant = await self._tenant_repo.get_by_id(principal.tenant_id)
        if tenant is None:
            raise EntityNotFoundError("Tenant not found")

        store = await self._store_repo.get_by_id(principal.tenant_id, closing.store_id)
        if store is None:
            raise EntityNotFoundError(f"Store {closing.store_id} not found")

        items = await self._payroll_repo.list_items(
            principal.tenant_id,
            closing.id,
            revision_number=closing.revision_number,
        )
        doc_ids = [
            it.attendance_report_document_id
            for it in items
            if it.attendance_report_document_id is not None
        ]
        receipts = await self._doc_repo.list_receipts_for_documents(principal.tenant_id, doc_ids)
        receipt_by_doc = {r.document_id: r for r in receipts}

        full_ids: dict[UUID, tuple[str, str]] = {}
        if include_full_identifiers:
            full_ids = await self._decrypt_identifiers(principal, [it.employee_id for it in items])

        buffer = io.StringIO()
        buffer.write("\ufeff")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(
            [
                "Razón Social",
                "CIF",
                "Código Centro",
                "Nombre Centro",
                "Periodo",
                "Revisión",
                "Código Empleado",
                "Nombre Completo",
                *(
                    ["NIE/DNI", "NAF (Nº Afiliación SS)"]
                    if include_full_identifiers
                    else ["NIE/DNI (Enmascarado)"]
                ),
                "Tipo Contrato",
                "Jornada",
                "Horas Semanales Pactadas",
                "Días Trabajados",
                "Horas Netas",
                "Horas Ordinarias",
                "Horas Extra",
                "Horas Extra Anuales",
                "Pausa Total (min)",
                "Vacaciones (días)",
                "Baja Médica IT (días)",
                "Permiso Retribuido (días)",
                "Permiso No Retribuido (días)",
                "Avisos Legales",
                "Infracciones Legales",
                "Acuse Firmado",
                "Fecha Firma UTC",
            ]
        )

        for it in items:
            receipt = (
                receipt_by_doc.get(it.attendance_report_document_id)
                if it.attendance_report_document_id is not None
                else None
            )
            writer.writerow(
                [
                    _sanitize_csv_cell(tenant.legal_name),
                    _sanitize_csv_cell(tenant.cif_nif),
                    _sanitize_csv_cell(store.code),
                    _sanitize_csv_cell(store.name),
                    _sanitize_csv_cell(closing.period_month),
                    str(closing.revision_number),
                    _sanitize_csv_cell(it.employee_code),
                    _sanitize_csv_cell(it.employee_full_name),
                    *(
                        [
                            _sanitize_csv_cell(full_ids.get(it.employee_id, ("", ""))[0]),
                            _sanitize_csv_cell(full_ids.get(it.employee_id, ("", ""))[1]),
                        ]
                        if include_full_identifiers
                        else [_sanitize_csv_cell(it.nie_dni_masked or "")]
                    ),
                    _sanitize_csv_cell(it.contract_type.value),
                    _sanitize_csv_cell(it.work_schedule_type.value),
                    f"{it.contracted_weekly_hours:.2f}",
                    str(it.worked_days),
                    f"{it.total_net_hours:.2f}",
                    f"{it.regular_hours:.2f}",
                    f"{it.overtime_hours:.2f}",
                    f"{it.annual_overtime_hours:.2f}",
                    str(it.total_break_minutes),
                    str(it.vacation_days),
                    str(it.sick_leave_days),
                    str(it.paid_leave_days),
                    str(it.unpaid_leave_days),
                    str(it.warning_alerts_count),
                    str(it.violation_alerts_count),
                    "Sí" if receipt is not None else "No",
                    _sanitize_csv_cell(
                        receipt.signed_at_utc.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
                        if receipt is not None
                        else ""
                    ),
                ]
            )

        safe_store = (
            "".join(
                ch if (ch.isascii() and ch.isalnum()) or ch in ("-", "_") else "_"
                for ch in store.code
            )
            or "store"
        )
        filename = (
            f"gestoria_nomina_{safe_store}_{closing.period_month}"
            f"_v{closing.revision_number}.csv"
        )
        return GestoriaExportResult(csv_text=buffer.getvalue(), filename=filename)
