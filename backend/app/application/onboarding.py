from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app.domain.organization import (
    ConflictError,
    Membership,
    Tenant,
    TenantMembershipSummary,
    normalize_cif_nif,
    validate_iana_timezone,
    validate_locale,
    validate_non_empty_text,
)
from app.domain.repositories import MembershipRepository, TenantRepository
from app.domain.security import MembershipStatus, UserRole, VerifiedIdentity


@dataclass(frozen=True, slots=True)
class CreateTenantCommand:
    legal_name: str
    trade_name: str
    cif_nif: str
    default_timezone: str = "Europe/Madrid"
    default_locale: str = "zh-CN"
    owner_display_name: str = ""


@dataclass(frozen=True, slots=True)
class CreateTenantResult:
    tenant: Tenant
    membership: Membership


class GetMyProfileUseCase:
    def __init__(self, membership_repo: MembershipRepository) -> None:
        self._membership_repo = membership_repo

    async def execute(self, identity: VerifiedIdentity) -> list[TenantMembershipSummary]:
        return await self._membership_repo.list_summaries_for_user(identity.user_id)


class CreateTenantUseCase:
    def __init__(
        self,
        tenant_repo: TenantRepository,
        membership_repo: MembershipRepository,
    ) -> None:
        self._tenant_repo = tenant_repo
        self._membership_repo = membership_repo

    async def execute(
        self,
        identity: VerifiedIdentity,
        cmd: CreateTenantCommand,
    ) -> CreateTenantResult:
        legal_name = validate_non_empty_text(cmd.legal_name, field="legal_name", max_length=200)
        trade_name = validate_non_empty_text(cmd.trade_name, field="trade_name", max_length=200)
        cif_nif = normalize_cif_nif(cmd.cif_nif)
        default_timezone = validate_iana_timezone(cmd.default_timezone)
        default_locale = validate_locale(cmd.default_locale)

        if await self._tenant_repo.exists_active_cif(cif_nif):
            raise ConflictError(f"A company with CIF/NIF '{cif_nif}' already exists")

        now = datetime.now(UTC)
        tenant = Tenant(
            id=uuid4(),
            legal_name=legal_name,
            trade_name=trade_name,
            cif_nif=cif_nif,
            default_timezone=default_timezone,
            default_locale=default_locale,
            created_at=now,
            updated_at=now,
        )
        created_tenant = await self._tenant_repo.create(tenant)

        owner_email = (identity.email or f"{identity.user_id}@okteam.local").strip().lower()
        display_name = (
            cmd.owner_display_name.strip()
            if cmd.owner_display_name.strip()
            else owner_email.split("@")[0]
        )[:120]

        membership = Membership(
            id=uuid4(),
            tenant_id=created_tenant.id,
            user_id=identity.user_id,
            email=owner_email,
            display_name=display_name,
            role=UserRole.OWNER,
            status=MembershipStatus.ACTIVE,
            store_ids=frozenset(),
            created_at=now,
            updated_at=now,
        )
        created_membership = await self._membership_repo.create(membership)
        return CreateTenantResult(tenant=created_tenant, membership=created_membership)
