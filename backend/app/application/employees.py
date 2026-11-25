"""Application use cases for Phase 2 Employee HR records, PII encryption & legal hold."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.employees import (
    ContractType,
    Employee,
    EmployeeSensitivePii,
    EmploymentStatus,
    FrozenRecordError,
    PiiCipher,
    RetentionLockError,
    WorkScheduleType,
    compute_retention_until,
    mask_iban,
    mask_naf,
    mask_nie_dni,
    normalize_employee_code,
    normalize_iban,
    normalize_naf,
    normalize_nie_dni,
    validate_annual_vacation_days,
    validate_contracted_weekly_hours,
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


@dataclass(frozen=True, slots=True)
class CreateEmployeeCommand:
    primary_store_id: UUID
    employee_code: str
    full_name: str
    preferred_name: str = ""
    email: str = ""
    phone: str = ""
    user_id: UUID | None = None
    birth_date: date | None = None
    contract_type: ContractType = ContractType.INDEFINIDO
    work_schedule_type: WorkScheduleType = WorkScheduleType.FULL_TIME
    contracted_weekly_hours: Decimal = Decimal("40.00")
    annual_vacation_days: int = 30
    hire_date: date = date(2026, 1, 1)
    nie_dni: str | None = field(default=None, repr=False)
    iban: str | None = field(default=None, repr=False)
    naf: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class UpdateEmployeeCommand:
    primary_store_id: UUID
    employee_code: str
    full_name: str
    preferred_name: str = ""
    email: str = ""
    phone: str = ""
    user_id: UUID | None = None
    birth_date: date | None = None
    contract_type: ContractType = ContractType.INDEFINIDO
    work_schedule_type: WorkScheduleType = WorkScheduleType.FULL_TIME
    contracted_weekly_hours: Decimal = Decimal("40.00")
    annual_vacation_days: int = 30
    hire_date: date = date(2026, 1, 1)
    employment_status: EmploymentStatus = EmploymentStatus.ACTIVE
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
            return await self._employee_repo.list_by_tenant(
                principal.tenant_id,
                allowed_store_ids=principal.store_ids,
                allowed_statuses=MUTABLE_STATUSES,
                store_id=store_id,
                status=status,
            )

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

        if await self._employee_repo.exists_active_code(principal.tenant_id, employee_code):
            raise ConflictError(f"Employee code '{employee_code}' already exists in this company")

        employee_id = uuid4()
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
            email=cmd.email.strip().lower()[:255],
            phone=cmd.phone.strip()[:40],
            birth_date=cmd.birth_date,
            contract_type=cmd.contract_type,
            work_schedule_type=cmd.work_schedule_type,
            contracted_weekly_hours=weekly_hours,
            annual_vacation_days=vacation_days,
            hire_date=cmd.hire_date,
            termination_date=None,
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

        employee_code = normalize_employee_code(cmd.employee_code)
        full_name = validate_non_empty_text(cmd.full_name, field="full_name", max_length=160)
        weekly_hours = validate_contracted_weekly_hours(
            cmd.contracted_weekly_hours,
            cmd.work_schedule_type,
        )
        vacation_days = validate_annual_vacation_days(cmd.annual_vacation_days)

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
            primary_store_id=cmd.primary_store_id,
            user_id=cmd.user_id,
            employee_code=employee_code,
            full_name=full_name,
            preferred_name=cmd.preferred_name.strip()[:120],
            email=cmd.email.strip().lower()[:255],
            phone=cmd.phone.strip()[:40],
            birth_date=cmd.birth_date,
            contract_type=cmd.contract_type,
            work_schedule_type=cmd.work_schedule_type,
            contracted_weekly_hours=weekly_hours,
            annual_vacation_days=vacation_days,
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
    def __init__(self, employee_repo: EmployeeRepository) -> None:
        self._employee_repo = employee_repo

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

        updated = await self._employee_repo.anonymize(
            principal.tenant_id,
            employee_id,
            anonymized_full_name=f"ANONYMIZED-{existing.employee_code}",
            anonymized_at=now,
        )
        if updated is None:
            raise EntityNotFoundError("Employee not found")
        return updated


def _require_employee_read_scope(principal: AuthenticatedPrincipal, employee: Employee) -> None:
    if principal.role in TENANT_WIDE_ROLES:
        return
    if principal.role == UserRole.STORE_MANAGER:
        principal.require_store_access(principal.tenant_id, employee.primary_store_id)
        if employee.employment_status not in MUTABLE_STATUSES or employee.deleted_at is not None:
            raise ForbiddenScopeError(
                "Frozen or anonymized employee records are blocked from operational access "
                "(Art. 32 LOPDGDD)"
            )
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
