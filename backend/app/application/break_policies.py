"""Application use cases and resolution helpers for 3-tier custom break policies."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.break_policies import (
    BreakMode,
    BreakPolicy,
    BreakScopeType,
    BreakTrackingMode,
    BreakWindow,
    EffectiveBreakPolicy,
    resolve_effective_break_policy,
    validate_break_policy_config,
)
from app.domain.employees import Employee
from app.domain.organization import EntityNotFoundError
from app.domain.repositories import BreakPolicyRepository, EmployeeRepository, StoreRepository
from app.domain.security import (
    TENANT_WIDE_ROLES,
    AuthenticatedPrincipal,
    ForbiddenScopeError,
    UserRole,
)

_TENANT_ADMIN_ROLES: frozenset[UserRole] = frozenset({UserRole.OWNER, UserRole.HR})


@dataclass(frozen=True, slots=True)
class BreakPolicyLookup:
    tenant_policy: BreakPolicy | None
    store_policies: dict[UUID, BreakPolicy]
    employee_policies: dict[UUID, BreakPolicy]


def build_break_policy_lookup(policies: Sequence[BreakPolicy]) -> BreakPolicyLookup:
    tenant_policy: BreakPolicy | None = None
    store_policies: dict[UUID, BreakPolicy] = {}
    employee_policies: dict[UUID, BreakPolicy] = {}
    for pol in policies:
        if pol.deleted_at is not None or not pol.is_active:
            continue
        if pol.scope_type == BreakScopeType.TENANT:
            tenant_policy = pol
        elif pol.scope_type == BreakScopeType.STORE and pol.store_id is not None:
            store_policies[pol.store_id] = pol
        elif pol.scope_type == BreakScopeType.EMPLOYEE and pol.employee_id is not None:
            employee_policies[pol.employee_id] = pol
    return BreakPolicyLookup(
        tenant_policy=tenant_policy,
        store_policies=store_policies,
        employee_policies=employee_policies,
    )


def resolve_policy_for_employee(
    employee: Employee,
    lookup: BreakPolicyLookup,
) -> EffectiveBreakPolicy:
    return resolve_effective_break_policy(
        tenant_policy=lookup.tenant_policy,
        store_policy=lookup.store_policies.get(employee.primary_store_id),
        employee_policy=lookup.employee_policies.get(employee.id),
        legacy_default_break_minutes=employee.default_break_minutes,
    )


@dataclass(frozen=True, slots=True)
class UpsertBreakPolicyCommand:
    break_mode: BreakMode
    duration_minutes: int | None = None
    windows: tuple[BreakWindow, ...] = ()
    is_paid: bool = False
    tracking_mode: BreakTrackingMode = BreakTrackingMode.MANUAL_PUNCH
    min_shift_minutes_for_auto_deduct: int = 360
    auto_end_manual_break: bool = True
    is_active: bool = True


class ListBreakPoliciesUseCase:
    def __init__(
        self,
        policy_repo: BreakPolicyRepository,
        employee_repo: EmployeeRepository,
    ) -> None:
        self._policy_repo = policy_repo
        self._employee_repo = employee_repo

    async def execute(self, principal: AuthenticatedPrincipal) -> list[BreakPolicy]:
        policies = await self._policy_repo.list_by_tenant(principal.tenant_id)
        if principal.role in TENANT_WIDE_ROLES:
            return policies

        if principal.role == UserRole.STORE_MANAGER:
            all_emps = await self._employee_repo.list_by_tenant(principal.tenant_id)
            scoped_emp_ids = {
                e.id
                for e in all_emps
                if e.deleted_at is None and principal.can_manage_employee(e)
            }
            return [
                p
                for p in policies
                if p.scope_type == BreakScopeType.TENANT
                or (p.scope_type == BreakScopeType.STORE and p.store_id in principal.store_ids)
                or (p.scope_type == BreakScopeType.EMPLOYEE and p.employee_id in scoped_emp_ids)
            ]

        # UserRole.EMPLOYEE: applicable policies for self
        self_emps = await self._employee_repo.list_by_tenant(
            principal.tenant_id, only_user_id=principal.user_id
        )
        self_emp = next((e for e in self_emps if e.deleted_at is None), None)
        if self_emp is None:
            return [p for p in policies if p.scope_type == BreakScopeType.TENANT]
        return [
            p
            for p in policies
            if p.scope_type == BreakScopeType.TENANT
            or (
                p.scope_type == BreakScopeType.STORE
                and p.store_id == self_emp.primary_store_id
            )
            or (p.scope_type == BreakScopeType.EMPLOYEE and p.employee_id == self_emp.id)
        ]


class UpsertTenantBreakPolicyUseCase:
    def __init__(self, policy_repo: BreakPolicyRepository) -> None:
        self._policy_repo = policy_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        cmd: UpsertBreakPolicyCommand,
        *,
        now_utc: datetime,
    ) -> BreakPolicy:
        if principal.role not in _TENANT_ADMIN_ROLES:
            raise ForbiddenScopeError(
                "Only owner or HR may configure tenant-level break policies"
            )
        duration, windows = validate_break_policy_config(
            break_mode=cmd.break_mode,
            duration_minutes=cmd.duration_minutes,
            windows=cmd.windows,
            min_shift_minutes_for_auto_deduct=cmd.min_shift_minutes_for_auto_deduct,
        )
        now = now_utc.astimezone(UTC)
        existing = await self._policy_repo.get_by_scope(
            principal.tenant_id, scope_type=BreakScopeType.TENANT
        )
        return await self._policy_repo.upsert(
            BreakPolicy(
                id=existing.id if existing is not None else uuid4(),
                tenant_id=principal.tenant_id,
                scope_type=BreakScopeType.TENANT,
                store_id=None,
                employee_id=None,
                break_mode=cmd.break_mode,
                duration_minutes=duration,
                windows=windows,
                is_paid=cmd.is_paid,
                tracking_mode=cmd.tracking_mode,
                min_shift_minutes_for_auto_deduct=cmd.min_shift_minutes_for_auto_deduct,
                auto_end_manual_break=cmd.auto_end_manual_break,
                is_active=cmd.is_active,
                updated_by_user_id=principal.user_id,
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
            )
        )


class UpsertStoreBreakPolicyUseCase:
    def __init__(
        self,
        policy_repo: BreakPolicyRepository,
        store_repo: StoreRepository,
    ) -> None:
        self._policy_repo = policy_repo
        self._store_repo = store_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        store_id: UUID,
        cmd: UpsertBreakPolicyCommand,
        *,
        now_utc: datetime,
    ) -> BreakPolicy:
        if principal.role not in (*_TENANT_ADMIN_ROLES, UserRole.STORE_MANAGER):
            raise ForbiddenScopeError(
                "Only owner, HR, or store_manager may configure store break policies"
            )
        if principal.role == UserRole.STORE_MANAGER:
            if principal.department_scope and principal.department_scope.strip():
                raise ForbiddenScopeError(
                    "Department-scoped store manager cannot configure store-wide break policies"
                )
            principal.require_store_access(principal.tenant_id, store_id)

        store = await self._store_repo.get_by_id(principal.tenant_id, store_id)
        if store is None:
            raise EntityNotFoundError(f"Store {store_id} not found")

        duration, windows = validate_break_policy_config(
            break_mode=cmd.break_mode,
            duration_minutes=cmd.duration_minutes,
            windows=cmd.windows,
            min_shift_minutes_for_auto_deduct=cmd.min_shift_minutes_for_auto_deduct,
        )
        now = now_utc.astimezone(UTC)
        existing = await self._policy_repo.get_by_scope(
            principal.tenant_id, scope_type=BreakScopeType.STORE, store_id=store_id
        )
        return await self._policy_repo.upsert(
            BreakPolicy(
                id=existing.id if existing is not None else uuid4(),
                tenant_id=principal.tenant_id,
                scope_type=BreakScopeType.STORE,
                store_id=store_id,
                employee_id=None,
                break_mode=cmd.break_mode,
                duration_minutes=duration,
                windows=windows,
                is_paid=cmd.is_paid,
                tracking_mode=cmd.tracking_mode,
                min_shift_minutes_for_auto_deduct=cmd.min_shift_minutes_for_auto_deduct,
                auto_end_manual_break=cmd.auto_end_manual_break,
                is_active=cmd.is_active,
                updated_by_user_id=principal.user_id,
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
            )
        )


class UpsertEmployeeBreakPolicyUseCase:
    def __init__(
        self,
        policy_repo: BreakPolicyRepository,
        employee_repo: EmployeeRepository,
    ) -> None:
        self._policy_repo = policy_repo
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        employee_id: UUID,
        cmd: UpsertBreakPolicyCommand,
        *,
        now_utc: datetime,
    ) -> BreakPolicy:
        if principal.role not in (*_TENANT_ADMIN_ROLES, UserRole.STORE_MANAGER):
            raise ForbiddenScopeError(
                "Only owner, HR, or store_manager may configure employee break policies"
            )
        emp = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
        if emp is None or emp.deleted_at is not None:
            raise EntityNotFoundError(f"Employee {employee_id} not found")
        if principal.role == UserRole.STORE_MANAGER:
            principal.require_employee_in_scope(emp)

        duration, windows = validate_break_policy_config(
            break_mode=cmd.break_mode,
            duration_minutes=cmd.duration_minutes,
            windows=cmd.windows,
            min_shift_minutes_for_auto_deduct=cmd.min_shift_minutes_for_auto_deduct,
        )
        now = now_utc.astimezone(UTC)
        existing = await self._policy_repo.get_by_scope(
            principal.tenant_id, scope_type=BreakScopeType.EMPLOYEE, employee_id=employee_id
        )
        return await self._policy_repo.upsert(
            BreakPolicy(
                id=existing.id if existing is not None else uuid4(),
                tenant_id=principal.tenant_id,
                scope_type=BreakScopeType.EMPLOYEE,
                store_id=None,
                employee_id=employee_id,
                break_mode=cmd.break_mode,
                duration_minutes=duration,
                windows=windows,
                is_paid=cmd.is_paid,
                tracking_mode=cmd.tracking_mode,
                min_shift_minutes_for_auto_deduct=cmd.min_shift_minutes_for_auto_deduct,
                auto_end_manual_break=cmd.auto_end_manual_break,
                is_active=cmd.is_active,
                updated_by_user_id=principal.user_id,
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
            )
        )


class DeleteBreakPolicyUseCase:
    def __init__(
        self,
        policy_repo: BreakPolicyRepository,
        employee_repo: EmployeeRepository,
    ) -> None:
        self._policy_repo = policy_repo
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        policy_id: UUID,
        *,
        now_utc: datetime,
    ) -> BreakPolicy:
        if principal.role not in (*_TENANT_ADMIN_ROLES, UserRole.STORE_MANAGER):
            raise ForbiddenScopeError("Only owner, HR, or store_manager may delete break policies")

        policy = await self._policy_repo.get_by_id(principal.tenant_id, policy_id)
        if policy is None:
            raise EntityNotFoundError(f"Break policy {policy_id} not found")

        if principal.role == UserRole.STORE_MANAGER:
            if policy.scope_type == BreakScopeType.TENANT:
                raise ForbiddenScopeError("Store manager cannot delete tenant-level break policy")
            if policy.scope_type == BreakScopeType.STORE and policy.store_id is not None:
                if principal.department_scope and principal.department_scope.strip():
                    raise ForbiddenScopeError(
                        "Department-scoped store manager cannot delete store-wide break policies"
                    )
                principal.require_store_access(principal.tenant_id, policy.store_id)
            if policy.scope_type == BreakScopeType.EMPLOYEE and policy.employee_id is not None:
                emp = await self._employee_repo.get_by_id(
                    principal.tenant_id, policy.employee_id
                )
                if emp is None or emp.deleted_at is not None:
                    raise EntityNotFoundError(f"Employee {policy.employee_id} not found")
                principal.require_employee_in_scope(emp)

        deleted = await self._policy_repo.soft_delete(
            principal.tenant_id,
            policy_id,
            deleted_at=now_utc.astimezone(UTC),
            updated_by_user_id=principal.user_id,
        )
        if deleted is None:
            raise EntityNotFoundError(f"Break policy {policy_id} not found")
        return deleted


class GetEffectiveBreakPolicyUseCase:
    def __init__(
        self,
        policy_repo: BreakPolicyRepository,
        employee_repo: EmployeeRepository,
    ) -> None:
        self._policy_repo = policy_repo
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        employee_id: UUID,
    ) -> EffectiveBreakPolicy:
        emp = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
        if emp is None or emp.deleted_at is not None:
            raise EntityNotFoundError(f"Employee {employee_id} not found")

        if principal.role == UserRole.EMPLOYEE:
            if emp.user_id != principal.user_id:
                raise ForbiddenScopeError(
                    "Employees may only view their own effective break policy"
                )
        elif principal.role == UserRole.STORE_MANAGER:
            principal.require_employee_in_scope(emp)

        policies = await self._policy_repo.list_by_tenant(principal.tenant_id)
        lookup = build_break_policy_lookup(policies)
        return resolve_policy_for_employee(emp, lookup)
