"""Async SQLAlchemy 2.0 repositories enforcing strict tenant_id isolation & soft deletes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.employees import (
    ContractType,
    Employee,
    EmploymentStatus,
    WorkScheduleType,
)
from app.domain.organization import Membership, Store, Tenant, TenantMembershipSummary
from app.domain.security import MembershipStatus, UserRole
from app.infrastructure.models import (
    EmployeeModel,
    MembershipModel,
    MembershipStoreModel,
    StoreModel,
    TenantModel,
)


def _to_tenant(model: TenantModel) -> Tenant:
    return Tenant(
        id=model.id,
        legal_name=model.legal_name,
        trade_name=model.trade_name,
        cif_nif=model.cif_nif,
        default_timezone=model.default_timezone,
        default_locale=model.default_locale,
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
    )


def _to_store(model: StoreModel) -> Store:
    return Store(
        id=model.id,
        tenant_id=model.tenant_id,
        name=model.name,
        code=model.code,
        timezone=model.timezone,
        address=model.address,
        city=model.city,
        postal_code=model.postal_code,
        is_active=model.is_active,
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
    )


def _to_membership(model: MembershipModel) -> Membership:
    return Membership(
        id=model.id,
        tenant_id=model.tenant_id,
        user_id=model.user_id,
        email=model.email,
        display_name=model.display_name,
        role=UserRole(model.role),
        status=MembershipStatus(model.status),
        store_ids=frozenset(link.store_id for link in model.store_links),
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
    )


class SqlAlchemyTenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, tenant_id: UUID) -> Tenant | None:
        stmt = select(TenantModel).where(
            TenantModel.id == tenant_id,
            TenantModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_tenant(model) if model is not None else None

    async def exists_active_cif(
        self,
        cif_nif: str,
        *,
        exclude_tenant_id: UUID | None = None,
    ) -> bool:
        stmt = select(TenantModel.id).where(
            func.upper(TenantModel.cif_nif) == cif_nif.upper(),
            TenantModel.deleted_at.is_(None),
        )
        if exclude_tenant_id is not None:
            stmt = stmt.where(TenantModel.id != exclude_tenant_id)
        result = await self._session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    async def create(self, tenant: Tenant) -> Tenant:
        model = TenantModel(
            id=tenant.id,
            legal_name=tenant.legal_name,
            trade_name=tenant.trade_name,
            cif_nif=tenant.cif_nif,
            default_timezone=tenant.default_timezone,
            default_locale=tenant.default_locale,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
            deleted_at=tenant.deleted_at,
        )
        self._session.add(model)
        await self._session.flush()
        return _to_tenant(model)

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
    ) -> Tenant | None:
        stmt = select(TenantModel).where(
            TenantModel.id == tenant_id,
            TenantModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None

        model.legal_name = legal_name
        model.trade_name = trade_name
        model.cif_nif = cif_nif
        model.default_timezone = default_timezone
        model.default_locale = default_locale
        model.updated_at = updated_at
        await self._session.flush()
        return _to_tenant(model)


class SqlAlchemyStoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        include_inactive: bool = False,
    ) -> list[Store]:
        if allowed_store_ids is not None and len(allowed_store_ids) == 0:
            return []

        stmt = select(StoreModel).where(
            StoreModel.tenant_id == tenant_id,
            StoreModel.deleted_at.is_(None),
        )
        if not include_inactive:
            stmt = stmt.where(StoreModel.is_active.is_(True))
        if allowed_store_ids is not None:
            stmt = stmt.where(StoreModel.id.in_(allowed_store_ids))

        stmt = stmt.order_by(StoreModel.code.asc(), StoreModel.name.asc())
        result = await self._session.execute(stmt)
        return [_to_store(row) for row in result.scalars().all()]

    async def get_by_id(self, tenant_id: UUID, store_id: UUID) -> Store | None:
        stmt = select(StoreModel).where(
            StoreModel.tenant_id == tenant_id,
            StoreModel.id == store_id,
            StoreModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_store(model) if model is not None else None

    async def exists_active_code(
        self,
        tenant_id: UUID,
        code: str,
        *,
        exclude_store_id: UUID | None = None,
    ) -> bool:
        stmt = select(StoreModel.id).where(
            StoreModel.tenant_id == tenant_id,
            func.upper(StoreModel.code) == code.upper(),
            StoreModel.deleted_at.is_(None),
        )
        if exclude_store_id is not None:
            stmt = stmt.where(StoreModel.id != exclude_store_id)
        result = await self._session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    async def count_existing_in_tenant(
        self,
        tenant_id: UUID,
        store_ids: frozenset[UUID],
    ) -> int:
        if not store_ids:
            return 0
        stmt = select(func.count(StoreModel.id)).where(
            StoreModel.tenant_id == tenant_id,
            StoreModel.id.in_(store_ids),
            StoreModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def create(self, store: Store) -> Store:
        model = StoreModel(
            id=store.id,
            tenant_id=store.tenant_id,
            name=store.name,
            code=store.code,
            timezone=store.timezone,
            address=store.address,
            city=store.city,
            postal_code=store.postal_code,
            is_active=store.is_active,
            created_at=store.created_at,
            updated_at=store.updated_at,
            deleted_at=store.deleted_at,
        )
        self._session.add(model)
        await self._session.flush()
        return _to_store(model)

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
    ) -> Store | None:
        stmt = select(StoreModel).where(
            StoreModel.tenant_id == tenant_id,
            StoreModel.id == store_id,
            StoreModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None

        model.name = name
        model.code = code
        model.timezone = timezone
        model.address = address
        model.city = city
        model.postal_code = postal_code
        model.is_active = is_active
        model.updated_at = updated_at
        await self._session.flush()
        return _to_store(model)

    async def soft_delete(
        self,
        tenant_id: UUID,
        store_id: UUID,
        *,
        deleted_at: datetime,
    ) -> bool:
        stmt = select(StoreModel).where(
            StoreModel.tenant_id == tenant_id,
            StoreModel.id == store_id,
            StoreModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return False

        model.is_active = False
        model.deleted_at = deleted_at
        model.updated_at = deleted_at
        await self._session.flush()
        return True


class SqlAlchemyMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_summaries_for_user(self, user_id: UUID) -> list[TenantMembershipSummary]:
        stmt = (
            select(MembershipModel, TenantModel)
            .join(TenantModel, TenantModel.id == MembershipModel.tenant_id)
            .options(selectinload(MembershipModel.store_links))
            .where(
                MembershipModel.user_id == user_id,
                MembershipModel.deleted_at.is_(None),
                MembershipModel.status == MembershipStatus.ACTIVE.value,
                TenantModel.deleted_at.is_(None),
            )
            .order_by(TenantModel.trade_name.asc())
        )
        result = await self._session.execute(stmt)
        summaries: list[TenantMembershipSummary] = []
        for membership_model, tenant_model in result.all():
            summaries.append(
                TenantMembershipSummary(
                    tenant_id=tenant_model.id,
                    tenant_legal_name=tenant_model.legal_name,
                    tenant_trade_name=tenant_model.trade_name,
                    default_timezone=tenant_model.default_timezone,
                    default_locale=tenant_model.default_locale,
                    role=UserRole(membership_model.role),
                    status=MembershipStatus(membership_model.status),
                    store_ids=frozenset(link.store_id for link in membership_model.store_links),
                )
            )
        return summaries

    async def get_active_for_user_in_tenant(
        self,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Membership | None:
        stmt = (
            select(MembershipModel)
            .join(TenantModel, TenantModel.id == MembershipModel.tenant_id)
            .options(selectinload(MembershipModel.store_links))
            .where(
                MembershipModel.tenant_id == tenant_id,
                MembershipModel.user_id == user_id,
                MembershipModel.deleted_at.is_(None),
                MembershipModel.status == MembershipStatus.ACTIVE.value,
                TenantModel.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_membership(model) if model is not None else None

    async def get_by_id(self, tenant_id: UUID, membership_id: UUID) -> Membership | None:
        stmt = (
            select(MembershipModel)
            .options(selectinload(MembershipModel.store_links))
            .where(
                MembershipModel.tenant_id == tenant_id,
                MembershipModel.id == membership_id,
                MembershipModel.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_membership(model) if model is not None else None

    async def exists_active_user_in_tenant(self, tenant_id: UUID, user_id: UUID) -> bool:
        stmt = select(MembershipModel.id).where(
            MembershipModel.tenant_id == tenant_id,
            MembershipModel.user_id == user_id,
            MembershipModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    async def count_active_owners(self, tenant_id: UUID) -> int:
        stmt = select(func.count(MembershipModel.id)).where(
            MembershipModel.tenant_id == tenant_id,
            MembershipModel.role == UserRole.OWNER.value,
            MembershipModel.status == MembershipStatus.ACTIVE.value,
            MembershipModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        filter_store_ids: frozenset[UUID] | None = None,
        only_user_id: UUID | None = None,
    ) -> list[Membership]:
        stmt = (
            select(MembershipModel)
            .options(selectinload(MembershipModel.store_links))
            .where(
                MembershipModel.tenant_id == tenant_id,
                MembershipModel.deleted_at.is_(None),
            )
        )

        if filter_store_ids is not None:
            if filter_store_ids:
                subq = select(MembershipStoreModel.membership_id).where(
                    MembershipStoreModel.tenant_id == tenant_id,
                    MembershipStoreModel.store_id.in_(filter_store_ids),
                )
                if only_user_id is not None:
                    stmt = stmt.where(
                        or_(
                            MembershipModel.id.in_(subq),
                            MembershipModel.user_id == only_user_id,
                        )
                    )
                else:
                    stmt = stmt.where(MembershipModel.id.in_(subq))
            elif only_user_id is not None:
                stmt = stmt.where(MembershipModel.user_id == only_user_id)
            else:
                return []
        elif only_user_id is not None:
            stmt = stmt.where(MembershipModel.user_id == only_user_id)

        stmt = stmt.order_by(MembershipModel.created_at.asc())
        result = await self._session.execute(stmt)
        return [_to_membership(row) for row in result.scalars().all()]

    async def create(self, membership: Membership) -> Membership:
        model = MembershipModel(
            id=membership.id,
            tenant_id=membership.tenant_id,
            user_id=membership.user_id,
            email=membership.email,
            display_name=membership.display_name,
            role=membership.role.value,
            status=membership.status.value,
            created_at=membership.created_at,
            updated_at=membership.updated_at,
            deleted_at=membership.deleted_at,
            store_links=[
                MembershipStoreModel(
                    membership_id=membership.id,
                    tenant_id=membership.tenant_id,
                    store_id=store_id,
                    created_at=membership.created_at,
                )
                for store_id in membership.store_ids
            ],
        )
        self._session.add(model)
        await self._session.flush()
        return _to_membership(model)

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
    ) -> Membership | None:
        stmt = (
            select(MembershipModel)
            .options(selectinload(MembershipModel.store_links))
            .where(
                MembershipModel.tenant_id == tenant_id,
                MembershipModel.id == membership_id,
                MembershipModel.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None

        model.display_name = display_name
        model.role = role.value
        model.status = status.value
        model.updated_at = updated_at

        await self._session.execute(
            delete(MembershipStoreModel).where(
                MembershipStoreModel.membership_id == membership_id,
                MembershipStoreModel.tenant_id == tenant_id,
            )
        )
        await self._session.flush()

        now = datetime.now(UTC)
        new_links = [
            MembershipStoreModel(
                membership_id=membership_id,
                tenant_id=tenant_id,
                store_id=store_id,
                created_at=now,
            )
            for store_id in store_ids
        ]
        self._session.add_all(new_links)
        await self._session.flush()

        await self._session.refresh(model, attribute_names=["store_links"])
        return _to_membership(model)

    async def soft_delete(
        self,
        tenant_id: UUID,
        membership_id: UUID,
        *,
        deleted_at: datetime,
    ) -> bool:
        stmt = select(MembershipModel).where(
            MembershipModel.tenant_id == tenant_id,
            MembershipModel.id == membership_id,
            MembershipModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return False

        model.status = MembershipStatus.DISABLED.value
        model.deleted_at = deleted_at
        model.updated_at = deleted_at
        await self._session.flush()
        return True


def _to_employee(model: EmployeeModel) -> Employee:
    return Employee(
        id=model.id,
        tenant_id=model.tenant_id,
        primary_store_id=model.primary_store_id,
        user_id=model.user_id,
        employee_code=model.employee_code,
        full_name=model.full_name,
        preferred_name=model.preferred_name,
        email=model.email,
        phone=model.phone,
        birth_date=model.birth_date,
        contract_type=ContractType(model.contract_type),
        work_schedule_type=WorkScheduleType(model.work_schedule_type),
        contracted_weekly_hours=Decimal(model.contracted_weekly_hours),
        annual_vacation_days=model.annual_vacation_days,
        hire_date=model.hire_date,
        termination_date=model.termination_date,
        nie_dni_enc=model.nie_dni_enc,
        nie_dni_masked=model.nie_dni_masked,
        nie_dni_blind_index=model.nie_dni_blind_index,
        iban_enc=model.iban_enc,
        iban_masked=model.iban_masked,
        naf_enc=model.naf_enc,
        naf_masked=model.naf_masked,
        employment_status=EmploymentStatus(model.employment_status),
        retention_until=model.retention_until,
        anonymized_at=model.anonymized_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
    )


class SqlAlchemyEmployeeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        allowed_statuses: frozenset[EmploymentStatus] | None = None,
        only_user_id: UUID | None = None,
        store_id: UUID | None = None,
        status: EmploymentStatus | None = None,
    ) -> list[Employee]:
        if allowed_store_ids is not None and len(allowed_store_ids) == 0:
            return []
        if allowed_statuses is not None and len(allowed_statuses) == 0:
            return []

        stmt = select(EmployeeModel).where(EmployeeModel.tenant_id == tenant_id)
        if allowed_store_ids is not None:
            stmt = stmt.where(EmployeeModel.primary_store_id.in_(allowed_store_ids))
        if allowed_statuses is not None:
            stmt = stmt.where(
                EmployeeModel.employment_status.in_([s.value for s in allowed_statuses]),
                EmployeeModel.deleted_at.is_(None),
            )
        if only_user_id is not None:
            stmt = stmt.where(EmployeeModel.user_id == only_user_id)
        if store_id is not None:
            stmt = stmt.where(EmployeeModel.primary_store_id == store_id)
        if status is not None:
            stmt = stmt.where(EmployeeModel.employment_status == status.value)

        stmt = stmt.order_by(EmployeeModel.employee_code.asc(), EmployeeModel.full_name.asc())
        result = await self._session.execute(stmt)
        return [_to_employee(row) for row in result.scalars().all()]

    async def get_by_id(self, tenant_id: UUID, employee_id: UUID) -> Employee | None:
        stmt = select(EmployeeModel).where(
            EmployeeModel.tenant_id == tenant_id,
            EmployeeModel.id == employee_id,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_employee(model) if model is not None else None

    async def exists_active_code(
        self,
        tenant_id: UUID,
        employee_code: str,
        *,
        exclude_employee_id: UUID | None = None,
    ) -> bool:
        stmt = select(EmployeeModel.id).where(
            EmployeeModel.tenant_id == tenant_id,
            func.upper(EmployeeModel.employee_code) == employee_code.upper(),
            EmployeeModel.deleted_at.is_(None),
        )
        if exclude_employee_id is not None:
            stmt = stmt.where(EmployeeModel.id != exclude_employee_id)
        result = await self._session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    async def exists_active_nie_blind_index(
        self,
        tenant_id: UUID,
        nie_dni_blind_index: str,
        *,
        exclude_employee_id: UUID | None = None,
    ) -> bool:
        stmt = select(EmployeeModel.id).where(
            EmployeeModel.tenant_id == tenant_id,
            EmployeeModel.nie_dni_blind_index == nie_dni_blind_index,
            EmployeeModel.deleted_at.is_(None),
        )
        if exclude_employee_id is not None:
            stmt = stmt.where(EmployeeModel.id != exclude_employee_id)
        result = await self._session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    async def create(self, employee: Employee) -> Employee:
        model = EmployeeModel(
            id=employee.id,
            tenant_id=employee.tenant_id,
            primary_store_id=employee.primary_store_id,
            user_id=employee.user_id,
            employee_code=employee.employee_code,
            full_name=employee.full_name,
            preferred_name=employee.preferred_name,
            email=employee.email,
            phone=employee.phone,
            birth_date=employee.birth_date,
            contract_type=employee.contract_type.value,
            work_schedule_type=employee.work_schedule_type.value,
            contracted_weekly_hours=employee.contracted_weekly_hours,
            annual_vacation_days=employee.annual_vacation_days,
            hire_date=employee.hire_date,
            termination_date=employee.termination_date,
            nie_dni_enc=employee.nie_dni_enc,
            nie_dni_masked=employee.nie_dni_masked,
            nie_dni_blind_index=employee.nie_dni_blind_index,
            iban_enc=employee.iban_enc,
            iban_masked=employee.iban_masked,
            naf_enc=employee.naf_enc,
            naf_masked=employee.naf_masked,
            employment_status=employee.employment_status.value,
            retention_until=employee.retention_until,
            anonymized_at=employee.anonymized_at,
            created_at=employee.created_at,
            updated_at=employee.updated_at,
            deleted_at=employee.deleted_at,
        )
        self._session.add(model)
        await self._session.flush()
        return _to_employee(model)

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
    ) -> Employee | None:
        stmt = select(EmployeeModel).where(
            EmployeeModel.tenant_id == tenant_id,
            EmployeeModel.id == employee_id,
            EmployeeModel.deleted_at.is_(None),
            EmployeeModel.employment_status.in_(
                [EmploymentStatus.ACTIVE.value, EmploymentStatus.ON_LEAVE.value]
            ),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None

        model.primary_store_id = primary_store_id
        model.user_id = user_id
        model.employee_code = employee_code
        model.full_name = full_name
        model.preferred_name = preferred_name
        model.email = email
        model.phone = phone
        model.birth_date = birth_date
        model.contract_type = contract_type.value
        model.work_schedule_type = work_schedule_type.value
        model.contracted_weekly_hours = contracted_weekly_hours
        model.annual_vacation_days = annual_vacation_days
        model.hire_date = hire_date
        model.employment_status = employment_status.value
        model.nie_dni_enc = nie_dni_enc
        model.nie_dni_masked = nie_dni_masked
        model.nie_dni_blind_index = nie_dni_blind_index
        model.iban_enc = iban_enc
        model.iban_masked = iban_masked
        model.naf_enc = naf_enc
        model.naf_masked = naf_masked
        model.updated_at = updated_at
        await self._session.flush()
        return _to_employee(model)

    async def terminate_and_hold(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        termination_date: date,
        retention_until: datetime,
        updated_at: datetime,
    ) -> Employee | None:
        stmt = select(EmployeeModel).where(
            EmployeeModel.tenant_id == tenant_id,
            EmployeeModel.id == employee_id,
            EmployeeModel.deleted_at.is_(None),
            EmployeeModel.employment_status.in_(
                [EmploymentStatus.ACTIVE.value, EmploymentStatus.ON_LEAVE.value]
            ),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None

        model.termination_date = termination_date
        model.employment_status = EmploymentStatus.LEGAL_HOLD.value
        model.retention_until = retention_until
        model.updated_at = updated_at
        await self._session.flush()
        return _to_employee(model)

    async def anonymize(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        anonymized_full_name: str,
        anonymized_at: datetime,
    ) -> Employee | None:
        stmt = select(EmployeeModel).where(
            EmployeeModel.tenant_id == tenant_id,
            EmployeeModel.id == employee_id,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None

        model.user_id = None
        model.full_name = anonymized_full_name
        model.preferred_name = ""
        model.email = ""
        model.phone = ""
        model.birth_date = None
        model.nie_dni_enc = None
        model.nie_dni_masked = None
        model.nie_dni_blind_index = None
        model.iban_enc = None
        model.iban_masked = None
        model.naf_enc = None
        model.naf_masked = None
        model.employment_status = EmploymentStatus.ANONYMIZED.value
        model.anonymized_at = anonymized_at
        model.deleted_at = anonymized_at
        model.updated_at = anonymized_at
        await self._session.flush()
        return _to_employee(model)
