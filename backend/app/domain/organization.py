"""Domain entities, value validations, and invariants for Tenant, Store, and Membership.

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import available_timezones

from app.domain.security import MembershipStatus, UserRole

SUPPORTED_LOCALES: frozenset[str] = frozenset({"zh-CN", "es-ES"})
_CIF_NIF_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]{4,30}[A-Z0-9]$")
_STORE_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,31}$")

_KNOWN_TIMEZONES: frozenset[str] = frozenset(available_timezones())
# Minimal fallback set if tzdata is absent in a stripped OS environment.
_FALLBACK_SPAIN_TIMEZONES: frozenset[str] = frozenset(
    {
        "UTC",
        "Europe/Madrid",
        "Atlantic/Canary",
        "Africa/Ceuta",
    }
)


class OrganizationDomainError(Exception):
    """Base exception for organization domain rule violations."""


class ValidationDomainError(OrganizationDomainError):
    """Raised when an input fails domain validation rules."""


class EntityNotFoundError(OrganizationDomainError):
    """Raised when a requested entity does not exist inside the caller's tenant."""


class ConflictError(OrganizationDomainError):
    """Raised when an operation violates a uniqueness constraint."""


class LastOwnerProtectionError(OrganizationDomainError):
    """Raised when an operation would leave a tenant with zero active owners."""


def validate_iana_timezone(tz_name: str) -> str:
    cleaned = tz_name.strip()
    if not cleaned:
        raise ValidationDomainError("Timezone cannot be empty")
    valid_zones = _KNOWN_TIMEZONES if _KNOWN_TIMEZONES else _FALLBACK_SPAIN_TIMEZONES
    if cleaned not in valid_zones:
        raise ValidationDomainError(f"Invalid IANA timezone: '{cleaned}'")
    return cleaned


def validate_locale(locale: str) -> str:
    cleaned = locale.strip()
    if cleaned not in SUPPORTED_LOCALES:
        raise ValidationDomainError(
            f"Unsupported locale '{cleaned}'. Expected one of: {sorted(SUPPORTED_LOCALES)}"
        )
    return cleaned


def normalize_cif_nif(raw: str) -> str:
    normalized = raw.strip().upper().replace(" ", "")
    if not _CIF_NIF_PATTERN.match(normalized):
        raise ValidationDomainError(
            "Invalid CIF/NIF format: expected 6-32 alphanumeric characters (e.g. B12345678)"
        )
    return normalized


def normalize_store_code(raw: str) -> str:
    normalized = raw.strip().upper().replace(" ", "-")
    if not _STORE_CODE_PATTERN.match(normalized):
        raise ValidationDomainError(
            "Invalid store code: expected 1-32 alphanumeric characters, '-' or '_'"
        )
    return normalized


def validate_non_empty_text(raw: str, *, field: str, max_length: int) -> str:
    cleaned = raw.strip()
    if not cleaned:
        raise ValidationDomainError(f"{field} cannot be empty")
    if len(cleaned) > max_length:
        raise ValidationDomainError(f"{field} exceeds maximum length of {max_length}")
    return cleaned


@dataclass(frozen=True, slots=True)
class Tenant:
    id: UUID
    legal_name: str
    trade_name: str
    cif_nif: str
    default_timezone: str
    default_locale: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Store:
    id: UUID
    tenant_id: UUID
    name: str
    code: str
    timezone: str
    address: str
    city: str
    postal_code: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    region_code: str = ""
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Membership:
    id: UUID
    tenant_id: UUID
    user_id: UUID
    email: str
    display_name: str
    role: UserRole
    status: MembershipStatus
    store_ids: frozenset[UUID]
    created_at: datetime
    updated_at: datetime
    department_scope: str | None = None
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TenantMembershipSummary:
    """Lightweight projection used by /auth/me to list tenants available to a user."""

    tenant_id: UUID
    tenant_legal_name: str
    tenant_trade_name: str
    default_timezone: str
    default_locale: str
    role: UserRole
    status: MembershipStatus
    store_ids: frozenset[UUID]
    department_scope: str | None = None
    display_name: str = ""
