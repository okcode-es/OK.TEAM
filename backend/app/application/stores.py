from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.organization import (
    ConflictError,
    EntityNotFoundError,
    Store,
    normalize_store_code,
    validate_iana_timezone,
    validate_non_empty_text,
)
from app.domain.repositories import StoreRepository
from app.domain.security import TENANT_WIDE_ROLES, AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class CreateStoreCommand:
    name: str
    code: str
    timezone: str = "Europe/Madrid"
    address: str = ""
    city: str = ""
    postal_code: str = ""


@dataclass(frozen=True, slots=True)
class UpdateStoreCommand:
    name: str
    code: str
    timezone: str
    address: str = ""
    city: str = ""
    postal_code: str = ""
    is_active: bool = True


class ListStoresUseCase:
    def __init__(self, store_repo: StoreRepository) -> None:
        self._store_repo = store_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        include_inactive: bool = False,
    ) -> list[Store]:
        allowed_store_ids = (
            None if principal.role in TENANT_WIDE_ROLES else principal.store_ids
        )
        return await self._store_repo.list_by_tenant(
            principal.tenant_id,
            allowed_store_ids=allowed_store_ids,
            include_inactive=include_inactive,
        )


class GetStoreUseCase:
    def __init__(self, store_repo: StoreRepository) -> None:
        self._store_repo = store_repo

    async def execute(self, principal: AuthenticatedPrincipal, store_id: UUID) -> Store:
        principal.require_store_access(principal.tenant_id, store_id)
        store = await self._store_repo.get_by_id(principal.tenant_id, store_id)
        if store is None:
            raise EntityNotFoundError("Store not found")
        return store


class CreateStoreUseCase:
    def __init__(self, store_repo: StoreRepository) -> None:
        self._store_repo = store_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        cmd: CreateStoreCommand,
    ) -> Store:
        principal.require_store_manage()

        name = validate_non_empty_text(cmd.name, field="name", max_length=160)
        code = normalize_store_code(cmd.code)
        timezone = validate_iana_timezone(cmd.timezone)

        if await self._store_repo.exists_active_code(principal.tenant_id, code):
            raise ConflictError(f"Store code '{code}' already exists in this company")

        now = datetime.now(UTC)
        store = Store(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            name=name,
            code=code,
            timezone=timezone,
            address=cmd.address.strip()[:300],
            city=cmd.city.strip()[:100],
            postal_code=cmd.postal_code.strip()[:16],
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        return await self._store_repo.create(store)


class UpdateStoreUseCase:
    def __init__(self, store_repo: StoreRepository) -> None:
        self._store_repo = store_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        store_id: UUID,
        cmd: UpdateStoreCommand,
    ) -> Store:
        principal.require_store_manage()

        name = validate_non_empty_text(cmd.name, field="name", max_length=160)
        code = normalize_store_code(cmd.code)
        timezone = validate_iana_timezone(cmd.timezone)

        if await self._store_repo.exists_active_code(
            principal.tenant_id,
            code,
            exclude_store_id=store_id,
        ):
            raise ConflictError(f"Store code '{code}' already exists in this company")

        updated = await self._store_repo.update(
            principal.tenant_id,
            store_id,
            name=name,
            code=code,
            timezone=timezone,
            address=cmd.address.strip()[:300],
            city=cmd.city.strip()[:100],
            postal_code=cmd.postal_code.strip()[:16],
            is_active=cmd.is_active,
            updated_at=datetime.now(UTC),
        )
        if updated is None:
            raise EntityNotFoundError("Store not found")
        return updated


class ArchiveStoreUseCase:
    def __init__(self, store_repo: StoreRepository) -> None:
        self._store_repo = store_repo

    async def execute(self, principal: AuthenticatedPrincipal, store_id: UUID) -> None:
        principal.require_store_manage()
        archived = await self._store_repo.soft_delete(
            principal.tenant_id,
            store_id,
            deleted_at=datetime.now(UTC),
        )
        if not archived:
            raise EntityNotFoundError("Store not found")
