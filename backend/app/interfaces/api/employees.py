from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from app.application.employees import (
    AnonymizeEmployeeUseCase,
    CreateEmployeeCommand,
    CreateEmployeeUseCase,
    GetEmployeeSensitivePiiUseCase,
    GetEmployeeUseCase,
    ListEmployeesUseCase,
    TerminateEmployeeUseCase,
    UpdateEmployeeCommand,
    UpdateEmployeeUseCase,
)
from app.domain.employees import (
    ContractType,
    Employee,
    EmploymentStatus,
    PiiCipher,
    WorkScheduleType,
)
from app.domain.repositories import EmployeeRepository, StoreRepository
from app.domain.security import AuthenticatedPrincipal
from app.interfaces.api.deps import (
    CommitFn,
    get_commit_fn,
    get_current_principal,
    get_employee_repository,
    get_pii_cipher,
    get_store_repository,
)

router = APIRouter(prefix="/employees", tags=["employees"])


class EmployeeCreateRequest(BaseModel):
    primary_store_id: UUID
    employee_code: str = Field(min_length=1, max_length=32)
    full_name: str = Field(min_length=1, max_length=160)
    preferred_name: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=40)
    user_id: UUID | None = None
    birth_date: date | None = None
    contract_type: ContractType = ContractType.INDEFINIDO
    work_schedule_type: WorkScheduleType = WorkScheduleType.FULL_TIME
    contracted_weekly_hours: Decimal = Field(
        default=Decimal("40.00"),
        gt=Decimal("0"),
        le=Decimal("40.00"),
    )
    annual_vacation_days: int = Field(default=30, ge=30, le=90)
    hire_date: date
    nie_dni: str | None = Field(default=None, max_length=32)
    iban: str | None = Field(default=None, max_length=40)
    naf: str | None = Field(default=None, max_length=32)


class EmployeeUpdateRequest(BaseModel):
    primary_store_id: UUID
    employee_code: str = Field(min_length=1, max_length=32)
    full_name: str = Field(min_length=1, max_length=160)
    preferred_name: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=40)
    user_id: UUID | None = None
    birth_date: date | None = None
    contract_type: ContractType
    work_schedule_type: WorkScheduleType
    contracted_weekly_hours: Decimal = Field(gt=Decimal("0"), le=Decimal("40.00"))
    annual_vacation_days: int = Field(ge=30, le=90)
    hire_date: date
    employment_status: EmploymentStatus = EmploymentStatus.ACTIVE
    # Omit/null to preserve existing encrypted value; send "" to clear; send new value to rotate.
    nie_dni: str | None = Field(default=None, max_length=32)
    iban: str | None = Field(default=None, max_length=40)
    naf: str | None = Field(default=None, max_length=32)


class EmployeeTerminateRequest(BaseModel):
    termination_date: date


class EmployeeResponse(BaseModel):
    """Masked HR profile response — NEVER exposes plaintext NIE/DNI, IBAN, or NAF."""

    id: UUID
    tenant_id: UUID
    primary_store_id: UUID
    user_id: UUID | None
    employee_code: str
    full_name: str
    preferred_name: str
    email: str
    phone: str
    birth_date: date | None
    contract_type: ContractType
    work_schedule_type: WorkScheduleType
    contracted_weekly_hours: Decimal
    annual_vacation_days: int
    is_overtime_prohibited: bool
    hire_date: date
    termination_date: date | None
    nie_dni_masked: str | None
    iban_masked: str | None
    naf_masked: str | None
    has_nie_dni: bool
    has_iban: bool
    has_naf: bool
    employment_status: EmploymentStatus
    retention_until: datetime | None
    anonymized_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, employee: Employee) -> EmployeeResponse:
        return cls(
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
            contract_type=employee.contract_type,
            work_schedule_type=employee.work_schedule_type,
            contracted_weekly_hours=employee.contracted_weekly_hours,
            annual_vacation_days=employee.annual_vacation_days,
            is_overtime_prohibited=employee.is_overtime_prohibited,
            hire_date=employee.hire_date,
            termination_date=employee.termination_date,
            nie_dni_masked=employee.nie_dni_masked,
            iban_masked=employee.iban_masked,
            naf_masked=employee.naf_masked,
            has_nie_dni=employee.nie_dni_enc is not None,
            has_iban=employee.iban_enc is not None,
            has_naf=employee.naf_enc is not None,
            employment_status=employee.employment_status,
            retention_until=employee.retention_until,
            anonymized_at=employee.anonymized_at,
            created_at=employee.created_at,
            updated_at=employee.updated_at,
        )


class EmployeeListResponse(BaseModel):
    items: list[EmployeeResponse]
    total: int


class EmployeeSensitivePiiResponse(BaseModel):
    """On-demand decrypted PII — strictly authorized roles only, never cached."""

    employee_id: UUID
    nie_dni: str | None
    iban: str | None
    naf: str | None


@router.get(
    "",
    operation_id="listEmployees",
    response_model=EmployeeListResponse,
    summary="List employees visible within caller tenant and store scope (masked PII only)",
)
async def list_employees(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_id: Annotated[UUID | None, Query()] = None,
    employment_status: Annotated[EmploymentStatus | None, Query(alias="status")] = None,
) -> EmployeeListResponse:
    use_case = ListEmployeesUseCase(employee_repo)
    items = await use_case.execute(
        principal,
        store_id=store_id,
        status=employment_status,
    )
    return EmployeeListResponse(
        items=[EmployeeResponse.from_domain(item) for item in items],
        total=len(items),
    )


@router.post(
    "",
    operation_id="createEmployee",
    response_model=EmployeeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an employee profile with AES-256-GCM encrypted PII (owner / hr only)",
)
async def create_employee(
    payload: EmployeeCreateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    pii_cipher: Annotated[PiiCipher, Depends(get_pii_cipher)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
) -> EmployeeResponse:
    use_case = CreateEmployeeUseCase(employee_repo, store_repo, pii_cipher)
    created = await use_case.execute(
        principal,
        CreateEmployeeCommand(
            primary_store_id=payload.primary_store_id,
            employee_code=payload.employee_code,
            full_name=payload.full_name,
            preferred_name=payload.preferred_name,
            email=payload.email,
            phone=payload.phone,
            user_id=payload.user_id,
            birth_date=payload.birth_date,
            contract_type=payload.contract_type,
            work_schedule_type=payload.work_schedule_type,
            contracted_weekly_hours=payload.contracted_weekly_hours,
            annual_vacation_days=payload.annual_vacation_days,
            hire_date=payload.hire_date,
            nie_dni=payload.nie_dni,
            iban=payload.iban,
            naf=payload.naf,
        ),
    )
    await commit()
    return EmployeeResponse.from_domain(created)


@router.get(
    "/{employee_id}",
    operation_id="getEmployee",
    response_model=EmployeeResponse,
    summary="Get a single employee HR profile (masked PII only)",
)
async def get_employee(
    employee_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
) -> EmployeeResponse:
    use_case = GetEmployeeUseCase(employee_repo)
    employee = await use_case.execute(principal, employee_id)
    return EmployeeResponse.from_domain(employee)


@router.patch(
    "/{employee_id}",
    operation_id="updateEmployee",
    response_model=EmployeeResponse,
    summary="Update employee profile and optionally rotate encrypted PII (owner / hr only)",
)
async def update_employee(
    employee_id: UUID,
    payload: EmployeeUpdateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    pii_cipher: Annotated[PiiCipher, Depends(get_pii_cipher)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
) -> EmployeeResponse:
    use_case = UpdateEmployeeUseCase(employee_repo, store_repo, pii_cipher)
    updated = await use_case.execute(
        principal,
        employee_id,
        UpdateEmployeeCommand(
            primary_store_id=payload.primary_store_id,
            employee_code=payload.employee_code,
            full_name=payload.full_name,
            preferred_name=payload.preferred_name,
            email=payload.email,
            phone=payload.phone,
            user_id=payload.user_id,
            birth_date=payload.birth_date,
            contract_type=payload.contract_type,
            work_schedule_type=payload.work_schedule_type,
            contracted_weekly_hours=payload.contracted_weekly_hours,
            annual_vacation_days=payload.annual_vacation_days,
            hire_date=payload.hire_date,
            employment_status=payload.employment_status,
            nie_dni=payload.nie_dni,
            iban=payload.iban,
            naf=payload.naf,
        ),
    )
    await commit()
    return EmployeeResponse.from_domain(updated)


@router.get(
    "/{employee_id}/sensitive",
    operation_id="getEmployeeSensitivePii",
    response_model=EmployeeSensitivePiiResponse,
    summary="Decrypt NIE/DNI, IBAN, and NAF on demand (owner / hr / accountant / self only)",
)
async def get_employee_sensitive_pii(
    employee_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    pii_cipher: Annotated[PiiCipher, Depends(get_pii_cipher)],
) -> EmployeeSensitivePiiResponse:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    use_case = GetEmployeeSensitivePiiUseCase(employee_repo, pii_cipher)
    decrypted = await use_case.execute(principal, employee_id)
    return EmployeeSensitivePiiResponse(
        employee_id=decrypted.employee_id,
        nie_dni=decrypted.nie_dni,
        iban=decrypted.iban,
        naf=decrypted.naf,
    )


@router.post(
    "/{employee_id}/terminate",
    operation_id="terminateEmployee",
    response_model=EmployeeResponse,
    summary="Terminate employee into Spanish 4-year statutory legal hold (owner / hr only)",
)
async def terminate_employee(
    employee_id: UUID,
    payload: EmployeeTerminateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
) -> EmployeeResponse:
    use_case = TerminateEmployeeUseCase(employee_repo)
    frozen = await use_case.execute(
        principal,
        employee_id,
        termination_date=payload.termination_date,
    )
    await commit()
    return EmployeeResponse.from_domain(frozen)


@router.post(
    "/{employee_id}/anonymize",
    operation_id="anonymizeEmployee",
    response_model=EmployeeResponse,
    summary="Irreversibly anonymize PII after 4-year legal hold expires (owner / hr only)",
)
async def anonymize_employee(
    employee_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
) -> EmployeeResponse:
    use_case = AnonymizeEmployeeUseCase(employee_repo)
    anonymized = await use_case.execute(principal, employee_id)
    await commit()
    return EmployeeResponse.from_domain(anonymized)
