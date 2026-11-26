"""FastAPI router for Company Announcements & Mandatory Acknowledgements."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from pydantic import BaseModel

from app.application.announcements import (
    AcknowledgeAnnouncementUseCase,
    AnnouncementViewItem,
    CreateAnnouncementAttachmentSignedUrlUseCase,
    CreateAnnouncementCommand,
    CreateAnnouncementUseCase,
    GetAnnouncementUseCase,
    ListAnnouncementAcknowledgementsUseCase,
    ListAnnouncementsUseCase,
)
from app.domain.announcements import (
    Announcement,
    AnnouncementAcknowledgement,
    AnnouncementCategory,
)
from app.domain.documents import MAX_DOCUMENT_SIZE_BYTES, DocumentStoragePort
from app.domain.repositories import (
    AnnouncementRepository,
    DocumentRepository,
    EmployeeRepository,
    StoreRepository,
)
from app.domain.security import AuthenticatedPrincipal
from app.infrastructure.config import Settings
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
from app.interfaces.api.documents import SignedUrlResponse, _extract_client_ip

router = APIRouter(prefix="/announcements", tags=["announcements"])


class AnnouncementResponse(BaseModel):
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
    acknowledged_count: int = 0
    target_count: int = 0
    acknowledged_by_self: bool = False
    acknowledged_at_utc: datetime | None = None


class AnnouncementListResponse(BaseModel):
    items: list[AnnouncementResponse]


class AnnouncementAcknowledgementResponse(BaseModel):
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


class AcknowledgementStatusEmployeeResponse(BaseModel):
    employee_id: UUID
    employee_code: str
    full_name: str
    department: str
    acknowledged_at_utc: datetime | None
    statement: str | None


class AnnouncementAcknowledgementsReportResponse(BaseModel):
    announcement_id: UUID
    total_target_count: int
    acknowledged_count: int
    pending_count: int
    acknowledged: list[AcknowledgementStatusEmployeeResponse]
    pending: list[AcknowledgementStatusEmployeeResponse]


def _to_ann_response(
    ann: Announcement,
    view: AnnouncementViewItem | None = None,
) -> AnnouncementResponse:
    return AnnouncementResponse(
        id=ann.id,
        tenant_id=ann.tenant_id,
        store_id=ann.store_id,
        department_scope=ann.department_scope,
        category=ann.category,
        title=ann.title,
        body=ann.body,
        attachment_document_id=ann.attachment_document_id,
        content_sha256_hex=ann.content_sha256_hex,
        requires_acknowledgement=ann.requires_acknowledgement,
        is_pinned=ann.is_pinned,
        published_by_user_id=ann.published_by_user_id,
        published_at_utc=ann.published_at_utc,
        retention_until=ann.retention_until,
        created_at=ann.created_at,
        updated_at=ann.updated_at,
        acknowledged_count=view.acknowledged_count if view else 0,
        target_count=view.target_count if view else 0,
        acknowledged_by_self=view.acknowledged_by_self if view else False,
        acknowledged_at_utc=view.acknowledged_at_utc if view else None,
    )


def _to_ack_response(ack: AnnouncementAcknowledgement) -> AnnouncementAcknowledgementResponse:
    return AnnouncementAcknowledgementResponse(
        id=ack.id,
        tenant_id=ack.tenant_id,
        announcement_id=ack.announcement_id,
        employee_id=ack.employee_id,
        acknowledged_by_user_id=ack.acknowledged_by_user_id,
        content_sha256_hex=ack.content_sha256_hex,
        acknowledged_at_utc=ack.acknowledged_at_utc,
        ip_address=ack.ip_address,
        user_agent=ack.user_agent,
        statement=ack.statement,
        retention_until=ack.retention_until,
    )


@router.get(
    "",
    response_model=AnnouncementListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="list_announcements",
)
async def list_announcements(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    ann_repo: Annotated[AnnouncementRepository, Depends(get_announcement_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    category: Annotated[Literal["all", "notice", "policy"], Query()] = "all",
    pending_only: Annotated[bool, Query()] = False,
) -> AnnouncementListResponse:
    cat_filter = None if category == "all" else AnnouncementCategory(category)
    views = await ListAnnouncementsUseCase(ann_repo, employee_repo).execute(
        principal, category=cat_filter, pending_only=pending_only
    )
    return AnnouncementListResponse(
        items=[_to_ann_response(v.announcement, v) for v in views]
    )


@router.post(
    "",
    response_model=AnnouncementResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_announcement",
)
async def create_announcement(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    ann_repo: Annotated[AnnouncementRepository, Depends(get_announcement_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    category: Annotated[AnnouncementCategory, Form()],
    title: Annotated[str, Form()],
    body: Annotated[str, Form()],
    store_id: Annotated[UUID | None, Form()] = None,
    department_scope: Annotated[str | None, Form()] = None,
    requires_acknowledgement: Annotated[bool, Form()] = False,
    is_pinned: Annotated[bool, Form()] = False,
    file: Annotated[UploadFile | None, File()] = None,
) -> AnnouncementResponse:
    att_content: bytes | None = None
    att_mime: str | None = None
    att_filename: str | None = None
    if file is not None:
        att_content = await file.read(MAX_DOCUMENT_SIZE_BYTES + 1)
        att_mime = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
        att_filename = file.filename or "attachment"

    created = await CreateAnnouncementUseCase(
        ann_repo, store_repo, doc_repo, storage
    ).execute(
        principal,
        CreateAnnouncementCommand(
            category=category,
            title=title,
            body=body,
            store_id=store_id,
            department_scope=department_scope,
            requires_acknowledgement=requires_acknowledgement,
            is_pinned=is_pinned,
            attachment_filename=att_filename,
            attachment_mime_type=att_mime,
            attachment_content=att_content,
        ),
        now_utc=now_utc,
    )
    await commit()
    return _to_ann_response(created)


@router.get(
    "/{announcement_id}",
    response_model=AnnouncementResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_announcement",
)
async def get_announcement(
    announcement_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    ann_repo: Annotated[AnnouncementRepository, Depends(get_announcement_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
) -> AnnouncementResponse:
    view = await GetAnnouncementUseCase(ann_repo, employee_repo).execute(
        principal, announcement_id=announcement_id
    )
    return _to_ann_response(view.announcement, view)


@router.post(
    "/{announcement_id}/acknowledge",
    response_model=AnnouncementAcknowledgementResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="acknowledge_announcement",
)
async def acknowledge_announcement(
    announcement_id: UUID,
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    ann_repo: Annotated[AnnouncementRepository, Depends(get_announcement_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
    now_utc: Annotated[datetime, Depends(get_utc_now)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AnnouncementAcknowledgementResponse:
    ack = await AcknowledgeAnnouncementUseCase(ann_repo, employee_repo).execute(
        principal,
        announcement_id=announcement_id,
        ip_address=_extract_client_ip(request, settings),
        user_agent=request.headers.get("user-agent", "unknown"),
        now_utc=now_utc,
    )
    await commit()
    return _to_ack_response(ack)


@router.get(
    "/{announcement_id}/acknowledgements",
    response_model=AnnouncementAcknowledgementsReportResponse,
    status_code=status.HTTP_200_OK,
    operation_id="list_announcement_acknowledgements",
)
async def list_announcement_acknowledgements(
    announcement_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    ann_repo: Annotated[AnnouncementRepository, Depends(get_announcement_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
) -> AnnouncementAcknowledgementsReportResponse:
    report = await ListAnnouncementAcknowledgementsUseCase(ann_repo, employee_repo).execute(
        principal, announcement_id=announcement_id
    )
    return AnnouncementAcknowledgementsReportResponse(
        announcement_id=report.announcement_id,
        total_target_count=report.total_target_count,
        acknowledged_count=report.acknowledged_count,
        pending_count=report.pending_count,
        acknowledged=[
            AcknowledgementStatusEmployeeResponse(
                employee_id=item.employee_id,
                employee_code=item.employee_code,
                full_name=item.full_name,
                department=item.department,
                acknowledged_at_utc=item.acknowledged_at_utc,
                statement=item.statement,
            )
            for item in report.acknowledged
        ],
        pending=[
            AcknowledgementStatusEmployeeResponse(
                employee_id=item.employee_id,
                employee_code=item.employee_code,
                full_name=item.full_name,
                department=item.department,
                acknowledged_at_utc=item.acknowledged_at_utc,
                statement=item.statement,
            )
            for item in report.pending
        ],
    )


@router.post(
    "/{announcement_id}/attachment/signed-url",
    response_model=SignedUrlResponse,
    status_code=status.HTTP_200_OK,
    operation_id="create_announcement_attachment_signed_url",
)
async def create_announcement_attachment_signed_url(
    announcement_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    ann_repo: Annotated[AnnouncementRepository, Depends(get_announcement_repository)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    doc_repo: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
) -> SignedUrlResponse:
    signed, sha256_hex = await CreateAnnouncementAttachmentSignedUrlUseCase(
        ann_repo, employee_repo, doc_repo, storage
    ).execute(principal, announcement_id=announcement_id, ttl_seconds=60)
    response.headers["Cache-Control"] = "no-store"
    return SignedUrlResponse(
        url=signed.url,
        expires_at_utc=signed.expires_at_utc,
        ttl_seconds=signed.ttl_seconds,
        sha256_hex=sha256_hex,
    )
