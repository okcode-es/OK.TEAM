"""Repository abstract interfaces (Dependency Inversion Principle).

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.domain.employees import (
    ContractType,
    Employee,
    EmploymentStatus,
    WorkScheduleType,
)
from app.domain.organization import Membership, Store, Tenant, TenantMembershipSummary
from app.domain.security import MembershipStatus, UserRole


class TenantRepository(Protocol):
    async def get_by_id(self, tenant_id: UUID) -> Tenant | None: ...

    async def exists_active_cif(
        self,
        cif_nif: str,
        *,
        exclude_tenant_id: UUID | None = None,
    ) -> bool: ...

    async def create(self, tenant: Tenant) -> Tenant: ...

    async def update(
        self,
        tenant_id: UUID,
        *,
        legal_name: str,
        trade_name: str,
        cif_nif: str,
        default_timezone: str,
        default_locale: str,
        updated_at: datetime,
    ) -> Tenant | None: ...


class StoreRepository(Protocol):
    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        include_inactive: bool = False,
    ) -> list[Store]: ...

    async def get_by_id(self, tenant_id: UUID, store_id: UUID) -> Store | None: ...

    async def exists_active_code(
        self,
        tenant_id: UUID,
        code: str,
        *,
        exclude_store_id: UUID | None = None,
    ) -> bool: ...

    async def count_existing_in_tenant(
        self,
        tenant_id: UUID,
        store_ids: frozenset[UUID],
    ) -> int: ...

    async def create(self, store: Store) -> Store: ...

    async def update(
        self,
        tenant_id: UUID,
        store_id: UUID,
        *,
        name: str,
        code: str,
        timezone: str,
        address: str,
        city: str,
        postal_code: str,
        is_active: bool,
        updated_at: datetime,
    ) -> Store | None: ...

    async def soft_delete(
        self,
        tenant_id: UUID,
        store_id: UUID,
        *,
        deleted_at: datetime,
    ) -> bool: ...


class MembershipRepository(Protocol):
    async def list_summaries_for_user(self, user_id: UUID) -> list[TenantMembershipSummary]: ...

    async def get_active_for_user_in_tenant(
        self,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Membership | None: ...

    async def get_by_id(self, tenant_id: UUID, membership_id: UUID) -> Membership | None: ...

    async def exists_active_user_in_tenant(self, tenant_id: UUID, user_id: UUID) -> bool: ...

    async def count_active_owners(self, tenant_id: UUID) -> int: ...

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        filter_store_ids: frozenset[UUID] | None = None,
        only_user_id: UUID | None = None,
    ) -> list[Membership]: ...

    async def create(self, membership: Membership) -> Membership: ...

    async def update(
        self,
        tenant_id: UUID,
        membership_id: UUID,
        *,
        display_name: str,
        role: UserRole,
        status: MembershipStatus,
        store_ids: frozenset[UUID],
        updated_at: datetime,
    ) -> Membership | None: ...

    async def soft_delete(
        self,
        tenant_id: UUID,
        membership_id: UUID,
        *,
        deleted_at: datetime,
    ) -> bool: ...


class EmployeeRepository(Protocol):
    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        allowed_statuses: frozenset[EmploymentStatus] | None = None,
        only_user_id: UUID | None = None,
        store_id: UUID | None = None,
        status: EmploymentStatus | None = None,
    ) -> list[Employee]: ...

    async def get_by_id(self, tenant_id: UUID, employee_id: UUID) -> Employee | None: ...

    async def exists_active_code(
        self,
        tenant_id: UUID,
        employee_code: str,
        *,
        exclude_employee_id: UUID | None = None,
    ) -> bool: ...

    async def exists_active_nie_blind_index(
        self,
        tenant_id: UUID,
        nie_dni_blind_index: str,
        *,
        exclude_employee_id: UUID | None = None,
    ) -> bool: ...

    async def create(self, employee: Employee) -> Employee: ...

    async def update(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        primary_store_id: UUID,
        user_id: UUID | None,
        employee_code: str,
        full_name: str,
        preferred_name: str,
        email: str,
        phone: str,
        birth_date: date | None,
        contract_type: ContractType,
        work_schedule_type: WorkScheduleType,
        contracted_weekly_hours: Decimal,
        annual_vacation_days: int,
        hire_date: date,
        employment_status: EmploymentStatus,
        nie_dni_enc: str | None,
        nie_dni_masked: str | None,
        nie_dni_blind_index: str | None,
        iban_enc: str | None,
        iban_masked: str | None,
        naf_enc: str | None,
        naf_masked: str | None,
        updated_at: datetime,
    ) -> Employee | None: ...

    async def terminate_and_hold(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        termination_date: date,
        retention_until: datetime,
        updated_at: datetime,
    ) -> Employee | None: ...

    async def anonymize(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        anonymized_full_name: str,
        anonymized_at: datetime,
    ) -> Employee | None: ...
