from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.organization import (
    ConflictError,
    EntityNotFoundError,
    Tenant,
    normalize_cif_nif,
    validate_iana_timezone,
    validate_locale,
    validate_non_empty_text,
)
from app.domain.repositories import TenantRepository
from app.domain.security import AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class UpdateTenantCommand:
    legal_name: str
    trade_name: str
    cif_nif: str
    default_timezone: str
    default_locale: str


class GetCurrentTenantUseCase:
    def __init__(self, tenant_repo: TenantRepository) -> None:
        self._tenant_repo = tenant_repo

    async def execute(self, principal: AuthenticatedPrincipal) -> Tenant:
        tenant = await self._tenant_repo.get_by_id(principal.tenant_id)
        if tenant is None:
            raise EntityNotFoundError("Current tenant not found")
        return tenant


class UpdateCurrentTenantUseCase:
    def __init__(self, tenant_repo: TenantRepository) -> None:
        self._tenant_repo = tenant_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        cmd: UpdateTenantCommand,
    ) -> Tenant:
        principal.require_tenant_write()

        legal_name = validate_non_empty_text(cmd.legal_name, field="legal_name", max_length=200)
        trade_name = validate_non_empty_text(cmd.trade_name, field="trade_name", max_length=200)
        cif_nif = normalize_cif_nif(cmd.cif_nif)
        default_timezone = validate_iana_timezone(cmd.default_timezone)
        default_locale = validate_locale(cmd.default_locale)

        if await self._tenant_repo.exists_active_cif(
            cif_nif,
            exclude_tenant_id=principal.tenant_id,
        ):
            raise ConflictError(f"A company with CIF/NIF '{cif_nif}' already exists")

        updated = await self._tenant_repo.update(
            principal.tenant_id,
            legal_name=legal_name,
            trade_name=trade_name,
            cif_nif=cif_nif,
            default_timezone=default_timezone,
            default_locale=default_locale,
            updated_at=datetime.now(UTC),
        )
        if updated is None:
            raise EntityNotFoundError("Current tenant not found")
        return updated
