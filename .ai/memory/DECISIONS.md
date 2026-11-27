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
- **Status**: Superseded by ADR-016 (2026-11-27)
- **Context**: Running code implementation directly on the coordinator model (Neon) bloats reasoning context and wastes tokens; mixing backend and frontend edits blurs contract boundaries.
- **Decision**: Coordinator (Neon) handles design, delegation, review, and `.ai/memory/` updates only — never edits business source code directly. Dedicated `Gemini 4 Hydrogen (Medium)` Swarm workers own Backend (`backend/*` + `contracts/openapi.json`) and Frontend (`frontend/*`) separately. Context compaction (`/compact`) is scheduled strictly at **Phase boundaries** (immediately after Red Team PASS + `git push` + `.ai/memory/` snapshot) to maximize token savings with zero mid-phase context loss.

## ADR-010: Pluggable Private Document Storage Port (<= 60s Signed URLs) & Append-Only SHA-256 Receipt Ledger
- **Status**: Accepted (Phase 4)
- **Context**: `DATA_AND_SECURITY.md` Grounding 4 mandates private-only storage for Spanish labor documents (contracts, payslips, *bajas médicas*, monthly attendance reports), temporary signed URLs strictly `<= 60s`, zero direct frontend Storage access, and tamper-evident SHA-256 electronic receipts — while still allowing zero-dependency local/Docker development before connecting EU Supabase.
- **Decision**: Define pure `DocumentStoragePort` in `domain/documents.py` with two adapters in `infrastructure/document_storage.py`: (1) `LocalDocumentStorageAdapter` (`STORAGE_BACKEND=local`, controlled Docker volume + domain-separated HMAC-SHA256 millisecond-expiring `<= 60.0s` tokens verified against live DB soft-delete state and served with `nosniff`, `CSP: default-src 'none'`, `Cache-Control: no-store`, and RFC 6266 UTF-8 `Content-Disposition`), and (2) `SupabaseDocumentStorageAdapter` (`STORAGE_BACKEND=supabase`, lightweight `httpx` REST client using backend-only `SUPABASE_SERVICE_ROLE_KEY` against a Private Bucket). Enforce PostgreSQL `BEFORE UPDATE OR DELETE` + `BEFORE TRUNCATE` triggers on `public.document_receipts` (and `BEFORE TRUNCATE` on `public.attendance_events`).

## ADR-011: Store-Level Monthly Payroll Closing, Immutable Snapshots & Zero-Dependency `es-ES` A4 RDL 8/2019 PDF Engine
- **Status**: Accepted (Phase 5)
- **Context**: Spanish *Registro Diario de Jornada* (RDL 8/2019 / Art. 34.9 ET) requires monthly attendance summaries delivered to employees for signature and preserved for >= 4 years, while external *Gestorías* require locked monthly summaries (hours, overtime, approved leaves, *bajas médicas*) without leaking plaintext NIE/DNI or inflating Docker images with heavy PDF/font binaries.
- **Decision**: Enforce store-level `monthly_closings` (`closed` / Owner+HR audited `reopened` with `revision_number` & optimistic concurrency) backed by strictly append-only `monthly_closing_items` protected by PostgreSQL `BEFORE UPDATE OR DELETE` + `BEFORE TRUNCATE` triggers. Enforce cross-module `409 Conflict` write locks on Attendance (including overnight cross-month & cross-store session diffs) and Leaves overlapping closed months. Generate deterministic multi-page A4 `es-ES` `%PDF-1.4` *Resumen Mensual de Jornada* reports via pure-stdlib `PurePythonAttendancePdfRenderer` (zero new dependencies, WinAnsi Spanish accents, control-char stripping, masked NIE/DNI only) published automatically into `compliance_documents` (`requires_signature=True`) for employee SHA-256 receipt signing, paired with role-gated (`owner`/`hr`/`accountant`) UTF-8 BOM + CWE-1236 Gestoría CSV exports.

## ADR-012: Department-Scoped RBAC, GDPR Art. 9 Medical Justificante Isolation & Zero-Dependency Workspace Ops (Phase 5A/5B)
- **Status**: Accepted (Phase 5A/5B)
- **Context**: Migrating active teams from Sesame HR requires dual-view admin/employee dashboards, mobile-first clocking, medical appointment justificantes, mandatory company announcement acknowledgements, employee avatars, department-level manager scoping, and Excel/CSV bulk employee onboarding — without adding heavy third-party dependencies or weakening Spanish GDPR/LOPDGDD boundaries.
- **Decision**: (1) Extend `memberships.department_scope` and `employees.department` so `store_manager` authority is constrained to `store_ids ∩ department_scope` across employees, attendance, scheduling, leaves, documents, dashboard, and announcements while preserving `NULL` backward compatibility for store-wide managers. (2) Enforce GDPR Art. 9 isolation on `medical_leave` / `justificante` documents: strictly accessible only to employee self, scoped manager (non-`legal_hold`), HR, and Owner via `<= 60s` signed URLs (`accountant` strictly `403`, while granting `accountant` `payslip`-only PDF upload). (3) Protect `public.announcement_acknowledgements` with PostgreSQL `BEFORE UPDATE OR DELETE` + `BEFORE TRUNCATE` triggers and canonical SHA-256 content+attachment digests. (4) Keep zero new backend/frontend dependencies via pure-stdlib hardened OOXML `.xlsx` codec (expat DTD/ENTITY rejection across encodings, ZIP entry/size/column/row caps, atomic row-level validation), pure-stdlib Spanish national/regional holiday engine, MAC-domain-separated private avatar signed URLs (`<= 2 MiB` JPEG/PNG/WebP), and native browser `<canvas>` square avatar cropping.






## ADR-013: Personio-Grade Enterprise Layout, okTeam Light Palette & GDPR Art. 9 Team Calendar (2026-11-26)
- **Visual Architecture**: Adopted Personio-grade layout structure (collapsible Deep Navy `#0B1120` sidebar with Cobalt Blue `#2563EB` active pill & pinned quick clock trigger, `#F3F5F9` cool light canvas, white `rounded-xl border-slate-200/75` cards, `WorkRingClockCard`, horizontal Team Calendar Gantt, and 5-tab Employee 360° Profile) while strictly rejecting dark workspaces, light-blue sidebars, and Performance/Goals modules (reusing only their clean `EmptyState` pattern).
- **Employee Profile Fields (`0007_employee_profile_fields`)**: Added `job_title`, `emergency_contact_name`, `emergency_contact_phone` to `public.employees`; all emergency contact PII is cleared on GDPR anonymization.
- **GDPR Art. 9 Anti-Oracle Team Calendar (`GET /api/v1/leaves/team-calendar`)**: To prevent health-data inference attacks where a colleague seeing `"absence_redacted"` could deduce `sick_leave`/`medical_appointment`, for `employee` and `accountant` viewing other colleagues ALL non-vacation leave types collapse into `"absence_redacted"` (while `vacation` remains visible and own leaves remain unredacted); `reason`, `review_note`, and `medical_document_id` are never returned.

## ADR-014: Self-Selectable RDL 8/2019 Clock Times, Two-Stage Leave Approval & Management Reports (2026-11-26)
- **Self-Selectable Attendance (`POST /api/v1/attendance/clock` `occurred_at_utc` + `POST /api/v1/attendance/manual-day`)**: Allows employees and scoped managers to enter exact work/break/clock-out times while preserving RDL 8/2019 append-only immutability (`recorded_at_utc` always server UTC), DST spring-forward gap rejection, chronology & overlap checks (`409`), and closed payroll month locks (`409`).
- **Two-Stage Leave Approval (`0008_leave_two_stage_approval`)**: Splits pending state into `pending_manager` (Stage 1: Store Manager) and `pending_hr` (Stage 2: HR/Owner) with separate `manager_reviewed_*` and `hr_reviewed_*` audit trails. Employee self-requests require Store Manager then HR; Store Manager requests on behalf of employees auto-endorse Stage 1 and proceed directly to `pending_hr` for HR final approval.
- **Management Reports (`/api/v1/reports/*`)**: Pure-stdlib UTF-8 BOM CSV (with CWE-1236 formula-injection escaping across leading whitespace/control chars) and A4 `%PDF-1.4` exports for attendance, leaves, and headcount roster, enforcing tenant/store/department scoping, GDPR Art. 9 redaction for `accountant`, and zero sensitive PII export.

## ADR-015: Custom HR Leave Entitlements, Direct Assignment & 3-Tier Spanish Break Policies (2026-11-26)
- **Leave Entitlements & Immutable Adjustments (`0010_hr_leave_break_policies`)**: Introduced `public.leave_entitlements` (annual statutory `>= 30` natural days Art. 38 ET floor, carryover, comp-off) paired with strictly append-only `public.leave_entitlement_adjustments` guarded by PostgreSQL `BEFORE UPDATE OR DELETE` and `BEFORE TRUNCATE` triggers. Both request creation, direct assignment, and final HR approval validate balances against resolved yearly entitlements while blocking mutations on `terminated`/`legal_hold`/`anonymized` records.
- **Direct Leave Assignment (`POST /api/v1/leaves/assign`)**: Allows `owner`, `hr`, and scoped `store_manager` (within `store_ids ∩ department_scope`) to assign immediately `approved` leaves (`assigned_directly=True`, `assigned_by_user_id`), enforcing closed payroll month locks (`409`), leave overlap (`409`), published shift conflicts (`409`), and vacation balance (`422`).
- **3-Tier Custom Break Policies (`0009_employee_fixed_break`, `0010_hr_leave_break_policies`)**: Introduced `public.break_policies` with `employee > store > tenant > system_default` precedence and partial unique indexes (`WHERE deleted_at IS NULL`). Supports `fixed_duration` and Spanish split-shift `fixed_window` (*jornada partida*), `is_paid` (paid *bocadillo* counted toward Art. 34.4 ET without deducting `net_minutes` vs unpaid deduction), `manual_punch` (with server-authoritative `auto_end_manual_break` resolving active/nearest window end time) vs `auto_deduct` (only when unpunched and threshold met). Department-scoped `store_manager` cannot mutate `STORE`-wide break policies (`403`).


## ADR-016: Adaptive Token-Efficient Execution & Proactive Session Boundary Hygiene (2026-11-27)
- **Status**: Accepted (Supersedes ADR-009)
- **Context**: Strict multi-agent splitting (Coordinator + 2 Hydrogen Swarm workers + Red Team subagent) on every task caused 4x repeated file reads of large backend/frontend modules, while long multi-phase conversations and excessive high-res screenshots compounded token spend.
- **Decision**: (1) **Small-to-medium tasks default to single-agent execution**: the main agent directly edits code and performs inline compliance/security self-checks against `.ai/rules/DATA_AND_SECURITY.md` and `SPAIN_LABOR_LAW.md` without spawning cold-start Swarm workers or separate Red Team subagents. (2) **Multi-agent on demand only**: reserve Swarm workers and independent Red Team subagents (<= 2 rounds) for genuinely large parallel cross-stack modules or high-risk auth/GDPR/payroll/migration changes. (3) **Tiered browser verification**: routine changes use DOM/script assertions + <= 1 key screenshot; full bilingual desktop+mobile multi-screenshot audits apply only to major redesigns/layout overhauls. (4) **Proactive new-conversation hygiene**: encoded in workspace root `GEMINI.md`, the agent proactively advises at milestone/compaction boundaries whether to start a fresh conversation using `.ai/memory/SESSION_STATE.md` for seamless handoff.
