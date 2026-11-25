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

## ADR-008: Application-Layer AES-256-GCM PII Encryption & Art. 32 LOPDGDD Bloqueo de Datos
- **Status**: Accepted (Phase 2)
- **Context**: Spanish employee identifiers (`NIE/DNI`, `IBAN`, `NAF`) must never be stored or logged in plaintext, yet require duplicate-NIE checks and selective access for HR/Gestoría while minimizing exposure to store managers and reconciling GDPR erasure with Spain's 4-year retention law.
- **Decision**: Encrypt `NIE/DNI`, `IBAN`, and `NAF` with `AES-256-GCM` (random 96-bit nonce, AAD bound to `tenant_id:employee_id:field_name`) and domain-separated `HMAC-SHA256` blind index for `NIE/DNI`. List/detail endpoints return masks only; `/sensitive` (`Cache-Control: no-store`) blocks `store_manager` (`403`). Termination freezes records into immutable 4-year `legal_hold` (hidden from daily `store_manager` ops under Art. 32 LOPDGDD) until `retention_until` expires, after which irreversible anonymization is unlocked.

## ADR-009: Strict Hydrogen Worker Separation & Phase-Boundary Context Compaction
- **Status**: Accepted (Phase 3+)
- **Context**: Running code implementation directly on the coordinator model (Neon) bloats reasoning context and wastes tokens; mixing backend and frontend edits blurs contract boundaries.
- **Decision**: Coordinator (Neon) handles design, delegation, review, and `.ai/memory/` updates only — never edits business source code directly. Dedicated `Gemini 4 Hydrogen (Medium)` Swarm workers own Backend (`backend/*` + `contracts/openapi.json`) and Frontend (`frontend/*`) separately. Context compaction (`/compact`) is scheduled strictly at **Phase boundaries** (immediately after Red Team PASS + `git push` + `.ai/memory/` snapshot) to maximize token savings with zero mid-phase context loss.



