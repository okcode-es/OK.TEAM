# Phase 5 Design Specification: Monthly Payroll Closing, Gestoría Export Pack, RDL 8/2019 Attendance PDF Receipts & P2 Backlog Cleanup

- **Date**: 2026-11-25
- **Project**: okTeam (`F:\workspace\OK.TEAM`)
- **Status**: Approved Design — Ready for Implementation
- **New Third-Party Dependencies**: **0** (backend 0, frontend 0)

---

## 1. Objective & Scope

Deliver the end-of-month Spanish payroll & compliance closing loop on top of Phases 1–4, while clearing all 5 non-blocking P2 items accumulated across Phases 1–4:

1. **Store-Level Monthly Closing (`monthly_closings` + immutable `monthly_closing_items`)**:
   - Pre-close readiness evaluation per `(store_id, period_month)` with hard blockers (`OPEN_SHIFT`, `PENDING_LEAVE`, `ALREADY_CLOSED`) and Spanish labor law warning counts.
   - Strict write-lock enforcement: once a store-month is `closed`, retroactive attendance punches/corrections and overlapping leave creations/reviews/cancellations are rejected with `409 Conflict`.
   - Controlled `owner`/`hr`-only Reopen (`reopen_reason` mandatory) that preserves all prior immutable snapshots, PDFs, and SHA-256 receipts; re-closing increments `revision_number`.
2. **Zero-Dependency `es-ES` A4 PDF Generator & Automatic Employee SHA-256 Receipt Tracking**:
   - Pure Python stdlib `%PDF-1.4` writer (`PurePythonAttendancePdfRenderer`) producing official Spanish *Resumen Mensual de Registro de Jornada* (RDL 8/2019 / Art. 34.9 ET) PDFs.
   - Stored automatically via Phase 4's `DocumentStoragePort` (`local` or `supabase`) into `compliance_documents` (`category = attendance_report`, `requires_signature = true`, `period_month = YYYY-MM`).
   - Live signature completion tracking (`signed_count / total_reports`) backed by Phase 4's append-only `document_receipts`.
3. **Gestoría Monthly Payroll Pack (`es-ES` UTF-8 BOM CSV)**:
   - Role-gated exclusively to `owner`, `hr`, and `accountant` (`store_manager` & `employee` receive `403`).
   - Default masked NIE/DNI (`*****123A`) for GDPR data minimization, CWE-1236 spreadsheet formula sanitization on all string cells, `Cache-Control: no-store`, RFC 6266 UTF-8 `Content-Disposition`.
4. **Frontend Monthly Closing Workspace (`PayrollPage.tsx`)**:
   - Template 1 (compact toolbar + 4-card `tabular-nums` KPI strip + blocker/warning banner + full-width TanStack Table + right-side `Sheet` detail with on-demand 60s signed PDF download + Reopen `Dialog`), full `zh-CN` / `es-ES` i18n, zero overflow at `1440px` & `390px`.
5. **Phase 1–4 P2 Backlog Cleanup (All 5 Items)**:
   - Spanish official DNI/NIE Mod-23 & ISO 7064 IBAN Mod-97 checksums in `domain/employees.py`.
   - 100% non-ASCII filename ASCII fallback normalized to `"document.pdf"` in `interfaces/api/documents.py`.
   - Allow `store_manager` to list/view/download/sign their **own** personal documents (`doc.employee_id == self_emp.id`, including `payslip`/`contract`/`id_document`) while continuing to block other employees' restricted categories and `legal_hold` records.
   - Partial unique index `uq_employees_tenant_user_active` on `employees(tenant_id, user_id)` where `user_id IS NOT NULL AND deleted_at IS NULL`.
   - Propagate `create_app(settings_override=...)` via `app.state.settings` in FastAPI dependencies (`deps.py`, `documents.py`, `auth.py`).

---

## 2. Database Schema & Reversible Migration (`0005_phase5_payroll_closing`)

- **File**: `backend/alembic/versions/0005_phase5_payroll_closing.py`
- **Revision**: `revision = "0005_phase5_payroll_closing"` (27 chars `<= 32`), `down_revision = "0004_phase4_sched_leaves_docs"`.
- **Schema**: `public` only (Alembic system schema exclusions remain untouched).

### 2.1 `public.monthly_closings`
| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | `UUID` | PK |
| `tenant_id` | `UUID` | FK `tenants.id` `RESTRICT`, NOT NULL |
| `store_id` | `UUID` | FK `stores.id` `RESTRICT`, NOT NULL |
| `period_month` | `VARCHAR(7)` | NOT NULL (`YYYY-MM`) |
| `status` | `VARCHAR(20)` | NOT NULL, CHECK `IN ('closed', 'reopened')` |
| `revision_number` | `INTEGER` | NOT NULL, DEFAULT `1`, CHECK `>= 1` |
| `closed_by_user_id` | `UUID` | NOT NULL |
| `closed_at_utc` | `TIMESTAMPTZ` | NOT NULL |
| `reopened_by_user_id` | `UUID` | NULL |
| `reopened_at_utc` | `TIMESTAMPTZ` | NULL |
| `reopen_reason` | `VARCHAR(500)` | NOT NULL, DEFAULT `''` |
| `notes` | `VARCHAR(500)` | NOT NULL, DEFAULT `''` |
| `retention_until` | `TIMESTAMPTZ` | NOT NULL |
| `created_at` | `TIMESTAMPTZ` | NOT NULL |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL |
| `deleted_at` | `TIMESTAMPTZ` | NULL |

- Partial Unique Index: `uq_monthly_closings_tenant_store_month_active` ON `(tenant_id, store_id, period_month)` WHERE `deleted_at IS NULL`.
- Index: `ix_monthly_closings_tenant_month` ON `(tenant_id, period_month)`.

### 2.2 `public.monthly_closing_items` (Strictly Append-Only Snapshot)
| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | `UUID` | PK |
| `tenant_id` | `UUID` | FK `tenants.id` `RESTRICT`, NOT NULL |
| `closing_id` | `UUID` | FK `monthly_closings.id` `RESTRICT`, NOT NULL |
| `store_id` | `UUID` | FK `stores.id` `RESTRICT`, NOT NULL |
| `period_month` | `VARCHAR(7)` | NOT NULL |
| `revision_number` | `INTEGER` | NOT NULL, CHECK `>= 1` |
| `employee_id` | `UUID` | FK `employees.id` `RESTRICT`, NOT NULL |
| `employee_code` | `VARCHAR(32)` | NOT NULL |
| `employee_full_name` | `VARCHAR(160)` | NOT NULL |
| `nie_dni_masked` | `VARCHAR(32)` | NULL |
| `contract_type` | `VARCHAR(32)` | NOT NULL |
| `work_schedule_type` | `VARCHAR(20)` | NOT NULL |
| `contracted_weekly_hours` | `NUMERIC(4, 2)` | NOT NULL |
| `worked_days` | `INTEGER` | NOT NULL, CHECK `>= 0` |
| `total_net_hours` | `NUMERIC(6, 2)` | NOT NULL, CHECK `>= 0` |
| `regular_hours` | `NUMERIC(6, 2)` | NOT NULL, CHECK `>= 0` |
| `overtime_hours` | `NUMERIC(6, 2)` | NOT NULL, CHECK `>= 0` |
| `annual_overtime_hours` | `NUMERIC(6, 2)` | NOT NULL, CHECK `>= 0` |
| `total_break_minutes` | `INTEGER` | NOT NULL, CHECK `>= 0` |
| `vacation_days` | `INTEGER` | NOT NULL, CHECK `>= 0` |
| `sick_leave_days` | `INTEGER` | NOT NULL, CHECK `>= 0` |
| `paid_leave_days` | `INTEGER` | NOT NULL, CHECK `>= 0` |
| `unpaid_leave_days` | `INTEGER` | NOT NULL, CHECK `>= 0` |
| `warning_alerts_count` | `INTEGER` | NOT NULL, CHECK `>= 0` |
| `violation_alerts_count` | `INTEGER` | NOT NULL, CHECK `>= 0` |
| `attendance_report_document_id` | `UUID` | FK `compliance_documents.id` `RESTRICT`, NULL |
| `retention_until` | `TIMESTAMPTZ` | NOT NULL |
| `created_at` | `TIMESTAMPTZ` | NOT NULL |

- Unique Constraint: `uq_monthly_closing_items_rev_emp` ON `(tenant_id, closing_id, revision_number, employee_id)`.
- Index: `ix_monthly_closing_items_closing_rev` ON `(tenant_id, closing_id, revision_number)`.
- **PostgreSQL Immutability Triggers**:
  - `trg_monthly_closing_items_immutable` (`BEFORE UPDATE OR DELETE ON public.monthly_closing_items FOR EACH ROW`) raising `ERROR: monthly_closing_items is strictly append-only (RDL 8/2019)`.
  - `trg_monthly_closing_items_no_truncate` (`BEFORE TRUNCATE ON public.monthly_closing_items FOR EACH STATEMENT`) raising `ERROR: TRUNCATE is forbidden on monthly_closing_items (RDL 8/2019)`.

### 2.3 P2 Index on `public.employees`
- Partial Unique Index: `uq_employees_tenant_user_active` ON `(tenant_id, user_id)` WHERE `user_id IS NOT NULL AND deleted_at IS NULL`.

### 2.4 Reversible `downgrade()`
- Drops `uq_employees_tenant_user_active`, triggers & trigger functions on `monthly_closing_items`, `monthly_closing_items`, and `monthly_closings` cleanly.

---

## 3. Pure Domain Layer & Zero-Dependency PDF Renderer

### 3.1 `backend/app/domain/payroll.py` (Pure Stdlib Only)
- Enforced by `scripts/check_domain_purity.py` (zero third-party imports).
- **Enums**:
  - `MonthlyClosingStatus`: `CLOSED = "closed"`, `REOPENED = "reopened"`.
  - `ClosingBlockerCode`: `OPEN_SHIFT = "OPEN_SHIFT"`, `PENDING_LEAVE = "PENDING_LEAVE"`, `ALREADY_CLOSED = "ALREADY_CLOSED"`.
- **Entities & Value Objects** (all `@dataclass(frozen=True, slots=True)`):
  - `MonthlyClosing`, `MonthlyClosingItem`, `ClosingBlocker`, `MonthlyEmployeeSummary`, `MonthlyReadinessReport`, `AttendancePdfDayRow`, `AttendancePdfDocumentInput`.
- **Port**:
  - `AttendancePdfRendererPort(Protocol)`: `def render_monthly_attendance_pdf(self, data: AttendancePdfDocumentInput) -> bytes: ...`.
- **Pure Functions**:
  - `month_date_bounds(period_month: str) -> tuple[date, date]` (validates `YYYY-MM`, leap-year safe via `calendar.monthrange`).
  - `spanned_period_months(start_date: date, end_date: date) -> tuple[str, ...]` (returns ordered `YYYY-MM` tuple spanning an inclusive date range for leave lock checks).
  - `count_overlap_days(start_date: date, end_date: date, window_start: date, window_end: date) -> int`.
  - `should_include_employee_in_month(employee: Employee, *, month_start: date, month_end: date, has_events_in_month: bool, has_leaves_in_month: bool) -> bool`.
  - `compute_closing_retention_until(period_month: str, now_utc: datetime) -> datetime` (`>= 4` years from `max(month_end_eod_utc, now_utc)`).
  - `evaluate_employee_monthly_summary(...) -> tuple[MonthlyEmployeeSummary, tuple[ClosingBlocker, ...]]`:
    - Runs `evaluate_attendance_ledger(..., date_from=month_start, date_to=month_end)`.
    - Emits `OPEN_SHIFT` blocker if any session in `report.sessions` has `is_open=True`.
    - Emits `PENDING_LEAVE` blocker for every `LeaveStatus.PENDING` request overlapping `[month_start, month_end]`.
    - Aggregates `APPROVED` leave overlap days by type (`vacation_days`, `sick_leave_days`, `paid_leave_days`, `unpaid_leave_days`).
    - Computes `worked_days`, `total_break_minutes`, `warning_alerts_count`, `violation_alerts_count`.

### 3.2 `backend/app/infrastructure/pdf_generator.py`
- `PurePythonAttendancePdfRenderer` implementing `AttendancePdfRendererPort` using **Python stdlib only**.
- Generates deterministic, multi-page A4 `%PDF-1.4` documents (`595.28 x 841.89 pt`) with `/BaseFont /Helvetica` and `/Helvetica-Bold` + `/Encoding /WinAnsiEncoding` (native Spanish `á é í ó ú ñ Ñ ü Ü ¡ ¿ €`).
- Non-WinAnsi characters (e.g. CJK in store display name) degrade gracefully to safe ASCII/code fallback without throwing encoding errors.
- Document sections (100% `es-ES` compliant with RDL 8/2019 / Art. 34.9 ET):
  1. Legal Header: `RESUMEN MENSUAL DE REGISTRO DIARIO DE JORNADA (RDL 8/2019)`, Revision `v{N}`, Period `YYYY-MM`.
  2. Employer & Store Block: `Razón Social`, `CIF`, `Centro` (`code` + `name`), `Zona Horaria IANA`.
  3. Employee Block: `Trabajador` (`full_name`), `Código`, `NIE/DNI` (masked only), `Contrato`, `Jornada`, `Horas/Sem`.
  4. Daily Ledger Table (paginated automatically across pages): `Fecha`, `Entrada`, `Salida`, `Pausa (min)`, `Horas Netas`, `Corr.`, `Notas`.
  5. Monthly Totals & Leave Summary: `Días Trabajados`, `Horas Netas`, `Horas Ordinarias`, `Horas Extra`, `Vacaciones`, `Baja Médica (IT)`, `Permisos Retribuidos`, `Permisos No Retribuidos`, `Alertas`.
  6. Electronic Signature Notice & SHA-256 Audit Footer.

---

## 4. Application Layer, Month-Lock Enforcement & RBAC Matrix

### 4.1 Cross-Module Closed-Month Write Locks (`ConflictError` -> HTTP 409)
- `PayrollClosingRepository` (Protocol in `domain/repositories.py`, SQLAlchemy impl in `infrastructure/repositories.py`):
  - `is_store_month_closed(tenant_id: UUID, store_id: UUID, period_month: str) -> bool`
  - `list_closed_months_for_store(tenant_id: UUID, store_id: UUID, period_months: Sequence[str]) -> set[str]`
- **Enforced in**:
  - `ClockAttendanceUseCase`: checks `(store.id, local_occurred_date.strftime("%Y-%m"))`.
  - `AppendCorrectionUseCase`: checks BOTH `(store.id, occurred_local_month)` AND, when `supersedes_event_id` is present, `(superseded.store_id, superseded_local_month)`.
  - `CreateLeaveRequestUseCase`, `ReviewLeaveRequestUseCase`, `CancelLeaveRequestUseCase`: checks `list_closed_months_for_store(tenant_id, store_id, spanned_period_months(start_date, end_date))`.
- Note: Existing unit tests constructing Attendance/Leave use cases without `payroll_repo` continue to work via optional `payroll_repo: PayrollClosingRepository | None = None`, while FastAPI `deps.py` **always** injects the live repository.

### 4.2 Payroll Use Cases (`backend/app/application/payroll.py`)
1. **`ListMonthlyClosingsUseCase`**:
   - Roles: `owner`, `hr`, `accountant` (all stores); `store_manager` (authorized `store_ids` only, hides `legal_hold`/`anonymized` employee items); `employee` -> `403`.
   - Returns list of closings with latest-revision aggregated KPIs (`employee_count`, `total_net_hours`, `total_overtime_hours`, `signed_count`, `total_reports`).
2. **`PreviewMonthlyClosingUseCase`**:
   - Roles: `owner`, `hr`, `accountant`, `store_manager` (authorized store only; hides `legal_hold`/`anonymized` employees); `employee` -> `403`.
   - Computes live `MonthlyReadinessReport` + existing closing state + `can_close` boolean.
3. **`CloseMonthlyPayrollUseCase`**:
   - Roles: **`owner` and `hr` ONLY** (`403` for `store_manager`, `accountant`, `employee`).
   - Runs full readiness check across ALL eligible store employees (including `legal_hold` if active/recorded in that month).
   - Rejects with `409 Conflict` if already `closed` or any `OPEN_SHIFT` / `PENDING_LEAVE` blocker exists.
   - Generates per-employee `es-ES` A4 PDF, uploads via `DocumentStoragePort` (tracks keys and rolls back uploaded storage objects if DB persistence fails), inserts `ComplianceDocument` (`category=ATTENDANCE_REPORT`, `requires_signature=True`, `period_month=period_month`), creates/updates `MonthlyClosing` (`status=CLOSED`, `revision_number += 1`), and appends immutable `MonthlyClosingItem` rows.
4. **`GetMonthlyClosingDetailUseCase`**:
   - Roles: `owner`, `hr`, `accountant` (full); `store_manager` (authorized store only, filters out `legal_hold`/`anonymized` items); `employee` -> `403`.
   - Returns closing metadata, latest revision items enriched with live `DocumentReceipt` status (`is_signed`, `signed_at_utc`, `document_sha256_hex`).
5. **`ReopenMonthlyClosingUseCase`**:
   - Roles: **`owner` and `hr` ONLY** (`403` for others).
   - Requires non-empty `reopen_reason` (`max_length=500`) and `status == CLOSED` (else `409 Conflict`).
   - Transitions status to `REOPENED` with `reopened_by_user_id`, `reopened_at_utc`, `reopen_reason`. Historical `monthly_closing_items`, PDFs, and receipts remain untouched.
6. **`ExportGestoriaPayrollCsvUseCase`**:
   - Roles: **`owner`, `hr`, `accountant` ONLY** (`store_manager` & `employee` -> `403`).
   - Requires `closing.status == CLOSED` (else `409 Conflict`).
   - Returns UTF-8 BOM (`\ufeff`) `es-ES` CSV with CWE-1236 `_sanitize_csv_cell` on every string cell, masked NIE/DNI only, live signature status per employee, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`.

### 4.3 API Endpoints (`/api/v1/payroll`)
| Method | Path | Operation ID | Allowed Roles |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/payroll/closings` | `list_monthly_closings` | `owner`, `hr`, `accountant`, `store_manager` (scoped) |
| `GET` | `/api/v1/payroll/preview` | `preview_monthly_closing` | `owner`, `hr`, `accountant`, `store_manager` (scoped) |
| `POST` | `/api/v1/payroll/closings` | `close_monthly_payroll` | `owner`, `hr` |
| `GET` | `/api/v1/payroll/closings/{closing_id}` | `get_monthly_closing` | `owner`, `hr`, `accountant`, `store_manager` (scoped) |
| `POST` | `/api/v1/payroll/closings/{closing_id}/reopen` | `reopen_monthly_closing` | `owner`, `hr` |
| `GET` | `/api/v1/payroll/closings/{closing_id}/gestoria-export` | `export_gestoria_payroll_csv` | `owner`, `hr`, `accountant` |

---

## 5. Phase 1–4 P2 Backlog Cleanup

1. **Spanish DNI/NIE Mod-23 & IBAN Mod-97 (`backend/app/domain/employees.py`)**:
   - `normalize_nie_dni`: verify check letter against `TRWAGMYFPDXBNJZSQVHLCKE` (mapping NIE `X->0`, `Y->1`, `Z->2`, remainder `number % 23`).
   - `normalize_iban`: verify ISO 7064 Mod-97 (`move first 4 chars to end, replace A-Z with 10-35, int(...) % 97 == 1`).
   - Update existing test fixtures in `backend/tests/` to use mathematically valid Spanish DNI/NIE & IBAN test values.
2. **Non-ASCII Filename Fallback (`backend/app/interfaces/api/documents.py`)**:
   - When stripping non-ASCII characters leaves an empty stem or leading dot (e.g. `".pdf"` from `"工资单.pdf"`), produce `"document.pdf"` (preserving valid ASCII extension) alongside RFC 6266 `filename*=UTF-8''...`.
3. **Store Manager Own-Document Access (`backend/app/application/documents.py`)**:
   - Allow `store_manager` to list, inspect, generate signed URL, and sign receipts for documents explicitly assigned to **themselves** (`doc.employee_id == self_emp.id`, even for `contract`/`payslip`/`id_document`), while keeping all other employees' restricted categories and `legal_hold` documents strictly `403`.
4. **Partial Unique Index on `employees(tenant_id, user_id)`**:
   - Added in `EmployeeModel.__table_args__` and migration `0005`.
5. **Settings Propagation (`main.py`, `deps.py`, `documents.py`, `auth.py`)**:
   - Attach `application.state.settings = settings` in `create_app(settings_override=...)`; add `get_app_settings(request: Request) -> Settings` in `deps.py` and propagate it to JWT verifier, PII cipher, document storage, and proxy IP extraction (no hidden reliance on global `get_settings()` singleton when overridden).

---

## 6. Frontend UI (`PayrollPage.tsx`, Template 1)

- **Navigation & Route**: `/payroll` added to `App.tsx` and `AppShell.tsx` (visible to `owner`, `hr`, `accountant`, `store_manager`; hidden from `employee`).
- **Design System (`okteam-ui-craft`)**:
  - `bg-slate-50` canvas, `bg-white` cards, `1px border-slate-200`, `slate-900` primary, `teal-700` <= 5% accent, `tabular-nums` on every number/date/hour/percentage, `lucide-react` icons only, zero gradients/emojis.
  - Responsive wrapping controls so `es-ES` labels never overflow at `1440px` or `390px`.
  - IME guard (`!isImeComposing(e)`) on Reopen reason input & notes.
- **Layout (Template 1)**:
  - Compact top bar: Store selector, Month (`YYYY-MM`) picker, role-gated primary buttons (`Close Month`, `Export Gestoría CSV`, `Reopen Month`).
  - 4-card KPI strip: Status & Revision (`vN`), Employees Included, Total / Overtime Hours, SHA-256 Signature Progress (`signed / total`).
  - Readiness Blocker & Warning Banner (shows exact `OPEN_SHIFT` / `PENDING_LEAVE` items with quick links to `/attendance` or `/leaves`).
  - Full-width TanStack Table of employees in the selected store-month (click row -> right-side `Sheet` showing detailed hours, leave breakdown, legal alert count, live SHA-256 signature receipt status, and on-demand 60s signed download of that employee's RDL 8/2019 PDF).
  - Reopen `Dialog` requiring mandatory `reopen_reason`.
- **Contracts**: Generated strictly via `scripts/sync-contracts.ps1` (`contracts/openapi.json` -> `frontend/src/api/generated/schema.d.ts`).

---

## 7. Verification & Quality Gates

1. `scripts/quality-gate.ps1 -Scope all`: `domain-purity` PASS | `ruff` PASS | `mypy app` PASS | `pytest` PASS (all existing + comprehensive Phase 5 unit/API/security/P2 tests) | `tsc --noEmit` PASS | `eslint` PASS.
2. `npm --prefix frontend run build`: PASS.
3. Live PostgreSQL 16 (`localhost:5433/okteam`): `alembic upgrade head` (`0004 -> 0005`), `downgrade -1` (`0005 -> 0004`), `upgrade head` (`0004 -> 0005`) + live SQL verification that `UPDATE`/`DELETE`/`TRUNCATE` on `public.monthly_closing_items` are blocked by PostgreSQL triggers.
4. Full-stack Docker rebuild (`docker compose --profile local-db up -d --build`) + live health check.
5. `chrome-devtools` `es-ES` visual audit on `/payroll` at `1440px` and `390px` (0 horizontal overflow, clean typography & alignment).
6. **Red Team Gatekeeper** (`research` subagent) audit of Phase 5 backend/DB/RBAC/PDF/CSV/month-lock security with 2-round circuit breaker — must achieve **PASS** (`P0: None`, `P1: None`) before commit & push.
