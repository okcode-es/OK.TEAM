"""FastAPI router for Phase 5I: Interactive Organization Chart."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from app.application.org_chart import GetOrgChartUseCase
from app.domain.documents import DocumentStoragePort
from app.domain.repositories import EmployeeRepository, StoreRepository
from app.domain.security import AuthenticatedPrincipal
from app.interfaces.api.deps import (
    get_current_principal,
    get_document_storage,
    get_employee_repository,
    get_store_repository,
)

router = APIRouter(prefix="/organization", tags=["organization"])


class OrgChartNodePublic(BaseModel):
    """Strictly GDPR-minimized public org-chart node (exact 12 fields, no sensitive HR/PII)."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    full_name: str
    avatar_url: str | None = None
    job_title: str
    department_id: str | None = None
    department_name: str
    store_id: UUID
    store_name: str
    work_email: str
    manager_id: UUID | None = None
    direct_reports_count: int
    total_reports_count: int


class OrgChartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[OrgChartNodePublic]
    root_ids: list[UUID]
    unassigned_ids: list[UUID]
    current_employee_id: UUID | None = None
    total_active_employees: int


@router.get(
    "/org-chart",
    operation_id="getOrganizationOrgChart",
    response_model=OrgChartResponse,
    summary="Get tenant interactive organization chart (GDPR-minimized, all active roles)",
)
async def get_organization_org_chart(
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    employee_repo: Annotated[EmployeeRepository, Depends(get_employee_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    storage: Annotated[DocumentStoragePort, Depends(get_document_storage)],
) -> OrgChartResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    use_case = GetOrgChartUseCase(employee_repo, store_repo, storage)
    result = await use_case.execute(principal)
    return OrgChartResponse(
        nodes=[
            OrgChartNodePublic(
                id=node.id,
                full_name=node.full_name,
                avatar_url=node.avatar_url,
                job_title=node.job_title,
                department_id=node.department_id,
                department_name=node.department_name,
                store_id=node.store_id,
                store_name=node.store_name,
                work_email=node.work_email,
                manager_id=node.manager_id,
                direct_reports_count=node.direct_reports_count,
                total_reports_count=node.total_reports_count,
            )
            for node in result.nodes
        ],
        root_ids=list(result.root_ids),
        unassigned_ids=list(result.unassigned_ids),
        current_employee_id=result.current_employee_id,
        total_active_employees=result.total_active_employees,
    )
