from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.organization import (
    ConflictError,
    EntityNotFoundError,
    LastOwnerProtectionError,
    Membership,
    ValidationDomainError,
    validate_non_empty_text,
)
from app.domain.repositories import MembershipRepository, StoreRepository
from app.domain.security import (
    TENANT_WIDE_ROLES,
    AuthenticatedPrincipal,
    MembershipStatus,
    UserRole,
)


@dataclass(frozen=True, slots=True)
class CreateMembershipCommand:
    user_id: UUID
    email: str
    display_name: str
    role: UserRole
    store_ids: frozenset[UUID] = frozenset()
    status: MembershipStatus = MembershipStatus.ACTIVE
    department_scope: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateMembershipCommand:
    display_name: str
    role: UserRole
    status: MembershipStatus
    store_ids: frozenset[UUID]
    department_scope: str | None = None


def _normalize_department_scope(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = raw.strip()[:100]
    return cleaned or None


class ListMembershipsUseCase:
    def __init__(self, membership_repo: MembershipRepository) -> None:
        self._membership_repo = membership_repo

    async def execute(self, principal: AuthenticatedPrincipal) -> list[Membership]:
        if principal.role in TENANT_WIDE_ROLES:
            return await self._membership_repo.list_by_tenant(principal.tenant_id)
        if principal.role == UserRole.STORE_MANAGER:
            return await self._membership_repo.list_by_tenant(
                principal.tenant_id,
                filter_store_ids=principal.store_ids,
                only_user_id=principal.user_id,
            )
        return await self._membership_repo.list_by_tenant(
            principal.tenant_id,
            only_user_id=principal.user_id,
        )


class CreateMembershipUseCase:
    def __init__(
        self,
        membership_repo: MembershipRepository,
        store_repo: StoreRepository,
    ) -> None:
        self._membership_repo = membership_repo
        self._store_repo = store_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        cmd: CreateMembershipCommand,
    ) -> Membership:
        principal.require_member_manage(target_role=cmd.role)

        email = validate_non_empty_text(cmd.email, field="email", max_length=255).lower()
        if "@" not in email:
            raise ValidationDomainError("Invalid email format")
        display_name = validate_non_empty_text(
            cmd.display_name,
            field="display_name",
            max_length=120,
        )

        if await self._membership_repo.exists_active_user_in_tenant(
            principal.tenant_id,
            cmd.user_id,
        ):
            raise ConflictError("User is already a member of this company")

        await _verify_stores_belong_to_tenant(
            self._store_repo,
            principal.tenant_id,
            cmd.store_ids,
        )

        now = datetime.now(UTC)
        membership = Membership(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            user_id=cmd.user_id,
            email=email,
            display_name=display_name,
            role=cmd.role,
            status=cmd.status,
            store_ids=cmd.store_ids,
            department_scope=_normalize_department_scope(cmd.department_scope),
            created_at=now,
            updated_at=now,
        )
        return await self._membership_repo.create(membership)


class UpdateMembershipUseCase:
    def __init__(
        self,
        membership_repo: MembershipRepository,
        store_repo: StoreRepository,
    ) -> None:
        self._membership_repo = membership_repo
        self._store_repo = store_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        membership_id: UUID,
        cmd: UpdateMembershipCommand,
    ) -> Membership:
        principal.require_member_manage(target_role=cmd.role)

        existing = await self._membership_repo.get_by_id(principal.tenant_id, membership_id)
        if existing is None:
            raise EntityNotFoundError("Membership not found")

        principal.require_member_manage(target_role=existing.role)

        display_name = validate_non_empty_text(
            cmd.display_name,
            field="display_name",
            max_length=120,
        )

        is_losing_active_owner = (
            existing.role == UserRole.OWNER
            and existing.status == MembershipStatus.ACTIVE
            and (cmd.role != UserRole.OWNER or cmd.status != MembershipStatus.ACTIVE)
        )
        if is_losing_active_owner:
            active_owners = await self._membership_repo.count_active_owners(principal.tenant_id)
            if active_owners <= 1:
                raise LastOwnerProtectionError(
                    "Cannot demote or disable the last active owner of the company"
                )

        await _verify_stores_belong_to_tenant(
            self._store_repo,
            principal.tenant_id,
            cmd.store_ids,
        )

        updated = await self._membership_repo.update(
            principal.tenant_id,
            membership_id,
            display_name=display_name,
            role=cmd.role,
            status=cmd.status,
            store_ids=cmd.store_ids,
            department_scope=_normalize_department_scope(cmd.department_scope),
            updated_at=datetime.now(UTC),
        )
        if updated is None:
            raise EntityNotFoundError("Membership not found")
        return updated


class ArchiveMembershipUseCase:
    def __init__(self, membership_repo: MembershipRepository) -> None:
        self._membership_repo = membership_repo

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        membership_id: UUID,
    ) -> None:
        principal.require_member_manage()

        existing = await self._membership_repo.get_by_id(principal.tenant_id, membership_id)
        if existing is None:
            raise EntityNotFoundError("Membership not found")

        principal.require_member_manage(target_role=existing.role)

        if existing.role == UserRole.OWNER and existing.status == MembershipStatus.ACTIVE:
            active_owners = await self._membership_repo.count_active_owners(principal.tenant_id)
            if active_owners <= 1:
                raise LastOwnerProtectionError(
                    "Cannot remove the last active owner of the company"
                )

        archived = await self._membership_repo.soft_delete(
            principal.tenant_id,
            membership_id,
            deleted_at=datetime.now(UTC),
        )
        if not archived:
            raise EntityNotFoundError("Membership not found")


async def _verify_stores_belong_to_tenant(
    store_repo: StoreRepository,
    tenant_id: UUID,
    store_ids: frozenset[UUID],
) -> None:
    if not store_ids:
        return
    matched_count = await store_repo.count_existing_in_tenant(tenant_id, store_ids)
    if matched_count != len(store_ids):
        raise ValidationDomainError(
            "One or more store_ids do not exist or do not belong to this company"
        )
