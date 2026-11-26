"""Application use cases for Private Compliance Documents & SHA-256 Receipts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.announcements import _can_principal_view_announcement
from app.domain.documents import (
    STORE_MANAGER_RESTRICTED_CATEGORIES,
    STORE_MANAGER_UPLOAD_CATEGORIES,
    ComplianceDocument,
    DocumentCategory,
    DocumentReceipt,
    DocumentStoragePort,
    SignedUrlResult,
    build_receipt_statement,
    build_storage_key,
    compute_document_retention_until,
    ensure_document_deletable,
    sanitize_filename,
    validate_document_upload,
    validate_period_month,
    validate_signed_url_ttl,
)
from app.domain.employees import Employee, EmploymentStatus
from app.domain.organization import (
    ConflictError,
    EntityNotFoundError,
    ValidationDomainError,
    validate_non_empty_text,
)
from app.domain.repositories import (
    AnnouncementRepository,
    DocumentRepository,
    EmployeeRepository,
    StoreRepository,
)
from app.domain.security import AuthenticatedPrincipal, ForbiddenScopeError, UserRole


@dataclass(frozen=True, slots=True)
class DocumentListItem:
    document: ComplianceDocument
    receipt_count: int
    signed_by_self: bool
    latest_signed_at_utc: datetime | None


@dataclass(frozen=True, slots=True)
class DocumentDetailResult:
    document: ComplianceDocument
    receipts: list[DocumentReceipt]


@dataclass(frozen=True, slots=True)
class UploadDocumentCommand:
    filename: str
    mime_type: str
    content: bytes
    category: DocumentCategory
    title: str
    employee_id: UUID | None = None
    store_id: UUID | None = None
    period_month: str | None = None
    requires_signature: bool = False


@dataclass(frozen=True, slots=True)
class SignedUrlWithDigest:
    signed_url: SignedUrlResult
    sha256_hex: str


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def _clean_header_value(raw: str, *, fallback: str, max_len: int) -> str:
    cleaned = _CONTROL_CHARS.sub(" ", raw).strip()
    return (cleaned or fallback)[:max_len]


def _require_document_read_access(
    principal: AuthenticatedPrincipal,
    document: ComplianceDocument,
    employee: Employee | None,
    *,
    self_employee: Employee | None = None,
) -> None:
    if principal.role == UserRole.ACCOUNTANT:
        if document.category == DocumentCategory.MEDICAL_LEAVE:
            raise ForbiddenScopeError(
                "Accountants are not permitted to access medical_leave documents"
            )
        return

    if principal.role in (UserRole.OWNER, UserRole.HR):
        return

    if principal.role == UserRole.STORE_MANAGER:
        is_self_doc = self_employee is not None and document.employee_id == self_employee.id
        if employee is not None and employee.employment_status in (
            EmploymentStatus.LEGAL_HOLD,
            EmploymentStatus.ANONYMIZED,
        ):
            raise ForbiddenScopeError(
                "Store managers cannot access documents for legal_hold employees (Art. 32 LOPDGDD)"
            )
        if is_self_doc:
            return
        if document.category in STORE_MANAGER_RESTRICTED_CATEGORIES:
            raise ForbiddenScopeError(
                f"Store managers cannot access '{document.category.value}' documents"
            )
        if employee is not None:
            principal.require_employee_in_scope(employee)
        elif document.store_id is None or document.store_id not in principal.store_ids:
            raise ForbiddenScopeError("Store manager lacks access to this document's store")
        return

    # Employee role: own documents OR general 'other' policies in own store / tenant-wide
    if document.employee_id is None:
        if document.category == DocumentCategory.OTHER and (
            document.store_id is None
            or (self_employee is not None and document.store_id == self_employee.primary_store_id)
        ):
            return
        raise ForbiddenScopeError("Employees may only access general policies in their store")
    if employee is not None and employee.user_id == principal.user_id:
        return
    raise ForbiddenScopeError("Employees may only access their own compliance documents")


async def _resolve_self_employee(
    employee_repo: EmployeeRepository,
    tenant_id: UUID,
    user_id: UUID,
) -> Employee | None:
    matches = await employee_repo.list_by_tenant(tenant_id, only_user_id=user_id)
    return matches[0] if matches else None


class ListDocumentsUseCase:
    def __init__(
        self,
        doc_repo: DocumentRepository,
        employee_repo: EmployeeRepository,
        ann_repo: AnnouncementRepository | None = None,
    ) -> None:
        self._doc_repo = doc_repo
        self._employee_repo = employee_repo
        self._ann_repo = ann_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        category: DocumentCategory | None = None,
        employee_id: UUID | None = None,
        store_id: UUID | None = None,
        period_month: str | None = None,
        requires_signature: bool | None = None,
    ) -> list[DocumentListItem]:
        validated_period = validate_period_month(period_month)
        self_emp = await _resolve_self_employee(
            self._employee_repo, principal.tenant_id, principal.user_id
        )

        in_scope_ids: set[UUID] | None = None
        if principal.role == UserRole.EMPLOYEE:
            if self_emp is None:
                return []
            if employee_id is not None and employee_id != self_emp.id:
                raise ForbiddenScopeError("Employees may only list their own documents")
            docs = await self._doc_repo.list_by_tenant(
                principal.tenant_id,
                only_employee_id=self_emp.id,
                include_tenant_wide_for_employee=True,
                employee_store_id=self_emp.primary_store_id,
                category=category,
                period_month=validated_period,
                requires_signature=requires_signature,
            )
        elif principal.role == UserRole.STORE_MANAGER:
            emps = await self._employee_repo.list_by_tenant(principal.tenant_id)
            in_scope_ids = {e.id for e in emps if principal.can_manage_employee(e)}
            if self_emp is not None:
                in_scope_ids.add(self_emp.id)
            if store_id is not None:
                principal.require_store_access(principal.tenant_id, store_id)
            if category in STORE_MANAGER_RESTRICTED_CATEGORIES:
                if (
                    self_emp is None
                    or (employee_id is not None and employee_id != self_emp.id)
                    or self_emp.employment_status
                    in (EmploymentStatus.LEGAL_HOLD, EmploymentStatus.ANONYMIZED)
                ):
                    raise ForbiddenScopeError(
                        f"Store managers cannot access '{category.value}' documents"
                    )
                docs = await self._doc_repo.list_by_tenant(
                    principal.tenant_id,
                    only_employee_id=self_emp.id,
                    category=category,
                    store_id=store_id,
                    period_month=validated_period,
                    requires_signature=requires_signature,
                )
            else:
                docs = await self._doc_repo.list_by_tenant(
                    principal.tenant_id,
                    allowed_store_ids=principal.store_ids,
                    category=category,
                    employee_id=employee_id,
                    store_id=store_id,
                    period_month=validated_period,
                    requires_signature=requires_signature,
                    exclude_categories=STORE_MANAGER_RESTRICTED_CATEGORIES,
                )
                docs = [
                    d for d in docs if d.employee_id is None or d.employee_id in in_scope_ids
                ]
                if (
                    category is None
                    and self_emp is not None
                    and self_emp.employment_status
                    not in (EmploymentStatus.LEGAL_HOLD, EmploymentStatus.ANONYMIZED)
                    and (employee_id is None or employee_id == self_emp.id)
                ):
                    self_docs = await self._doc_repo.list_by_tenant(
                        principal.tenant_id,
                        only_employee_id=self_emp.id,
                        store_id=store_id,
                        period_month=validated_period,
                        requires_signature=requires_signature,
                    )
                    seen_ids = {d.id for d in docs}
                    for sd in self_docs:
                        if sd.id not in seen_ids:
                            docs.append(sd)
                            seen_ids.add(sd.id)
                    docs.sort(key=lambda d: d.created_at, reverse=True)
        elif principal.role == UserRole.ACCOUNTANT:
            if category == DocumentCategory.MEDICAL_LEAVE:
                raise ForbiddenScopeError(
                    "Accountants are not permitted to list medical_leave documents"
                )
            docs = await self._doc_repo.list_by_tenant(
                principal.tenant_id,
                category=category,
                employee_id=employee_id,
                store_id=store_id,
                period_month=validated_period,
                requires_signature=requires_signature,
                exclude_categories=frozenset({DocumentCategory.MEDICAL_LEAVE}),
            )
        else:
            docs = await self._doc_repo.list_by_tenant(
                principal.tenant_id,
                category=category,
                employee_id=employee_id,
                store_id=store_id,
                period_month=validated_period,
                requires_signature=requires_signature,
            )

        if self._ann_repo is not None and principal.role not in (UserRole.OWNER, UserRole.HR):
            anns = await self._ann_repo.list_by_tenant(principal.tenant_id)
            att_ann_map = {
                a.attachment_document_id: a
                for a in anns
                if a.attachment_document_id is not None
            }
            if att_ann_map:
                docs = [
                    d
                    for d in docs
                    if d.id not in att_ann_map
                    or _can_principal_view_announcement(principal, att_ann_map[d.id], self_emp)
                ]

        receipts = await self._doc_repo.list_receipts_for_documents(
            principal.tenant_id, [d.id for d in docs]
        )
        receipts_by_doc: dict[UUID, list[DocumentReceipt]] = {}
        for r in receipts:
            receipts_by_doc.setdefault(r.document_id, []).append(r)

        items: list[DocumentListItem] = []
        for doc in docs:
            doc_receipts = receipts_by_doc.get(doc.id, [])
            if principal.role == UserRole.EMPLOYEE and self_emp is not None:
                doc_receipts = [r for r in doc_receipts if r.employee_id == self_emp.id]
            elif principal.role == UserRole.STORE_MANAGER and in_scope_ids is not None:
                doc_receipts = [r for r in doc_receipts if r.employee_id in in_scope_ids]
            signed_by_self = (
                any(r.employee_id == self_emp.id for r in doc_receipts)
                if self_emp is not None
                else False
            )
            latest_signed = doc_receipts[-1].signed_at_utc if doc_receipts else None
            items.append(
                DocumentListItem(
                    document=doc,
                    receipt_count=len(doc_receipts),
                    signed_by_self=signed_by_self,
                    latest_signed_at_utc=latest_signed,
                )
            )
        return items


class UploadDocumentUseCase:
    def __init__(
        self,
        doc_repo: DocumentRepository,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        storage: DocumentStoragePort,
    ) -> None:
        self._doc_repo = doc_repo
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._storage = storage

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        cmd: UploadDocumentCommand,
        *,
        now_utc: datetime,
    ) -> ComplianceDocument:
        target_employee_id = cmd.employee_id
        target_store_id = cmd.store_id

        if principal.role == UserRole.ACCOUNTANT:
            if cmd.category != DocumentCategory.PAYSLIP:
                raise ForbiddenScopeError(
                    "Accountants may only upload 'payslip' documents"
                )
            if cmd.mime_type != "application/pdf":
                raise ValidationDomainError("Payslip uploads must be application/pdf")
            if target_employee_id is None:
                raise ValidationDomainError("employee_id is required when uploading a payslip")
            if validate_period_month(cmd.period_month) is None:
                raise ValidationDomainError("period_month (YYYY-MM) is required for payslips")
            emp = await self._employee_repo.get_by_id(principal.tenant_id, target_employee_id)
            if emp is None:
                raise EntityNotFoundError(f"Employee {target_employee_id} not found")
            if target_store_id is None:
                target_store_id = emp.primary_store_id

        elif principal.role == UserRole.EMPLOYEE:
            if cmd.category != DocumentCategory.MEDICAL_LEAVE:
                raise ForbiddenScopeError(
                    "Employees may only upload 'medical_leave' (Baja médica) documents"
                )
            self_emp = await _resolve_self_employee(
                self._employee_repo, principal.tenant_id, principal.user_id
            )
            if self_emp is None:
                raise EntityNotFoundError("No employee record linked to current user")
            if target_employee_id is not None and target_employee_id != self_emp.id:
                raise ForbiddenScopeError("Employees may only upload documents for themselves")
            target_employee_id = self_emp.id
            target_store_id = self_emp.primary_store_id

        elif principal.role == UserRole.STORE_MANAGER:
            if cmd.category not in STORE_MANAGER_UPLOAD_CATEGORIES:
                raise ForbiddenScopeError(
                    f"Store managers cannot upload '{cmd.category.value}' documents"
                )
            if target_employee_id is not None:
                emp = await self._employee_repo.get_by_id(
                    principal.tenant_id, target_employee_id
                )
                if emp is None:
                    raise EntityNotFoundError(f"Employee {target_employee_id} not found")
                principal.require_employee_in_scope(emp)
                target_store_id = emp.primary_store_id
            elif target_store_id is not None:
                principal.require_store_access(principal.tenant_id, target_store_id)
            else:
                raise ValidationDomainError(
                    "Store managers must specify store_id or employee_id within authorized stores"
                )
        else:
            # OWNER or HR
            if target_employee_id is not None:
                emp = await self._employee_repo.get_by_id(
                    principal.tenant_id, target_employee_id
                )
                if emp is None:
                    raise EntityNotFoundError(f"Employee {target_employee_id} not found")
                if target_store_id is None:
                    target_store_id = emp.primary_store_id

        if target_store_id is not None:
            store = await self._store_repo.get_by_id(principal.tenant_id, target_store_id)
            if store is None:
                raise EntityNotFoundError(f"Store {target_store_id} not found")

        title = validate_non_empty_text(cmd.title, field="title", max_length=200)
        period_month = validate_period_month(cmd.period_month)
        safe_filename = sanitize_filename(cmd.filename)
        sha256_hex = validate_document_upload(cmd.mime_type, cmd.content)

        doc_id = uuid4()
        storage_key = build_storage_key(principal.tenant_id, doc_id)
        now = now_utc.astimezone(UTC)

        await self._storage.put_object(
            storage_key=storage_key,
            content=cmd.content,
            mime_type=cmd.mime_type,
        )

        document = ComplianceDocument(
            id=doc_id,
            tenant_id=principal.tenant_id,
            store_id=target_store_id,
            employee_id=target_employee_id,
            category=cmd.category,
            title=title,
            period_month=period_month,
            storage_backend=self._storage.backend_name,
            storage_key=storage_key,
            original_filename=safe_filename,
            mime_type=cmd.mime_type,
            size_bytes=len(cmd.content),
            sha256_hex=sha256_hex,
            requires_signature=cmd.requires_signature,
            uploaded_by_user_id=principal.user_id,
            retention_until=compute_document_retention_until(now),
            created_at=now,
            updated_at=now,
        )
        try:
            return await self._doc_repo.create(document)
        except Exception:
            await self._storage.delete_object(storage_key=storage_key)
            raise


class GetDocumentUseCase:
    def __init__(
        self,
        doc_repo: DocumentRepository,
        employee_repo: EmployeeRepository,
        ann_repo: AnnouncementRepository | None = None,
    ) -> None:
        self._doc_repo = doc_repo
        self._employee_repo = employee_repo
        self._ann_repo = ann_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        document_id: UUID,
    ) -> DocumentDetailResult:
        doc = await self._doc_repo.get_by_id(principal.tenant_id, document_id)
        if doc is None:
            raise EntityNotFoundError(f"Document {document_id} not found")

        emp = (
            await self._employee_repo.get_by_id(principal.tenant_id, doc.employee_id)
            if doc.employee_id is not None
            else None
        )
        self_emp = (
            await _resolve_self_employee(
                self._employee_repo, principal.tenant_id, principal.user_id
            )
            if principal.role in (UserRole.EMPLOYEE, UserRole.STORE_MANAGER)
            else None
        )
        if self._ann_repo is not None and principal.role not in (UserRole.OWNER, UserRole.HR):
            anns = await self._ann_repo.list_by_tenant(principal.tenant_id)
            linked_ann = next(
                (a for a in anns if a.attachment_document_id == doc.id),
                None,
            )
            if linked_ann is not None and not _can_principal_view_announcement(
                principal, linked_ann, self_emp
            ):
                raise ForbiddenScopeError(
                    "Announcement attachment is outside your authorized scope"
                )
        _require_document_read_access(principal, doc, emp, self_employee=self_emp)
        receipts = await self._doc_repo.list_receipts_for_document(principal.tenant_id, doc.id)
        if principal.role == UserRole.EMPLOYEE:
            receipts = [
                r for r in receipts if self_emp is not None and r.employee_id == self_emp.id
            ]
        elif principal.role == UserRole.STORE_MANAGER:
            emps = await self._employee_repo.list_by_tenant(principal.tenant_id)
            in_scope_ids = {e.id for e in emps if principal.can_manage_employee(e)}
            if self_emp is not None:
                in_scope_ids.add(self_emp.id)
            receipts = [r for r in receipts if r.employee_id in in_scope_ids]
        return DocumentDetailResult(document=doc, receipts=receipts)


class CreateSignedUrlUseCase:
    def __init__(
        self,
        doc_repo: DocumentRepository,
        employee_repo: EmployeeRepository,
        storage: DocumentStoragePort,
        ann_repo: AnnouncementRepository | None = None,
    ) -> None:
        self._doc_repo = doc_repo
        self._employee_repo = employee_repo
        self._storage = storage
        self._ann_repo = ann_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        document_id: UUID,
        ttl_seconds: int = 60,
    ) -> SignedUrlWithDigest:
        ttl = validate_signed_url_ttl(ttl_seconds)
        doc = await self._doc_repo.get_by_id(principal.tenant_id, document_id)
        if doc is None:
            raise EntityNotFoundError(f"Document {document_id} not found")

        emp = (
            await self._employee_repo.get_by_id(principal.tenant_id, doc.employee_id)
            if doc.employee_id is not None
            else None
        )
        self_emp = (
            await _resolve_self_employee(
                self._employee_repo, principal.tenant_id, principal.user_id
            )
            if principal.role in (UserRole.EMPLOYEE, UserRole.STORE_MANAGER)
            else None
        )
        if self._ann_repo is not None and principal.role not in (UserRole.OWNER, UserRole.HR):
            anns = await self._ann_repo.list_by_tenant(principal.tenant_id)
            linked_ann = next(
                (a for a in anns if a.attachment_document_id == doc.id),
                None,
            )
            if linked_ann is not None and not _can_principal_view_announcement(
                principal, linked_ann, self_emp
            ):
                raise ForbiddenScopeError(
                    "Announcement attachment is outside your authorized scope"
                )
        _require_document_read_access(principal, doc, emp, self_employee=self_emp)

        signed = await self._storage.create_signed_url(
            storage_key=doc.storage_key,
            ttl_seconds=ttl,
            download_filename=doc.original_filename,
            mime_type=doc.mime_type,
        )
        return SignedUrlWithDigest(signed_url=signed, sha256_hex=doc.sha256_hex)


class SignDocumentReceiptUseCase:
    def __init__(
        self,
        doc_repo: DocumentRepository,
        employee_repo: EmployeeRepository,
    ) -> None:
        self._doc_repo = doc_repo
        self._employee_repo = employee_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        document_id: UUID,
        ip_address: str,
        user_agent: str,
        now_utc: datetime,
    ) -> DocumentReceipt:
        if principal.role == UserRole.ACCOUNTANT:
            raise ForbiddenScopeError("Accountants cannot sign employee document receipts")

        doc = await self._doc_repo.get_by_id(principal.tenant_id, document_id)
        if doc is None:
            raise EntityNotFoundError(f"Document {document_id} not found")

        self_emp = await _resolve_self_employee(
            self._employee_repo, principal.tenant_id, principal.user_id
        )
        if self_emp is None:
            raise ForbiddenScopeError("Signer must have a linked employee record in this tenant")
        if doc.employee_id is not None and doc.employee_id != self_emp.id:
            raise ForbiddenScopeError("Cannot sign a document assigned to another employee")
        if doc.employee_id is None and not (
            doc.category == DocumentCategory.OTHER
            and (doc.store_id is None or doc.store_id == self_emp.primary_store_id)
        ):
            raise ForbiddenScopeError("Cannot sign a document outside your store or category scope")
        if principal.role == UserRole.STORE_MANAGER:
            _require_document_read_access(principal, doc, self_emp, self_employee=self_emp)

        if not doc.requires_signature:
            raise ValidationDomainError("This document does not require an electronic receipt")

        existing = await self._doc_repo.get_receipt(principal.tenant_id, doc.id, self_emp.id)
        if existing is not None:
            raise ConflictError("Electronic receipt already signed for this document")

        now = now_utc.astimezone(UTC)
        receipt = DocumentReceipt(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            document_id=doc.id,
            employee_id=self_emp.id,
            signed_by_user_id=principal.user_id,
            document_sha256_hex=doc.sha256_hex,
            signed_at_utc=now,
            ip_address=_clean_header_value(ip_address, fallback="unknown", max_len=64),
            user_agent=_clean_header_value(user_agent, fallback="unknown", max_len=512),
            receipt_statement=build_receipt_statement(doc, self_emp, now),
            retention_until=compute_document_retention_until(now),
        )
        return await self._doc_repo.append_receipt(receipt)


class DeleteDocumentUseCase:
    def __init__(self, doc_repo: DocumentRepository) -> None:
        self._doc_repo = doc_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        document_id: UUID,
        now_utc: datetime,
    ) -> None:
        if principal.role not in (UserRole.OWNER, UserRole.HR):
            raise ForbiddenScopeError("Only owners or HR may delete compliance documents")

        doc = await self._doc_repo.get_by_id(principal.tenant_id, document_id)
        if doc is None:
            raise EntityNotFoundError(f"Document {document_id} not found")

        receipts = await self._doc_repo.list_receipts_for_document(principal.tenant_id, doc.id)
        ensure_document_deletable(doc, receipts_count=len(receipts), now_utc=now_utc)
        await self._doc_repo.soft_delete(
            principal.tenant_id, doc.id, deleted_at=now_utc.astimezone(UTC)
        )
