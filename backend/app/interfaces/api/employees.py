from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

from app.application.bulk_import import BulkImportEmployeesUseCase, DownloadImportTemplateUseCase
from app.application.employees import (
    AnonymizeEmployeeUseCase,
    CreateEmployeeCommand,
    CreateEmployeeUseCase,
    DeleteEmployeeAvatarUseCase,
    GetEmployeeSensitivePiiUseCase,
    GetEmployeeUseCase,
    ListEmployeesUseCase,
    TerminateEmployeeUseCase,
    UpdateEmployeeCommand,
    UpdateEmployeeUseCase,
    UploadEmployeeAvatarUseCase,
    mint_avatar_url,
)
from app.domain.bulk_import import MAX_BULK_IMPORT_FILE_BYTES
from app.domain.documents import DocumentStoragePort
from app.domain.employees import (
    MAX_AVATAR_SIZE_BYTES,
    ContractType,
    Employee,
    EmploymentStatus,
    PiiCipher,
    WorkScheduleType,
)
from app.domain.organization import EntityNotFoundError
from app.domain.repositories import EmployeeRepository, StoreRepository
from app.domain.security import AuthenticatedPrincipal
from app.infrastructure.document_storage import LocalDocumentStorageAdapter, SignedTokenError
from app.interfaces.api.deps import (
    CommitFn,
    get_commit_fn,
    get_current_principal,
    get_document_storage,
    get_employee_repository,
    get_pii_cipher,
    get_store_repository,
    get_utc_now,
)

router = APIRouter(prefix="/employees", tags=["employees"])


class EmployeeCreateRequest(BaseModel):
    primary_store_id: UUID
    employee_code: str = Field(min_length=1, max_length=32)
    full_name: str = Field(min_length=1, max_length=160)
    preferred_name: str = Field(default="", max_length=120)
    department: str = Field(default="", max_length=100)
    job_title: str = Field(default="", max_length=120)
    emergency_contact_name: str = Field(default="", max_length=120)
    emergency_contact_phone: str = Field(default="", max_length=40)
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
    default_break_minutes: int | None = Field(default=None, ge=5, le=180)
    hire_date: date
    nie_dni: str | None = Field(default=None, max_length=32)
    iban: str | None = Field(default=None, max_length=40)
    naf: str | None = Field(default=None, max_length=32)


class EmployeeUpdateRequest(BaseModel):
    primary_store_id: UUID
    employee_code: str = Field(min_length=1, max_length=32)
    full_name: str = Field(min_length=1, max_length=160)
    preferred_name: str = Field(default="", max_length=120)
    department: str = Field(default="", max_length=100)
    job_title: str = Field(default="", max_length=120)
    emergency_contact_name: str = Field(default="", max_length=120)
    emergency_contact_phone: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=40)
    user_id: UUID | None = None
    birth_date: date | None = None
    contract_type: ContractType
    work_schedule_type: WorkScheduleType
    contracted_weekly_hours: Decimal = Field(gt=Decimal("0"), le=Decimal("40.00"))
    annual_vacation_days: int = Field(ge=30, le=90)
    default_break_minutes: int | None = Field(default=None, ge=5, le=180)
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
    department: str
    job_title: str = ""
    emergency_contact_name: str = ""
    emergency_contact_phone: str = ""
    email: str
    phone: str
    birth_date: date | None
    contract_type: ContractType
    work_schedule_type: WorkScheduleType
    contracted_weekly_hours: Decimal
    annual_vacation_days: int
    default_break_minutes: int | None = None
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
    avatar_url: str | None = None
    retention_until: datetime | None
    anonymized_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(
        cls,
        employee: Employee,
        *,
        avatar_url: str | None = None,
    ) -> EmployeeResponse:
        return cls(
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
            contract_type=employee.contract_type,
            work_schedule_type=employee.work_schedule_type,
            contracted_weekly_hours=employee.contracted_weekly_hours,
            annual_vacation_days=employee.annual_vacation_days,
            default_break_minutes=employee.default_break_minutes,
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
            avatar_url=avatar_url,
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


class BulkImportErrorResponse(BaseModel):
    row_number: int
    field: str
    code: str
    message: str


class BulkImportResponse(BaseModel):
    dry_run: bool
    committed: bool
    total_rows: int
    valid_rows: int
    error_rows: int
    created_count: int
    errors: list[BulkImportErrorResponse]


@router.get(
    "/import/template",
    operation_id="downloadEmployeeImportTemplate",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "content": {
                "text/csv": {},
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
            },
            "description": "Download CSV or XLSX bulk employee import template (owner / hr only)",
        }
    },
)
async def download_employee_import_template(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    file_format: Annotated[str, Query(alias="format")] = "csv",
) -> Response:
    content, media_type, filename = DownloadImportTemplateUseCase().execute(
        principal, file_format=file_format
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/import",
    operation_id="bulkImportEmployees",
    response_model=BulkImportResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate or atomically bulk-import employees from CSV/XLSX (owner / hr only)",
)
async def bulk_import_employees(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    pii_cipher: Annotated[PiiCipher, Depends(get_pii_cipher)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    file: Annotated[UploadFile, File()],
    dry_run: Annotated[bool, Query()] = False,
) -> BulkImportResponse:
    content = await file.read(MAX_BULK_IMPORT_FILE_BYTES + 1)
    result = await BulkImportEmployeesUseCase(
        employee_repo, store_repo, pii_cipher
    ).execute(
        principal,
        filename=file.filename or "employees.csv",
        content=content,
        dry_run=dry_run,
        now_utc=now_utc,
    )
    if result.committed:
        await commit()
    return BulkImportResponse(
        dry_run=result.dry_run,
        committed=result.committed,
        total_rows=result.total_rows,
        valid_rows=result.valid_rows,
        error_rows=result.error_rows,
        created_count=result.created_count,
        errors=[
            BulkImportErrorResponse(
                row_number=e.row_number,
                field=e.field,
                code=e.code,
                message=e.message,
            )
            for e in result.errors
        ],
    )


@router.get(
    "/avatars/local/{token}",
    operation_id="getLocalEmployeeAvatar",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "content": {"image/png": {}, "image/jpeg": {}, "image/webp": {}},
            "description": "Short-lived (<= 60s) HMAC-signed employee avatar image",
        }
    },
)
async def get_local_employee_avatar(
    token: str,
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
) -> Response:
    if not isinstance(storage, LocalDocumentStorageAdapter):
        raise SignedTokenError("Local avatar endpoint is disabled for current storage backend")

    content, mime_type, storage_key = storage.verify_and_read_avatar(token)
    parts = storage_key.split("/")
    if len(parts) != 4 or parts[0] != "avatars":
        raise SignedTokenError("Invalid avatar storage key in token")
    try:
        tenant_id = UUID(parts[1])
        employee_id = UUID(parts[2])
    except ValueError as exc:
        raise SignedTokenError("Invalid UUID in avatar storage key") from exc

    emp = await employee_repo.get_by_id(tenant_id, employee_id)
    if (
        emp is None
        or emp.deleted_at is not None
        or emp.employment_status == EmploymentStatus.ANONYMIZED
        or emp.avatar_storage_key != storage_key
    ):
        raise EntityNotFoundError("Avatar not found or employee anonymized")

    return Response(
        content=content,
        media_type=mime_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
        },
    )


@router.get(
    "",
    operation_id="listEmployees",
    response_model=EmployeeListResponse,
    summary="List employees visible within caller tenant and store scope (masked PII only)",
)
async def list_employees(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
    store_id: Annotated[UUID | None, Query()] = None,
    employment_status: Annotated[EmploymentStatus | None, Query(alias="status")] = None,
) -> EmployeeListResponse:
    use_case = ListEmployeesUseCase(employee_repo)
    items = await use_case.execute(
        principal,
        store_id=store_id,
        status=employment_status,
    )
    responses = [
        EmployeeResponse.from_domain(
            item,
            avatar_url=await mint_avatar_url(storage, item),
        )
        for item in items
    ]
    return EmployeeListResponse(
        items=responses,
        total=len(responses),
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
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
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
            department=payload.department,
            job_title=payload.job_title,
            emergency_contact_name=payload.emergency_contact_name,
            emergency_contact_phone=payload.emergency_contact_phone,
            email=payload.email,
            phone=payload.phone,
            user_id=payload.user_id,
            birth_date=payload.birth_date,
            contract_type=payload.contract_type,
            work_schedule_type=payload.work_schedule_type,
            contracted_weekly_hours=payload.contracted_weekly_hours,
            annual_vacation_days=payload.annual_vacation_days,
            default_break_minutes=payload.default_break_minutes,
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
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
) -> EmployeeResponse:
    use_case = GetEmployeeUseCase(employee_repo)
    employee = await use_case.execute(principal, employee_id)
    return EmployeeResponse.from_domain(
        employee,
        avatar_url=await mint_avatar_url(storage, employee),
    )


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
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
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
            department=payload.department,
            job_title=payload.job_title,
            emergency_contact_name=payload.emergency_contact_name,
            emergency_contact_phone=payload.emergency_contact_phone,
            email=payload.email,
            phone=payload.phone,
            user_id=payload.user_id,
            birth_date=payload.birth_date,
            contract_type=payload.contract_type,
            work_schedule_type=payload.work_schedule_type,
            contracted_weekly_hours=payload.contracted_weekly_hours,
            annual_vacation_days=payload.annual_vacation_days,
            default_break_minutes=payload.default_break_minutes,
            hire_date=payload.hire_date,
            employment_status=payload.employment_status,
            nie_dni=payload.nie_dni,
            iban=payload.iban,
            naf=payload.naf,
        ),
    )
    await commit()
    return EmployeeResponse.from_domain(
        updated,
        avatar_url=await mint_avatar_url(storage, updated),
    )


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
    "/{employee_id}/avatar",
    operation_id="uploadEmployeeAvatar",
    response_model=EmployeeResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload or replace employee avatar image (JPEG/PNG/WebP <= 2 MiB)",
)
async def upload_employee_avatar(
    employee_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    file: Annotated[UploadFile, File()],
) -> EmployeeResponse:
    content = await file.read(MAX_AVATAR_SIZE_BYTES + 1)
    mime_type = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    updated = await UploadEmployeeAvatarUseCase(employee_repo, storage).execute(
        principal,
        employee_id,
        mime_type=mime_type,
        content=content,
        now_utc=now_utc,
    )
    await commit()
    return EmployeeResponse.from_domain(
        updated,
        avatar_url=await mint_avatar_url(storage, updated),
    )


@router.delete(
    "/{employee_id}/avatar",
    operation_id="deleteEmployeeAvatar",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove employee avatar image",
)
async def delete_employee_avatar(
    employee_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> Response:
    await DeleteEmployeeAvatarUseCase(employee_repo, storage).execute(
        principal, employee_id, now_utc=now_utc
    )
    await commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
) -> EmployeeResponse:
    use_case = AnonymizeEmployeeUseCase(employee_repo, storage=storage)
    anonymized = await use_case.execute(principal, employee_id)
    await commit()
    return EmployeeResponse.from_domain(anonymized)
