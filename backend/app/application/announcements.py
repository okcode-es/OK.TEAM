"""Application use cases for Company Announcements & Mandatory Acknowledgements."""

from __future__ import annotations

import contextlib
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.announcements import (
    Announcement,
    AnnouncementAcknowledgement,
    AnnouncementCategory,
    build_acknowledgement_statement,
    compute_announcement_content_sha256,
    compute_announcement_retention_until,
    is_employee_in_announcement_scope,
)
from app.domain.documents import (
    ComplianceDocument,
    DocumentCategory,
    DocumentStoragePort,
    SignedUrlResult,
    build_storage_key,
    compute_document_retention_until,
    sanitize_filename,
    validate_document_upload,
    validate_signed_url_ttl,
)
from app.domain.employees import Employee
from app.domain.organization import (
    ConflictError,
    EntityNotFoundError,
    validate_non_empty_text,
)
from app.domain.repositories import (
    AnnouncementRepository,
    DocumentRepository,
    EmployeeRepository,
    StoreRepository,
)
from app.domain.security import AuthenticatedPrincipal, ForbiddenScopeError, UserRole

_ANNOUNCE_WRITE_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.OWNER, UserRole.HR, UserRole.STORE_MANAGER}
)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def _clean_header_value(raw: str, *, fallback: str, max_len: int) -> str:
    cleaned = _CONTROL_CHARS.sub(" ", raw).strip()
    return (cleaned or fallback)[:max_len]


@dataclass(frozen=True, slots=True)
class AnnouncementViewItem:
    announcement: Announcement
    acknowledged_count: int
    target_count: int
    acknowledged_by_self: bool
    acknowledged_at_utc: datetime | None


@dataclass(frozen=True, slots=True)
class CreateAnnouncementCommand:
    category: AnnouncementCategory
    title: str
    body: str
    store_id: UUID | None = None
    department_scope: str | None = None
    requires_acknowledgement: bool = False
    is_pinned: bool = False
    attachment_filename: str | None = None
    attachment_mime_type: str | None = None
    attachment_content: bytes | None = None


@dataclass(frozen=True, slots=True)
class AcknowledgementStatusEmployee:
    employee_id: UUID
    employee_code: str
    full_name: str
    department: str
    acknowledged_at_utc: datetime | None
    statement: str | None


@dataclass(frozen=True, slots=True)
class AnnouncementAcknowledgementsReport:
    announcement_id: UUID
    total_target_count: int
    acknowledged_count: int
    pending_count: int
    acknowledged: tuple[AcknowledgementStatusEmployee, ...]
    pending: tuple[AcknowledgementStatusEmployee, ...]


async def _resolve_self_employee(
    employee_repo: EmployeeRepository,
    tenant_id: UUID,
    user_id: UUID,
) -> Employee | None:
    matches = await employee_repo.list_by_tenant(tenant_id, only_user_id=user_id)
    return matches[0] if matches else None


def _can_principal_view_announcement(
    principal: AuthenticatedPrincipal,
    ann: Announcement,
    self_emp: Employee | None,
) -> bool:
    if principal.role in (UserRole.OWNER, UserRole.HR, UserRole.ACCOUNTANT):
        return True
    if principal.role == UserRole.STORE_MANAGER:
        if ann.store_id is not None and ann.store_id not in principal.store_ids:
            return False
        return not (
            principal.department_scope
            and principal.department_scope.strip()
            and ann.department_scope
            and ann.department_scope.strip()
            and ann.department_scope.strip().casefold()
            != principal.department_scope.strip().casefold()
        )
    if self_emp is None:
        return ann.store_id is None and not (ann.department_scope and ann.department_scope.strip())
    return is_employee_in_announcement_scope(ann, self_emp)


class ListAnnouncementsUseCase:
    def __init__(
        self,
        ann_repo: AnnouncementRepository,
        employee_repo: EmployeeRepository,
    ) -> None:
        self._ann_repo = ann_repo
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        category: AnnouncementCategory | None = None,
        pending_only: bool = False,
    ) -> list[AnnouncementViewItem]:
        all_anns = await self._ann_repo.list_by_tenant(principal.tenant_id, category=category)
        emps = await self._employee_repo.list_by_tenant(principal.tenant_id)
        self_emp = next((e for e in emps if e.user_id == principal.user_id), None)

        visible = [a for a in all_anns if _can_principal_view_announcement(principal, a, self_emp)]
        if not visible:
            return []

        acks = await self._ann_repo.list_acknowledgements_for_announcements(
            principal.tenant_id, [a.id for a in visible]
        )
        acks_by_ann: dict[UUID, list[AnnouncementAcknowledgement]] = defaultdict(list)
        for ack in acks:
            acks_by_ann[ack.announcement_id].append(ack)

        result: list[AnnouncementViewItem] = []
        for ann in visible:
            target_emps = [e for e in emps if is_employee_in_announcement_scope(ann, e)]
            ann_acks = acks_by_ann.get(ann.id, [])
            self_ack = next(
                (a for a in ann_acks if self_emp is not None and a.employee_id == self_emp.id),
                None,
            )
            is_acked_by_self = self_ack is not None
            if pending_only and (not ann.requires_acknowledgement or is_acked_by_self):
                continue
            result.append(
                AnnouncementViewItem(
                    announcement=ann,
                    acknowledged_count=len(ann_acks),
                    target_count=len(target_emps),
                    acknowledged_by_self=is_acked_by_self,
                    acknowledged_at_utc=self_ack.acknowledged_at_utc if self_ack else None,
                )
            )
        return result


class CreateAnnouncementUseCase:
    def __init__(
        self,
        ann_repo: AnnouncementRepository,
        store_repo: StoreRepository,
        doc_repo: DocumentRepository,
        storage: DocumentStoragePort,
    ) -> None:
        self._ann_repo = ann_repo
        self._store_repo = store_repo
        self._doc_repo = doc_repo
        self._storage = storage

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        cmd: CreateAnnouncementCommand,
        *,
        now_utc: datetime,
    ) -> Announcement:
        if principal.role not in _ANNOUNCE_WRITE_ROLES:
            raise ForbiddenScopeError(
                "Only owners, HR, or scoped store managers may publish announcements"
            )

        dept_scope = cmd.department_scope.strip()[:100] if cmd.department_scope else None
        if dept_scope == "":
            dept_scope = None

        if principal.role == UserRole.STORE_MANAGER:
            if cmd.store_id is None:
                raise ForbiddenScopeError(
                    "Store managers must scope announcements to an authorized store_id"
                )
            principal.require_store_access(principal.tenant_id, cmd.store_id)
            if (
                principal.department_scope
                and principal.department_scope.strip()
                and (
                    dept_scope is None
                    or dept_scope.casefold() != principal.department_scope.strip().casefold()
                )
            ):
                raise ForbiddenScopeError(
                    "Store manager with department_scope must publish within that department"
                )

        if cmd.store_id is not None:
            store = await self._store_repo.get_by_id(principal.tenant_id, cmd.store_id)
            if store is None:
                raise EntityNotFoundError(f"Store {cmd.store_id} not found")

        title = validate_non_empty_text(cmd.title, field="title", max_length=200)
        body = validate_non_empty_text(cmd.body, field="body", max_length=10000)
        now = now_utc.astimezone(UTC)

        attachment_doc_id: UUID | None = None
        attachment_sha256: str | None = None
        uploaded_key: str | None = None

        if cmd.attachment_content is not None and len(cmd.attachment_content) > 0:
            mime = cmd.attachment_mime_type or "application/octet-stream"
            attachment_sha256 = validate_document_upload(mime, cmd.attachment_content)
            safe_fn = sanitize_filename(cmd.attachment_filename or "attachment")
            doc_id = uuid4()
            storage_key = build_storage_key(principal.tenant_id, doc_id)
            await self._storage.put_object(
                storage_key=storage_key,
                content=cmd.attachment_content,
                mime_type=mime,
            )
            uploaded_key = storage_key
            doc = ComplianceDocument(
                id=doc_id,
                tenant_id=principal.tenant_id,
                store_id=cmd.store_id,
                employee_id=None,
                category=DocumentCategory.OTHER,
                title=f"Announcement: {title}"[:200],
                period_month=None,
                storage_backend=self._storage.backend_name,
                storage_key=storage_key,
                original_filename=safe_fn,
                mime_type=mime,
                size_bytes=len(cmd.attachment_content),
                sha256_hex=attachment_sha256,
                requires_signature=False,
                uploaded_by_user_id=principal.user_id,
                retention_until=compute_document_retention_until(now),
                created_at=now,
                updated_at=now,
            )
            try:
                saved_doc = await self._doc_repo.create(doc)
                attachment_doc_id = saved_doc.id
            except Exception:
                with contextlib.suppress(Exception):
                    await self._storage.delete_object(storage_key=storage_key)
                raise

        content_sha256 = compute_announcement_content_sha256(title, body, attachment_sha256)
        ann = Announcement(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            store_id=cmd.store_id,
            department_scope=dept_scope,
            category=cmd.category,
            title=title,
            body=body,
            attachment_document_id=attachment_doc_id,
            content_sha256_hex=content_sha256,
            requires_acknowledgement=cmd.requires_acknowledgement,
            is_pinned=cmd.is_pinned,
            published_by_user_id=principal.user_id,
            published_at_utc=now,
            retention_until=compute_announcement_retention_until(now),
            created_at=now,
            updated_at=now,
        )
        try:
            return await self._ann_repo.create(ann)
        except Exception:
            if uploaded_key:
                with contextlib.suppress(Exception):
                    await self._storage.delete_object(storage_key=uploaded_key)
            raise


class GetAnnouncementUseCase:
    def __init__(
        self,
        ann_repo: AnnouncementRepository,
        employee_repo: EmployeeRepository,
    ) -> None:
        self._ann_repo = ann_repo
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        announcement_id: UUID,
    ) -> AnnouncementViewItem:
        ann = await self._ann_repo.get_by_id(principal.tenant_id, announcement_id)
        if ann is None:
            raise EntityNotFoundError(f"Announcement {announcement_id} not found")

        emps = await self._employee_repo.list_by_tenant(principal.tenant_id)
        self_emp = next((e for e in emps if e.user_id == principal.user_id), None)
        if not _can_principal_view_announcement(principal, ann, self_emp):
            raise ForbiddenScopeError("Announcement is outside your authorized scope")

        acks = await self._ann_repo.list_acknowledgements_for_announcement(
            principal.tenant_id, ann.id
        )
        target_emps = [e for e in emps if is_employee_in_announcement_scope(ann, e)]
        self_ack = (
            next((a for a in acks if self_emp is not None and a.employee_id == self_emp.id), None)
        )
        return AnnouncementViewItem(
            announcement=ann,
            acknowledged_count=len(acks),
            target_count=len(target_emps),
            acknowledged_by_self=self_ack is not None,
            acknowledged_at_utc=self_ack.acknowledged_at_utc if self_ack else None,
        )


class AcknowledgeAnnouncementUseCase:
    def __init__(
        self,
        ann_repo: AnnouncementRepository,
        employee_repo: EmployeeRepository,
    ) -> None:
        self._ann_repo = ann_repo
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        announcement_id: UUID,
        ip_address: str,
        user_agent: str,
        now_utc: datetime,
    ) -> AnnouncementAcknowledgement:
        ann = await self._ann_repo.get_by_id(principal.tenant_id, announcement_id)
        if ann is None:
            raise EntityNotFoundError(f"Announcement {announcement_id} not found")

        self_emp = await _resolve_self_employee(
            self._employee_repo, principal.tenant_id, principal.user_id
        )
        if self_emp is None:
            raise ForbiddenScopeError("Acknowledger must have a linked employee profile")
        if not is_employee_in_announcement_scope(ann, self_emp):
            raise ForbiddenScopeError("Announcement is outside your target audience scope")

        existing = await self._ann_repo.get_acknowledgement(
            principal.tenant_id, ann.id, self_emp.id
        )
        if existing is not None:
            raise ConflictError("Announcement already acknowledged by this employee")

        now = now_utc.astimezone(UTC)
        ack = AnnouncementAcknowledgement(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            announcement_id=ann.id,
            employee_id=self_emp.id,
            acknowledged_by_user_id=principal.user_id,
            content_sha256_hex=ann.content_sha256_hex,
            acknowledged_at_utc=now,
            ip_address=_clean_header_value(ip_address, fallback="unknown", max_len=64),
            user_agent=_clean_header_value(user_agent, fallback="unknown", max_len=512),
            statement=build_acknowledgement_statement(ann, self_emp, now),
            retention_until=compute_announcement_retention_until(now),
        )
        return await self._ann_repo.append_acknowledgement(ack)


class ListAnnouncementAcknowledgementsUseCase:
    def __init__(
        self,
        ann_repo: AnnouncementRepository,
        employee_repo: EmployeeRepository,
    ) -> None:
        self._ann_repo = ann_repo
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        announcement_id: UUID,
    ) -> AnnouncementAcknowledgementsReport:
        if principal.role not in _ANNOUNCE_WRITE_ROLES:
            raise ForbiddenScopeError(
                "Only owners, HR, or scoped store managers may view acknowledgement reports"
            )

        ann = await self._ann_repo.get_by_id(principal.tenant_id, announcement_id)
        if ann is None:
            raise EntityNotFoundError(f"Announcement {announcement_id} not found")

        if principal.role == UserRole.STORE_MANAGER:
            if ann.store_id is None or ann.store_id not in principal.store_ids:
                raise ForbiddenScopeError("Store manager lacks access to this announcement's store")
            if (
                principal.department_scope
                and principal.department_scope.strip()
                and ann.department_scope
                and ann.department_scope.strip()
                and ann.department_scope.strip().casefold()
                != principal.department_scope.strip().casefold()
            ):
                raise ForbiddenScopeError(
                    "Store manager lacks access to this announcement's department"
                )

        emps = await self._employee_repo.list_by_tenant(principal.tenant_id)
        target_emps = [
            e
            for e in emps
            if is_employee_in_announcement_scope(ann, e) and principal.can_manage_employee(e)
        ]
        acks = await self._ann_repo.list_acknowledgements_for_announcement(
            principal.tenant_id, ann.id
        )
        ack_by_emp = {a.employee_id: a for a in acks}

        acknowledged: list[AcknowledgementStatusEmployee] = []
        pending: list[AcknowledgementStatusEmployee] = []
        for emp in target_emps:
            ack = ack_by_emp.get(emp.id)
            item = AcknowledgementStatusEmployee(
                employee_id=emp.id,
                employee_code=emp.employee_code,
                full_name=emp.full_name,
                department=emp.department,
                acknowledged_at_utc=ack.acknowledged_at_utc if ack else None,
                statement=ack.statement if ack else None,
            )
            if ack is not None:
                acknowledged.append(item)
            else:
                pending.append(item)

        return AnnouncementAcknowledgementsReport(
            announcement_id=ann.id,
            total_target_count=len(target_emps),
            acknowledged_count=len(acknowledged),
            pending_count=len(pending),
            acknowledged=tuple(acknowledged),
            pending=tuple(pending),
        )


class CreateAnnouncementAttachmentSignedUrlUseCase:
    def __init__(
        self,
        ann_repo: AnnouncementRepository,
        employee_repo: EmployeeRepository,
        doc_repo: DocumentRepository,
        storage: DocumentStoragePort,
    ) -> None:
        self._ann_repo = ann_repo
        self._employee_repo = employee_repo
        self._doc_repo = doc_repo
        self._storage = storage

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        announcement_id: UUID,
        ttl_seconds: int = 60,
    ) -> tuple[SignedUrlResult, str]:
        ttl = validate_signed_url_ttl(ttl_seconds)
        ann = await self._ann_repo.get_by_id(principal.tenant_id, announcement_id)
        if ann is None:
            raise EntityNotFoundError(f"Announcement {announcement_id} not found")

        emps = await self._employee_repo.list_by_tenant(principal.tenant_id)
        self_emp = next((e for e in emps if e.user_id == principal.user_id), None)
        if not _can_principal_view_announcement(principal, ann, self_emp):
            raise ForbiddenScopeError("Announcement is outside your authorized scope")

        if ann.attachment_document_id is None:
            raise EntityNotFoundError("Announcement has no attachment")

        doc = await self._doc_repo.get_by_id(principal.tenant_id, ann.attachment_document_id)
        if doc is None:
            raise EntityNotFoundError("Announcement attachment document not found")

        signed = await self._storage.create_signed_url(
            storage_key=doc.storage_key,
            ttl_seconds=ttl,
            download_filename=doc.original_filename,
            mime_type=doc.mime_type,
        )
        return signed, doc.sha256_hex
