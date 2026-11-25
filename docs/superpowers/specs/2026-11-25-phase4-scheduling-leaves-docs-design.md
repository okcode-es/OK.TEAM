# Phase 4 Design Spec — Shift Scheduling Calendar, Leave Management (Art. 38 ET), Private Compliance Documents (<= 60s Signed URLs + SHA-256 Receipts) & Docker Delivery

- **Date**: 2026-11-25
- **Status**: Approved by User
- **Author**: Coordinator (Gemini 4 Neon)
- **Execution**: Strict ADR-009 separation (`backend-p4` and `frontend-p4` on `gemini-4-hydrogen-medium`)

---

## 1. Scope & Goals

Deliver three tightly integrated enterprise HR & Spanish labor compliance modules plus full-stack Docker delivery:

1. **Store Shift Scheduling Calendar (`public.shifts`)**:
   - Store-scoped weekly scheduling with UTC storage + store IANA timezone display.
   - Pure-Python domain compliance engine (`app/domain/scheduling.py`) enforcing Spanish *Estatuto de los Trabajadores* rules: overlap prevention, approved-leave conflict blocking, Art. 12.4.c part-time overtime prohibition, Art. 34 daily/weekly limits, 12h UTC inter-shift rest, continuous-work break check, and Art. 37.1 ET 1.5-day (36h) weekly continuous rest.
   - Frontend Template 3 (`SchedulePage.tsx`): sticky left employee column, 7-day grid, `tabular-nums` weekly hour totals vs contracted hours, compliance indicators, and right `Sheet` shift editor.
2. **Annual Vacation & Leave Management (`public.leave_requests`)**:
   - Art. 38 ET annual vacation balance ledger (`>= 30` natural days per calendar year, split accurately across cross-year requests, zero cash-out endpoints).
   - Supports `vacation`, `sick_leave` (*Baja médica*), `paid_leave` (Art. 37.3 ET), and `unpaid_leave`.
   - Overlap detection, approval conflict check against published shifts, and role-scoped approval workflow.
   - Frontend Template 1 (`LeavesPage.tsx`): `tabular-nums` balance KPI strip, filterable table, request submission & manager approval `Sheet`.
3. **Private Compliance Documents & Immutable SHA-256 Receipt Ledger (`public.compliance_documents`, `public.document_receipts`)**:
   - Private-only storage behind `DocumentStoragePort` with two adapters:
     - `LocalDocumentStorageAdapter` (`STORAGE_BACKEND=local`, default for local dev): local controlled volume + HMAC-SHA256 signed download endpoint strictly capped at `<= 60s`.
     - `SupabaseDocumentStorageAdapter` (`STORAGE_BACKEND=supabase`, production EU Supabase): `httpx` REST client using backend-only `SUPABASE_SERVICE_ROLE_KEY` against a Private Bucket (zero direct frontend Storage access).
   - Magic-bytes + MIME whitelist (`PDF/JPG/PNG/WEBP`), 10 MiB max size, UUID-only `storage_key` (path-traversal immune), server-computed SHA-256 digest.
   - Append-only `public.document_receipts` protected by PostgreSQL trigger blocking `UPDATE`, `DELETE`, and `TRUNCATE` (and retroactively adds `BEFORE TRUNCATE` guard to `public.attendance_events`).
   - Frontend Template 1 (`DocumentsPage.tsx`): category filter, upload `Sheet`, high-density table with SHA-256 fingerprint & receipt badges, and detail `Sheet` with on-demand 60s preview/download + electronic receipt signing.
4. **Docker Full-Stack Readiness**:
   - Configurable host ports (`BACKEND_PORT`, `FRONTEND_PORT`), optional `VITE_DEV_AUTH_ENABLED` build arg (default `false`), local private-docs volume (`okteam_docs_data`), updated `.env.example` and `README.md`.

---

## 2. Dependencies (Approved)

- **Backend** (`backend/pyproject.toml`):
  - `python-multipart>=0.0.20` (FastAPI `UploadFile` / `Form` multipart parsing)
  - `tzdata>=2024.2` (full IANA timezone database on Windows)
- **Frontend**: Zero new npm packages.

---

## 3. Database Schema & Migration (`0004_phase4_scheduling_leaves_docs.py`)

Strictly in `public` schema. Must implement complete reversible `downgrade()`.

### 3.1 `public.shifts`
- `id` `UUID PK`
- `tenant_id` `UUID NOT NULL FK -> tenants.id`
- `store_id` `UUID NOT NULL FK -> stores.id`
- `employee_id` `UUID NOT NULL FK -> employees.id`
- `start_at_utc` `TIMESTAMPTZ NOT NULL`
- `end_at_utc` `TIMESTAMPTZ NOT NULL` (`CHECK (end_at_utc > start_at_utc)`)
- `planned_break_minutes` `INTEGER NOT NULL DEFAULT 0` (`CHECK (planned_break_minutes >= 0)`)
- `status` `VARCHAR(24) NOT NULL DEFAULT 'draft'` (`draft` | `published` | `cancelled`)
- `notes` `VARCHAR(500) NOT NULL DEFAULT ''`
- `created_by_user_id` `UUID NOT NULL`
- `published_by_user_id` `UUID NULL`
- `published_at_utc` `TIMESTAMPTZ NULL`
- `retention_until` `TIMESTAMPTZ NOT NULL` (4 years from `end_at_utc`)
- `created_at` `TIMESTAMPTZ NOT NULL`
- `updated_at` `TIMESTAMPTZ NOT NULL`
- `deleted_at` `TIMESTAMPTZ NULL` (soft delete only; physical DELETE forbidden in app code)
- Indexes: `(tenant_id, store_id, start_at_utc)`, `(tenant_id, employee_id, start_at_utc)`.

### 3.2 `public.leave_requests`
- `id` `UUID PK`
- `tenant_id` `UUID NOT NULL FK -> tenants.id`
- `store_id` `UUID NOT NULL FK -> stores.id`
- `employee_id` `UUID NOT NULL FK -> employees.id`
- `leave_type` `VARCHAR(24) NOT NULL` (`vacation` | `sick_leave` | `paid_leave` | `unpaid_leave`)
- `start_date` `DATE NOT NULL`
- `end_date` `DATE NOT NULL` (`CHECK (end_date >= start_date)`)
- `natural_days` `INTEGER NOT NULL` (`CHECK (natural_days > 0)`)
- `status` `VARCHAR(24) NOT NULL DEFAULT 'pending'` (`pending` | `approved` | `rejected` | `cancelled`)
- `reason` `VARCHAR(500) NOT NULL DEFAULT ''`
- `requested_by_user_id` `UUID NOT NULL`
- `reviewed_by_user_id` `UUID NULL`
- `reviewed_at_utc` `TIMESTAMPTZ NULL`
- `review_note` `VARCHAR(500) NOT NULL DEFAULT ''`
- `medical_document_id` `UUID NULL` (soft link to `compliance_documents.id`, validated within tenant)
- `retention_until` `TIMESTAMPTZ NOT NULL` (4 years from `end_date` UTC end-of-day)
- `created_at` `TIMESTAMPTZ NOT NULL`
- `updated_at` `TIMESTAMPTZ NOT NULL`
- `deleted_at` `TIMESTAMPTZ NULL`
- Indexes: `(tenant_id, employee_id, start_date, end_date)`, `(tenant_id, store_id, status)`.

### 3.3 `public.compliance_documents`
- `id` `UUID PK`
- `tenant_id` `UUID NOT NULL FK -> tenants.id`
- `store_id` `UUID NULL FK -> stores.id`
- `employee_id` `UUID NULL FK -> employees.id`
- `category` `VARCHAR(32) NOT NULL` (`contract` | `payslip` | `attendance_report` | `medical_leave` | `id_document` | `other`)
- `title` `VARCHAR(200) NOT NULL`
- `period_month` `VARCHAR(7) NULL` (`YYYY-MM`, optional)
- `storage_backend` `VARCHAR(24) NOT NULL` (`local` | `supabase`)
- `storage_key` `VARCHAR(255) NOT NULL UNIQUE` (`{tenant_id}/{doc_id}`, UUID-only)
- `original_filename` `VARCHAR(255) NOT NULL`
- `mime_type` `VARCHAR(100) NOT NULL`
- `size_bytes` `INTEGER NOT NULL` (`CHECK (size_bytes > 0)`)
- `sha256_hex` `VARCHAR(64) NOT NULL`
- `requires_signature` `BOOLEAN NOT NULL DEFAULT false`
- `uploaded_by_user_id` `UUID NOT NULL`
- `retention_until` `TIMESTAMPTZ NOT NULL` (4 years from `created_at`)
- `created_at` `TIMESTAMPTZ NOT NULL`
- `updated_at` `TIMESTAMPTZ NOT NULL`
- `deleted_at` `TIMESTAMPTZ NULL`
- Indexes: `(tenant_id, employee_id, category)`, `(tenant_id, store_id, created_at)`.

### 3.4 `public.document_receipts` (Strictly Append-Only)
- `id` `UUID PK`
- `tenant_id` `UUID NOT NULL FK -> tenants.id`
- `document_id` `UUID NOT NULL FK -> compliance_documents.id`
- `employee_id` `UUID NOT NULL FK -> employees.id`
- `signed_by_user_id` `UUID NOT NULL`
- `document_sha256_hex` `VARCHAR(64) NOT NULL`
- `signed_at_utc` `TIMESTAMPTZ NOT NULL`
- `ip_address` `VARCHAR(64) NOT NULL`
- `user_agent` `VARCHAR(512) NOT NULL`
- `receipt_statement` `VARCHAR(500) NOT NULL`
- `retention_until` `TIMESTAMPTZ NOT NULL` (4 years from `signed_at_utc`)
- Unique constraint: `uq_document_receipts_tenant_doc_emp (tenant_id, document_id, employee_id)`.
- PostgreSQL immutability triggers:
  - `trg_document_receipts_immutable`: `BEFORE UPDATE OR DELETE ON public.document_receipts FOR EACH ROW EXECUTE FUNCTION public.prevent_document_receipts_mutation()`
  - `trg_document_receipts_no_truncate`: `BEFORE TRUNCATE ON public.document_receipts FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_document_receipts_mutation()`
  - Also add `trg_attendance_events_no_truncate`: `BEFORE TRUNCATE ON public.attendance_events FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_attendance_events_mutation()`

---

## 4. Domain Rules & Security Matrix (Pure Stdlib `app/domain/`)

### 4.1 `app/domain/scheduling.py`
- `ShiftStatus`: `draft`, `published`, `cancelled`.
- `ScheduleAlertCode`:
  - `DAILY_MAX_EXCEEDED` (adult > 9h, minor > 8h)
  - `INTER_SHIFT_REST_VIOLATION` (< 12h UTC gap between adjacent shifts)
  - `MISSING_CONTINUOUS_BREAK` (> 6h net/continuous without >= 15m break; minor > 4.5h without >= 30m break)
  - `WEEKLY_HOURS_EXCEEDED` (full-time weekly net hours > 40h)
  - `PART_TIME_OVERTIME_PROHIBITED` (part-time weekly net hours > `contracted_weekly_hours`, Art. 12.4.c ET)
  - `WEEKLY_REST_VIOLATION` (Art. 37.1 ET: largest continuous rest gap inside the 7-day Monday-Sunday week window < 36h / 1.5 days, calculated in UTC)
- Hard rejections:
  - `ConflictError` (409): overlapping non-cancelled shift for same employee; shift overlapping any `approved` leave request on local store date(s).
  - `ValidationDomainError` (422): invalid time bounds/break, `part_time` weekly total exceeding `contracted_weekly_hours`, minor single-day net > 8h, scheduling `terminated`/`legal_hold`/`anonymized` employee, or publishing/editing cancelled shift.

### 4.2 `app/domain/leaves.py`
- `LeaveType`: `vacation`, `sick_leave`, `paid_leave`, `unpaid_leave`.
- `LeaveStatus`: `pending`, `approved`, `rejected`, `cancelled`.
- `calculate_natural_days(start_date, end_date)` -> `(end_date - start_date).days + 1` (1..365).
- `split_days_by_year(start_date, end_date)` -> `dict[int, int]` (handles cross-year vacations like Dec 28 – Jan 4 accurately per calendar year).
- `compute_vacation_balance(employee, requests, year)` -> `VacationBalance(year, entitled_days, approved_days, pending_days, remaining_days)` where `entitled_days = max(employee.annual_vacation_days, 30)` and `remaining_days = entitled_days - approved_days - pending_days`.
- Hard rejections:
  - Overlap with existing `pending` or `approved` leave (`409`).
  - Vacation exceeding remaining balance in any covered year (`422`).
  - Approving a leave that overlaps any non-cancelled `published` shift (`409`).
  - Invalid state transitions (only `pending -> approved | rejected | cancelled`).

### 4.3 `app/domain/documents.py`
- `DocumentCategory`: `contract`, `payslip`, `attendance_report`, `medical_leave`, `id_document`, `other`.
- `ALLOWED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}` + strict magic-bytes signature validation (`validate_file_magic(mime_type, content)`).
- `MAX_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024` (10 MiB).
- `MAX_SIGNED_URL_TTL_SECONDS = 60` (`1 <= ttl_seconds <= 60`, otherwise `ValidationDomainError`).
- `sanitize_filename(filename)`: strip path components, control chars, newlines, quotes; fallback to `"document"`.
- `DocumentStoragePort(Protocol)`:
  - `async def put_object(self, *, storage_key: str, content: bytes, mime_type: str) -> None`
  - `async def create_signed_url(self, *, storage_key: str, ttl_seconds: int, download_filename: str, mime_type: str) -> SignedUrlResult`
  - `async def delete_object(self, *, storage_key: str) -> None` (only for rollback if DB insert fails before commit; never called on retention-locked records).
- Role & Scope Matrix:
  - `owner`, `hr`: full tenant read/upload/signed-url; soft delete allowed ONLY if no receipts exist AND `retention_until <= now_utc` (otherwise `RetentionLockError` 422).
  - `store_manager`: authorized `store_ids` only; **blocked (403)** from `legal_hold` employees (Art. 32 LOPDGDD) AND **blocked (403)** from `payslip` & `id_document` categories (aligned with Phase 2 PII boundary); upload limited to `attendance_report`, `medical_leave`, `other`.
  - `accountant`: tenant-wide read + 60s signed URL download for compliance audit; **blocked (403)** from upload/delete/sign-for-others.
  - `employee`: read & 60s signed URL strictly for own employee record (`employee.user_id == principal.user_id`) or tenant-wide general policies (`employee_id is None`); upload limited to own `medical_leave` (*Baja médica*); electronic receipt signing strictly for own `requires_signature=True` documents.

---

## 5. REST API Endpoints (`/api/v1`)

All endpoints declare explicit `response_model`, `status_code`, and `operation_id`.

### 5.1 Scheduling (`/api/v1/shifts`)
- `GET /api/v1/shifts/weekly` (`operation_id="get_weekly_schedule"`): query `store_id` (required for managers/owners/hr/accountant; optional for `employee` defaulting to own primary store), `week_start` (`YYYY-MM-DD`, normalized to Monday). Returns store info, 7-day range, employee rows with shifts, weekly net hours, contracted hours, approved leaves in that week, and computed `ScheduleAlert` list.
- `POST /api/v1/shifts` (`operation_id="create_shift"`, `201`): create draft/published shift with full conflict & statutory validation.
- `PATCH /api/v1/shifts/{shift_id}` (`operation_id="update_shift"`): update times/break/notes/status with re-validation.
- `POST /api/v1/shifts/publish-week` (`operation_id="publish_weekly_shifts"`): publish all `draft` shifts in a store's week.
- `DELETE /api/v1/shifts/{shift_id}` (`operation_id="cancel_shift"`): soft-cancel (`status="cancelled"` + `deleted_at` set — never physical SQL DELETE).

### 5.2 Leaves (`/api/v1/leaves`)
- `GET /api/v1/leaves` (`operation_id="list_leave_requests"`): filter by `store_id`, `employee_id`, `status`, `year`.
- `GET /api/v1/leaves/balance` (`operation_id="get_vacation_balance"`): query `employee_id` (optional for `employee` -> self), `year` (default current year).
- `POST /api/v1/leaves` (`operation_id="create_leave_request"`, `201`): submit request.
- `POST /api/v1/leaves/{leave_id}/review` (`operation_id="review_leave_request"`): approve/reject (`owner`, `hr`, `store_manager` in scope).
- `POST /api/v1/leaves/{leave_id}/cancel` (`operation_id="cancel_leave_request"`): employee cancels own `pending` request, or manager/HR/owner cancels `pending` request in scope.

### 5.3 Documents & Receipts (`/api/v1/documents`)
- `GET /api/v1/documents` (`operation_id="list_documents"`): filter by `category`, `employee_id`, `store_id`, `requires_signature`. Returns document metadata + receipt summary (whether signed, `signed_at_utc`, receipt count) — never leaks raw storage URLs.
- `POST /api/v1/documents` (`operation_id="upload_document"`, `201`): `multipart/form-data` (`file`, `category`, `title`, `employee_id`, `store_id`, `period_month`, `requires_signature`).
- `GET /api/v1/documents/{document_id}` (`operation_id="get_document"`): metadata + receipts list (including SHA-256, `signed_at_utc`, `ip_address`, `user_agent`).
- `POST /api/v1/documents/{document_id}/signed-url` (`operation_id="create_document_signed_url"`): body `{ttl_seconds: int = 60}` (`1..60`), sets `Cache-Control: no-store`, returns `{url, expires_at_utc, ttl_seconds, sha256_hex}`.
- `GET /api/v1/documents/local-download` (`operation_id="download_local_signed_document"`): validates short-lived HMAC token (`<= 60s`), streams file with `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'`, `Cache-Control: no-store`, `Content-Disposition: attachment`.
- `POST /api/v1/documents/{document_id}/receipts` (`operation_id="sign_document_receipt"`, `201`): employee signs own document; records SHA-256, `user_id`, server UTC, client IP, sanitized User-Agent, and bilingual statement snapshot.

---

## 6. Frontend UX Specification (`okteam-ui-craft`)

- Navigation (`AppShell.tsx`): add `schedule` (CalendarDays), `leaves` (CalendarOff / Palmtree -> use `CalendarCheck2` / `BriefcaseMedical` / `CalendarClock` from `lucide-react`), `documents` (FileText). Keep `zh-CN` and `es-ES` 100% symmetric, zero fixed widths (`min-w-*`), `tabular-nums` on all numeric/date/hour metrics, IME guard (`!e.nativeEvent.isComposing && e.keyCode !== 229`), zero gradients/emojis.
- `SchedulePage.tsx` (Template 3): store selector, week pager, "Publish Week" button, horizontal scroll table with `sticky left-0 bg-white` employee column, 7 day cells showing shift chips + approved leave tags, weekly `tabular-nums` hours summary + compliance badge, right `Sheet` for create/edit/cancel shift.
- `LeavesPage.tsx` (Template 1): 4-card `tabular-nums` vacation balance strip, filters, "New Leave Request" `Sheet`, high-density TanStack Table, row click opens detail & approval `Sheet`.
- `DocumentsPage.tsx` (Template 1): category/signature filters, "Upload Document" `Sheet` (file picker + metadata), high-density table with SHA-256 short fingerprint & receipt badge, row click opens detail `Sheet` with "60s Secure Download" button (fetches fresh URL on demand, never cached) and "Sign Electronic Receipt" CTA + immutable legal receipt proof card.

---

## 7. Verification & Acceptance Criteria

1. Backend quality gate (`scripts/quality-gate.ps1 -Scope backend`): `check_domain_purity.py` PASS, `ruff` PASS, `mypy app` PASS, `pytest` PASS (covering all domain rules, RBAC negative tests, cross-tenant/cross-store isolation, `legal_hold` & `payslip`/`id_document` blocks, magic-bytes rejection, TTL > 60s rejection, HMAC tamper/expiry rejection, receipt immutability & duplicate-sign block, and Supabase adapter mocked HTTP tests).
2. Live Postgres 16 migration test: `alembic upgrade head` (`0003 -> 0004`), verify live SQL `UPDATE`/`DELETE`/`TRUNCATE` blocked on `document_receipts` and `TRUNCATE` blocked on `attendance_events`, then `downgrade 0003` -> `upgrade head`.
3. Contract sync (`scripts/sync-contracts.ps1`) + Frontend quality gate (`scripts/quality-gate.ps1 -Scope frontend`) + `vite build` PASS.
4. Docker full-stack test: `docker compose --profile local-db up -d --build`, verify healthy backend + frontend + DB.
5. `chrome-devtools` `es-ES` `1440px` & `390px` visual audit across Schedule, Leaves, and Documents pages (0 horizontal overflow, zero truncated buttons).
6. Red Team Gatekeeper audit PASS (P0: None, P1: None).
