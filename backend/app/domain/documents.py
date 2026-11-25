"""Pure domain logic for Private Compliance Documents & Immutable SHA-256 Receipts.

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.domain.employees import STATUTORY_RETENTION_YEARS, Employee, RetentionLockError
from app.domain.organization import ValidationDomainError

MAX_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024  # 10 MiB
MAX_SIGNED_URL_TTL_SECONDS = 60
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)

_PERIOD_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_UNSAFE_FILENAME_CHARS = re.compile(r'[\x00-\x1f\x7f"\\/;]+')


class DocumentCategory(StrEnum):
    CONTRACT = "contract"
    PAYSLIP = "payslip"
    ATTENDANCE_REPORT = "attendance_report"
    MEDICAL_LEAVE = "medical_leave"
    ID_DOCUMENT = "id_document"
    OTHER = "other"


class StorageBackend(StrEnum):
    LOCAL = "local"
    SUPABASE = "supabase"


STORE_MANAGER_RESTRICTED_CATEGORIES: frozenset[DocumentCategory] = frozenset(
    {
        DocumentCategory.CONTRACT,
        DocumentCategory.PAYSLIP,
        DocumentCategory.ID_DOCUMENT,
    }
)

STORE_MANAGER_UPLOAD_CATEGORIES: frozenset[DocumentCategory] = frozenset(
    {
        DocumentCategory.ATTENDANCE_REPORT,
        DocumentCategory.MEDICAL_LEAVE,
        DocumentCategory.OTHER,
    }
)


@dataclass(frozen=True, slots=True)
class ComplianceDocument:
    id: UUID
    tenant_id: UUID
    store_id: UUID | None
    employee_id: UUID | None
    category: DocumentCategory
    title: str
    period_month: str | None
    storage_backend: StorageBackend
    storage_key: str
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256_hex: str
    requires_signature: bool
    uploaded_by_user_id: UUID
    retention_until: datetime
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DocumentReceipt:
    id: UUID
    tenant_id: UUID
    document_id: UUID
    employee_id: UUID
    signed_by_user_id: UUID
    document_sha256_hex: str
    signed_at_utc: datetime
    ip_address: str
    user_agent: str
    receipt_statement: str
    retention_until: datetime


@dataclass(frozen=True, slots=True)
class SignedUrlResult:
    url: str
    expires_at_utc: datetime
    ttl_seconds: int


class DocumentStoragePort(Protocol):
    """Port for private object storage (local volume or EU Supabase Private Bucket)."""

    backend_name: StorageBackend

    async def put_object(self, *, storage_key: str, content: bytes, mime_type: str) -> None: ...

    async def create_signed_url(
        self,
        *,
        storage_key: str,
        ttl_seconds: int,
        download_filename: str,
        mime_type: str,
    ) -> SignedUrlResult: ...

    async def delete_object(self, *, storage_key: str) -> None: ...


def sanitize_filename(raw_filename: str) -> str:
    """Strip path traversal, control chars, quotes, and newlines from uploaded filenames."""
    base = raw_filename.replace("\\", "/").split("/")[-1]
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", base).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return "document"
    return cleaned[:200]


def validate_period_month(period_month: str | None) -> str | None:
    if period_month is None:
        return None
    cleaned = period_month.strip()
    if not cleaned:
        return None
    if not _PERIOD_MONTH_PATTERN.match(cleaned):
        raise ValidationDomainError("period_month must match YYYY-MM format")
    return cleaned


def validate_file_magic(mime_type: str, content: bytes) -> None:
    """Validate MIME whitelist and magic-bytes signature to block spoofed uploads."""
    if mime_type not in ALLOWED_MIME_TYPES:
        raise ValidationDomainError(
            f"Unsupported mime_type '{mime_type}'. Allowed: {sorted(ALLOWED_MIME_TYPES)}"
        )
    if not content:
        raise ValidationDomainError("Uploaded file cannot be empty")
    if len(content) > MAX_DOCUMENT_SIZE_BYTES:
        raise ValidationDomainError(
            f"Uploaded file exceeds maximum size of {MAX_DOCUMENT_SIZE_BYTES} bytes (10 MiB)"
        )

    valid = False
    if mime_type == "application/pdf":
        valid = content.startswith(b"%PDF-")
    elif mime_type == "image/jpeg":
        valid = content.startswith(b"\xff\xd8\xff")
    elif mime_type == "image/png":
        valid = content.startswith(b"\x89PNG\r\n\x1a\n")
    elif mime_type == "image/webp":
        valid = len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"

    if not valid:
        raise ValidationDomainError(
            f"File content magic bytes do not match declared MIME type '{mime_type}'"
        )


def validate_document_upload(mime_type: str, content: bytes) -> str:
    """Validate uploaded bytes and return server-computed SHA-256 hex digest."""
    validate_file_magic(mime_type, content)
    return hashlib.sha256(content).hexdigest()


def validate_signed_url_ttl(ttl_seconds: int) -> int:
    """Enforce hard <= 60s signed URL lifetime (Data & Security Groundwork 4)."""
    if ttl_seconds < 1 or ttl_seconds > MAX_SIGNED_URL_TTL_SECONDS:
        raise ValidationDomainError(
            f"ttl_seconds must be between 1 and {MAX_SIGNED_URL_TTL_SECONDS}"
        )
    return ttl_seconds


def build_storage_key(tenant_id: UUID, document_id: UUID) -> str:
    """UUID-only storage key immune to path traversal."""
    return f"{tenant_id}/{document_id}"


def compute_document_retention_until(created_at_utc: datetime) -> datetime:
    """Compute statutory 4-year retention expiry from UTC timestamp."""
    anchor = created_at_utc.astimezone(UTC)
    target_year = anchor.year + STATUTORY_RETENTION_YEARS
    try:
        return anchor.replace(year=target_year)
    except ValueError:
        return anchor.replace(year=target_year, day=28)


def build_receipt_statement(
    document: ComplianceDocument,
    employee: Employee,
    signed_at_utc: datetime,
) -> str:
    statement = (
        f"Electronic receipt / Acuse de recibo: {employee.full_name} ({employee.employee_code}) "
        f"confirmed '{document.title}' [{document.category.value}] "
        f"SHA-256={document.sha256_hex} at {signed_at_utc.astimezone(UTC).isoformat()}."
    )
    return statement[:500]


def ensure_document_deletable(
    document: ComplianceDocument,
    *,
    receipts_count: int,
    now_utc: datetime,
) -> None:
    """Prevent deleting documents with receipts or active 4-year statutory retention."""
    if receipts_count > 0:
        raise RetentionLockError(
            "Cannot delete document with existing immutable electronic receipts"
        )
    if document.retention_until > now_utc.astimezone(UTC):
        raise RetentionLockError(
            "Document is under statutory retention lock until "
            f"{document.retention_until.isoformat()}"
        )
