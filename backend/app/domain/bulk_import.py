"""Pure stdlib domain logic for Bulk Employee Import validation & CSV parsing.

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
NEVER echoes raw NIE/DNI, IBAN, or NAF in error messages.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.domain.employees import (
    ContractType,
    WorkScheduleType,
    normalize_employee_code,
    normalize_iban,
    normalize_naf,
    normalize_nie_dni,
    validate_annual_vacation_days,
    validate_contracted_weekly_hours,
)
from app.domain.organization import ValidationDomainError

MAX_BULK_IMPORT_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB
MAX_BULK_IMPORT_ROWS = 500
_ES_IBAN_STRICT = re.compile(r"^ES[0-9]{22}$")

IMPORT_TEMPLATE_COLUMNS: tuple[str, ...] = (
    "employee_code",
    "full_name",
    "preferred_name",
    "store_code",
    "department",
    "email",
    "phone",
    "birth_date",
    "contract_type",
    "work_schedule_type",
    "contracted_weekly_hours",
    "annual_vacation_days",
    "hire_date",
    "nie_dni",
    "iban",
    "naf",
    "job_title",
)


@dataclass(frozen=True, slots=True)
class BulkImportErrorItem:
    row_number: int
    field: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidatedImportRow:
    row_number: int
    employee_code: str
    full_name: str
    preferred_name: str
    store_code: str
    department: str
    email: str
    phone: str
    birth_date: date | None
    contract_type: ContractType
    work_schedule_type: WorkScheduleType
    contracted_weekly_hours: Decimal
    annual_vacation_days: int
    hire_date: date
    nie_dni: str | None
    iban: str | None
    naf: str | None
    job_title: str = ""


@dataclass(frozen=True, slots=True)
class BulkImportResult:
    dry_run: bool
    committed: bool
    total_rows: int
    valid_rows: int
    error_rows: int
    created_count: int
    errors: tuple[BulkImportErrorItem, ...]


def parse_csv_rows(content: bytes) -> tuple[list[str], list[list[str]]]:
    """Parse UTF-8 (or UTF-8 BOM) CSV bytes into (headers, data_rows)."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationDomainError("CSV file must be valid UTF-8 encoded") from exc
    try:
        reader = csv.reader(io.StringIO(text))
        all_rows = list(reader)
    except csv.Error as exc:
        raise ValidationDomainError(f"Invalid CSV format: {exc}") from exc
    if not all_rows:
        raise ValidationDomainError("Import file is empty")
    headers = [h.strip() for h in all_rows[0]]
    data_rows = [r for r in all_rows[1:] if any(cell.strip() for cell in r)]
    if len(data_rows) > MAX_BULK_IMPORT_ROWS:
        raise ValidationDomainError(
            f"Import file exceeds maximum of {MAX_BULK_IMPORT_ROWS} employee rows"
        )
    return headers, data_rows


def _parse_iso_or_excel_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    try:
        serial_dec = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Invalid date") from exc
    if not serial_dec.is_finite() or serial_dec < 1 or serial_dec > 2958465:
        raise ValueError("Excel serial date out of range")
    return date(1899, 12, 30) + timedelta(days=int(serial_dec))


def _parse_int_or_float_int(raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        dec = Decimal(raw)
        if not dec.is_finite() or dec < -1000 or dec > 1000 or dec % 1 != 0:
            raise ValueError("Non-integer or out-of-range value") from None
        return int(dec)


def validate_import_row(
    *,
    row_number: int,
    raw_map: dict[str, str],
) -> tuple[ValidatedImportRow | None, list[BulkImportErrorItem]]:
    """Validate a single 2-based import row without echoing raw sensitive PII."""
    errors: list[BulkImportErrorItem] = []

    def _get(col: str) -> str:
        return raw_map.get(col, "").strip()

    # 1. employee_code
    raw_code = _get("employee_code")
    employee_code = ""
    if not raw_code:
        errors.append(
            BulkImportErrorItem(
                row_number, "employee_code", "REQUIRED", "employee_code is required"
            )
        )
    else:
        try:
            employee_code = normalize_employee_code(raw_code)
        except ValidationDomainError as exc:
            errors.append(
                BulkImportErrorItem(row_number, "employee_code", "INVALID_FORMAT", str(exc))
            )

    # 2. full_name
    full_name = _get("full_name")
    if not full_name:
        errors.append(
            BulkImportErrorItem(row_number, "full_name", "REQUIRED", "full_name is required")
        )
    elif len(full_name) > 160:
        errors.append(
            BulkImportErrorItem(
                row_number, "full_name", "TOO_LONG", "full_name exceeds 160 characters"
            )
        )

    # 3. store_code
    store_code = _get("store_code").upper()
    if not store_code:
        errors.append(
            BulkImportErrorItem(row_number, "store_code", "REQUIRED", "store_code is required")
        )

    # 4. contract_type
    raw_contract = _get("contract_type").lower()
    contract_type = ContractType.INDEFINIDO
    if not raw_contract:
        errors.append(
            BulkImportErrorItem(
                row_number, "contract_type", "REQUIRED", "contract_type is required"
            )
        )
    else:
        try:
            contract_type = ContractType(raw_contract)
        except ValueError:
            errors.append(
                BulkImportErrorItem(
                    row_number,
                    "contract_type",
                    "INVALID_ENUM",
                    "contract_type must be one of: "
                    "indefinido, temporal, fijo_discontinuo, practicas",
                )
            )

    # 5. work_schedule_type
    raw_sched = _get("work_schedule_type").lower()
    work_schedule_type = WorkScheduleType.FULL_TIME
    sched_valid = False
    if not raw_sched:
        errors.append(
            BulkImportErrorItem(
                row_number, "work_schedule_type", "REQUIRED", "work_schedule_type is required"
            )
        )
    else:
        try:
            work_schedule_type = WorkScheduleType(raw_sched)
            sched_valid = True
        except ValueError:
            errors.append(
                BulkImportErrorItem(
                    row_number,
                    "work_schedule_type",
                    "INVALID_ENUM",
                    "work_schedule_type must be full_time or part_time",
                )
            )

    # 6. contracted_weekly_hours
    raw_hours = _get("contracted_weekly_hours")
    weekly_hours = Decimal("40.00")
    if not raw_hours:
        errors.append(
            BulkImportErrorItem(
                row_number,
                "contracted_weekly_hours",
                "REQUIRED",
                "contracted_weekly_hours is required",
            )
        )
    else:
        try:
            parsed_hours = Decimal(raw_hours)
            if sched_valid:
                weekly_hours = validate_contracted_weekly_hours(parsed_hours, work_schedule_type)
            else:
                weekly_hours = parsed_hours
        except (InvalidOperation, ValidationDomainError) as exc:
            errors.append(
                BulkImportErrorItem(
                    row_number, "contracted_weekly_hours", "INVALID_HOURS", str(exc)
                )
            )

    # 7. annual_vacation_days
    raw_vac = _get("annual_vacation_days")
    vacation_days = 30
    if not raw_vac:
        errors.append(
            BulkImportErrorItem(
                row_number, "annual_vacation_days", "REQUIRED", "annual_vacation_days is required"
            )
        )
    else:
        try:
            vacation_days = validate_annual_vacation_days(_parse_int_or_float_int(raw_vac))
        except (ValueError, InvalidOperation, ValidationDomainError) as exc:
            errors.append(
                BulkImportErrorItem(
                    row_number, "annual_vacation_days", "INVALID_VACATION_DAYS", str(exc)
                )
            )

    # 8. hire_date
    raw_hire = _get("hire_date")
    hire_date = date(2026, 1, 1)
    if not raw_hire:
        errors.append(
            BulkImportErrorItem(row_number, "hire_date", "REQUIRED", "hire_date is required")
        )
    else:
        try:
            hire_date = _parse_iso_or_excel_date(raw_hire)
        except ValueError:
            errors.append(
                BulkImportErrorItem(
                    row_number, "hire_date", "INVALID_DATE", "hire_date must be YYYY-MM-DD"
                )
            )

    # 9. birth_date (optional)
    raw_birth = _get("birth_date")
    birth_date: date | None = None
    if raw_birth:
        try:
            birth_date = _parse_iso_or_excel_date(raw_birth)
        except ValueError:
            errors.append(
                BulkImportErrorItem(
                    row_number, "birth_date", "INVALID_DATE", "birth_date must be YYYY-MM-DD"
                )
            )

    # 10. nie_dni (optional, Mod-23, NEVER echo raw value)
    raw_nie = _get("nie_dni")
    nie_dni: str | None = None
    if raw_nie:
        try:
            nie_dni = normalize_nie_dni(raw_nie)
        except ValidationDomainError:
            errors.append(
                BulkImportErrorItem(
                    row_number,
                    "nie_dni",
                    "INVALID_NIE_DNI",
                    "Invalid Spanish DNI/NIE format or Mod-23 checksum",
                )
            )

    # 11. iban (optional, Mod-97 + ^ES[0-9]{22}$, NEVER echo raw value)
    raw_iban = _get("iban")
    iban: str | None = None
    if raw_iban:
        try:
            normalized_iban = normalize_iban(raw_iban)
            if not _ES_IBAN_STRICT.match(normalized_iban):
                raise ValidationDomainError("Spanish IBAN required")
            iban = normalized_iban
        except ValidationDomainError:
            errors.append(
                BulkImportErrorItem(
                    row_number,
                    "iban",
                    "INVALID_IBAN",
                    "Invalid Spanish IBAN (must match ^ES[0-9]{22}$ and Mod-97 checksum)",
                )
            )

    # 12. naf (optional, 12 digits, NEVER echo raw value)
    raw_naf = _get("naf")
    naf: str | None = None
    if raw_naf:
        try:
            naf = normalize_naf(raw_naf)
        except ValidationDomainError:
            errors.append(
                BulkImportErrorItem(
                    row_number,
                    "naf",
                    "INVALID_NAF",
                    "Invalid Spanish NAF (expected exactly 12 digits)",
                )
            )

    email = _get("email").lower()[:255]
    if email and "@" not in email:
        errors.append(
            BulkImportErrorItem(row_number, "email", "INVALID_EMAIL", "Invalid email format")
        )

    if errors:
        return None, errors

    return (
        ValidatedImportRow(
            row_number=row_number,
            employee_code=employee_code,
            full_name=full_name,
            preferred_name=_get("preferred_name")[:120],
            store_code=store_code,
            department=_get("department")[:100],
            job_title=_get("job_title")[:120],
            email=email,
            phone=_get("phone")[:40],
            birth_date=birth_date,
            contract_type=contract_type,
            work_schedule_type=work_schedule_type,
            contracted_weekly_hours=weekly_hours,
            annual_vacation_days=vacation_days,
            hire_date=hire_date,
            nie_dni=nie_dni,
            iban=iban,
            naf=naf,
        ),
        [],
    )
