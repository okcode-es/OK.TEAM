"""FastAPI router for Private Compliance Documents (<= 60s Signed URLs & SHA-256 Receipts)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field

from app.application.documents import (
    CreateSignedUrlUseCase,
    DeleteDocumentUseCase,
    GetDocumentUseCase,
    ListDocumentsUseCase,
    SignDocumentReceiptUseCase,
    UploadDocumentCommand,
    UploadDocumentUseCase,
)
from app.domain.documents import (
    MAX_DOCUMENT_SIZE_BYTES,
    ComplianceDocument,
    DocumentCategory,
    DocumentReceipt,
    DocumentStoragePort,
    StorageBackend,
)
from app.domain.organization import EntityNotFoundError
from app.domain.repositories import (
    AnnouncementRepository,
    DocumentRepository,
    EmployeeRepository,
    StoreRepository,
)
from app.domain.security import AuthenticatedPrincipal
from app.infrastructure.config import Settings
from app.infrastructure.document_storage import LocalDocumentStorageAdapter, SignedTokenError
from app.interfaces.api.deps import (
    CommitFn,
    get_announcement_repository,
    get_app_settings,
    get_commit_fn,
    get_current_principal,
    get_document_repository,
    get_document_storage,
    get_employee_repository,
    get_store_repository,
    get_utc_now,
)

router = APIRouter(prefix="/documents", tags=["documents"])


class ComplianceDocumentResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    store_id: UUID | None
    employee_id: UUID | None
    category: DocumentCategory
    title: str
    period_month: str | None
    storage_backend: StorageBackend
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256_hex: str
    requires_signature: bool
    uploaded_by_user_id: UUID
    retention_until: datetime
    created_at: datetime
    updated_at: datetime


class DocumentListItemResponse(BaseModel):
    document: ComplianceDocumentResponse
    receipt_count: int
    signed_by_self: bool
    latest_signed_at_utc: datetime | None


class DocumentListResponse(BaseModel):
    items: list[DocumentListItemResponse]


class DocumentReceiptResponse(BaseModel):
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


class DocumentDetailResponse(BaseModel):
    document: ComplianceDocumentResponse
    receipts: list[DocumentReceiptResponse]


class CreateSignedUrlRequest(BaseModel):
    ttl_seconds: int = Field(default=60, ge=1, le=60)


class SignedUrlResponse(BaseModel):
    url: str
    expires_at_utc: datetime
    ttl_seconds: int
    sha256_hex: str


def _to_doc_response(doc: ComplianceDocument) -> ComplianceDocumentResponse:
    return ComplianceDocumentResponse(
        id=doc.id,
        tenant_id=doc.tenant_id,
        store_id=doc.store_id,
        employee_id=doc.employee_id,
        category=doc.category,
        title=doc.title,
        period_month=doc.period_month,
        storage_backend=doc.storage_backend,
        original_filename=doc.original_filename,
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        sha256_hex=doc.sha256_hex,
        requires_signature=doc.requires_signature,
        uploaded_by_user_id=doc.uploaded_by_user_id,
        retention_until=doc.retention_until,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _to_receipt_response(r: DocumentReceipt) -> DocumentReceiptResponse:
    return DocumentReceiptResponse(
        id=r.id,
        tenant_id=r.tenant_id,
        document_id=r.document_id,
        employee_id=r.employee_id,
        signed_by_user_id=r.signed_by_user_id,
        document_sha256_hex=r.document_sha256_hex,
        signed_at_utc=r.signed_at_utc,
        ip_address=r.ip_address,
        user_agent=r.user_agent,
        receipt_statement=r.receipt_statement,
        retention_until=r.retention_until,
    )


def _extract_client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


@router.get(
    "",
    response_model=DocumentListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="list_documents",
)
async def list_documents(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    ann_repo: Annotated[AnnouncementRepository, Depends(get_announcement_repository)],
    category: Annotated[DocumentCategory | None, Query()] = None,
    employee_id: Annotated[UUID | None, Query()] = None,
    store_id: Annotated[UUID | None, Query()] = None,
    period_month: Annotated[str | None, Query()] = None,
    requires_signature: Annotated[bool | None, Query()] = None,
) -> DocumentListResponse:
    items = await ListDocumentsUseCase(doc_repo, employee_repo, ann_repo=ann_repo).execute(
        principal,
        category=category,
        employee_id=employee_id,
        store_id=store_id,
        period_month=period_month,
        requires_signature=requires_signature,
    )
    return DocumentListResponse(
        items=[
            DocumentListItemResponse(
                document=_to_doc_response(item.document),
                receipt_count=item.receipt_count,
                signed_by_self=item.signed_by_self,
                latest_signed_at_utc=item.latest_signed_at_utc,
            )
            for item in items
        ]
    )


@router.post(
    "",
    response_model=ComplianceDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="upload_document",
)
async def upload_document(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    file: Annotated[UploadFile, File()],
    category: Annotated[DocumentCategory, Form()],
    title: Annotated[str, Form()],
    employee_id: Annotated[UUID | None, Form()] = None,
    store_id: Annotated[UUID | None, Form()] = None,
    period_month: Annotated[str | None, Form()] = None,
    requires_signature: Annotated[bool, Form()] = False,
) -> ComplianceDocumentResponse:
    content = await file.read(MAX_DOCUMENT_SIZE_BYTES + 1)
    mime_type = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    created = await UploadDocumentUseCase(
        doc_repo, employee_repo, store_repo, storage
    ).execute(
        principal,
        UploadDocumentCommand(
            filename=file.filename or "document",
            mime_type=mime_type,
            content=content,
            category=category,
            title=title,
            employee_id=employee_id,
            store_id=store_id,
            period_month=period_month,
            requires_signature=requires_signature,
        ),
        now_utc=now_utc,
    )
    await commit()
    return _to_doc_response(created)


@router.get(
    "/local-download",
    response_model=None,
    status_code=status.HTTP_200_OK,
    operation_id="download_local_signed_document",
    responses={
        200: {
            "content": {"application/octet-stream": {}},
            "description": "Short-lived (<= 60s) HMAC-signed private document download",
        }
    },
)
async def download_local_signed_document(
    token: Annotated[str, Query(min_length=10)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> Response:
    if not isinstance(storage, LocalDocumentStorageAdapter):
        raise SignedTokenError("Local signed download endpoint is disabled for current backend")

    content, filename, mime_type, storage_key = storage.verify_and_read(token)
    try:
        tenant_str, doc_str = storage_key.split("/", 1)
        tenant_id = UUID(tenant_str)
        doc_id = UUID(doc_str)
    except ValueError as exc:
        raise SignedTokenError("Invalid storage key in token") from exc

    doc = await doc_repo.get_by_id(tenant_id, doc_id)
    if doc is None or doc.storage_key != storage_key:
        raise EntityNotFoundError("Document not found or deleted")

    raw_ascii = filename.encode("ascii", "ignore").decode("ascii").strip()
    raw_ascii = raw_ascii.replace('"', "").replace("\r", "").replace("\n", "")
    if not raw_ascii:
        ascii_fallback = "document"
    elif raw_ascii.startswith("."):
        ascii_fallback = f"document{raw_ascii}" if len(raw_ascii) > 1 else "document"
    else:
        ascii_fallback = raw_ascii
    encoded_fn = quote(filename, safe="")
    content_disp = f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded_fn}'
    return Response(
        content=content,
        media_type=mime_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Content-Disposition": content_disp,
        },
    )


@router.get(
    "/{document_id}",
    response_model=DocumentDetailResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_document",
)
async def get_document(
    document_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    ann_repo: Annotated[AnnouncementRepository, Depends(get_announcement_repository)],
) -> DocumentDetailResponse:
    detail = await GetDocumentUseCase(doc_repo, employee_repo, ann_repo=ann_repo).execute(
        principal, document_id=document_id
    )
    return DocumentDetailResponse(
        document=_to_doc_response(detail.document),
        receipts=[_to_receipt_response(r) for r in detail.receipts],
    )


@router.post(
    "/{document_id}/signed-url",
    response_model=SignedUrlResponse,
    status_code=status.HTTP_200_OK,
    operation_id="create_document_signed_url",
)
async def create_document_signed_url(
    document_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
    ann_repo: Annotated[AnnouncementRepository, Depends(get_announcement_repository)],
    body: CreateSignedUrlRequest | None = None,
) -> SignedUrlResponse:
    ttl = body.ttl_seconds if body is not None else 60
    result = await CreateSignedUrlUseCase(
        doc_repo, employee_repo, storage, ann_repo=ann_repo
    ).execute(
        principal, document_id=document_id, ttl_seconds=ttl
    )
    response.headers["Cache-Control"] = "no-store"
    return SignedUrlResponse(
        url=result.signed_url.url,
        expires_at_utc=result.signed_url.expires_at_utc,
        ttl_seconds=result.signed_url.ttl_seconds,
        sha256_hex=result.sha256_hex,
    )


@router.post(
    "/{document_id}/receipts",
    response_model=DocumentReceiptResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="sign_document_receipt",
)
async def sign_document_receipt(
    document_id: UUID,
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> DocumentReceiptResponse:
    receipt = await SignDocumentReceiptUseCase(doc_repo, employee_repo).execute(
        principal,
        document_id=document_id,
        ip_address=_extract_client_ip(request, settings),
        user_agent=request.headers.get("user-agent", "unknown"),
        now_utc=now_utc,
    )
    await commit()
    return _to_receipt_response(receipt)


@router.delete(
    "/{document_id}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_document",
)
async def delete_document(
    document_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
) -> None:
    await DeleteDocumentUseCase(doc_repo).execute(
        principal, document_id=document_id, now_utc=now_utc
    )
    await commit()
