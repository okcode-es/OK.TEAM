"""Pure domain logic for Company Announcements & Immutable Acknowledgements.

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from app.domain.employees import STATUTORY_RETENTION_YEARS, Employee, EmploymentStatus


class AnnouncementCategory(StrEnum):
    NOTICE = "notice"
    POLICY = "policy"


@dataclass(frozen=True, slots=True)
class Announcement:
    id: UUID
    tenant_id: UUID
    store_id: UUID | None
    department_scope: str | None
    category: AnnouncementCategory
    title: str
    body: str
    attachment_document_id: UUID | None
    content_sha256_hex: str
    requires_acknowledgement: bool
    is_pinned: bool
    published_by_user_id: UUID
    published_at_utc: datetime
    retention_until: datetime
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AnnouncementAcknowledgement:
    id: UUID
    tenant_id: UUID
    announcement_id: UUID
    employee_id: UUID
    acknowledged_by_user_id: UUID
    content_sha256_hex: str
    acknowledged_at_utc: datetime
    ip_address: str
    user_agent: str
    statement: str
    retention_until: datetime


def compute_announcement_content_sha256(
    title: str,
    body: str,
    attachment_sha256_hex: str | None = None,
) -> str:
    """Compute canonical SHA-256 hex digest over announcement title, body, and attachment."""
    payload = f"{title}\n{body}\n{attachment_sha256_hex or ''}".encode()
    return hashlib.sha256(payload).hexdigest()


def compute_announcement_retention_until(published_at_utc: datetime) -> datetime:
    """Compute statutory 4-year retention expiry in UTC."""
    anchor = published_at_utc.astimezone(UTC)
    target_year = anchor.year + STATUTORY_RETENTION_YEARS
    try:
        return anchor.replace(year=target_year)
    except ValueError:
        return anchor.replace(year=target_year, day=28)


def build_acknowledgement_statement(
    announcement: Announcement,
    employee: Employee,
    acknowledged_at_utc: datetime,
) -> str:
    statement = (
        f"Acknowledgement / Acuse de lectura: {employee.full_name} ({employee.employee_code}) "
        f"confirmed '{announcement.title}' [{announcement.category.value}] "
        f"SHA-256={announcement.content_sha256_hex} at "
        f"{acknowledged_at_utc.astimezone(UTC).isoformat()}."
    )
    return statement[:500]


def is_employee_in_announcement_scope(announcement: Announcement, employee: Employee) -> bool:
    """Return True if an active/on_leave employee is in the announcement's target audience."""
    if employee.deleted_at is not None:
        return False
    if employee.employment_status not in (EmploymentStatus.ACTIVE, EmploymentStatus.ON_LEAVE):
        return False
    if announcement.store_id is not None and employee.primary_store_id != announcement.store_id:
        return False
    if announcement.department_scope and announcement.department_scope.strip():
        return (
            employee.department.strip().casefold()
            == announcement.department_scope.strip().casefold()
        )
    return True
