# Phase 1 Design Spec: Multi-Tenant Organization, Stores & RBAC Foundation

- **Status**: Proposed (Awaiting User Approval)
- **Date**: 2026-11-25
- **Phase**: Phase 1 (方案 A — 组织与权限闭环)

## 1. Scope & Non-Goals

### In Scope
1. **Multi-Tenant Organization (`tenants`)**: Create and update company profile (legal name, Chinese/common trade name, Spanish CIF/NIF, default IANA timezone, default locale `zh-CN`/`es-ES`).
2. **Stores / Work Centers (`stores`)**: Full CRUD with soft-delete archival, required IANA timezone per store (e.g., `Europe/Madrid`, `Atlantic/Canary`), and strict tenant + store-scope visibility filtering.
3. **5-Role Membership & Store Scope (`memberships`, `membership_stores`)**: Canonical Day-1 roles (`owner`, `hr`, `store_manager`, `accountant`, `employee`), live DB-backed authorization, multi-store assignment, and last-owner protection.
4. **Auth & Local Dev Loop**: Supabase Auth JWT verification (JWKS ES256/RS256 + optional HS256), `GET /api/v1/auth/me` tenant bootstrap, and strictly development-only token helper (`POST /api/v1/dev/token`) for offline/local verification.
5. **Frontend Management Console**: Login & Onboarding, AppShell with tenant switcher and `zh-CN`/`es-ES` switch, Stores page (Template 1: high-density table + right Sheet drawer), Members & RBAC page (Template 1), and Organization Settings page (Template 2: two-column form), built on official `shadcn/ui` primitives.
6. **First Reversible Alembic Migration**: `0001_phase1_org_rbac` strictly inside `public` schema, never touching Supabase system schemas (`auth`, `storage`, `realtime`, `supabase_functions`, `extensions`, `vault`).

### Explicit Non-Goals (Deferred to Phase 2+)
- Employee HR records, contract types, encrypted PII (`NIE/DNI`, `IBAN`, `NAF`) — Phase 2.
- Append-only daily time tracking (*Registro de Jornada*), kiosk mode, labor-law hour alerts — Phase 3.
- Shift scheduling calendar and private file 60s signed URLs — Phase 4.

---

## 2. Architecture: Hybrid Auth & RBAC Authority

1. **Identity Layer (`VerifiedIdentity`)**:
   - `SupabaseJWTVerifier.verify_identity(token)` validates JWT signature (`ES256`/`RS256` via JWKS or `HS256` via `SUPABASE_JWT_SECRET`), audience, and `exp`, returning `(user_id: UUID, email: str | None)`.
   - Never trusts client-mutable `user_metadata`.
2. **Tenant Authorization Layer (`AuthenticatedPrincipal`)**:
   - Protected tenant endpoints require `Authorization: Bearer <jwt>` + `X-Tenant-Id: <uuid>` header.
   - FastAPI dependency resolves active (`status == 'active'`, `deleted_at IS NULL`) `Membership` + assigned `store_ids` live from PostgreSQL.
   - Revoking a role, disabling a member, or changing assigned stores takes effect in **0 seconds** without waiting for JWT expiry or calling Supabase Admin API.
   - Supports one user (e.g., external `accountant` / *Gestoría*) belonging to multiple tenants cleanly.
3. **Role Permission Matrix (Phase 1)**:

| Action | `owner` | `hr` | `store_manager` | `accountant` | `employee` |
| :--- | :---: | :---: | :---: | :---: | :---: |
| View current tenant | All | All | All | All | All |
| Update current tenant | Yes | No | No | No | No |
| List / View stores | All tenant | All tenant | Assigned `store_ids` only | All tenant (RO) | Assigned `store_ids` only |
| Create / Update / Archive store | Yes | Yes | No | No | No |
| List members | All tenant | All tenant | Members sharing assigned `store_ids` | All tenant (RO) | Self only |
| Add / Update / Remove member | Yes | Yes (cannot assign/modify `owner`) | No | No | No |

4. **Local Development Auth Guard**:
   - `POST /api/v1/dev/token` is mounted **only** when `APP_ENV == "development"` AND `DEV_AUTH_ENABLED == True` AND `SUPABASE_JWT_SECRET` is configured. Production/staging environments physically do not register this route (verified by unit test).

---

## 3. PostgreSQL Schema & Alembic Migration (`public` schema only)

All timestamps use `TIMESTAMPTZ` (UTC). No physical deletes; soft delete via `deleted_at`. Zero cross-schema foreign keys to `auth.users`.

1. **`tenants`**
   - `id UUID PRIMARY KEY`
   - `legal_name VARCHAR(200) NOT NULL`
   - `trade_name VARCHAR(200) NOT NULL`
   - `cif_nif VARCHAR(32) NOT NULL` — partial unique index `uq_tenants_cif_nif_active` on `(upper(cif_nif)) WHERE deleted_at IS NULL`
   - `default_timezone VARCHAR(64) NOT NULL DEFAULT 'Europe/Madrid'`
   - `default_locale VARCHAR(10) NOT NULL DEFAULT 'zh-CN'`
   - `created_at TIMESTAMPTZ NOT NULL`, `updated_at TIMESTAMPTZ NOT NULL`, `deleted_at TIMESTAMPTZ NULL`

2. **`stores`**
   - `id UUID PRIMARY KEY`
   - `tenant_id UUID NOT NULL REFERENCES tenants(id)` (indexed)
   - `name VARCHAR(160) NOT NULL`
   - `code VARCHAR(32) NOT NULL` — partial unique index `uq_stores_tenant_code_active` on `(tenant_id, upper(code)) WHERE deleted_at IS NULL`
   - `timezone VARCHAR(64) NOT NULL`
   - `address VARCHAR(300) NOT NULL DEFAULT ''`, `city VARCHAR(100) NOT NULL DEFAULT ''`, `postal_code VARCHAR(16) NOT NULL DEFAULT ''`
   - `is_active BOOLEAN NOT NULL DEFAULT TRUE`
   - `created_at TIMESTAMPTZ NOT NULL`, `updated_at TIMESTAMPTZ NOT NULL`, `deleted_at TIMESTAMPTZ NULL`

3. **`memberships`**
   - `id UUID PRIMARY KEY`
   - `tenant_id UUID NOT NULL REFERENCES tenants(id)` (indexed)
   - `user_id UUID NOT NULL` (references Supabase `auth.users.id` logically; no cross-schema FK)
   - `email VARCHAR(255) NOT NULL`, `display_name VARCHAR(120) NOT NULL`
   - `role VARCHAR(32) NOT NULL` (`CHECK role IN ('owner','hr','store_manager','accountant','employee')`)
   - `status VARCHAR(20) NOT NULL DEFAULT 'active'` (`CHECK status IN ('active','invited','disabled')`)
   - `created_at TIMESTAMPTZ NOT NULL`, `updated_at TIMESTAMPTZ NOT NULL`, `deleted_at TIMESTAMPTZ NULL`
   - Partial unique index `uq_memberships_tenant_user_active` on `(tenant_id, user_id) WHERE deleted_at IS NULL`

4. **`membership_stores`**
   - `membership_id UUID NOT NULL REFERENCES memberships(id)`
   - `tenant_id UUID NOT NULL REFERENCES tenants(id)`
   - `store_id UUID NOT NULL REFERENCES stores(id)`
   - `created_at TIMESTAMPTZ NOT NULL`
   - `PRIMARY KEY (membership_id, store_id)`, index on `(tenant_id, store_id)`.
   - Repository enforces that every assigned `store_id` belongs to the same `tenant_id` (prevents cross-tenant store binding).

---

## 4. Backend Clean Architecture & API Contracts

- **`app/domain/`** (Pure Python stdlib only, checked by `scripts/check_domain_purity.py`):
  - `security.py`: `UserRole`, `VerifiedIdentity`, `AuthenticatedPrincipal`, domain auth/scope exceptions and permission predicates.
  - `organization.py`: `Tenant`, `Store`, `Membership` dataclasses, `validate_iana_timezone()` via stdlib `zoneinfo.available_timezones()`, `normalize_cif_nif()`, domain exceptions (`EntityNotFoundError`, `ConflictError`, `ValidationDomainError`, `LastOwnerProtectionError`).
  - `repositories.py`: `TenantRepository`, `StoreRepository`, `MembershipRepository` (`Protocol`).
- **`app/application/`**:
  - `onboarding.py`: `GetMyProfileUseCase`, `CreateTenantUseCase` (creates tenant + owner membership atomically).
  - `tenants.py`: `GetCurrentTenantUseCase`, `UpdateCurrentTenantUseCase`.
  - `stores.py`: `ListStoresUseCase`, `CreateStoreUseCase`, `UpdateStoreUseCase`, `ArchiveStoreUseCase`.
  - `memberships.py`: `ListMembershipsUseCase`, `CreateMembershipUseCase`, `UpdateMembershipUseCase`, `ArchiveMembershipUseCase` (guards against removing/demoting the last active `owner` and guards `hr` from escalating to/modifying `owner`).
- **`app/infrastructure/`**:
  - `models.py`: SQLAlchemy 2.0 ORM models (`TenantModel`, `StoreModel`, `MembershipModel`, `MembershipStoreModel`).
  - `repositories.py`: Async SQLAlchemy repository implementations enforcing `tenant_id` predicates on every single query.
  - `supabase_jwt.py`: `SupabaseJWTVerifier` returning `VerifiedIdentity`.
- **`app/interfaces/api/`**:
  - `deps.py`: `get_verified_identity`, `get_current_principal` (loads live membership via `X-Tenant-Id`), repository factories, domain-to-HTTP exception mapping.
  - Routers: `auth.py`, `tenants.py`, `stores.py`, `memberships.py`, `dev_auth.py` (dev-only).
  - After backend completion, `scripts/sync-contracts.ps1` exports `contracts/openapi.json` and regenerates `frontend/src/api/generated/schema.d.ts`.

---

## 5. Frontend Architecture & `shadcn/ui` Component Plan

### Official `shadcn/ui` Primitives (via `shadcn` MCP into `frontend/src/components/ui/`)
Installed from the official `@shadcn` registry (no hand-invented outdated APIs):
- Already installed: `button`
- To add via `shadcn`: `badge`, `card`, `checkbox`, `input`, `label`, `select`, `separator`, `sheet`, `skeleton`, `table`, `alert-dialog`

### Layout & Feature Modules (`frontend/src/`)
1. **`components/layout/AppShell.tsx`**: Neutral `bg-slate-50` canvas, `bg-white` left sidebar (`border-r border-slate-200`) with `teal-700` active indicator (<= 5% area), tenant switcher (`Select`), `zh-CN`/`es-ES` switcher, current role badge, and sign-out action.
2. **`features/auth/`**:
   - `AuthProvider.tsx`: Manages Supabase Auth session (or dev JWT session in local dev mode) + active `tenant_id` (`X-Tenant-Id` injected into `apiClient` middleware).
   - `LoginPage.tsx`: Clean enterprise sign-in card (`Card`, `Input`, `Label`, `Button`) + local dev quick-role login panel (shown only when backend dev auth is active).
   - `OnboardingPage.tsx`: First-company creation form (`legal_name`, `trade_name`, `cif_nif`, `default_timezone`).
3. **`features/stores/`** (Template 1 — High-Density Table + Right Sheet Drawer):
   - `StoresPage.tsx`: TanStack Table rendered with shadcn `Table` primitives + top filter toolbar + status `Badge` + `tabular-nums` store code/timezone + right `Sheet` drawer for create/edit + `AlertDialog` confirmation for soft-delete archive. IME `!isComposing` guard on search/form inputs.
4. **`features/members/`** (Template 1 — High-Density Table + Right Sheet Drawer):
   - `MembersPage.tsx`: TanStack Table + low-saturation 5-role `Badge`s + assigned store tags + right `Sheet` drawer with `Select` (role) and `Checkbox` list (store scope assignment) + `AlertDialog` for disabling/removing member.
5. **`features/organization/`** (Template 2 — Two-Column Settings Page):
   - `OrganizationSettingsPage.tsx`: Left anchor navigation + right `Card` two-column form (`legal_name`, `trade_name`, `cif_nif`, `default_timezone`, `default_locale`) with read-only state for non-owners.

---

## 6. Verification, Visual Audit & Red Team Gate

1. **Docker Compose Local Postgres**: Add optional `postgres:16-alpine` under `profiles: ["local-db"]` so developers can run full migration + DB tests locally while keeping production deployment decoupled.
2. **Backend Tests (`pytest`)**:
   - Domain unit tests: IANA timezone validation, CIF normalization, RBAC matrix, last-owner protection.
   - API / Isolation tests (using isolated test DB / in-memory or Postgres test harness): cross-tenant isolation (`403`/`404`), `store_manager` denied access to unassigned stores, `accountant` denied all write operations (`403`), `hr` denied modifying `owner` (`403`), soft-delete leaves DB row intact with `deleted_at IS NOT NULL`, `POST /api/v1/dev/token` absent when `APP_ENV=production`.
   - Alembic migration check: `env.py` still excludes all 6 Supabase system schemas; `upgrade` and `downgrade` cleanly defined.
3. **Zero-Token Quality Gate**: `scripts/sync-contracts.ps1` + `scripts/quality-gate.ps1 -Scope all` + `npm run build`.
4. **High-Order Visual Audit (Milestone Gate)**: Once Phase 1 pages are complete, run `.ai/skills/web-design-guidelines/SKILL.md` review + `chrome-devtools` MCP screenshots in `es-ES` locale at `1440px` desktop and `390px` mobile viewports.
5. **Red Team Gatekeeper Audit**: Mandatory trigger on Phase 1 completion (Alembic migration + RBAC/tenant/store isolation + Phase sign-off) with P0/P1/P2 grading and 2-round circuit breaker.
