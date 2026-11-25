from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.domain.organization import Membership, TenantMembershipSummary
from app.domain.security import (
    AuthenticatedPrincipal,
    ForbiddenScopeError,
    MembershipStatus,
    TokenVerificationError,
    UserRole,
)
from app.infrastructure.supabase_jwt import SupabaseJWTVerifier
from app.interfaces.api.deps import (
    get_current_principal,
    get_jwt_verifier,
    get_membership_repository,
)
from app.interfaces.api.errors import register_exception_handlers

TEST_SECRET = "phase1-unit-test-secret-minimum-32-bytes-key"


def _load_alembic_env() -> Any:
    env_path = Path(__file__).resolve().parent.parent / "alembic" / "env.py"
    spec = importlib.util.spec_from_file_location("okteam_alembic_env", env_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_token(
    *,
    sub: str | None = None,
    app_metadata: dict[str, Any] | None = None,
    user_metadata: dict[str, Any] | None = None,
    exp_delta: timedelta = timedelta(minutes=15),
    secret: str = TEST_SECRET,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "aud": "authenticated",
        "iat": int(now.timestamp()),
        "exp": int((now + exp_delta).timestamp()),
        "email": "manager@okteam.es",
    }
    if sub is not None:
        payload["sub"] = sub
    if app_metadata is not None:
        payload["app_metadata"] = app_metadata
    if user_metadata is not None:
        payload["user_metadata"] = user_metadata
    return jwt.encode(payload, secret, algorithm="HS256")


class _StubMembershipRepo:
    def __init__(self, memberships: list[Membership]) -> None:
        self._memberships = memberships

    async def list_summaries_for_user(self, user_id: UUID) -> list[TenantMembershipSummary]:
        return []

    async def get_active_for_user_in_tenant(
        self,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Membership | None:
        for item in self._memberships:
            if (
                item.tenant_id == tenant_id
                and item.user_id == user_id
                and item.status == MembershipStatus.ACTIVE
                and item.deleted_at is None
            ):
                return item
        return None

    async def get_by_id(self, tenant_id: UUID, membership_id: UUID) -> Membership | None:
        return None

    async def exists_active_user_in_tenant(self, tenant_id: UUID, user_id: UUID) -> bool:
        return False

    async def count_active_owners(self, tenant_id: UUID) -> int:
        return 0

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        filter_store_ids: frozenset[UUID] | None = None,
        only_user_id: UUID | None = None,
    ) -> list[Membership]:
        return []

    async def create(self, membership: Membership) -> Membership:
        return membership

    async def update(
        self,
        tenant_id: UUID,
        membership_id: UUID,
        *,
        display_name: str,
        role: UserRole,
        status: MembershipStatus,
        store_ids: frozenset[UUID],
        updated_at: datetime,
    ) -> Membership | None:
        return None

    async def soft_delete(
        self,
        tenant_id: UUID,
        membership_id: UUID,
        *,
        deleted_at: datetime,
    ) -> bool:
        return False


def test_verifier_extracts_principal_and_enforces_store_scope() -> None:
    user_id = uuid4()
    tenant_id = uuid4()
    allowed_store = uuid4()
    other_store = uuid4()

    verifier = SupabaseJWTVerifier(
        supabase_url="https://example.supabase.co",
        jwt_secret=TEST_SECRET,
    )
    token = _make_token(
        sub=str(user_id),
        app_metadata={
            "tenant_id": str(tenant_id),
            "role": "store_manager",
            "store_ids": [str(allowed_store)],
        },
    )

    principal = verifier.verify(token)
    assert principal.user_id == user_id
    assert principal.tenant_id == tenant_id
    assert principal.role == UserRole.STORE_MANAGER
    assert principal.can_access_store(tenant_id, allowed_store) is True
    assert principal.can_access_store(tenant_id, other_store) is False

    with pytest.raises(ForbiddenScopeError):
        principal.require_store_access(tenant_id, other_store)


def test_verifier_ignores_untrusted_user_metadata() -> None:
    verifier = SupabaseJWTVerifier(
        supabase_url="https://example.supabase.co",
        jwt_secret=TEST_SECRET,
    )
    token = _make_token(
        sub=str(uuid4()),
        user_metadata={"tenant_id": str(uuid4()), "role": "owner"},
    )

    with pytest.raises(TokenVerificationError, match="app_metadata"):
        verifier.verify(token)


def test_verifier_rejects_expired_token() -> None:
    verifier = SupabaseJWTVerifier(
        supabase_url="https://example.supabase.co",
        jwt_secret=TEST_SECRET,
    )
    token = _make_token(
        sub=str(uuid4()),
        app_metadata={"tenant_id": str(uuid4()), "role": "owner"},
        exp_delta=timedelta(minutes=-5),
    )

    with pytest.raises(TokenVerificationError):
        verifier.verify(token)


def test_hybrid_auth_dependency_resolves_db_membership() -> None:
    user_id = uuid4()
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    now = datetime.now(UTC)

    membership = Membership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        email="hr@okteam.es",
        display_name="HR Admin",
        role=UserRole.HR,
        status=MembershipStatus.ACTIVE,
        store_ids=frozenset(),
        created_at=now,
        updated_at=now,
    )
    verifier = SupabaseJWTVerifier(
        supabase_url="https://example.supabase.co",
        jwt_secret=TEST_SECRET,
    )
    probe_app = FastAPI()
    register_exception_handlers(probe_app)
    probe_app.dependency_overrides[get_jwt_verifier] = lambda: verifier
    probe_app.dependency_overrides[get_membership_repository] = lambda: _StubMembershipRepo(
        [membership]
    )

    @probe_app.get("/_probe")
    def _probe(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    ) -> dict[str, str]:
        return {"role": principal.role.value, "tenant_id": str(principal.tenant_id)}

    client = TestClient(probe_app)
    token = _make_token(sub=str(user_id))

    assert client.get("/_probe").status_code == 401
    assert client.get("/_probe", headers={"Authorization": f"Bearer {token}"}).status_code == 400
    assert (
        client.get(
            "/_probe",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Tenant-Id": str(other_tenant_id),
            },
        ).status_code
        == 403
    )

    res = client.get(
        "/_probe",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": str(tenant_id),
        },
    )
    assert res.status_code == 200
    assert res.json() == {"role": "hr", "tenant_id": str(tenant_id)}


@pytest.mark.parametrize(
    ("status", "deleted_at"),
    [
        (MembershipStatus.DISABLED, None),
        (MembershipStatus.INVITED, None),
        (MembershipStatus.ACTIVE, datetime.now(UTC)),
    ],
)
def test_hybrid_auth_rejects_disabled_invited_or_soft_deleted_memberships(
    status: MembershipStatus,
    deleted_at: datetime | None,
) -> None:
    user_id = uuid4()
    tenant_id = uuid4()
    now = datetime.now(UTC)

    inactive_membership = Membership(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        email="revoked@okteam.es",
        display_name="Revoked User",
        role=UserRole.STORE_MANAGER,
        status=status,
        store_ids=frozenset(),
        created_at=now,
        updated_at=now,
        deleted_at=deleted_at,
    )
    verifier = SupabaseJWTVerifier(
        supabase_url="https://example.supabase.co",
        jwt_secret=TEST_SECRET,
    )
    probe_app = FastAPI()
    register_exception_handlers(probe_app)
    probe_app.dependency_overrides[get_jwt_verifier] = lambda: verifier
    probe_app.dependency_overrides[get_membership_repository] = lambda: _StubMembershipRepo(
        [inactive_membership]
    )

    @probe_app.get("/_probe")
    def _probe(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    ) -> dict[str, str]:
        return {"role": principal.role.value}

    client = TestClient(probe_app)
    token = _make_token(sub=str(user_id))
    res = client.get(
        "/_probe",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": str(tenant_id),
        },
    )
    assert res.status_code == 403


@pytest.mark.parametrize(
    "schema_name",
    ["auth", "storage", "realtime", "supabase_functions", "extensions", "vault"],
)
def test_alembic_excludes_supabase_system_schemas(schema_name: str) -> None:
    alembic_env = _load_alembic_env()
    assert schema_name in alembic_env.EXCLUDED_SCHEMAS
    assert alembic_env.include_name(schema_name, "schema", {}) is False
    assert alembic_env.include_name("users", "table", {"schema_name": schema_name}) is False
    assert alembic_env.include_name("public", "schema", {}) is True
