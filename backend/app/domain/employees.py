"""Domain entities, Spanish contract invariants, PII masking, and 4-year retention rules.

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.domain.organization import OrganizationDomainError, ValidationDomainError

STATUTORY_MAX_WEEKLY_HOURS = Decimal("40.00")
STATUTORY_MIN_VACATION_DAYS = 30
STATUTORY_RETENTION_YEARS = 4

_EMPLOYEE_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,31}$")
_DNI_PATTERN = re.compile(r"^[0-9]{8}[A-Z]$")
_NIE_PATTERN = re.compile(r"^[XYZ][0-9]{7}[A-Z]$")
_DNI_CHECKSUM_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_NIE_PREFIX_MAP = {"X": "0", "Y": "1", "Z": "2"}
_IBAN_PATTERN = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
_ES_IBAN_PATTERN = re.compile(r"^ES[0-9]{22}$")
_NAF_PATTERN = re.compile(r"^[0-9]{12}$")


class ContractType(StrEnum):
    INDEFINIDO = "indefinido"
    TEMPORAL = "temporal"
    FIJO_DISCONTINUO = "fijo_discontinuo"
    PRACTICAS = "practicas"


class WorkScheduleType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"


class EmploymentStatus(StrEnum):
    ACTIVE = "active"
    ON_LEAVE = "on_leave"
    TERMINATED = "terminated"
    LEGAL_HOLD = "legal_hold"
    ANONYMIZED = "anonymized"


class RetentionLockError(OrganizationDomainError):
    """Raised when attempting to anonymize PII before statutory 4-year retention expires."""


class FrozenRecordError(OrganizationDomainError):
    """Raised when attempting to mutate an employee record that is frozen or anonymized."""


class PiiCipher(Protocol):
    """Abstract application-layer PII cipher (AES-256-GCM + blind index in infrastructure)."""

    def encrypt(
        self,
        plaintext: str,
        *,
        tenant_id: UUID,
        employee_id: UUID,
        field_name: str,
    ) -> str: ...

    def decrypt(
        self,
        ciphertext: str,
        *,
        tenant_id: UUID,
        employee_id: UUID,
        field_name: str,
    ) -> str: ...

    def blind_index(self, normalized_value: str, *, tenant_id: UUID) -> str: ...


def normalize_employee_code(raw: str) -> str:
    normalized = raw.strip().upper().replace(" ", "-")
    if not _EMPLOYEE_CODE_PATTERN.match(normalized):
        raise ValidationDomainError(
            "Invalid employee_code: expected 1-32 alphanumeric characters, '-' or '_'"
        )
    return normalized


def normalize_nie_dni(raw: str) -> str:
    cleaned = re.sub(r"[\s-]+", "", raw.strip().upper())
    if _DNI_PATTERN.match(cleaned):
        number = int(cleaned[:8])
        expected = _DNI_CHECKSUM_LETTERS[number % 23]
        if cleaned[8] != expected:
            raise ValidationDomainError("Invalid Spanish DNI checksum letter")
        return cleaned
    if _NIE_PATTERN.match(cleaned):
        mapped = _NIE_PREFIX_MAP[cleaned[0]] + cleaned[1:8]
        number = int(mapped)
        expected = _DNI_CHECKSUM_LETTERS[number % 23]
        if cleaned[8] != expected:
            raise ValidationDomainError("Invalid Spanish NIE checksum letter")
        return cleaned
    raise ValidationDomainError(
        "Invalid NIE/DNI format: expected 8 digits + letter (DNI) or X/Y/Z + 7 digits + letter"
    )


def normalize_iban(raw: str) -> str:
    cleaned = re.sub(r"[\s-]+", "", raw.strip().upper())
    if not _IBAN_PATTERN.match(cleaned):
        raise ValidationDomainError("Invalid IBAN format")
    if cleaned.startswith("ES") and not _ES_IBAN_PATTERN.match(cleaned):
        raise ValidationDomainError("Spanish IBAN must be exactly 24 characters (ES + 22 digits)")
    rearranged = cleaned[4:] + cleaned[:4]
    numeric = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in rearranged)
    if int(numeric) % 97 != 1:
        raise ValidationDomainError("Invalid IBAN checksum (ISO 7064 Mod-97)")
    return cleaned


def normalize_naf(raw: str) -> str:
    cleaned = re.sub(r"[\s/-]+", "", raw.strip())
    if not _NAF_PATTERN.match(cleaned):
        raise ValidationDomainError(
            "Invalid Spanish NAF (Social Security number): expected exactly 12 digits"
        )
    return cleaned


def mask_nie_dni(normalized: str) -> str:
    if len(normalized) <= 4:
        return "****"
    return f"*****{normalized[-4:]}"


def mask_iban(normalized: str) -> str:
    if len(normalized) <= 8:
        return "****"
    return f"{normalized[:4]} **** **** {normalized[-4:]}"


def mask_naf(normalized: str) -> str:
    if len(normalized) <= 4:
        return "****"
    return f"{normalized[:2]}******{normalized[-4:]}"


def validate_contracted_weekly_hours(
    hours: Decimal,
    schedule_type: WorkScheduleType,
) -> Decimal:
    quantized = hours.quantize(Decimal("0.01"))
    if quantized <= Decimal("0.00"):
        raise ValidationDomainError("contracted_weekly_hours must be greater than 0")
    if quantized > STATUTORY_MAX_WEEKLY_HOURS:
        raise ValidationDomainError(
            "contracted_weekly_hours cannot exceed Spanish statutory 40.00h/week (Art. 34.1 ET)"
        )
    if schedule_type == WorkScheduleType.PART_TIME and quantized >= STATUTORY_MAX_WEEKLY_HOURS:
        raise ValidationDomainError(
            "part_time employees must have contracted_weekly_hours strictly below 40.00h"
        )
    return quantized


def validate_annual_vacation_days(days: int) -> int:
    if days < STATUTORY_MIN_VACATION_DAYS:
        raise ValidationDomainError(
            "annual_vacation_days cannot be below Spanish statutory minimum of 30 natural days "
            "(Art. 38 ET)"
        )
    if days > 90:
        raise ValidationDomainError("annual_vacation_days cannot exceed 90 days")
    return days


def compute_retention_until(termination_date: date, now_utc: datetime) -> datetime:
    """Compute statutory >= 4-year legal hold expiry in UTC (handling leap days safely)."""
    term_eod = datetime(
        termination_date.year,
        termination_date.month,
        termination_date.day,
        23,
        59,
        59,
        tzinfo=UTC,
    )
    anchor = max(term_eod, now_utc.astimezone(UTC))
    target_year = anchor.year + STATUTORY_RETENTION_YEARS
    try:
        return anchor.replace(year=target_year)
    except ValueError:
        # Feb 29 -> Feb 28 four years later if target year is non-leap
        return anchor.replace(year=target_year, day=28)


@dataclass(frozen=True, slots=True)
class Employee:
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
    hire_date: date
    termination_date: date | None
    nie_dni_enc: str | None
    nie_dni_masked: str | None
    nie_dni_blind_index: str | None
    iban_enc: str | None
    iban_masked: str | None
    naf_enc: str | None
    naf_masked: str | None
    employment_status: EmploymentStatus
    retention_until: datetime | None
    anonymized_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @property
    def is_overtime_prohibited(self) -> bool:
        """Art. 12.4.c ET: part-time employees are prohibited from regular overtime."""
        return self.work_schedule_type == WorkScheduleType.PART_TIME


@dataclass(frozen=True, slots=True)
class EmployeeSensitivePii:
    employee_id: UUID
    nie_dni: str | None = field(repr=False)
    iban: str | None = field(repr=False)
    naf: str | None = field(repr=False)
