"""Application use cases for Phase 2 Employee HR records, PII encryption & legal hold."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.documents import DocumentStoragePort
from app.domain.employees import (
    ContractType,
    Employee,
    EmployeeSensitivePii,
    EmploymentStatus,
    FrozenRecordError,
    PiiCipher,
    RetentionLockError,
    WorkScheduleType,
    build_avatar_storage_key,
    compute_retention_until,
    hash_kiosk_pin,
    mask_iban,
    mask_naf,
    mask_nie_dni,
    normalize_employee_code,
    normalize_iban,
    normalize_naf,
    normalize_nie_dni,
    validate_annual_vacation_days,
    validate_avatar_upload,
    validate_contract_end_date,
    validate_contracted_weekly_hours,
    validate_default_break_minutes,
    validate_kiosk_pin,
    validate_no_manager_cycle,
)
from app.domain.organization import (
    ConflictError,
    EntityNotFoundError,
    ValidationDomainError,
    validate_non_empty_text,
)
from app.domain.repositories import EmployeeRepository, StoreRepository
from app.domain.security import (
    TENANT_WIDE_ROLES,
    AuthenticatedPrincipal,
    ForbiddenScopeError,
    UserRole,
)

MUTABLE_STATUSES: frozenset[EmploymentStatus] = frozenset(
    {EmploymentStatus.ACTIVE, EmploymentStatus.ON_LEAVE}
)


class _UnsetType:
    """Sentinel distinguishing omitted optional PATCH fields from explicit null."""


UNSET = _UnsetType()


@dataclass(frozen=True, slots=True)
class CreateEmployeeCommand:
    primary_store_id: UUID
    employee_code: str
    full_name: str
    preferred_name: str = ""
    department: str = ""
    job_title: str = ""
    emergency_contact_name: str = ""
    emergency_contact_phone: str = ""
    email: str = ""
    phone: str = ""
    user_id: UUID | None = None
    birth_date: date | None = None
    contract_type: ContractType = ContractType.INDEFINIDO
    work_schedule_type: WorkScheduleType = WorkScheduleType.FULL_TIME
    contracted_weekly_hours: Decimal = Decimal("40.00")
    annual_vacation_days: int = 30
    default_break_minutes: int | None = None
    manager_id: UUID | None = None
    hire_date: date = date(2026, 1, 1)
    work_permit_expiry_date: date | None = None
    contract_end_date: date | None = None
    nie_dni: str | None = field(default=None, repr=False)
    iban: str | None = field(default=None, repr=False)
    naf: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class UpdateEmployeeCommand:
    primary_store_id: UUID
    employee_code: str
    full_name: str
    preferred_name: str = ""
    department: str = ""
    job_title: str = ""
    emergency_contact_name: str = ""
    emergency_contact_phone: str = ""
    email: str = ""
    phone: str = ""
    user_id: UUID | None = None
    birth_date: date | None = None
    contract_type: ContractType = ContractType.INDEFINIDO
    work_schedule_type: WorkScheduleType = WorkScheduleType.FULL_TIME
    contracted_weekly_hours: Decimal = Decimal("40.00")
    annual_vacation_days: int = 30
    default_break_minutes: int | None = None
    manager_id: UUID | None | _UnsetType = UNSET
    hire_date: date = date(2026, 1, 1)
    employment_status: EmploymentStatus = EmploymentStatus.ACTIVE
    # UNSET => keep existing value; explicit None => clear the date.
    work_permit_expiry_date: date | None | _UnsetType = UNSET
    contract_end_date: date | None | _UnsetType = UNSET
    # None => keep existing encrypted value; "" => clear; non-empty => re-encrypt new value
    nie_dni: str | None = field(default=None, repr=False)
    iban: str | None = field(default=None, repr=False)
    naf: str | None = field(default=None, repr=False)


class ListEmployeesUseCase:
    def __init__(self, employee_repo: EmployeeRepository) -> None:
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        store_id: UUID | None = None,
        status: EmploymentStatus | None = None,
    ) -> list[Employee]:
        if principal.role in TENANT_WIDE_ROLES:
            return await self._employee_repo.list_by_tenant(
                principal.tenant_id,
                store_id=store_id,
                status=status,
            )

        if principal.role == UserRole.STORE_MANAGER:
            if store_id is not None and store_id not in principal.store_ids:
                raise ForbiddenScopeError("Store manager cannot list employees of unassigned store")
            if status is not None and status not in MUTABLE_STATUSES:
                raise ForbiddenScopeError(
                    "Frozen or anonymized employee records are blocked from operational access "
                    "(Art. 32 LOPDGDD)"
                )
            emps = await self._employee_repo.list_by_tenant(
                principal.tenant_id,
                allowed_store_ids=principal.store_ids,
                allowed_statuses=MUTABLE_STATUSES,
                store_id=store_id,
                status=status,
            )
            return [e for e in emps if principal.can_manage_employee(e)]

        return await self._employee_repo.list_by_tenant(
            principal.tenant_id,
            only_user_id=principal.user_id,
            store_id=store_id,
            status=status,
        )


class GetEmployeeUseCase:
    def __init__(self, employee_repo: EmployeeRepository) -> None:
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        employee_id: UUID,
    ) -> Employee:
        employee = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
        if employee is None:
            raise EntityNotFoundError("Employee not found")
        _require_employee_read_scope(principal, employee)
        return employee


class GetEmployeeSensitivePiiUseCase:
    def __init__(
        self,
        employee_repo: EmployeeRepository,
        pii_cipher: PiiCipher,
    ) -> None:
        self._employee_repo = employee_repo
        self._pii_cipher = pii_cipher

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        employee_id: UUID,
    ) -> EmployeeSensitivePii:
        # Fail fast for store_manager before touching DB (avoids existence oracle).
        if principal.role == UserRole.STORE_MANAGER:
            raise ForbiddenScopeError("Store managers are not permitted to decrypt employee PII")

        employee = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
        if employee is None:
            raise EntityNotFoundError("Employee not found")

        principal.require_sensitive_pii_access(target_user_id=employee.user_id)

        if employee.employment_status == EmploymentStatus.ANONYMIZED:
            return EmployeeSensitivePii(
                employee_id=employee.id,
                nie_dni=None,
                iban=None,
                naf=None,
            )

        return EmployeeSensitivePii(
            employee_id=employee.id,
            nie_dni=(
                self._pii_cipher.decrypt(
                    employee.nie_dni_enc,
                    tenant_id=principal.tenant_id,
                    employee_id=employee.id,
                    field_name="nie_dni",
                )
                if employee.nie_dni_enc
                else None
            ),
            iban=(
                self._pii_cipher.decrypt(
                    employee.iban_enc,
                    tenant_id=principal.tenant_id,
                    employee_id=employee.id,
                    field_name="iban",
                )
                if employee.iban_enc
                else None
            ),
            naf=(
                self._pii_cipher.decrypt(
                    employee.naf_enc,
                    tenant_id=principal.tenant_id,
                    employee_id=employee.id,
                    field_name="naf",
                )
                if employee.naf_enc
                else None
            ),
        )


async def _validate_manager_assignment(
    employee_repo: EmployeeRepository,
    *,
    tenant_id: UUID,
    employee_id: UUID,
    manager_id: UUID | None,
) -> None:
    if manager_id is None:
        return
    if manager_id == employee_id:
        raise ValidationDomainError("An employee cannot be their own manager")

    target = await employee_repo.get_by_id(tenant_id, manager_id)
    if (
        target is None
        or target.deleted_at is not None
        or target.employment_status not in MUTABLE_STATUSES
    ):
        raise ValidationDomainError(
            "manager_id must reference an active or on-leave employee in this company"
        )

    active_employees = await employee_repo.list_by_tenant(
        tenant_id,
        allowed_statuses=MUTABLE_STATUSES,
    )
    manager_by_employee_id = {emp.id: emp.manager_id for emp in active_employees}
    validate_no_manager_cycle(employee_id, manager_id, manager_by_employee_id)


class CreateEmployeeUseCase:
    def __init__(
        self,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        pii_cipher: PiiCipher,
    ) -> None:
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._pii_cipher = pii_cipher

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        cmd: CreateEmployeeCommand,
    ) -> Employee:
        principal.require_employee_manage()

        if await self._store_repo.get_by_id(principal.tenant_id, cmd.primary_store_id) is None:
            raise ValidationDomainError("primary_store_id does not exist in this company")

        employee_code = normalize_employee_code(cmd.employee_code)
        full_name = validate_non_empty_text(cmd.full_name, field="full_name", max_length=160)
        weekly_hours = validate_contracted_weekly_hours(
            cmd.contracted_weekly_hours,
            cmd.work_schedule_type,
        )
        vacation_days = validate_annual_vacation_days(cmd.annual_vacation_days)
        default_break_minutes = validate_default_break_minutes(cmd.default_break_minutes)

        if await self._employee_repo.exists_active_code(principal.tenant_id, employee_code):
            raise ConflictError(f"Employee code '{employee_code}' already exists in this company")

        employee_id = uuid4()
        await _validate_manager_assignment(
            self._employee_repo,
            tenant_id=principal.tenant_id,
            employee_id=employee_id,
            manager_id=cmd.manager_id,
        )
        nie_enc, nie_masked, nie_blind = _prepare_nie_dni(
            cmd.nie_dni,
            cipher=self._pii_cipher,
            tenant_id=principal.tenant_id,
            employee_id=employee_id,
        )
        if nie_blind and await self._employee_repo.exists_active_nie_blind_index(
            principal.tenant_id,
            nie_blind,
        ):
            raise ConflictError("An active employee with this NIE/DNI already exists")

        iban_enc, iban_masked = _prepare_iban(
            cmd.iban,
            cipher=self._pii_cipher,
            tenant_id=principal.tenant_id,
            employee_id=employee_id,
        )
        naf_enc, naf_masked = _prepare_naf(
            cmd.naf,
            cipher=self._pii_cipher,
            tenant_id=principal.tenant_id,
            employee_id=employee_id,
        )

        now = datetime.now(UTC)
        employee = Employee(
            id=employee_id,
            tenant_id=principal.tenant_id,
            primary_store_id=cmd.primary_store_id,
            user_id=cmd.user_id,
            employee_code=employee_code,
            full_name=full_name,
            preferred_name=cmd.preferred_name.strip()[:120],
            department=cmd.department.strip()[:100],
            job_title=cmd.job_title.strip()[:120],
            emergency_contact_name=cmd.emergency_contact_name.strip()[:120],
            emergency_contact_phone=cmd.emergency_contact_phone.strip()[:40],
            email=cmd.email.strip().lower()[:255],
            phone=cmd.phone.strip()[:40],
            birth_date=cmd.birth_date,
            contract_type=cmd.contract_type,
            work_schedule_type=cmd.work_schedule_type,
            contracted_weekly_hours=weekly_hours,
            annual_vacation_days=vacation_days,
            default_break_minutes=default_break_minutes,
            manager_id=cmd.manager_id,
            hire_date=cmd.hire_date,
            termination_date=None,
            work_permit_expiry_date=cmd.work_permit_expiry_date,
            contract_end_date=validate_contract_end_date(cmd.hire_date, cmd.contract_end_date),
            nie_dni_enc=nie_enc,
            nie_dni_masked=nie_masked,
            nie_dni_blind_index=nie_blind,
            iban_enc=iban_enc,
            iban_masked=iban_masked,
            naf_enc=naf_enc,
            naf_masked=naf_masked,
            employment_status=EmploymentStatus.ACTIVE,
            retention_until=None,
            anonymized_at=None,
            created_at=now,
            updated_at=now,
        )
        return await self._employee_repo.create(employee)


class UpdateEmployeeUseCase:
    def __init__(
        self,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        pii_cipher: PiiCipher,
    ) -> None:
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._pii_cipher = pii_cipher

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        employee_id: UUID,
        cmd: UpdateEmployeeCommand,
    ) -> Employee:
        principal.require_employee_manage()

        existing = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
        if existing is None:
            raise EntityNotFoundError("Employee not found")

        if existing.employment_status in {
            EmploymentStatus.LEGAL_HOLD,
            EmploymentStatus.ANONYMIZED,
            EmploymentStatus.TERMINATED,
        }:
            raise FrozenRecordError(
                "Employee record is frozen under statutory legal hold or anonymized"
            )

        if cmd.employment_status not in MUTABLE_STATUSES:
            raise ValidationDomainError(
                "Use /terminate or /anonymize lifecycle endpoints to freeze or anonymize records"
            )

        if await self._store_repo.get_by_id(principal.tenant_id, cmd.primary_store_id) is None:
            raise ValidationDomainError("primary_store_id does not exist in this company")

        if isinstance(cmd.manager_id, _UnsetType):
            resolved_manager_id = existing.manager_id
        else:
            await _validate_manager_assignment(
                self._employee_repo,
                tenant_id=principal.tenant_id,
                employee_id=employee_id,
                manager_id=cmd.manager_id,
            )
            resolved_manager_id = cmd.manager_id

        employee_code = normalize_employee_code(cmd.employee_code)
        full_name = validate_non_empty_text(cmd.full_name, field="full_name", max_length=160)
        weekly_hours = validate_contracted_weekly_hours(
            cmd.contracted_weekly_hours,
            cmd.work_schedule_type,
        )
        vacation_days = validate_annual_vacation_days(cmd.annual_vacation_days)
        default_break_minutes = validate_default_break_minutes(cmd.default_break_minutes)

        resolved_work_permit_expiry = (
            existing.work_permit_expiry_date
            if isinstance(cmd.work_permit_expiry_date, _UnsetType)
            else cmd.work_permit_expiry_date
        )
        resolved_contract_end = validate_contract_end_date(
            cmd.hire_date,
            existing.contract_end_date
            if isinstance(cmd.contract_end_date, _UnsetType)
            else cmd.contract_end_date,
        )

        if await self._employee_repo.exists_active_code(
            principal.tenant_id,
            employee_code,
            exclude_employee_id=employee_id,
        ):
            raise ConflictError(f"Employee code '{employee_code}' already exists in this company")

        if cmd.nie_dni is None:
            nie_enc = existing.nie_dni_enc
            nie_masked = existing.nie_dni_masked
            nie_blind = existing.nie_dni_blind_index
        else:
            nie_enc, nie_masked, nie_blind = _prepare_nie_dni(
                cmd.nie_dni,
                cipher=self._pii_cipher,
                tenant_id=principal.tenant_id,
                employee_id=employee_id,
            )
            if nie_blind and await self._employee_repo.exists_active_nie_blind_index(
                principal.tenant_id,
                nie_blind,
                exclude_employee_id=employee_id,
            ):
                raise ConflictError("An active employee with this NIE/DNI already exists")

        if cmd.iban is None:
            iban_enc, iban_masked = existing.iban_enc, existing.iban_masked
        else:
            iban_enc, iban_masked = _prepare_iban(
                cmd.iban,
                cipher=self._pii_cipher,
                tenant_id=principal.tenant_id,
                employee_id=employee_id,
            )

        if cmd.naf is None:
            naf_enc, naf_masked = existing.naf_enc, existing.naf_masked
        else:
            naf_enc, naf_masked = _prepare_naf(
                cmd.naf,
                cipher=self._pii_cipher,
                tenant_id=principal.tenant_id,
                employee_id=employee_id,
            )

        updated = await self._employee_repo.update(
            principal.tenant_id,
            employee_id,
            work_permit_expiry_date=resolved_work_permit_expiry,
            contract_end_date=resolved_contract_end,
            primary_store_id=cmd.primary_store_id,
            user_id=cmd.user_id,
            employee_code=employee_code,
            full_name=full_name,
            preferred_name=cmd.preferred_name.strip()[:120],
            department=cmd.department.strip()[:100],
            job_title=cmd.job_title.strip()[:120],
            emergency_contact_name=cmd.emergency_contact_name.strip()[:120],
            emergency_contact_phone=cmd.emergency_contact_phone.strip()[:40],
            email=cmd.email.strip().lower()[:255],
            phone=cmd.phone.strip()[:40],
            birth_date=cmd.birth_date,
            contract_type=cmd.contract_type,
            work_schedule_type=cmd.work_schedule_type,
            contracted_weekly_hours=weekly_hours,
            annual_vacation_days=vacation_days,
            default_break_minutes=default_break_minutes,
            manager_id=resolved_manager_id,
            hire_date=cmd.hire_date,
            employment_status=cmd.employment_status,
            nie_dni_enc=nie_enc,
            nie_dni_masked=nie_masked,
            nie_dni_blind_index=nie_blind,
            iban_enc=iban_enc,
            iban_masked=iban_masked,
            naf_enc=naf_enc,
            naf_masked=naf_masked,
            updated_at=datetime.now(UTC),
        )
        if updated is None:
            raise EntityNotFoundError("Employee not found")
        return updated


class SetEmployeeKioskPinUseCase:
    """Owner / HR / in-scope store_manager sets, resets or clears an employee's kiosk PIN."""

    def __init__(self, employee_repo: EmployeeRepository) -> None:
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        employee_id: UUID,
        *,
        pin: str | None,
        now_utc: datetime | None = None,
    ) -> Employee:
        if principal.role not in {UserRole.OWNER, UserRole.HR, UserRole.STORE_MANAGER}:
            raise ForbiddenScopeError(
                "Only owner, HR, or in-scope store_manager may manage kiosk PINs"
            )

        existing = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
        if existing is None:
            raise EntityNotFoundError("Employee not found")
        if principal.role == UserRole.STORE_MANAGER:
            principal.require_employee_in_scope(existing)

        if existing.deleted_at is not None or existing.employment_status not in MUTABLE_STATUSES:
            raise FrozenRecordError(
                "Cannot change kiosk PIN of a terminated, legal-hold or anonymized employee"
            )

        pin_hash = hash_kiosk_pin(validate_kiosk_pin(pin)) if pin is not None else None
        updated = await self._employee_repo.set_kiosk_pin_hash(
            principal.tenant_id,
            employee_id,
            kiosk_pin_hash=pin_hash,
            updated_at=(now_utc or datetime.now(UTC)).astimezone(UTC),
        )
        if updated is None:
            raise EntityNotFoundError("Employee not found")
        return updated


class TerminateEmployeeUseCase:
    def __init__(self, employee_repo: EmployeeRepository) -> None:
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        employee_id: UUID,
        *,
        termination_date: date,
    ) -> Employee:
        principal.require_employee_manage()

        existing = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
        if existing is None:
            raise EntityNotFoundError("Employee not found")

        if existing.employment_status in {
            EmploymentStatus.LEGAL_HOLD,
            EmploymentStatus.TERMINATED,
            EmploymentStatus.ANONYMIZED,
        }:
            raise FrozenRecordError(
                "Employee record is already terminated, under 4-year legal hold, or anonymized"
            )
        if termination_date < existing.hire_date:
            raise ValidationDomainError("termination_date cannot precede hire_date")

        now = datetime.now(UTC)
        retention_until = compute_retention_until(termination_date, now)
        updated = await self._employee_repo.terminate_and_hold(
            principal.tenant_id,
            employee_id,
            termination_date=termination_date,
            retention_until=retention_until,
            updated_at=now,
        )
        if updated is None:
            raise EntityNotFoundError("Employee not found")
        return updated


class AnonymizeEmployeeUseCase:
    def __init__(
        self,
        employee_repo: EmployeeRepository,
        storage: DocumentStoragePort | None = None,
    ) -> None:
        self._employee_repo = employee_repo
        self._storage = storage

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        employee_id: UUID,
        *,
        now_utc: datetime | None = None,
    ) -> Employee:
        principal.require_employee_manage()

        existing = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
        if existing is None:
            raise EntityNotFoundError("Employee not found")

        if existing.employment_status == EmploymentStatus.ANONYMIZED:
            return existing

        if existing.employment_status not in {
            EmploymentStatus.TERMINATED,
            EmploymentStatus.LEGAL_HOLD,
        }:
            raise RetentionLockError(
                "Active employees must be terminated into 4-year legal hold before anonymization"
            )

        now = (now_utc or datetime.now(UTC)).astimezone(UTC)
        if existing.retention_until is None or now < existing.retention_until:
            raise RetentionLockError(
                "Cannot anonymize PII before Spanish 4-year statutory retention period expires "
                f"(retention_until={existing.retention_until})"
            )

        old_avatar_key = existing.avatar_storage_key
        updated = await self._employee_repo.anonymize(
            principal.tenant_id,
            employee_id,
            anonymized_full_name=f"ANONYMIZED-{existing.employee_code}",
            anonymized_at=now,
        )
        if updated is None:
            raise EntityNotFoundError("Employee not found")
        if old_avatar_key and self._storage is not None:
            with contextlib.suppress(Exception):
                await self._storage.delete_object(storage_key=old_avatar_key)
        return updated


class UploadEmployeeAvatarUseCase:
    def __init__(
        self,
        employee_repo: EmployeeRepository,
        storage: DocumentStoragePort,
    ) -> None:
        self._employee_repo = employee_repo
        self._storage = storage

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        employee_id: UUID,
        *,
        mime_type: str,
        content: bytes,
        now_utc: datetime,
    ) -> Employee:
        if principal.role == UserRole.ACCOUNTANT:
            raise ForbiddenScopeError("Accountants cannot modify employee avatars")

        emp = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
        if emp is None or emp.deleted_at is not None:
            raise EntityNotFoundError("Employee not found")

        _require_avatar_write_scope(principal, emp)

        if emp.employment_status in (EmploymentStatus.LEGAL_HOLD, EmploymentStatus.ANONYMIZED):
            raise FrozenRecordError("Cannot update avatar for legal_hold or anonymized employee")

        validate_avatar_upload(mime_type, content)
        new_key = build_avatar_storage_key(principal.tenant_id, emp.id, uuid4())
        old_key = emp.avatar_storage_key
        now = now_utc.astimezone(UTC)

        await self._storage.put_object(
            storage_key=new_key,
            content=content,
            mime_type=mime_type,
        )
        try:
            updated = await self._employee_repo.update_avatar(
                principal.tenant_id,
                emp.id,
                avatar_storage_backend=self._storage.backend_name.value,
                avatar_storage_key=new_key,
                avatar_mime_type=mime_type,
                avatar_updated_at=now,
                updated_at=now,
            )
            if updated is None:
                raise EntityNotFoundError("Employee not found")
        except Exception:
            with contextlib.suppress(Exception):
                await self._storage.delete_object(storage_key=new_key)
            raise

        if old_key and old_key != new_key:
            with contextlib.suppress(Exception):
                await self._storage.delete_object(storage_key=old_key)
        return updated


class DeleteEmployeeAvatarUseCase:
    def __init__(
        self,
        employee_repo: EmployeeRepository,
        storage: DocumentStoragePort,
    ) -> None:
        self._employee_repo = employee_repo
        self._storage = storage

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        employee_id: UUID,
        *,
        now_utc: datetime,
    ) -> None:
        if principal.role == UserRole.ACCOUNTANT:
            raise ForbiddenScopeError("Accountants cannot modify employee avatars")

        emp = await self._employee_repo.get_by_id(principal.tenant_id, employee_id)
        if emp is None or emp.deleted_at is not None:
            raise EntityNotFoundError("Employee not found")

        _require_avatar_write_scope(principal, emp)

        if emp.employment_status in (EmploymentStatus.LEGAL_HOLD, EmploymentStatus.ANONYMIZED):
            raise FrozenRecordError("Cannot delete avatar for legal_hold or anonymized employee")

        old_key = emp.avatar_storage_key
        now = now_utc.astimezone(UTC)
        await self._employee_repo.update_avatar(
            principal.tenant_id,
            emp.id,
            avatar_storage_backend=None,
            avatar_storage_key=None,
            avatar_mime_type=None,
            avatar_updated_at=None,
            updated_at=now,
        )
        if old_key:
            with contextlib.suppress(Exception):
                await self._storage.delete_object(storage_key=old_key)


def _require_avatar_write_scope(principal: AuthenticatedPrincipal, employee: Employee) -> None:
    if principal.role in (UserRole.OWNER, UserRole.HR):
        return
    if principal.role == UserRole.STORE_MANAGER:
        principal.require_employee_in_scope(employee)
        return
    if employee.user_id != principal.user_id:
        raise ForbiddenScopeError("Employees may only manage their own avatar")


async def mint_avatar_url(storage: DocumentStoragePort, employee: Employee) -> str | None:
    """Mint a <= 60s signed URL for an employee's avatar if present."""
    if not employee.avatar_storage_key or not employee.avatar_mime_type:
        return None
    local_fn = getattr(storage, "create_avatar_signed_url", None)
    if callable(local_fn):
        res = local_fn(
            storage_key=employee.avatar_storage_key,
            mime_type=employee.avatar_mime_type,
            ttl_seconds=60,
        )
        return str(res.url)
    signed = await storage.create_signed_url(
        storage_key=employee.avatar_storage_key,
        ttl_seconds=60,
        download_filename="avatar",
        mime_type=employee.avatar_mime_type,
    )
    return signed.url


def _require_employee_read_scope(principal: AuthenticatedPrincipal, employee: Employee) -> None:
    if principal.role in TENANT_WIDE_ROLES:
        return
    if principal.role == UserRole.STORE_MANAGER:
        if employee.employment_status not in MUTABLE_STATUSES or employee.deleted_at is not None:
            raise ForbiddenScopeError(
                "Frozen or anonymized employee records are blocked from operational access "
                "(Art. 32 LOPDGDD)"
            )
        principal.require_employee_in_scope(employee)
        return
    if employee.user_id != principal.user_id:
        raise ForbiddenScopeError("Employees may only view their own HR record")


def _prepare_nie_dni(
    raw: str | None,
    *,
    cipher: PiiCipher,
    tenant_id: UUID,
    employee_id: UUID,
) -> tuple[str | None, str | None, str | None]:
    if raw is None or not raw.strip():
        return None, None, None
    normalized = normalize_nie_dni(raw)
    return (
        cipher.encrypt(
            normalized,
            tenant_id=tenant_id,
            employee_id=employee_id,
            field_name="nie_dni",
        ),
        mask_nie_dni(normalized),
        cipher.blind_index(normalized, tenant_id=tenant_id),
    )


def _prepare_iban(
    raw: str | None,
    *,
    cipher: PiiCipher,
    tenant_id: UUID,
    employee_id: UUID,
) -> tuple[str | None, str | None]:
    if raw is None or not raw.strip():
        return None, None
    normalized = normalize_iban(raw)
    return (
        cipher.encrypt(
            normalized,
            tenant_id=tenant_id,
            employee_id=employee_id,
            field_name="iban",
        ),
        mask_iban(normalized),
    )


def _prepare_naf(
    raw: str | None,
    *,
    cipher: PiiCipher,
    tenant_id: UUID,
    employee_id: UUID,
) -> tuple[str | None, str | None]:
    if raw is None or not raw.strip():
        return None, None
    normalized = normalize_naf(raw)
    return (
        cipher.encrypt(
            normalized,
            tenant_id=tenant_id,
            employee_id=employee_id,
            field_name="naf",
        ),
        mask_naf(normalized),
    )
