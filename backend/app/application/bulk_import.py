"""Application use cases for Bulk Employee Import (CSV & pure-stdlib XLSX)."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from uuid import uuid4

from app.application.employees import _prepare_iban, _prepare_naf, _prepare_nie_dni
from app.domain.bulk_import import (
    IMPORT_TEMPLATE_COLUMNS,
    MAX_BULK_IMPORT_FILE_BYTES,
    BulkImportErrorItem,
    BulkImportResult,
    ValidatedImportRow,
    parse_csv_rows,
    validate_import_row,
)
from app.domain.employees import Employee, EmploymentStatus, PiiCipher
from app.domain.organization import ValidationDomainError
from app.domain.repositories import EmployeeRepository, StoreRepository
from app.domain.security import ADMIN_ROLES, AuthenticatedPrincipal, ForbiddenScopeError
from app.infrastructure.xlsx_codec import generate_xlsx, parse_xlsx


class DownloadImportTemplateUseCase:
    def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        file_format: str = "csv",
    ) -> tuple[bytes, str, str]:
        if principal.role not in ADMIN_ROLES:
            raise ForbiddenScopeError("Only owners or HR may download employee import templates")

        fmt = file_format.strip().lower()
        sample_row = [
            "EMP-001",
            "María García López",
            "María",
            "MAD-01",
            "Cocina",
            "maria@empresa.es",
            "+34600111222",
            "1992-05-14",
            "indefinido",
            "full_time",
            "40.00",
            "30",
            "2026-01-15",
            "",
            "",
            "",
            "Cocinero/a",
        ]
        if fmt == "csv":
            buf = io.StringIO()
            buf.write("\ufeff")
            writer = csv.writer(buf, lineterminator="\n")
            writer.writerow(list(IMPORT_TEMPLATE_COLUMNS))
            writer.writerow(sample_row)
            return (
                buf.getvalue().encode("utf-8"),
                "text/csv; charset=utf-8",
                "okteam_employees_template.csv",
            )
        if fmt == "xlsx":
            xlsx_bytes = generate_xlsx(
                "Employees",
                list(IMPORT_TEMPLATE_COLUMNS),
                [sample_row],
            )
            return (
                xlsx_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "okteam_employees_template.xlsx",
            )
        raise ValidationDomainError("Unsupported template format: expected 'csv' or 'xlsx'")


class BulkImportEmployeesUseCase:
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
        *,
        filename: str,
        content: bytes,
        dry_run: bool = False,
        now_utc: datetime,
    ) -> BulkImportResult:
        if principal.role not in ADMIN_ROLES:
            raise ForbiddenScopeError("Only owners or HR may bulk-import employees")

        if not content:
            raise ValidationDomainError("Uploaded import file cannot be empty")
        if len(content) > MAX_BULK_IMPORT_FILE_BYTES:
            raise ValidationDomainError(
                f"Import file exceeds maximum size of {MAX_BULK_IMPORT_FILE_BYTES} bytes (5 MiB)"
            )

        lower_fn = filename.strip().lower()
        if lower_fn.endswith(".xlsx"):
            headers, data_rows = parse_xlsx(content)
        elif lower_fn.endswith(".csv"):
            headers, data_rows = parse_csv_rows(content)
        else:
            raise ValidationDomainError("Import file must be .csv or .xlsx")

        norm_headers = [h.strip().lower() for h in headers]
        required_cols = {
            "employee_code",
            "full_name",
            "store_code",
            "contract_type",
            "work_schedule_type",
            "contracted_weekly_hours",
            "annual_vacation_days",
            "hire_date",
        }
        missing_cols = sorted(required_cols - set(norm_headers))
        if missing_cols:
            raise ValidationDomainError(
                f"Missing required column(s): {', '.join(missing_cols)}"
            )

        stores = await self._store_repo.list_by_tenant(principal.tenant_id, include_inactive=False)
        store_by_code = {s.code.upper(): s for s in stores}
        existing_emps = await self._employee_repo.list_by_tenant(principal.tenant_id)
        existing_codes = {
            e.employee_code.upper() for e in existing_emps if e.deleted_at is None
        }
        existing_emails = {
            e.email.strip().lower()
            for e in existing_emps
            if e.deleted_at is None and e.email.strip()
        }

        all_errors: list[BulkImportErrorItem] = []
        valid_rows: list[ValidatedImportRow] = []
        seen_codes: set[str] = set()
        seen_emails: set[str] = set()
        seen_nies: set[str] = set()
        error_row_numbers: set[int] = set()

        for row_idx, row_cells in enumerate(data_rows, start=2):
            raw_map = {
                norm_headers[i]: (row_cells[i] if i < len(row_cells) else "")
                for i in range(len(norm_headers))
            }
            validated, row_errors = validate_import_row(row_number=row_idx, raw_map=raw_map)
            if row_errors:
                error_row_numbers.add(row_idx)
                all_errors.extend(row_errors)
                continue
            assert validated is not None

            row_failed = False
            if validated.store_code not in store_by_code:
                all_errors.append(
                    BulkImportErrorItem(
                        row_idx,
                        "store_code",
                        "STORE_NOT_FOUND",
                        f"Active store with code '{validated.store_code}' not found",
                    )
                )
                row_failed = True

            if validated.employee_code in existing_codes or validated.employee_code in seen_codes:
                all_errors.append(
                    BulkImportErrorItem(
                        row_idx,
                        "employee_code",
                        "DUPLICATE_CODE",
                        f"employee_code '{validated.employee_code}' already exists",
                    )
                )
                row_failed = True
            seen_codes.add(validated.employee_code)

            if validated.email:
                if validated.email in existing_emails or validated.email in seen_emails:
                    all_errors.append(
                        BulkImportErrorItem(
                            row_idx,
                            "email",
                            "DUPLICATE_EMAIL",
                            "email already exists in company or import file",
                        )
                    )
                    row_failed = True
                seen_emails.add(validated.email)

            if validated.nie_dni:
                blind = self._pii_cipher.blind_index(
                    validated.nie_dni, tenant_id=principal.tenant_id
                )
                if blind in seen_nies or await self._employee_repo.exists_active_nie_blind_index(
                    principal.tenant_id, blind
                ):
                    all_errors.append(
                        BulkImportErrorItem(
                            row_idx,
                            "nie_dni",
                            "DUPLICATE_NIE_DNI",
                            "NIE/DNI already exists in company or import file",
                        )
                    )
                    row_failed = True
                seen_nies.add(blind)

            if row_failed:
                error_row_numbers.add(row_idx)
            else:
                valid_rows.append(validated)

        total_rows = len(data_rows)
        error_rows_count = len(error_row_numbers)
        valid_rows_count = total_rows - error_rows_count

        if dry_run or error_rows_count > 0:
            return BulkImportResult(
                dry_run=dry_run,
                committed=False,
                total_rows=total_rows,
                valid_rows=valid_rows_count,
                error_rows=error_rows_count,
                created_count=0,
                errors=tuple(all_errors),
            )

        now = now_utc.astimezone(UTC)
        created_count = 0
        for row in valid_rows:
            store = store_by_code[row.store_code]
            emp_id = uuid4()
            nie_enc, nie_masked, nie_blind = _prepare_nie_dni(
                row.nie_dni,
                cipher=self._pii_cipher,
                tenant_id=principal.tenant_id,
                employee_id=emp_id,
            )
            iban_enc, iban_masked = _prepare_iban(
                row.iban,
                cipher=self._pii_cipher,
                tenant_id=principal.tenant_id,
                employee_id=emp_id,
            )
            naf_enc, naf_masked = _prepare_naf(
                row.naf,
                cipher=self._pii_cipher,
                tenant_id=principal.tenant_id,
                employee_id=emp_id,
            )
            emp = Employee(
                id=emp_id,
                tenant_id=principal.tenant_id,
                primary_store_id=store.id,
                user_id=None,
                employee_code=row.employee_code,
                full_name=row.full_name,
                preferred_name=row.preferred_name,
                department=row.department,
                job_title=row.job_title,
                email=row.email,
                phone=row.phone,
                birth_date=row.birth_date,
                contract_type=row.contract_type,
                work_schedule_type=row.work_schedule_type,
                contracted_weekly_hours=row.contracted_weekly_hours,
                annual_vacation_days=row.annual_vacation_days,
                hire_date=row.hire_date,
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
            await self._employee_repo.create(emp)
            created_count += 1

        return BulkImportResult(
            dry_run=False,
            committed=True,
            total_rows=total_rows,
            valid_rows=valid_rows_count,
            error_rows=0,
            created_count=created_count,
            errors=(),
        )
