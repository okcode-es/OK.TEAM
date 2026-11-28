"""Async SQLAlchemy 2.0 repositories enforcing strict tenant_id isolation & soft deletes."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.announcements import (
    Announcement,
    AnnouncementAcknowledgement,
    AnnouncementCategory,
)
from app.domain.attendance import (
    AttendanceDayReview,
    AttendanceDayReviewStatus,
    AttendanceEvent,
    AttendanceEventType,
    AttendanceSource,
    CorrectedEventType,
    WorkMode,
)
from app.domain.break_policies import (
    BreakMode,
    BreakPolicy,
    BreakScopeType,
    BreakTrackingMode,
    BreakWindow,
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
from app.domain.leaves import (
    EntitlementAdjustmentType,
    LeaveEntitlement,
    LeaveEntitlementAdjustment,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
)
from app.domain.organization import (
    ConflictError,
    Membership,
    Store,
    Tenant,
    TenantMembershipSummary,
)
from app.domain.payroll import (
    MonthlyClosing,
    MonthlyClosingItem,
    MonthlyClosingStatus,
)
from app.domain.scheduling import Shift, ShiftStatus
from app.domain.security import MembershipStatus, UserRole
from app.infrastructure.models import (
    AnnouncementAcknowledgementModel,
    AnnouncementModel,
    AttendanceDayReviewModel,
    AttendanceEventModel,
    BreakPolicyModel,
    ComplianceDocumentModel,
    DocumentReceiptModel,
    EmployeeModel,
    LeaveEntitlementAdjustmentModel,
    LeaveEntitlementModel,
    LeaveRequestModel,
    MembershipModel,
    MembershipStoreModel,
    MonthlyClosingItemModel,
    MonthlyClosingModel,
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
        region_code=model.region_code,
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
        department_scope=model.department_scope,
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
            region_code=store.region_code,
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
        region_code: str,
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
        model.region_code = region_code
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

    async def lock_tenant_for_membership_changes(self, tenant_id: UUID) -> None:
        # PostgreSQL FOR NO KEY UPDATE serializes writers without blocking foreign-key checks.
        stmt = (
            select(TenantModel.id)
            .where(TenantModel.id == tenant_id)
            .with_for_update(key_share=True)
        )
        await self._session.execute(stmt)

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
                    department_scope=membership_model.department_scope,
                    display_name=membership_model.display_name,
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
            .execution_options(populate_existing=True)
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
            .execution_options(populate_existing=True)
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
            department_scope=membership.department_scope,
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
        department_scope: str | None,
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
        model.department_scope = department_scope
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
        department=model.department,
        job_title=model.job_title,
        emergency_contact_name=model.emergency_contact_name,
        emergency_contact_phone=model.emergency_contact_phone,
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
        avatar_storage_backend=model.avatar_storage_backend,
        avatar_storage_key=model.avatar_storage_key,
        avatar_mime_type=model.avatar_mime_type,
        avatar_updated_at=model.avatar_updated_at,
        default_break_minutes=model.default_break_minutes,
        manager_id=model.manager_id,
        work_permit_expiry_date=model.work_permit_expiry_date,
        contract_end_date=model.contract_end_date,
        kiosk_pin_hash=model.kiosk_pin_hash,
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
        user_ids: frozenset[UUID] | None = None,
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
        if user_ids is not None:
            stmt = stmt.where(EmployeeModel.user_id.in_(user_ids))
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
            department=employee.department,
            job_title=employee.job_title,
            emergency_contact_name=employee.emergency_contact_name,
            emergency_contact_phone=employee.emergency_contact_phone,
            email=employee.email,
            phone=employee.phone,
            birth_date=employee.birth_date,
            contract_type=employee.contract_type.value,
            work_schedule_type=employee.work_schedule_type.value,
            contracted_weekly_hours=employee.contracted_weekly_hours,
            annual_vacation_days=employee.annual_vacation_days,
            default_break_minutes=employee.default_break_minutes,
            manager_id=employee.manager_id,
            hire_date=employee.hire_date,
            termination_date=employee.termination_date,
            work_permit_expiry_date=employee.work_permit_expiry_date,
            contract_end_date=employee.contract_end_date,
            kiosk_pin_hash=employee.kiosk_pin_hash,
            nie_dni_enc=employee.nie_dni_enc,
            nie_dni_masked=employee.nie_dni_masked,
            nie_dni_blind_index=employee.nie_dni_blind_index,
            iban_enc=employee.iban_enc,
            iban_masked=employee.iban_masked,
            naf_enc=employee.naf_enc,
            naf_masked=employee.naf_masked,
            employment_status=employee.employment_status.value,
            avatar_storage_backend=employee.avatar_storage_backend,
            avatar_storage_key=employee.avatar_storage_key,
            avatar_mime_type=employee.avatar_mime_type,
            avatar_updated_at=employee.avatar_updated_at,
            retention_until=employee.retention_until,
            anonymized_at=employee.anonymized_at,
            created_at=employee.created_at,
            updated_at=employee.updated_at,
            deleted_at=employee.deleted_at,
        )
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Employee conflict: duplicate employee_code, NIE/DNI, or user_id"
            ) from exc
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
        department: str,
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
        job_title: str = "",
        emergency_contact_name: str = "",
        emergency_contact_phone: str = "",
        default_break_minutes: int | None = None,
        manager_id: UUID | None = None,
        work_permit_expiry_date: date | None = None,
        contract_end_date: date | None = None,
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

        model.work_permit_expiry_date = work_permit_expiry_date
        model.contract_end_date = contract_end_date
        model.primary_store_id = primary_store_id
        model.user_id = user_id
        model.employee_code = employee_code
        model.full_name = full_name
        model.preferred_name = preferred_name
        model.department = department
        model.job_title = job_title
        model.emergency_contact_name = emergency_contact_name
        model.emergency_contact_phone = emergency_contact_phone
        model.email = email
        model.phone = phone
        model.birth_date = birth_date
        model.contract_type = contract_type.value
        model.work_schedule_type = work_schedule_type.value
        model.contracted_weekly_hours = contracted_weekly_hours
        model.annual_vacation_days = annual_vacation_days
        model.default_break_minutes = default_break_minutes
        model.manager_id = manager_id
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
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Employee conflict: duplicate employee_code, NIE/DNI, or user_id"
            ) from exc
        return _to_employee(model)

    async def set_kiosk_pin_hash(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        kiosk_pin_hash: str | None,
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
        model.kiosk_pin_hash = kiosk_pin_hash
        model.updated_at = updated_at
        await self._session.flush()
        return _to_employee(model)

    async def update_avatar(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        avatar_storage_backend: str | None,
        avatar_storage_key: str | None,
        avatar_mime_type: str | None,
        avatar_updated_at: datetime | None,
        updated_at: datetime,
    ) -> Employee | None:
        stmt = select(EmployeeModel).where(
            EmployeeModel.tenant_id == tenant_id,
            EmployeeModel.id == employee_id,
            EmployeeModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        model.avatar_storage_backend = avatar_storage_backend
        model.avatar_storage_key = avatar_storage_key
        model.avatar_mime_type = avatar_mime_type
        model.avatar_updated_at = avatar_updated_at
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
        model.manager_id = None
        model.work_permit_expiry_date = None
        model.contract_end_date = None
        model.kiosk_pin_hash = None
        model.full_name = anonymized_full_name
        model.preferred_name = ""
        model.job_title = ""
        model.emergency_contact_name = ""
        model.emergency_contact_phone = ""
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
        model.avatar_storage_backend = None
        model.avatar_storage_key = None
        model.avatar_mime_type = None
        model.avatar_updated_at = None
        model.employment_status = EmploymentStatus.ANONYMIZED.value
        model.anonymized_at = anonymized_at
        model.deleted_at = anonymized_at
        model.updated_at = anonymized_at
        await self._session.execute(
            sa_update(EmployeeModel)
            .where(
                EmployeeModel.tenant_id == tenant_id,
                EmployeeModel.manager_id == employee_id,
            )
            .values(manager_id=None, updated_at=anonymized_at)
        )
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
        work_mode=WorkMode(model.work_mode),
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
            work_mode=event.work_mode.value,
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
        manager_reviewed_by_user_id=model.manager_reviewed_by_user_id,
        manager_reviewed_at_utc=model.manager_reviewed_at_utc,
        manager_review_note=model.manager_review_note,
        hr_reviewed_by_user_id=model.hr_reviewed_by_user_id,
        hr_reviewed_at_utc=model.hr_reviewed_at_utc,
        hr_review_note=model.hr_review_note,
        assigned_directly=model.assigned_directly,
        assigned_by_user_id=model.assigned_by_user_id,
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
            manager_reviewed_by_user_id=leave.manager_reviewed_by_user_id,
            manager_reviewed_at_utc=leave.manager_reviewed_at_utc,
            manager_review_note=leave.manager_review_note,
            hr_reviewed_by_user_id=leave.hr_reviewed_by_user_id,
            hr_reviewed_at_utc=leave.hr_reviewed_at_utc,
            hr_review_note=leave.hr_review_note,
            assigned_directly=leave.assigned_directly,
            assigned_by_user_id=leave.assigned_by_user_id,
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
        model.manager_reviewed_by_user_id = leave.manager_reviewed_by_user_id
        model.manager_reviewed_at_utc = leave.manager_reviewed_at_utc
        model.manager_review_note = leave.manager_review_note
        model.hr_reviewed_by_user_id = leave.hr_reviewed_by_user_id
        model.hr_reviewed_at_utc = leave.hr_reviewed_at_utc
        model.hr_review_note = leave.hr_review_note
        model.medical_document_id = leave.medical_document_id
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
        period_month: str | None = None,
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
        if period_month is not None:
            stmt = stmt.where(ComplianceDocumentModel.period_month == period_month)
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


def _to_monthly_closing(model: MonthlyClosingModel) -> MonthlyClosing:
    return MonthlyClosing(
        id=model.id,
        tenant_id=model.tenant_id,
        store_id=model.store_id,
        period_month=model.period_month,
        status=MonthlyClosingStatus(model.status),
        revision_number=model.revision_number,
        closed_by_user_id=model.closed_by_user_id,
        closed_at_utc=model.closed_at_utc,
        reopened_by_user_id=model.reopened_by_user_id,
        reopened_at_utc=model.reopened_at_utc,
        reopen_reason=model.reopen_reason,
        notes=model.notes,
        retention_until=model.retention_until,
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
    )


def _to_monthly_closing_item(model: MonthlyClosingItemModel) -> MonthlyClosingItem:
    return MonthlyClosingItem(
        id=model.id,
        tenant_id=model.tenant_id,
        closing_id=model.closing_id,
        store_id=model.store_id,
        period_month=model.period_month,
        revision_number=model.revision_number,
        employee_id=model.employee_id,
        employee_code=model.employee_code,
        employee_full_name=model.employee_full_name,
        nie_dni_masked=model.nie_dni_masked,
        contract_type=ContractType(model.contract_type),
        work_schedule_type=WorkScheduleType(model.work_schedule_type),
        contracted_weekly_hours=Decimal(str(model.contracted_weekly_hours)),
        worked_days=model.worked_days,
        total_net_hours=Decimal(str(model.total_net_hours)),
        regular_hours=Decimal(str(model.regular_hours)),
        overtime_hours=Decimal(str(model.overtime_hours)),
        annual_overtime_hours=Decimal(str(model.annual_overtime_hours)),
        total_break_minutes=model.total_break_minutes,
        vacation_days=model.vacation_days,
        sick_leave_days=model.sick_leave_days,
        paid_leave_days=model.paid_leave_days,
        unpaid_leave_days=model.unpaid_leave_days,
        warning_alerts_count=model.warning_alerts_count,
        violation_alerts_count=model.violation_alerts_count,
        attendance_report_document_id=model.attendance_report_document_id,
        retention_until=model.retention_until,
        created_at=model.created_at,
    )


class SqlAlchemyPayrollClosingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_store_month_closed(
        self,
        tenant_id: UUID,
        store_id: UUID,
        period_month: str,
    ) -> bool:
        stmt = select(MonthlyClosingModel.id).where(
            MonthlyClosingModel.tenant_id == tenant_id,
            MonthlyClosingModel.store_id == store_id,
            MonthlyClosingModel.period_month == period_month,
            MonthlyClosingModel.status == MonthlyClosingStatus.CLOSED.value,
            MonthlyClosingModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def list_closed_months_for_store(
        self,
        tenant_id: UUID,
        store_id: UUID,
        period_months: Sequence[str],
    ) -> set[str]:
        if not period_months:
            return set()
        stmt = select(MonthlyClosingModel.period_month).where(
            MonthlyClosingModel.tenant_id == tenant_id,
            MonthlyClosingModel.store_id == store_id,
            MonthlyClosingModel.period_month.in_(list(period_months)),
            MonthlyClosingModel.status == MonthlyClosingStatus.CLOSED.value,
            MonthlyClosingModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def get_by_id(
        self,
        tenant_id: UUID,
        closing_id: UUID,
    ) -> MonthlyClosing | None:
        stmt = select(MonthlyClosingModel).where(
            MonthlyClosingModel.tenant_id == tenant_id,
            MonthlyClosingModel.id == closing_id,
            MonthlyClosingModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_monthly_closing(model) if model is not None else None

    async def get_by_store_month(
        self,
        tenant_id: UUID,
        store_id: UUID,
        period_month: str,
    ) -> MonthlyClosing | None:
        stmt = select(MonthlyClosingModel).where(
            MonthlyClosingModel.tenant_id == tenant_id,
            MonthlyClosingModel.store_id == store_id,
            MonthlyClosingModel.period_month == period_month,
            MonthlyClosingModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_monthly_closing(model) if model is not None else None

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        store_id: UUID | None = None,
        period_month: str | None = None,
    ) -> list[MonthlyClosing]:
        if allowed_store_ids is not None and len(allowed_store_ids) == 0:
            return []
        stmt = select(MonthlyClosingModel).where(
            MonthlyClosingModel.tenant_id == tenant_id,
            MonthlyClosingModel.deleted_at.is_(None),
        )
        if allowed_store_ids is not None:
            stmt = stmt.where(MonthlyClosingModel.store_id.in_(allowed_store_ids))
        if store_id is not None:
            stmt = stmt.where(MonthlyClosingModel.store_id == store_id)
        if period_month is not None:
            stmt = stmt.where(MonthlyClosingModel.period_month == period_month)
        stmt = stmt.order_by(
            MonthlyClosingModel.period_month.desc(),
            MonthlyClosingModel.closed_at_utc.desc(),
        )
        result = await self._session.execute(stmt)
        return [_to_monthly_closing(row) for row in result.scalars().all()]

    async def create_closing(self, closing: MonthlyClosing) -> MonthlyClosing:
        model = MonthlyClosingModel(
            id=closing.id,
            tenant_id=closing.tenant_id,
            store_id=closing.store_id,
            period_month=closing.period_month,
            status=closing.status.value,
            revision_number=closing.revision_number,
            closed_by_user_id=closing.closed_by_user_id,
            closed_at_utc=closing.closed_at_utc,
            reopened_by_user_id=closing.reopened_by_user_id,
            reopened_at_utc=closing.reopened_at_utc,
            reopen_reason=closing.reopen_reason,
            notes=closing.notes,
            retention_until=closing.retention_until,
            created_at=closing.created_at,
            updated_at=closing.updated_at,
            deleted_at=closing.deleted_at,
        )
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                f"Monthly closing already exists for store {closing.store_id} "
                f"in {closing.period_month}"
            ) from exc
        return _to_monthly_closing(model)

    async def update_closing(
        self,
        closing: MonthlyClosing,
        *,
        expected_status: MonthlyClosingStatus | None = None,
        expected_revision: int | None = None,
    ) -> MonthlyClosing | None:
        stmt = select(MonthlyClosingModel).where(
            MonthlyClosingModel.tenant_id == closing.tenant_id,
            MonthlyClosingModel.id == closing.id,
            MonthlyClosingModel.deleted_at.is_(None),
        )
        if expected_status is not None:
            stmt = stmt.where(MonthlyClosingModel.status == expected_status.value)
        if expected_revision is not None:
            stmt = stmt.where(MonthlyClosingModel.revision_number == expected_revision)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            if expected_status is not None or expected_revision is not None:
                raise ConflictError(
                    "Monthly closing state or revision changed concurrently; please refresh"
                )
            return None
        model.status = closing.status.value
        model.revision_number = closing.revision_number
        model.closed_by_user_id = closing.closed_by_user_id
        model.closed_at_utc = closing.closed_at_utc
        model.reopened_by_user_id = closing.reopened_by_user_id
        model.reopened_at_utc = closing.reopened_at_utc
        model.reopen_reason = closing.reopen_reason
        model.notes = closing.notes
        model.retention_until = closing.retention_until
        model.updated_at = closing.updated_at
        await self._session.flush()
        return _to_monthly_closing(model)

    async def append_items(
        self,
        items: Sequence[MonthlyClosingItem],
    ) -> list[MonthlyClosingItem]:
        models: list[MonthlyClosingItemModel] = []
        for item in items:
            model = MonthlyClosingItemModel(
                id=item.id,
                tenant_id=item.tenant_id,
                closing_id=item.closing_id,
                store_id=item.store_id,
                period_month=item.period_month,
                revision_number=item.revision_number,
                employee_id=item.employee_id,
                employee_code=item.employee_code,
                employee_full_name=item.employee_full_name,
                nie_dni_masked=item.nie_dni_masked,
                contract_type=item.contract_type.value,
                work_schedule_type=item.work_schedule_type.value,
                contracted_weekly_hours=item.contracted_weekly_hours,
                worked_days=item.worked_days,
                total_net_hours=item.total_net_hours,
                regular_hours=item.regular_hours,
                overtime_hours=item.overtime_hours,
                annual_overtime_hours=item.annual_overtime_hours,
                total_break_minutes=item.total_break_minutes,
                vacation_days=item.vacation_days,
                sick_leave_days=item.sick_leave_days,
                paid_leave_days=item.paid_leave_days,
                unpaid_leave_days=item.unpaid_leave_days,
                warning_alerts_count=item.warning_alerts_count,
                violation_alerts_count=item.violation_alerts_count,
                attendance_report_document_id=item.attendance_report_document_id,
                retention_until=item.retention_until,
                created_at=item.created_at,
            )
            self._session.add(model)
            models.append(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Duplicate monthly closing item for this closing revision and employee"
            ) from exc
        return [_to_monthly_closing_item(m) for m in models]

    async def list_items(
        self,
        tenant_id: UUID,
        closing_id: UUID,
        *,
        revision_number: int | None = None,
    ) -> list[MonthlyClosingItem]:
        stmt = select(MonthlyClosingItemModel).where(
            MonthlyClosingItemModel.tenant_id == tenant_id,
            MonthlyClosingItemModel.closing_id == closing_id,
        )
        if revision_number is not None:
            stmt = stmt.where(MonthlyClosingItemModel.revision_number == revision_number)
        stmt = stmt.order_by(
            MonthlyClosingItemModel.revision_number.desc(),
            MonthlyClosingItemModel.employee_code.asc(),
        )
        result = await self._session.execute(stmt)
        return [_to_monthly_closing_item(row) for row in result.scalars().all()]


    async def list_items_for_closings(
        self,
        tenant_id: UUID,
        closing_ids: Sequence[UUID],
    ) -> list[MonthlyClosingItem]:
        if not closing_ids:
            return []
        stmt = (
            select(MonthlyClosingItemModel)
            .where(
                MonthlyClosingItemModel.tenant_id == tenant_id,
                MonthlyClosingItemModel.closing_id.in_(list(closing_ids)),
            )
            .order_by(
                MonthlyClosingItemModel.revision_number.desc(),
                MonthlyClosingItemModel.employee_code.asc(),
            )
        )
        result = await self._session.execute(stmt)
        return [_to_monthly_closing_item(row) for row in result.scalars().all()]


def _to_announcement(model: AnnouncementModel) -> Announcement:
    return Announcement(
        id=model.id,
        tenant_id=model.tenant_id,
        store_id=model.store_id,
        department_scope=model.department_scope,
        category=AnnouncementCategory(model.category),
        title=model.title,
        body=model.body,
        attachment_document_id=model.attachment_document_id,
        content_sha256_hex=model.content_sha256_hex,
        requires_acknowledgement=model.requires_acknowledgement,
        is_pinned=model.is_pinned,
        published_by_user_id=model.published_by_user_id,
        published_at_utc=model.published_at_utc,
        retention_until=model.retention_until,
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
    )


def _to_announcement_ack(model: AnnouncementAcknowledgementModel) -> AnnouncementAcknowledgement:
    return AnnouncementAcknowledgement(
        id=model.id,
        tenant_id=model.tenant_id,
        announcement_id=model.announcement_id,
        employee_id=model.employee_id,
        acknowledged_by_user_id=model.acknowledged_by_user_id,
        content_sha256_hex=model.content_sha256_hex,
        acknowledged_at_utc=model.acknowledged_at_utc,
        ip_address=model.ip_address,
        user_agent=model.user_agent,
        statement=model.statement,
        retention_until=model.retention_until,
    )


class SqlAlchemyAnnouncementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        tenant_id: UUID,
        announcement_id: UUID,
    ) -> Announcement | None:
        stmt = select(AnnouncementModel).where(
            AnnouncementModel.tenant_id == tenant_id,
            AnnouncementModel.id == announcement_id,
            AnnouncementModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_announcement(model) if model is not None else None

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        category: AnnouncementCategory | None = None,
    ) -> list[Announcement]:
        stmt = select(AnnouncementModel).where(
            AnnouncementModel.tenant_id == tenant_id,
            AnnouncementModel.deleted_at.is_(None),
        )
        if category is not None:
            stmt = stmt.where(AnnouncementModel.category == category.value)
        stmt = stmt.order_by(
            AnnouncementModel.is_pinned.desc(),
            AnnouncementModel.published_at_utc.desc(),
            AnnouncementModel.id.asc(),
        )
        result = await self._session.execute(stmt)
        return [_to_announcement(row) for row in result.scalars().all()]

    async def create(self, announcement: Announcement) -> Announcement:
        model = AnnouncementModel(
            id=announcement.id,
            tenant_id=announcement.tenant_id,
            store_id=announcement.store_id,
            department_scope=announcement.department_scope,
            category=announcement.category.value,
            title=announcement.title,
            body=announcement.body,
            attachment_document_id=announcement.attachment_document_id,
            content_sha256_hex=announcement.content_sha256_hex,
            requires_acknowledgement=announcement.requires_acknowledgement,
            is_pinned=announcement.is_pinned,
            published_by_user_id=announcement.published_by_user_id,
            published_at_utc=announcement.published_at_utc,
            retention_until=announcement.retention_until,
            created_at=announcement.created_at,
            updated_at=announcement.updated_at,
            deleted_at=announcement.deleted_at,
        )
        self._session.add(model)
        await self._session.flush()
        return _to_announcement(model)

    async def list_acknowledgements_for_announcement(
        self,
        tenant_id: UUID,
        announcement_id: UUID,
    ) -> list[AnnouncementAcknowledgement]:
        stmt = (
            select(AnnouncementAcknowledgementModel)
            .where(
                AnnouncementAcknowledgementModel.tenant_id == tenant_id,
                AnnouncementAcknowledgementModel.announcement_id == announcement_id,
            )
            .order_by(AnnouncementAcknowledgementModel.acknowledged_at_utc.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_announcement_ack(row) for row in result.scalars().all()]

    async def list_acknowledgements_for_announcements(
        self,
        tenant_id: UUID,
        announcement_ids: Sequence[UUID],
    ) -> list[AnnouncementAcknowledgement]:
        if not announcement_ids:
            return []
        stmt = (
            select(AnnouncementAcknowledgementModel)
            .where(
                AnnouncementAcknowledgementModel.tenant_id == tenant_id,
                AnnouncementAcknowledgementModel.announcement_id.in_(list(announcement_ids)),
            )
            .order_by(AnnouncementAcknowledgementModel.acknowledged_at_utc.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_announcement_ack(row) for row in result.scalars().all()]

    async def get_acknowledgement(
        self,
        tenant_id: UUID,
        announcement_id: UUID,
        employee_id: UUID,
    ) -> AnnouncementAcknowledgement | None:
        stmt = select(AnnouncementAcknowledgementModel).where(
            AnnouncementAcknowledgementModel.tenant_id == tenant_id,
            AnnouncementAcknowledgementModel.announcement_id == announcement_id,
            AnnouncementAcknowledgementModel.employee_id == employee_id,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _to_announcement_ack(model) if model is not None else None

    async def append_acknowledgement(
        self,
        ack: AnnouncementAcknowledgement,
    ) -> AnnouncementAcknowledgement:
        model = AnnouncementAcknowledgementModel(
            id=ack.id,
            tenant_id=ack.tenant_id,
            announcement_id=ack.announcement_id,
            employee_id=ack.employee_id,
            acknowledged_by_user_id=ack.acknowledged_by_user_id,
            content_sha256_hex=ack.content_sha256_hex,
            acknowledged_at_utc=ack.acknowledged_at_utc,
            ip_address=ack.ip_address,
            user_agent=ack.user_agent,
            statement=ack.statement,
            retention_until=ack.retention_until,
        )
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("Announcement already acknowledged by this employee") from exc
        return _to_announcement_ack(model)


def _to_leave_entitlement(model: LeaveEntitlementModel) -> LeaveEntitlement:
    return LeaveEntitlement(
        id=model.id,
        tenant_id=model.tenant_id,
        employee_id=model.employee_id,
        year=model.year,
        base_vacation_days=model.base_vacation_days,
        carryover_days=model.carryover_days,
        comp_off_days=model.comp_off_days,
        updated_by_user_id=model.updated_by_user_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _to_leave_entitlement_adjustment(
    model: LeaveEntitlementAdjustmentModel,
) -> LeaveEntitlementAdjustment:
    return LeaveEntitlementAdjustment(
        id=model.id,
        tenant_id=model.tenant_id,
        employee_id=model.employee_id,
        year=model.year,
        adjustment_type=EntitlementAdjustmentType(model.adjustment_type),
        delta_days=model.delta_days,
        previous_total_days=model.previous_total_days,
        new_total_days=model.new_total_days,
        reason=model.reason,
        adjusted_by_user_id=model.adjusted_by_user_id,
        created_at=model.created_at,
    )


class SqlAlchemyLeaveEntitlementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_employee_year(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        year: int,
    ) -> LeaveEntitlement | None:
        stmt = select(LeaveEntitlementModel).where(
            LeaveEntitlementModel.tenant_id == tenant_id,
            LeaveEntitlementModel.employee_id == employee_id,
            LeaveEntitlementModel.year == year,
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_leave_entitlement(model) if model is not None else None

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        year: int | None = None,
        employee_id: UUID | None = None,
    ) -> list[LeaveEntitlement]:
        stmt = select(LeaveEntitlementModel).where(LeaveEntitlementModel.tenant_id == tenant_id)
        if year is not None:
            stmt = stmt.where(LeaveEntitlementModel.year == year)
        if employee_id is not None:
            stmt = stmt.where(LeaveEntitlementModel.employee_id == employee_id)
        stmt = stmt.order_by(LeaveEntitlementModel.year.desc(), LeaveEntitlementModel.id.asc())
        result = await self._session.execute(stmt)
        return [_to_leave_entitlement(row) for row in result.scalars().all()]

    async def upsert(self, entitlement: LeaveEntitlement) -> LeaveEntitlement:
        stmt = select(LeaveEntitlementModel).where(
            LeaveEntitlementModel.tenant_id == entitlement.tenant_id,
            LeaveEntitlementModel.employee_id == entitlement.employee_id,
            LeaveEntitlementModel.year == entitlement.year,
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        if model is None:
            model = LeaveEntitlementModel(
                id=entitlement.id,
                tenant_id=entitlement.tenant_id,
                employee_id=entitlement.employee_id,
                year=entitlement.year,
                base_vacation_days=entitlement.base_vacation_days,
                carryover_days=entitlement.carryover_days,
                comp_off_days=entitlement.comp_off_days,
                updated_by_user_id=entitlement.updated_by_user_id,
                created_at=entitlement.created_at,
                updated_at=entitlement.updated_at,
            )
            self._session.add(model)
        else:
            model.base_vacation_days = entitlement.base_vacation_days
            model.carryover_days = entitlement.carryover_days
            model.comp_off_days = entitlement.comp_off_days
            model.updated_by_user_id = entitlement.updated_by_user_id
            model.updated_at = entitlement.updated_at
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError("Concurrent leave entitlement update conflict") from exc
        return _to_leave_entitlement(model)

    async def list_adjustments(
        self,
        tenant_id: UUID,
        *,
        year: int | None = None,
        employee_id: UUID | None = None,
    ) -> list[LeaveEntitlementAdjustment]:
        stmt = select(LeaveEntitlementAdjustmentModel).where(
            LeaveEntitlementAdjustmentModel.tenant_id == tenant_id
        )
        if year is not None:
            stmt = stmt.where(LeaveEntitlementAdjustmentModel.year == year)
        if employee_id is not None:
            stmt = stmt.where(LeaveEntitlementAdjustmentModel.employee_id == employee_id)
        stmt = stmt.order_by(
            LeaveEntitlementAdjustmentModel.created_at.desc(),
            LeaveEntitlementAdjustmentModel.id.asc(),
        )
        result = await self._session.execute(stmt)
        return [_to_leave_entitlement_adjustment(row) for row in result.scalars().all()]

    async def append_adjustment(
        self,
        adjustment: LeaveEntitlementAdjustment,
    ) -> LeaveEntitlementAdjustment:
        model = LeaveEntitlementAdjustmentModel(
            id=adjustment.id,
            tenant_id=adjustment.tenant_id,
            employee_id=adjustment.employee_id,
            year=adjustment.year,
            adjustment_type=adjustment.adjustment_type.value,
            delta_days=adjustment.delta_days,
            previous_total_days=adjustment.previous_total_days,
            new_total_days=adjustment.new_total_days,
            reason=adjustment.reason,
            adjusted_by_user_id=adjustment.adjusted_by_user_id,
            created_at=adjustment.created_at,
        )
        self._session.add(model)
        await self._session.flush()
        return _to_leave_entitlement_adjustment(model)


def _to_break_policy(model: BreakPolicyModel) -> BreakPolicy:
    return BreakPolicy(
        id=model.id,
        tenant_id=model.tenant_id,
        scope_type=BreakScopeType(model.scope_type),
        store_id=model.store_id,
        employee_id=model.employee_id,
        break_mode=BreakMode(model.break_mode),
        duration_minutes=model.duration_minutes,
        windows=tuple(
            BreakWindow(start=w["start"], end=w["end"]) for w in (model.windows_json or [])
        ),
        is_paid=model.is_paid,
        tracking_mode=BreakTrackingMode(model.tracking_mode),
        min_shift_minutes_for_auto_deduct=model.min_shift_minutes_for_auto_deduct,
        auto_end_manual_break=model.auto_end_manual_break,
        is_active=model.is_active,
        updated_by_user_id=model.updated_by_user_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
    )


class SqlAlchemyBreakPolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, tenant_id: UUID, policy_id: UUID) -> BreakPolicy | None:
        stmt = select(BreakPolicyModel).where(
            BreakPolicyModel.tenant_id == tenant_id,
            BreakPolicyModel.id == policy_id,
            BreakPolicyModel.deleted_at.is_(None),
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_break_policy(model) if model is not None else None

    async def get_by_scope(
        self,
        tenant_id: UUID,
        *,
        scope_type: BreakScopeType,
        store_id: UUID | None = None,
        employee_id: UUID | None = None,
    ) -> BreakPolicy | None:
        stmt = select(BreakPolicyModel).where(
            BreakPolicyModel.tenant_id == tenant_id,
            BreakPolicyModel.scope_type == scope_type.value,
            BreakPolicyModel.deleted_at.is_(None),
        )
        if scope_type == BreakScopeType.STORE:
            stmt = stmt.where(BreakPolicyModel.store_id == store_id)
        elif scope_type == BreakScopeType.EMPLOYEE:
            stmt = stmt.where(BreakPolicyModel.employee_id == employee_id)
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_break_policy(model) if model is not None else None

    async def list_by_tenant(self, tenant_id: UUID) -> list[BreakPolicy]:
        stmt = (
            select(BreakPolicyModel)
            .where(
                BreakPolicyModel.tenant_id == tenant_id,
                BreakPolicyModel.deleted_at.is_(None),
            )
            .order_by(BreakPolicyModel.scope_type.asc(), BreakPolicyModel.id.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_break_policy(row) for row in result.scalars().all()]

    async def upsert(self, policy: BreakPolicy) -> BreakPolicy:
        stmt = select(BreakPolicyModel).where(
            BreakPolicyModel.tenant_id == policy.tenant_id,
            BreakPolicyModel.scope_type == policy.scope_type.value,
            BreakPolicyModel.deleted_at.is_(None),
        )
        if policy.scope_type == BreakScopeType.STORE:
            stmt = stmt.where(BreakPolicyModel.store_id == policy.store_id)
        elif policy.scope_type == BreakScopeType.EMPLOYEE:
            stmt = stmt.where(BreakPolicyModel.employee_id == policy.employee_id)
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        windows_payload = [{"start": w.start, "end": w.end} for w in policy.windows]
        if model is None:
            model = BreakPolicyModel(
                id=policy.id,
                tenant_id=policy.tenant_id,
                scope_type=policy.scope_type.value,
                store_id=policy.store_id,
                employee_id=policy.employee_id,
                break_mode=policy.break_mode.value,
                duration_minutes=policy.duration_minutes,
                windows_json=windows_payload,
                is_paid=policy.is_paid,
                tracking_mode=policy.tracking_mode.value,
                min_shift_minutes_for_auto_deduct=policy.min_shift_minutes_for_auto_deduct,
                auto_end_manual_break=policy.auto_end_manual_break,
                is_active=policy.is_active,
                updated_by_user_id=policy.updated_by_user_id,
                created_at=policy.created_at,
                updated_at=policy.updated_at,
                deleted_at=None,
            )
            self._session.add(model)
        else:
            model.break_mode = policy.break_mode.value
            model.duration_minutes = policy.duration_minutes
            model.windows_json = windows_payload
            model.is_paid = policy.is_paid
            model.tracking_mode = policy.tracking_mode.value
            model.min_shift_minutes_for_auto_deduct = policy.min_shift_minutes_for_auto_deduct
            model.auto_end_manual_break = policy.auto_end_manual_break
            model.is_active = policy.is_active
            model.updated_by_user_id = policy.updated_by_user_id
            model.updated_at = policy.updated_at
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError("Active break policy already exists for this scope") from exc
        return _to_break_policy(model)

    async def soft_delete(
        self,
        tenant_id: UUID,
        policy_id: UUID,
        *,
        deleted_at: datetime,
        updated_by_user_id: UUID,
    ) -> BreakPolicy | None:
        stmt = select(BreakPolicyModel).where(
            BreakPolicyModel.tenant_id == tenant_id,
            BreakPolicyModel.id == policy_id,
            BreakPolicyModel.deleted_at.is_(None),
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        if model is None:
            return None
        model.deleted_at = deleted_at
        model.updated_at = deleted_at
        model.updated_by_user_id = updated_by_user_id
        await self._session.flush()
        return _to_break_policy(model)


def _to_attendance_day_review(model: AttendanceDayReviewModel) -> AttendanceDayReview:
    return AttendanceDayReview(
        id=model.id,
        tenant_id=model.tenant_id,
        employee_id=model.employee_id,
        store_id=model.store_id,
        local_date=model.local_date,
        status=AttendanceDayReviewStatus(model.status),
        reviewed_by_user_id=model.reviewed_by_user_id,
        reviewed_at_utc=model.reviewed_at_utc,
        note=model.note,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyAttendanceDayReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_employee(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[AttendanceDayReview]:
        stmt = select(AttendanceDayReviewModel).where(
            AttendanceDayReviewModel.tenant_id == tenant_id,
            AttendanceDayReviewModel.employee_id == employee_id,
        )
        if date_from is not None:
            stmt = stmt.where(AttendanceDayReviewModel.local_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(AttendanceDayReviewModel.local_date <= date_to)
        stmt = stmt.order_by(AttendanceDayReviewModel.local_date.asc())
        result = await self._session.execute(stmt)
        return [_to_attendance_day_review(row) for row in result.scalars().all()]

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        employee_ids: Sequence[UUID] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[AttendanceDayReview]:
        if employee_ids is not None and len(employee_ids) == 0:
            return []
        stmt = select(AttendanceDayReviewModel).where(
            AttendanceDayReviewModel.tenant_id == tenant_id
        )
        if employee_ids is not None:
            stmt = stmt.where(AttendanceDayReviewModel.employee_id.in_(list(employee_ids)))
        if date_from is not None:
            stmt = stmt.where(AttendanceDayReviewModel.local_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(AttendanceDayReviewModel.local_date <= date_to)
        stmt = stmt.order_by(
            AttendanceDayReviewModel.local_date.asc(),
            AttendanceDayReviewModel.employee_id.asc(),
        )
        result = await self._session.execute(stmt)
        return [_to_attendance_day_review(row) for row in result.scalars().all()]

    async def upsert_many(
        self,
        reviews: Sequence[AttendanceDayReview],
    ) -> list[AttendanceDayReview]:
        if not reviews:
            return []
        models: list[AttendanceDayReviewModel] = []
        for review in reviews:
            stmt = select(AttendanceDayReviewModel).where(
                AttendanceDayReviewModel.tenant_id == review.tenant_id,
                AttendanceDayReviewModel.employee_id == review.employee_id,
                AttendanceDayReviewModel.local_date == review.local_date,
            )
            model = (await self._session.execute(stmt)).scalar_one_or_none()
            if model is None:
                model = AttendanceDayReviewModel(
                    id=review.id,
                    tenant_id=review.tenant_id,
                    employee_id=review.employee_id,
                    store_id=review.store_id,
                    local_date=review.local_date,
                    status=review.status.value,
                    reviewed_by_user_id=review.reviewed_by_user_id,
                    reviewed_at_utc=review.reviewed_at_utc,
                    note=review.note,
                    created_at=review.created_at,
                    updated_at=review.updated_at,
                )
                self._session.add(model)
            else:
                model.store_id = review.store_id
                model.status = review.status.value
                model.reviewed_by_user_id = review.reviewed_by_user_id
                model.reviewed_at_utc = review.reviewed_at_utc
                model.note = review.note
                model.updated_at = review.updated_at
            models.append(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError("Concurrent attendance day review update conflict") from exc
        return [_to_attendance_day_review(m) for m in models]
