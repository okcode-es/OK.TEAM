"""Async SQLAlchemy 2.0 repositories enforcing strict tenant_id isolation & soft deletes."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.attendance import (
    AttendanceEvent,
    AttendanceEventType,
    AttendanceSource,
    CorrectedEventType,
)
from app.domain.documents import (
    ComplianceDocument,
    DocumentCategory,
    DocumentReceipt,
    StorageBackend,
)
from app.domain.employees import (
    ContractType,
    Employee,
    EmploymentStatus,
    WorkScheduleType,
)
from app.domain.leaves import LeaveRequest, LeaveStatus, LeaveType
from app.domain.organization import (
    ConflictError,
    Membership,
    Store,
    Tenant,
    TenantMembershipSummary,
)
from app.domain.scheduling import Shift, ShiftStatus
from app.domain.security import MembershipStatus, UserRole
from app.infrastructure.models import (
    AttendanceEventModel,
    ComplianceDocumentModel,
    DocumentReceiptModel,
    EmployeeModel,
    LeaveRequestModel,
    MembershipModel,
    MembershipStoreModel,
    ShiftModel,
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


def _to_attendance_event(model: AttendanceEventModel) -> AttendanceEvent:
    return AttendanceEvent(
        id=model.id,
        tenant_id=model.tenant_id,
        store_id=model.store_id,
        employee_id=model.employee_id,
        event_type=AttendanceEventType(model.event_type),
        occurred_at_utc=model.occurred_at_utc,
        store_timezone=model.store_timezone,
        source=AttendanceSource(model.source),
        recorded_by_user_id=model.recorded_by_user_id,
        recorded_at_utc=model.recorded_at_utc,
        retention_until=model.retention_until,
        supersedes_event_id=model.supersedes_event_id,
        corrected_event_type=(
            CorrectedEventType(model.corrected_event_type)
            if model.corrected_event_type is not None
            else None
        ),
        correction_reason=model.correction_reason,
    )


class SqlAlchemyAttendanceRepository:
    """Strictly append-only & read-only repository for attendance_events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: AttendanceEvent) -> AttendanceEvent:
        model = AttendanceEventModel(
            id=event.id,
            tenant_id=event.tenant_id,
            store_id=event.store_id,
            employee_id=event.employee_id,
            event_type=event.event_type.value,
            occurred_at_utc=event.occurred_at_utc,
            store_timezone=event.store_timezone,
            source=event.source.value,
            recorded_by_user_id=event.recorded_by_user_id,
            recorded_at_utc=event.recorded_at_utc,
            supersedes_event_id=event.supersedes_event_id,
            corrected_event_type=(
                event.corrected_event_type.value
                if event.corrected_event_type is not None
                else None
            ),
            correction_reason=event.correction_reason,
            retention_until=event.retention_until,
        )
        self._session.add(model)
        await self._session.flush()
        return _to_attendance_event(model)

    async def get_by_id(self, tenant_id: UUID, event_id: UUID) -> AttendanceEvent | None:
        stmt = select(AttendanceEventModel).where(
            AttendanceEventModel.tenant_id == tenant_id,
            AttendanceEventModel.id == event_id,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_attendance_event(model) if model is not None else None

    async def list_by_employee(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        occurred_from_utc: datetime | None = None,
        occurred_to_utc: datetime | None = None,
    ) -> list[AttendanceEvent]:
        stmt = select(AttendanceEventModel).where(
            AttendanceEventModel.tenant_id == tenant_id,
            AttendanceEventModel.employee_id == employee_id,
        )
        if occurred_from_utc is not None:
            stmt = stmt.where(AttendanceEventModel.occurred_at_utc >= occurred_from_utc)
        if occurred_to_utc is not None:
            stmt = stmt.where(AttendanceEventModel.occurred_at_utc <= occurred_to_utc)

        stmt = stmt.order_by(
            AttendanceEventModel.occurred_at_utc.asc(),
            AttendanceEventModel.recorded_at_utc.asc(),
            AttendanceEventModel.id.asc(),
        )
        result = await self._session.execute(stmt)
        return [_to_attendance_event(row) for row in result.scalars().all()]

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        store_id: UUID | None = None,
        employee_id: UUID | None = None,
        occurred_from_utc: datetime | None = None,
        occurred_to_utc: datetime | None = None,
    ) -> list[AttendanceEvent]:
        if allowed_store_ids is not None and len(allowed_store_ids) == 0:
            return []

        stmt = select(AttendanceEventModel).where(AttendanceEventModel.tenant_id == tenant_id)
        if allowed_store_ids is not None:
            stmt = stmt.where(AttendanceEventModel.store_id.in_(allowed_store_ids))
        if store_id is not None:
            stmt = stmt.where(AttendanceEventModel.store_id == store_id)
        if employee_id is not None:
            stmt = stmt.where(AttendanceEventModel.employee_id == employee_id)
        if occurred_from_utc is not None:
            stmt = stmt.where(AttendanceEventModel.occurred_at_utc >= occurred_from_utc)
        if occurred_to_utc is not None:
            stmt = stmt.where(AttendanceEventModel.occurred_at_utc <= occurred_to_utc)

        stmt = stmt.order_by(
            AttendanceEventModel.occurred_at_utc.asc(),
            AttendanceEventModel.recorded_at_utc.asc(),
            AttendanceEventModel.id.asc(),
        )
        result = await self._session.execute(stmt)
        return [_to_attendance_event(row) for row in result.scalars().all()]


def _to_shift(model: ShiftModel) -> Shift:
    return Shift(
        id=model.id,
        tenant_id=model.tenant_id,
        store_id=model.store_id,
        employee_id=model.employee_id,
        start_at_utc=model.start_at_utc,
        end_at_utc=model.end_at_utc,
        planned_break_minutes=model.planned_break_minutes,
        status=ShiftStatus(model.status),
        notes=model.notes,
        created_by_user_id=model.created_by_user_id,
        published_by_user_id=model.published_by_user_id,
        published_at_utc=model.published_at_utc,
        retention_until=model.retention_until,
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
    )


class SqlAlchemyShiftRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, tenant_id: UUID, shift_id: UUID) -> Shift | None:
        stmt = select(ShiftModel).where(
            ShiftModel.tenant_id == tenant_id,
            ShiftModel.id == shift_id,
            ShiftModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_shift(model) if model is not None else None

    async def list_by_store_window(
        self,
        tenant_id: UUID,
        store_id: UUID,
        *,
        window_start_utc: datetime,
        window_end_utc: datetime,
    ) -> list[Shift]:
        stmt = (
            select(ShiftModel)
            .where(
                ShiftModel.tenant_id == tenant_id,
                ShiftModel.store_id == store_id,
                ShiftModel.deleted_at.is_(None),
                ShiftModel.start_at_utc < window_end_utc,
                ShiftModel.end_at_utc > window_start_utc,
            )
            .order_by(ShiftModel.start_at_utc.asc(), ShiftModel.id.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_shift(row) for row in result.scalars().all()]

    async def list_by_employee_window(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        window_start_utc: datetime,
        window_end_utc: datetime,
    ) -> list[Shift]:
        stmt = (
            select(ShiftModel)
            .where(
                ShiftModel.tenant_id == tenant_id,
                ShiftModel.employee_id == employee_id,
                ShiftModel.deleted_at.is_(None),
                ShiftModel.start_at_utc < window_end_utc,
                ShiftModel.end_at_utc > window_start_utc,
            )
            .order_by(ShiftModel.start_at_utc.asc(), ShiftModel.id.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_shift(row) for row in result.scalars().all()]

    async def create(self, shift: Shift) -> Shift:
        model = ShiftModel(
            id=shift.id,
            tenant_id=shift.tenant_id,
            store_id=shift.store_id,
            employee_id=shift.employee_id,
            start_at_utc=shift.start_at_utc,
            end_at_utc=shift.end_at_utc,
            planned_break_minutes=shift.planned_break_minutes,
            status=shift.status.value,
            notes=shift.notes,
            created_by_user_id=shift.created_by_user_id,
            published_by_user_id=shift.published_by_user_id,
            published_at_utc=shift.published_at_utc,
            retention_until=shift.retention_until,
            created_at=shift.created_at,
            updated_at=shift.updated_at,
            deleted_at=shift.deleted_at,
        )
        self._session.add(model)
        await self._session.flush()
        return _to_shift(model)

    async def update(self, shift: Shift) -> Shift | None:
        stmt = select(ShiftModel).where(
            ShiftModel.tenant_id == shift.tenant_id,
            ShiftModel.id == shift.id,
            ShiftModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None

        model.start_at_utc = shift.start_at_utc
        model.end_at_utc = shift.end_at_utc
        model.planned_break_minutes = shift.planned_break_minutes
        model.status = shift.status.value
        model.notes = shift.notes
        model.published_by_user_id = shift.published_by_user_id
        model.published_at_utc = shift.published_at_utc
        model.retention_until = shift.retention_until
        model.updated_at = shift.updated_at
        model.deleted_at = shift.deleted_at
        await self._session.flush()
        return _to_shift(model)


def _to_leave_request(model: LeaveRequestModel) -> LeaveRequest:
    return LeaveRequest(
        id=model.id,
        tenant_id=model.tenant_id,
        store_id=model.store_id,
        employee_id=model.employee_id,
        leave_type=LeaveType(model.leave_type),
        start_date=model.start_date,
        end_date=model.end_date,
        natural_days=model.natural_days,
        status=LeaveStatus(model.status),
        reason=model.reason,
        requested_by_user_id=model.requested_by_user_id,
        reviewed_by_user_id=model.reviewed_by_user_id,
        reviewed_at_utc=model.reviewed_at_utc,
        review_note=model.review_note,
        medical_document_id=model.medical_document_id,
        retention_until=model.retention_until,
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
    )


class SqlAlchemyLeaveRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, tenant_id: UUID, leave_id: UUID) -> LeaveRequest | None:
        stmt = select(LeaveRequestModel).where(
            LeaveRequestModel.tenant_id == tenant_id,
            LeaveRequestModel.id == leave_id,
            LeaveRequestModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_leave_request(model) if model is not None else None

    async def list_by_employee(
        self,
        tenant_id: UUID,
        employee_id: UUID,
    ) -> list[LeaveRequest]:
        stmt = (
            select(LeaveRequestModel)
            .where(
                LeaveRequestModel.tenant_id == tenant_id,
                LeaveRequestModel.employee_id == employee_id,
                LeaveRequestModel.deleted_at.is_(None),
            )
            .order_by(LeaveRequestModel.start_date.desc(), LeaveRequestModel.id.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_leave_request(row) for row in result.scalars().all()]

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        store_id: UUID | None = None,
        employee_id: UUID | None = None,
        status: LeaveStatus | None = None,
        year: int | None = None,
    ) -> list[LeaveRequest]:
        if allowed_store_ids is not None and len(allowed_store_ids) == 0:
            return []

        stmt = select(LeaveRequestModel).where(
            LeaveRequestModel.tenant_id == tenant_id,
            LeaveRequestModel.deleted_at.is_(None),
        )
        if allowed_store_ids is not None:
            stmt = stmt.where(LeaveRequestModel.store_id.in_(allowed_store_ids))
        if store_id is not None:
            stmt = stmt.where(LeaveRequestModel.store_id == store_id)
        if employee_id is not None:
            stmt = stmt.where(LeaveRequestModel.employee_id == employee_id)
        if status is not None:
            stmt = stmt.where(LeaveRequestModel.status == status.value)
        if year is not None:
            year_start = date(year, 1, 1)
            year_end = date(year, 12, 31)
            stmt = stmt.where(
                LeaveRequestModel.start_date <= year_end,
                LeaveRequestModel.end_date >= year_start,
            )

        stmt = stmt.order_by(LeaveRequestModel.start_date.desc(), LeaveRequestModel.id.asc())
        result = await self._session.execute(stmt)
        return [_to_leave_request(row) for row in result.scalars().all()]

    async def create(self, leave: LeaveRequest) -> LeaveRequest:
        model = LeaveRequestModel(
            id=leave.id,
            tenant_id=leave.tenant_id,
            store_id=leave.store_id,
            employee_id=leave.employee_id,
            leave_type=leave.leave_type.value,
            start_date=leave.start_date,
            end_date=leave.end_date,
            natural_days=leave.natural_days,
            status=leave.status.value,
            reason=leave.reason,
            requested_by_user_id=leave.requested_by_user_id,
            reviewed_by_user_id=leave.reviewed_by_user_id,
            reviewed_at_utc=leave.reviewed_at_utc,
            review_note=leave.review_note,
            medical_document_id=leave.medical_document_id,
            retention_until=leave.retention_until,
            created_at=leave.created_at,
            updated_at=leave.updated_at,
            deleted_at=leave.deleted_at,
        )
        self._session.add(model)
        await self._session.flush()
        return _to_leave_request(model)

    async def update(self, leave: LeaveRequest) -> LeaveRequest | None:
        stmt = select(LeaveRequestModel).where(
            LeaveRequestModel.tenant_id == leave.tenant_id,
            LeaveRequestModel.id == leave.id,
            LeaveRequestModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None

        model.status = leave.status.value
        model.reviewed_by_user_id = leave.reviewed_by_user_id
        model.reviewed_at_utc = leave.reviewed_at_utc
        model.review_note = leave.review_note
        model.updated_at = leave.updated_at
        await self._session.flush()
        return _to_leave_request(model)


def _to_document(model: ComplianceDocumentModel) -> ComplianceDocument:
    return ComplianceDocument(
        id=model.id,
        tenant_id=model.tenant_id,
        store_id=model.store_id,
        employee_id=model.employee_id,
        category=DocumentCategory(model.category),
        title=model.title,
        period_month=model.period_month,
        storage_backend=StorageBackend(model.storage_backend),
        storage_key=model.storage_key,
        original_filename=model.original_filename,
        mime_type=model.mime_type,
        size_bytes=model.size_bytes,
        sha256_hex=model.sha256_hex,
        requires_signature=model.requires_signature,
        uploaded_by_user_id=model.uploaded_by_user_id,
        retention_until=model.retention_until,
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
    )


def _to_document_receipt(model: DocumentReceiptModel) -> DocumentReceipt:
    return DocumentReceipt(
        id=model.id,
        tenant_id=model.tenant_id,
        document_id=model.document_id,
        employee_id=model.employee_id,
        signed_by_user_id=model.signed_by_user_id,
        document_sha256_hex=model.document_sha256_hex,
        signed_at_utc=model.signed_at_utc,
        ip_address=model.ip_address,
        user_agent=model.user_agent,
        receipt_statement=model.receipt_statement,
        retention_until=model.retention_until,
    )


class SqlAlchemyDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        tenant_id: UUID,
        document_id: UUID,
    ) -> ComplianceDocument | None:
        stmt = select(ComplianceDocumentModel).where(
            ComplianceDocumentModel.tenant_id == tenant_id,
            ComplianceDocumentModel.id == document_id,
            ComplianceDocumentModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_document(model) if model is not None else None

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        only_employee_id: UUID | None = None,
        include_tenant_wide_for_employee: bool = False,
        employee_store_id: UUID | None = None,
        category: DocumentCategory | None = None,
        employee_id: UUID | None = None,
        store_id: UUID | None = None,
        requires_signature: bool | None = None,
        exclude_categories: frozenset[DocumentCategory] | None = None,
    ) -> list[ComplianceDocument]:
        if allowed_store_ids is not None and len(allowed_store_ids) == 0:
            return []

        stmt = select(ComplianceDocumentModel).where(
            ComplianceDocumentModel.tenant_id == tenant_id,
            ComplianceDocumentModel.deleted_at.is_(None),
        )
        if allowed_store_ids is not None:
            stmt = stmt.where(ComplianceDocumentModel.store_id.in_(allowed_store_ids))
        if only_employee_id is not None:
            if include_tenant_wide_for_employee:
                store_cond = (
                    ComplianceDocumentModel.store_id.is_(None)
                    if employee_store_id is None
                    else or_(
                        ComplianceDocumentModel.store_id.is_(None),
                        ComplianceDocumentModel.store_id == employee_store_id,
                    )
                )
                stmt = stmt.where(
                    or_(
                        ComplianceDocumentModel.employee_id == only_employee_id,
                        and_(
                            ComplianceDocumentModel.employee_id.is_(None),
                            ComplianceDocumentModel.category == DocumentCategory.OTHER.value,
                            store_cond,
                        ),
                    )
                )
            else:
                stmt = stmt.where(ComplianceDocumentModel.employee_id == only_employee_id)
        if category is not None:
            stmt = stmt.where(ComplianceDocumentModel.category == category.value)
        if exclude_categories:
            stmt = stmt.where(
                ComplianceDocumentModel.category.notin_([c.value for c in exclude_categories])
            )
        if employee_id is not None:
            stmt = stmt.where(ComplianceDocumentModel.employee_id == employee_id)
        if store_id is not None:
            stmt = stmt.where(ComplianceDocumentModel.store_id == store_id)
        if requires_signature is not None:
            stmt = stmt.where(ComplianceDocumentModel.requires_signature == requires_signature)

        stmt = stmt.order_by(
            ComplianceDocumentModel.created_at.desc(),
            ComplianceDocumentModel.id.asc(),
        )
        result = await self._session.execute(stmt)
        return [_to_document(row) for row in result.scalars().all()]

    async def create(self, document: ComplianceDocument) -> ComplianceDocument:
        model = ComplianceDocumentModel(
            id=document.id,
            tenant_id=document.tenant_id,
            store_id=document.store_id,
            employee_id=document.employee_id,
            category=document.category.value,
            title=document.title,
            period_month=document.period_month,
            storage_backend=document.storage_backend.value,
            storage_key=document.storage_key,
            original_filename=document.original_filename,
            mime_type=document.mime_type,
            size_bytes=document.size_bytes,
            sha256_hex=document.sha256_hex,
            requires_signature=document.requires_signature,
            uploaded_by_user_id=document.uploaded_by_user_id,
            retention_until=document.retention_until,
            created_at=document.created_at,
            updated_at=document.updated_at,
            deleted_at=document.deleted_at,
        )
        self._session.add(model)
        await self._session.flush()
        return _to_document(model)

    async def soft_delete(
        self,
        tenant_id: UUID,
        document_id: UUID,
        *,
        deleted_at: datetime,
    ) -> bool:
        stmt = select(ComplianceDocumentModel).where(
            ComplianceDocumentModel.tenant_id == tenant_id,
            ComplianceDocumentModel.id == document_id,
            ComplianceDocumentModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return False
        model.deleted_at = deleted_at
        model.updated_at = deleted_at
        await self._session.flush()
        return True

    async def list_receipts_for_document(
        self,
        tenant_id: UUID,
        document_id: UUID,
    ) -> list[DocumentReceipt]:
        stmt = (
            select(DocumentReceiptModel)
            .where(
                DocumentReceiptModel.tenant_id == tenant_id,
                DocumentReceiptModel.document_id == document_id,
            )
            .order_by(DocumentReceiptModel.signed_at_utc.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_document_receipt(row) for row in result.scalars().all()]

    async def list_receipts_for_documents(
        self,
        tenant_id: UUID,
        document_ids: Sequence[UUID],
    ) -> list[DocumentReceipt]:
        if not document_ids:
            return []
        stmt = (
            select(DocumentReceiptModel)
            .where(
                DocumentReceiptModel.tenant_id == tenant_id,
                DocumentReceiptModel.document_id.in_(list(document_ids)),
            )
            .order_by(DocumentReceiptModel.signed_at_utc.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_document_receipt(row) for row in result.scalars().all()]

    async def get_receipt(
        self,
        tenant_id: UUID,
        document_id: UUID,
        employee_id: UUID,
    ) -> DocumentReceipt | None:
        stmt = select(DocumentReceiptModel).where(
            DocumentReceiptModel.tenant_id == tenant_id,
            DocumentReceiptModel.document_id == document_id,
            DocumentReceiptModel.employee_id == employee_id,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_document_receipt(model) if model is not None else None

    async def append_receipt(self, receipt: DocumentReceipt) -> DocumentReceipt:
        model = DocumentReceiptModel(
            id=receipt.id,
            tenant_id=receipt.tenant_id,
            document_id=receipt.document_id,
            employee_id=receipt.employee_id,
            signed_by_user_id=receipt.signed_by_user_id,
            document_sha256_hex=receipt.document_sha256_hex,
            signed_at_utc=receipt.signed_at_utc,
            ip_address=receipt.ip_address,
            user_agent=receipt.user_agent,
            receipt_statement=receipt.receipt_statement,
            retention_until=receipt.retention_until,
        )
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("Electronic receipt already signed for this document") from exc
        return _to_document_receipt(model)
