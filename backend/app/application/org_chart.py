"""Application use case for Phase 5I: Interactive Organization Chart."""

from __future__ import annotations

import logging
from uuid import UUID

from app.application.employees import MUTABLE_STATUSES, mint_avatar_url
from app.domain.documents import DocumentStoragePort
from app.domain.employees import Employee
from app.domain.org_chart import OrgChartResult, build_org_chart
from app.domain.repositories import EmployeeRepository, StoreRepository
from app.domain.security import AuthenticatedPrincipal

_logger = logging.getLogger(__name__)


async def _safe_mint_avatar_url(storage: DocumentStoragePort, employee: Employee) -> str | None:
    try:
        return await mint_avatar_url(storage, employee)
    except Exception:  # pragma: no cover - defensive resilience for broken storage tokens
        _logger.warning(
            "Failed to mint avatar_url for employee_id=%s in org chart",
            employee.id,
            exc_info=True,
        )
        return None


class GetOrgChartUseCase:
    """Return the GDPR-minimized tenant organization chart for any active tenant member."""

    def __init__(
        self,
        employee_repo: EmployeeRepository,
        store_repo: StoreRepository,
        storage: DocumentStoragePort,
    ) -> None:
        self._employee_repo = employee_repo
        self._store_repo = store_repo
        self._storage = storage

    async def execute(self, principal: AuthenticatedPrincipal) -> OrgChartResult:
        employees = await self._employee_repo.list_by_tenant(
            principal.tenant_id,
            allowed_statuses=MUTABLE_STATUSES,
        )
        stores = await self._store_repo.list_by_tenant(
            principal.tenant_id,
            include_inactive=True,
        )
        store_names = {store.id: store.name for store in stores}

        avatar_urls: dict[UUID, str | None] = {}
        current_employee_id: UUID | None = None
        for emp in employees:
            avatar_urls[emp.id] = await _safe_mint_avatar_url(self._storage, emp)
            if current_employee_id is None and emp.user_id == principal.user_id:
                current_employee_id = emp.id

        return build_org_chart(
            employees,
            store_names=store_names,
            avatar_urls=avatar_urls,
            current_employee_id=current_employee_id,
        )
