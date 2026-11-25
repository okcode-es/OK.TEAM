# Architecture Decision Records (ADR)

## ADR-001: Monorepo with Independent Frontend & Backend Deployment
- **Status**: Accepted (Phase 0)
- **Context**: Need unified contract governance (`contracts/openapi.json`) while allowing frontend (Nginx static SPA) and backend (FastAPI) to scale and deploy independently.
- **Decision**: Single repository (`backend/`, `frontend/`, `contracts/`, `.ai/`), independent `Dockerfile`s, coordinated locally via `docker-compose.yml`.

## ADR-002: Python 3.12 + `uv` for Backend Toolchain
- **Status**: Accepted (Phase 0)
- **Context**: Host has Python 3.14 and 3.12. Python 3.12 provides mature binary wheel support for `pydantic-core`, `asyncpg`, `SQLAlchemy`, and `mypy`. User requested `uv`.
- **Decision**: Pin backend Python to `>=3.12,<3.13` with `uv` virtual environment and lockfile.

## ADR-003: Strict 4-Layer Clean Architecture + AST Purity Gate
- **Status**: Accepted (Phase 0)
- **Context**: Spanish labor law calculations must remain testable, deterministic, and decoupled from FastAPI/SQLAlchemy.
- **Decision**: Enforce `domain` / `application` / `infrastructure` / `interfaces` isolation with `scripts/check_domain_purity.py` in local quality gate.

## ADR-004: Supabase Auth-Only Frontend Boundary & Alembic System Schema Filtering
- **Status**: Accepted (Phase 0)
- **Context**: Direct frontend queries to Supabase business tables risk bypassing tenant/store scope checks and audit trails; Alembic autogenerate could touch Supabase system schemas.
- **Decision**: Frontend uses Supabase strictly for Auth JWT acquisition; all business data and private files go through FastAPI. `alembic/env.py` explicitly excludes `auth`, `storage`, `realtime`, `supabase_functions`, `extensions`, `vault`.

## ADR-005: OpenAPI Contract-First Type Generation
- **Status**: Accepted (Phase 0)
- **Context**: Hand-written frontend API types drift from FastAPI schemas and waste tokens debugging mismatches.
- **Decision**: Export `contracts/openapi.json` offline from FastAPI and generate `frontend/src/api/generated/schema.d.ts` via `openapi-typescript`, consumed by `openapi-fetch`.

## ADR-006: Token-Efficient Multi-Agent Workflow & Risk-Based Red Team Gate
- **Status**: Accepted (Phase 0)
- **Context**: Avoid context bloat and unnecessary audits on routine UI tweaks.
- **Decision**: Role-scoped context loading, mandatory zero-token local scripts before reporting, Red Team Gatekeeper triggered only on high-risk domains with a 2-round circuit breaker.

## ADR-007: Hybrid Auth (Supabase JWT Identity + Live DB RBAC Authority)
- **Status**: Accepted (Phase 1)
- **Context**: Storing `store_ids` and `role` solely inside Supabase JWT `app_metadata` introduces revocation lag until token expiry, couples routine store/role changes to Supabase Admin API, and complicates multi-tenant switching for external accountants (*Gestoría*).
- **Decision**: Verify `user_id` (`sub`) from Supabase JWT (`VerifiedIdentity`), then resolve active `Membership` + `store_ids` live from PostgreSQL (`memberships`, `membership_stores`) scoped by `X-Tenant-Id`. Revocation and store reassignment take effect in 0 seconds, and no cross-schema FK touches Supabase `auth.users`.

