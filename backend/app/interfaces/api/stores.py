from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from app.application.stores import (
    ArchiveStoreUseCase,
    CreateStoreCommand,
    CreateStoreUseCase,
    GetStoreUseCase,
    ListStoresUseCase,
    UpdateStoreCommand,
    UpdateStoreUseCase,
)
from app.domain.organization import Store
from app.domain.repositories import StoreRepository
from app.domain.security import AuthenticatedPrincipal
from app.interfaces.api.deps import (
    CommitFn,
    get_commit_fn,
    get_current_principal,
    get_store_repository,
)

router = APIRouter(prefix="/stores", tags=["stores"])


class StoreCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    code: str = Field(min_length=1, max_length=32)
    timezone: str = Field(default="Europe/Madrid", max_length=64)
    address: str = Field(default="", max_length=300)
    city: str = Field(default="", max_length=100)
    postal_code: str = Field(default="", max_length=16)


class StoreUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    code: str = Field(min_length=1, max_length=32)
    timezone: str = Field(min_length=1, max_length=64)
    address: str = Field(default="", max_length=300)
    city: str = Field(default="", max_length=100)
    postal_code: str = Field(default="", max_length=16)
    is_active: bool = True


class StoreResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    code: str
    timezone: str
    address: str
    city: str
    postal_code: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, store: Store) -> StoreResponse:
        return cls(
            id=store.id,
            tenant_id=store.tenant_id,
            name=store.name,
            code=store.code,
            timezone=store.timezone,
            address=store.address,
            city=store.city,
            postal_code=store.postal_code,
            is_active=store.is_active,
            created_at=store.created_at,
            updated_at=store.updated_at,
        )


class StoreListResponse(BaseModel):
    items: list[StoreResponse]
    total: int


@router.get(
    "",
    operation_id="listStores",
    response_model=StoreListResponse,
    summary="List stores visible to the current principal (filtered by tenant and store scope)",
)
async def list_stores(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    include_inactive: Annotated[bool, Query()] = False,
) -> StoreListResponse:
    use_case = ListStoresUseCase(store_repo)
    stores = await use_case.execute(principal, include_inactive=include_inactive)
    return StoreListResponse(
        items=[StoreResponse.from_domain(item) for item in stores],
        total=len(stores),
    )


@router.get(
    "/{store_id}",
    operation_id="getStore",
    response_model=StoreResponse,
    summary="Get a single store within principal's authorized scope",
)
async def get_store(
    store_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
) -> StoreResponse:
    use_case = GetStoreUseCase(store_repo)
    store = await use_case.execute(principal, store_id)
    return StoreResponse.from_domain(store)


@router.post(
    "",
    operation_id="createStore",
    response_model=StoreResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new store (owner / hr only)",
)
async def create_store(
    payload: StoreCreateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
) -> StoreResponse:
    use_case = CreateStoreUseCase(store_repo)
    store = await use_case.execute(
        principal,
        CreateStoreCommand(
            name=payload.name,
            code=payload.code,
            timezone=payload.timezone,
            address=payload.address,
            city=payload.city,
            postal_code=payload.postal_code,
        ),
    )
    await commit()
    return StoreResponse.from_domain(store)


@router.patch(
    "/{store_id}",
    operation_id="updateStore",
    response_model=StoreResponse,
    summary="Update store metadata or timezone (owner / hr only)",
)
async def update_store(
    store_id: UUID,
    payload: StoreUpdateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
) -> StoreResponse:
    use_case = UpdateStoreUseCase(store_repo)
    store = await use_case.execute(
        principal,
        store_id,
        UpdateStoreCommand(
            name=payload.name,
            code=payload.code,
            timezone=payload.timezone,
            address=payload.address,
            city=payload.city,
            postal_code=payload.postal_code,
            is_active=payload.is_active,
        ),
    )
    await commit()
    return StoreResponse.from_domain(store)


@router.delete(
    "/{store_id}",
    operation_id="archiveStore",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete (archive) a store — never physically deleted (owner / hr only)",
)
async def archive_store(
    store_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    commit: Annotated[CommitFn, Depends(get_commit_fn)],
) -> Response:
    use_case = ArchiveStoreUseCase(store_repo)
    await use_case.execute(principal, store_id)
    await commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
