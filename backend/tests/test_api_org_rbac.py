"""End-to-end API tests covering Phase 1 Tenant, Store, Membership RBAC & isolation."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.domain.organization import Membership, Store, Tenant, TenantMembershipSummary
from app.domain.security import MembershipStatus, UserRole
from app.infrastructure.config import Settings
from app.infrastructure.supabase_jwt import SupabaseJWTVerifier, sign_dev_hs256_token
from app.interfaces.api.deps import (
    get_commit_fn,
    get_jwt_verifier,
    get_membership_repository,
    get_store_repository,
    get_tenant_repository,
)
from app.main import create_app

DEV_SECRET = "phase1-api-test-secret-minimum-32-bytes-key"


class InMemoryOrgRepos:
    """Deterministic in-memory repositories implementing domain Protocols for fast API tests."""

    def __init__(self) -> None:
        self.tenants: dict[UUID, Tenant] = {}
        self.stores: dict[UUID, Store] = {}
        self.memberships: dict[UUID, Membership] = {}

    # --- TenantRepository ---
    async def get_by_id(self, tenant_id: UUID) -> Tenant | None:
        t = self.tenants.get(tenant_id)
        return t if t is not None and t.deleted_at is None else None

    async def exists_active_cif(
        self,
        cif_nif: str,
        *,
        exclude_tenant_id: UUID | None = None,
    ) -> bool:
        return any(
            t.deleted_at is None
            and t.cif_nif.upper() == cif_nif.upper()
            and t.id != exclude_tenant_id
            for t in self.tenants.values()
        )

    async def create(self, tenant: Tenant) -> Tenant:
        self.tenants[tenant.id] = tenant
        return tenant

    async def update(
        self,
        tenant_id: UUID,
        *,
        legal_name: str,
        trade_name: str,
        cif_nif: str,
        default_timezone: str,
        default_locale: str,
        updated_at: datetime,
    ) -> Tenant | None:
        existing = await self.get_by_id(tenant_id)
        if existing is None:
            return None
        updated = replace(
            existing,
            legal_name=legal_name,
            trade_name=trade_name,
            cif_nif=cif_nif,
            default_timezone=default_timezone,
            default_locale=default_locale,
            updated_at=updated_at,
        )
        self.tenants[tenant_id] = updated
        return updated


class InMemoryStoreRepo:
    def __init__(self, state: InMemoryOrgRepos) -> None:
        self._state = state

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        allowed_store_ids: frozenset[UUID] | None = None,
        include_inactive: bool = False,
    ) -> list[Store]:
        if allowed_store_ids is not None and not allowed_store_ids:
            return []
        result = [
            s
            for s in self._state.stores.values()
            if s.tenant_id == tenant_id
            and s.deleted_at is None
            and (include_inactive or s.is_active)
            and (allowed_store_ids is None or s.id in allowed_store_ids)
        ]
        return sorted(result, key=lambda s: (s.code, s.name))

    async def get_by_id(self, tenant_id: UUID, store_id: UUID) -> Store | None:
        s = self._state.stores.get(store_id)
        if s is None or s.tenant_id != tenant_id or s.deleted_at is not None:
            return None
        return s

    async def exists_active_code(
        self,
        tenant_id: UUID,
        code: str,
        *,
        exclude_store_id: UUID | None = None,
    ) -> bool:
        return any(
            s.tenant_id == tenant_id
            and s.deleted_at is None
            and s.code.upper() == code.upper()
            and s.id != exclude_store_id
            for s in self._state.stores.values()
        )

    async def count_existing_in_tenant(
        self,
        tenant_id: UUID,
        store_ids: frozenset[UUID],
    ) -> int:
        return sum(
            1
            for sid in store_ids
            if (s := self._state.stores.get(sid)) is not None
            and s.tenant_id == tenant_id
            and s.deleted_at is None
        )

    async def create(self, store: Store) -> Store:
        self._state.stores[store.id] = store
        return store

    async def update(
        self,
        tenant_id: UUID,
        store_id: UUID,
        *,
        name: str,
        code: str,
        timezone: str,
        address: str,
        city: str,
        postal_code: str,
        is_active: bool,
        updated_at: datetime,
    ) -> Store | None:
        existing = await self.get_by_id(tenant_id, store_id)
        if existing is None:
            return None
        updated = replace(
            existing,
            name=name,
            code=code,
            timezone=timezone,
            address=address,
            city=city,
            postal_code=postal_code,
            is_active=is_active,
            updated_at=updated_at,
        )
        self._state.stores[store_id] = updated
        return updated

    async def soft_delete(
        self,
        tenant_id: UUID,
        store_id: UUID,
        *,
        deleted_at: datetime,
    ) -> bool:
        existing = await self.get_by_id(tenant_id, store_id)
        if existing is None:
            return False
        self._state.stores[store_id] = replace(
            existing,
            is_active=False,
            deleted_at=deleted_at,
            updated_at=deleted_at,
        )
        return True


class InMemoryMembershipRepo:
    def __init__(self, state: InMemoryOrgRepos) -> None:
        self._state = state

    async def list_summaries_for_user(self, user_id: UUID) -> list[TenantMembershipSummary]:
        summaries: list[TenantMembershipSummary] = []
        for m in self._state.memberships.values():
            if m.user_id != user_id or m.deleted_at is not None:
                continue
            if m.status != MembershipStatus.ACTIVE:
                continue
            t = self._state.tenants.get(m.tenant_id)
            if t is None or t.deleted_at is not None:
                continue
            summaries.append(
                TenantMembershipSummary(
                    tenant_id=t.id,
                    tenant_legal_name=t.legal_name,
                    tenant_trade_name=t.trade_name,
                    default_timezone=t.default_timezone,
                    default_locale=t.default_locale,
                    role=m.role,
                    status=m.status,
                    store_ids=m.store_ids,
                )
            )
        return summaries

    async def get_active_for_user_in_tenant(
        self,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Membership | None:
        for m in self._state.memberships.values():
            if (
                m.tenant_id == tenant_id
                and m.user_id == user_id
                and m.status == MembershipStatus.ACTIVE
                and m.deleted_at is None
            ):
                t = self._state.tenants.get(tenant_id)
                if t is not None and t.deleted_at is None:
                    return m
        return None

    async def get_by_id(self, tenant_id: UUID, membership_id: UUID) -> Membership | None:
        m = self._state.memberships.get(membership_id)
        if m is None or m.tenant_id != tenant_id or m.deleted_at is not None:
            return None
        return m

    async def exists_active_user_in_tenant(self, tenant_id: UUID, user_id: UUID) -> bool:
        return any(
            m.tenant_id == tenant_id and m.user_id == user_id and m.deleted_at is None
            for m in self._state.memberships.values()
        )

    async def count_active_owners(self, tenant_id: UUID) -> int:
        return sum(
            1
            for m in self._state.memberships.values()
            if m.tenant_id == tenant_id
            and m.role == UserRole.OWNER
            and m.status == MembershipStatus.ACTIVE
            and m.deleted_at is None
        )

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        filter_store_ids: frozenset[UUID] | None = None,
        only_user_id: UUID | None = None,
    ) -> list[Membership]:
        items: list[Membership] = []
        for m in self._state.memberships.values():
            if m.tenant_id != tenant_id or m.deleted_at is not None:
                continue
            if filter_store_ids is not None:
                shares_store = bool(m.store_ids & filter_store_ids)
                is_self = only_user_id is not None and m.user_id == only_user_id
                if not (shares_store or is_self):
                    continue
            elif only_user_id is not None and m.user_id != only_user_id:
                continue
            items.append(m)
        return sorted(items, key=lambda item: item.created_at)

    async def create(self, membership: Membership) -> Membership:
        self._state.memberships[membership.id] = membership
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
        existing = await self.get_by_id(tenant_id, membership_id)
        if existing is None:
            return None
        updated = replace(
            existing,
            display_name=display_name,
            role=role,
            status=status,
            store_ids=store_ids,
            updated_at=updated_at,
        )
        self._state.memberships[membership_id] = updated
        return updated

    async def soft_delete(
        self,
        tenant_id: UUID,
        membership_id: UUID,
        *,
        deleted_at: datetime,
    ) -> bool:
        existing = await self.get_by_id(tenant_id, membership_id)
        if existing is None:
            return False
        self._state.memberships[membership_id] = replace(
            existing,
            status=MembershipStatus.DISABLED,
            deleted_at=deleted_at,
            updated_at=deleted_at,
        )
        return True


def _build_test_client(
    *,
    app_env: str = "development",
    dev_auth_enabled: bool = True,
) -> tuple[TestClient, InMemoryOrgRepos]:
    settings = Settings(
        app_env=app_env,
        dev_auth_enabled=dev_auth_enabled,
        supabase_jwt_secret=DEV_SECRET,
    )
    app = create_app(settings_override=settings)
    state = InMemoryOrgRepos()
    store_repo = InMemoryStoreRepo(state)
    membership_repo = InMemoryMembershipRepo(state)
    verifier = SupabaseJWTVerifier(
        supabase_url="https://example.supabase.co",
        jwt_secret=DEV_SECRET,
    )

    async def _noop_commit() -> None:
        return None

    app.dependency_overrides[get_jwt_verifier] = lambda: verifier
    app.dependency_overrides[get_tenant_repository] = lambda: state
    app.dependency_overrides[get_store_repository] = lambda: store_repo
    app.dependency_overrides[get_membership_repository] = lambda: membership_repo
    app.dependency_overrides[get_commit_fn] = lambda: _noop_commit
    return TestClient(app), state


def _auth_headers(user_id: UUID, email: str, tenant_id: str | None = None) -> dict[str, str]:
    token = sign_dev_hs256_token(user_id=user_id, email=email, secret=DEV_SECRET)
    headers = {"Authorization": f"Bearer {token}"}
    if tenant_id is not None:
        headers["X-Tenant-Id"] = tenant_id
    return headers


def test_dev_token_route_physically_disabled_in_production() -> None:
    prod_client, _ = _build_test_client(app_env="production", dev_auth_enabled=True)
    res = prod_client.post("/api/v1/dev/token", json={"email": "owner@okteam.es"})
    assert res.status_code == 404

    dev_client, _ = _build_test_client(app_env="development", dev_auth_enabled=True)
    res_dev = dev_client.post("/api/v1/dev/token", json={"email": "owner@okteam.es"})
    assert res_dev.status_code == 200
    assert "access_token" in res_dev.json()


def test_full_org_store_rbac_isolation_and_soft_delete() -> None:
    client, state = _build_test_client()

    owner_a_id = uuid4()
    owner_b_id = uuid4()
    hr_id = uuid4()
    manager_id = uuid4()
    accountant_id = uuid4()

    # 1. Owner A onboards Tenant A
    res_ta = client.post(
        "/api/v1/tenants",
        headers=_auth_headers(owner_a_id, "owner.a@dragon.es"),
        json={
            "legal_name": "Restaurante Dragón S.L.",
            "trade_name": "金龙餐饮",
            "cif_nif": "B12345678",
            "default_timezone": "Europe/Madrid",
            "default_locale": "zh-CN",
            "owner_display_name": "陈老板",
        },
    )
    assert res_ta.status_code == 201
    tenant_a_id = res_ta.json()["id"]

    # Duplicate CIF rejected with 409
    dup_res = client.post(
        "/api/v1/tenants",
        headers=_auth_headers(owner_b_id, "owner.b@other.es"),
        json={
            "legal_name": "Duplicate CIF S.L.",
            "trade_name": "重复税号",
            "cif_nif": "b12345678",
        },
    )
    assert dup_res.status_code == 409

    # Owner B onboards Tenant B
    res_tb = client.post(
        "/api/v1/tenants",
        headers=_auth_headers(owner_b_id, "owner.b@bazar.es"),
        json={
            "legal_name": "Bazar Sol S.L.",
            "trade_name": "太阳百货",
            "cif_nif": "B87654321",
        },
    )
    assert res_tb.status_code == 201
    tenant_b_id = res_tb.json()["id"]

    # 2. Owner A creates 2 stores in Tenant A (Madrid & Canary)
    s1_res = client.post(
        "/api/v1/stores",
        headers=_auth_headers(owner_a_id, "owner.a@dragon.es", tenant_a_id),
        json={
            "name": "马德里 Sol 总店",
            "code": "MAD-01",
            "timezone": "Europe/Madrid",
            "city": "Madrid",
        },
    )
    assert s1_res.status_code == 201
    store_mad_id = s1_res.json()["id"]

    s2_res = client.post(
        "/api/v1/stores",
        headers=_auth_headers(owner_a_id, "owner.a@dragon.es", tenant_a_id),
        json={
            "name": "加那利特内里费店",
            "code": "TFN-01",
            "timezone": "Atlantic/Canary",
            "city": "Santa Cruz de Tenerife",
        },
    )
    assert s2_res.status_code == 201
    store_tfn_id = s2_res.json()["id"]

    # Owner B creates a store in Tenant B
    sb_res = client.post(
        "/api/v1/stores",
        headers=_auth_headers(owner_b_id, "owner.b@bazar.es", tenant_b_id),
        json={"name": "Tenant B Store", "code": "TB-01", "timezone": "Europe/Madrid"},
    )
    assert sb_res.status_code == 201
    store_b_id = sb_res.json()["id"]

    # 3. Cross-tenant isolation: Owner B cannot access Tenant A stores
    assert (
        client.get(
            "/api/v1/stores",
            headers=_auth_headers(owner_b_id, "owner.b@bazar.es", tenant_a_id),
        ).status_code
        == 403
    )

    # 4. Cross-tenant store binding rejected (Owner A cannot assign Tenant B's store)
    bad_bind = client.post(
        "/api/v1/memberships",
        headers=_auth_headers(owner_a_id, "owner.a@dragon.es", tenant_a_id),
        json={
            "user_id": str(manager_id),
            "email": "manager@dragon.es",
            "display_name": "王店长",
            "role": "store_manager",
            "store_ids": [store_b_id],
        },
    )
    assert bad_bind.status_code == 422

    # 5. Add Store Manager (assigned ONLY to store_mad_id), HR, and Accountant in Tenant A
    mgr_res = client.post(
        "/api/v1/memberships",
        headers=_auth_headers(owner_a_id, "owner.a@dragon.es", tenant_a_id),
        json={
            "user_id": str(manager_id),
            "email": "manager@dragon.es",
            "display_name": "王店长",
            "role": "store_manager",
            "store_ids": [store_mad_id],
        },
    )
    assert mgr_res.status_code == 201

    hr_res = client.post(
        "/api/v1/memberships",
        headers=_auth_headers(owner_a_id, "owner.a@dragon.es", tenant_a_id),
        json={
            "user_id": str(hr_id),
            "email": "hr@dragon.es",
            "display_name": "林人事",
            "role": "hr",
            "store_ids": [],
        },
    )
    assert hr_res.status_code == 201

    acc_res = client.post(
        "/api/v1/memberships",
        headers=_auth_headers(owner_a_id, "owner.a@dragon.es", tenant_a_id),
        json={
            "user_id": str(accountant_id),
            "email": "gestoria@asesoria.es",
            "display_name": "Gestoría López",
            "role": "accountant",
            "store_ids": [],
        },
    )
    assert acc_res.status_code == 201

    # 6. Store Manager sees ONLY store_mad_id and gets 403 on store_tfn_id & store creation
    mgr_list = client.get(
        "/api/v1/stores",
        headers=_auth_headers(manager_id, "manager@dragon.es", tenant_a_id),
    )
    assert mgr_list.status_code == 200
    assert [item["id"] for item in mgr_list.json()["items"]] == [store_mad_id]

    assert (
        client.get(
            f"/api/v1/stores/{store_tfn_id}",
            headers=_auth_headers(manager_id, "manager@dragon.es", tenant_a_id),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/stores",
            headers=_auth_headers(manager_id, "manager@dragon.es", tenant_a_id),
            json={"name": "Unauthorized", "code": "NO-01", "timezone": "Europe/Madrid"},
        ).status_code
        == 403
    )

    # 7. Accountant sees all Tenant A stores (read-only) and cannot mutate anything
    acc_stores = client.get(
        "/api/v1/stores",
        headers=_auth_headers(accountant_id, "gestoria@asesoria.es", tenant_a_id),
    )
    assert acc_stores.status_code == 200
    assert acc_stores.json()["total"] == 2

    assert (
        client.delete(
            f"/api/v1/stores/{store_mad_id}",
            headers=_auth_headers(accountant_id, "gestoria@asesoria.es", tenant_a_id),
        ).status_code
        == 403
    )

    # 8. HR cannot update company profile (owner-only) and cannot modify/demote Owner
    assert (
        client.patch(
            "/api/v1/tenants/current",
            headers=_auth_headers(hr_id, "hr@dragon.es", tenant_a_id),
            json={
                "legal_name": "Hijacked S.L.",
                "trade_name": "越权修改",
                "cif_nif": "B12345678",
                "default_timezone": "Europe/Madrid",
                "default_locale": "zh-CN",
            },
        ).status_code
        == 403
    )

    owner_membership_id = next(
        str(m.id)
        for m in state.memberships.values()
        if m.tenant_id == UUID(tenant_a_id) and m.role == UserRole.OWNER
    )
    assert (
        client.patch(
            f"/api/v1/memberships/{owner_membership_id}",
            headers=_auth_headers(hr_id, "hr@dragon.es", tenant_a_id),
            json={
                "display_name": "Demoted",
                "role": "employee",
                "status": "active",
                "store_ids": [],
            },
        ).status_code
        == 403
    )

    # 9. Last-owner protection: even Owner A cannot remove or demote themselves if sole owner
    assert (
        client.delete(
            f"/api/v1/memberships/{owner_membership_id}",
            headers=_auth_headers(owner_a_id, "owner.a@dragon.es", tenant_a_id),
        ).status_code
        == 422
    )

    # 10. Soft-delete store: hidden from API, but row remains intact with deleted_at set
    del_store = client.delete(
        f"/api/v1/stores/{store_tfn_id}",
        headers=_auth_headers(owner_a_id, "owner.a@dragon.es", tenant_a_id),
    )
    assert del_store.status_code == 204
    persisted_store = state.stores[UUID(store_tfn_id)]
    assert persisted_store.deleted_at is not None
    assert persisted_store.is_active is False

    # /auth/me returns active tenant summary
    me_res = client.get(
        "/api/v1/auth/me",
        headers=_auth_headers(owner_a_id, "owner.a@dragon.es"),
    )
    assert me_res.status_code == 200
    assert len(me_res.json()["tenants"]) == 1
    assert me_res.json()["tenants"][0]["role"] == "owner"


def test_membership_scoping_employee_isolation_and_privilege_escalation_guards() -> None:
    client, state = _build_test_client()

    owner_id = uuid4()
    hr_id = uuid4()
    mgr_mad_id = uuid4()
    acc_id = uuid4()
    emp_mad_id = uuid4()
    emp_tfn_id = uuid4()

    # Setup Tenant + 2 Stores
    tenant_id = client.post(
        "/api/v1/tenants",
        headers=_auth_headers(owner_id, "owner@dragon.es"),
        json={
            "legal_name": "Restaurante Dragón S.L.",
            "trade_name": "金龙餐饮",
            "cif_nif": "B11112222",
        },
    ).json()["id"]

    store_mad_id = client.post(
        "/api/v1/stores",
        headers=_auth_headers(owner_id, "owner@dragon.es", tenant_id),
        json={"name": "Madrid", "code": "MAD-01", "timezone": "Europe/Madrid"},
    ).json()["id"]
    store_tfn_id = client.post(
        "/api/v1/stores",
        headers=_auth_headers(owner_id, "owner@dragon.es", tenant_id),
        json={"name": "Tenerife", "code": "TFN-01", "timezone": "Atlantic/Canary"},
    ).json()["id"]

    owner_membership_id = next(
        str(m.id)
        for m in state.memberships.values()
        if m.tenant_id == UUID(tenant_id) and m.role == UserRole.OWNER
    )

    # Create HR, Store Manager (MAD), Accountant, Employee (MAD), Employee (TFN)
    for uid, email, name, role, stores in [
        (hr_id, "hr@dragon.es", "HR", "hr", []),
        (mgr_mad_id, "mgr@dragon.es", "Manager MAD", "store_manager", [store_mad_id]),
        (acc_id, "acc@asesoria.es", "Gestoría", "accountant", []),
        (emp_mad_id, "emp.mad@dragon.es", "Emp MAD", "employee", [store_mad_id]),
        (emp_tfn_id, "emp.tfn@dragon.es", "Emp TFN", "employee", [store_tfn_id]),
    ]:
        created = client.post(
            "/api/v1/memberships",
            headers=_auth_headers(owner_id, "owner@dragon.es", tenant_id),
            json={
                "user_id": str(uid),
                "email": email,
                "display_name": name,
                "role": role,
                "store_ids": stores,
            },
        )
        assert created.status_code == 201

    emp_mad_membership_id = next(
        str(m.id) for m in state.memberships.values() if m.user_id == emp_mad_id
    )

    # 1. GET /api/v1/memberships scoping across all 5 roles
    for uid, email in [
        (owner_id, "owner@dragon.es"),
        (hr_id, "hr@dragon.es"),
        (acc_id, "acc@asesoria.es"),
    ]:
        res = client.get("/api/v1/memberships", headers=_auth_headers(uid, email, tenant_id))
        assert res.status_code == 200
        assert res.json()["total"] == 6

    # Store Manager sees ONLY members sharing MAD store (+ self) — NOT emp_tfn_id or owner/hr/acc
    mgr_members = client.get(
        "/api/v1/memberships",
        headers=_auth_headers(mgr_mad_id, "mgr@dragon.es", tenant_id),
    )
    assert mgr_members.status_code == 200
    mgr_visible_users = {item["user_id"] for item in mgr_members.json()["items"]}
    assert mgr_visible_users == {str(mgr_mad_id), str(emp_mad_id)}
    assert str(emp_tfn_id) not in mgr_visible_users

    # Employee sees ONLY self in memberships AND ONLY assigned store in stores
    emp_members = client.get(
        "/api/v1/memberships",
        headers=_auth_headers(emp_mad_id, "emp.mad@dragon.es", tenant_id),
    )
    assert emp_members.status_code == 200
    assert [item["user_id"] for item in emp_members.json()["items"]] == [str(emp_mad_id)]

    emp_stores = client.get(
        "/api/v1/stores",
        headers=_auth_headers(emp_mad_id, "emp.mad@dragon.es", tenant_id),
    )
    assert emp_stores.status_code == 200
    assert [item["id"] for item in emp_stores.json()["items"]] == [store_mad_id]
    assert (
        client.get(
            f"/api/v1/stores/{store_tfn_id}",
            headers=_auth_headers(emp_mad_id, "emp.mad@dragon.es", tenant_id),
        ).status_code
        == 403
    )

    # 2. Non-admin roles (store_manager, accountant, employee) receive 403 on ALL membership writes
    for uid, email in [
        (mgr_mad_id, "mgr@dragon.es"),
        (acc_id, "acc@asesoria.es"),
        (emp_mad_id, "emp.mad@dragon.es"),
    ]:
        headers = _auth_headers(uid, email, tenant_id)
        assert (
            client.post(
                "/api/v1/memberships",
                headers=headers,
                json={
                    "user_id": str(uuid4()),
                    "email": "x@dragon.es",
                    "display_name": "X",
                    "role": "employee",
                    "store_ids": [],
                },
            ).status_code
            == 403
        )
        assert (
            client.patch(
                f"/api/v1/memberships/{emp_mad_membership_id}",
                headers=headers,
                json={
                    "display_name": "Tampered",
                    "role": "employee",
                    "status": "active",
                    "store_ids": [],
                },
            ).status_code
            == 403
        )
        assert (
            client.delete(
                f"/api/v1/memberships/{emp_mad_membership_id}",
                headers=headers,
            ).status_code
            == 403
        )
        # Non-existent ID also returns 403 (no 404 existence oracle)
        assert (
            client.delete(
                f"/api/v1/memberships/{uuid4()}",
                headers=headers,
            ).status_code
            == 403
        )

    # Employee also denied tenant & store writes
    emp_headers = _auth_headers(emp_mad_id, "emp.mad@dragon.es", tenant_id)
    assert (
        client.patch(
            "/api/v1/tenants/current",
            headers=emp_headers,
            json={
                "legal_name": "X",
                "trade_name": "X",
                "cif_nif": "B11112222",
                "default_timezone": "Europe/Madrid",
                "default_locale": "zh-CN",
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/stores",
            headers=emp_headers,
            json={"name": "Rogue", "code": "RG-1", "timezone": "Europe/Madrid"},
        ).status_code
        == 403
    )

    # 3. HR cannot create a new owner OR archive an existing owner
    hr_headers = _auth_headers(hr_id, "hr@dragon.es", tenant_id)
    assert (
        client.post(
            "/api/v1/memberships",
            headers=hr_headers,
            json={
                "user_id": str(uuid4()),
                "email": "fake.owner@dragon.es",
                "display_name": "Fake Owner",
                "role": "owner",
                "store_ids": [],
            },
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/memberships/{owner_membership_id}",
            headers=hr_headers,
        ).status_code
        == 403
    )

    # 4. Last-owner protection on PATCH (both demoting role AND disabling status -> 422)
    owner_headers = _auth_headers(owner_id, "owner@dragon.es", tenant_id)
    assert (
        client.patch(
            f"/api/v1/memberships/{owner_membership_id}",
            headers=owner_headers,
            json={
                "display_name": "Sole Owner",
                "role": "hr",
                "status": "active",
                "store_ids": [],
            },
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/v1/memberships/{owner_membership_id}",
            headers=owner_headers,
            json={
                "display_name": "Sole Owner",
                "role": "owner",
                "status": "disabled",
                "store_ids": [],
            },
        ).status_code
        == 422
    )

    # 5. Live revocation: disabling a member immediately returns 403 on next API call
    disable_res = client.patch(
        f"/api/v1/memberships/{emp_mad_membership_id}",
        headers=owner_headers,
        json={
            "display_name": "Emp MAD",
            "role": "employee",
            "status": "disabled",
            "store_ids": [store_mad_id],
        },
    )
    assert disable_res.status_code == 200
    assert (
        client.get(
            "/api/v1/stores",
            headers=_auth_headers(emp_mad_id, "emp.mad@dragon.es", tenant_id),
        ).status_code
        == 403
    )
