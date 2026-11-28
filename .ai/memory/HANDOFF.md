# okTeam Handoff Snapshot (2026-11-28 — P1 Retail Ops & Compliance Enhancements)

> **Handoff Target**: Seamless Antigravity ↔ OpenAI Codex CLI takeover. Read `AGENTS.md`, `.ai/memory/SESSION_STATE.md`, and run `git status -s` first.

## 1. Current Workspace State
- **Branch**: `main` (all P1 changes + `AGENTS.md` committed and pushed cleanly to `origin/main`).
- **Alembic Head**: `0013_p1_retail_ops_enhancements` (applied and downgrade/upgrade round-trip verified on local Postgres `localhost:5433`, db `okteam`).
- **Quality Gate Status (`.\scripts\quality-gate.ps1 -Scope all` + `npm run build`)**: **ALL PASS**
  - Backend: Domain Purity `17/0`, Ruff `0`, Mypy `67/0`, Pytest **`146/146`** (`+47` new tests across 3 new Wave 1 test suites).
  - Frontend: `tsc --noEmit` `0`, ESLint `0`, Vite build PASS (`1826` modules), i18n parity **`1099/1099`** (`zh-CN.json` == `es-ES.json`).
- **Red Team Gatekeeper**: **Round 2/2 `PASS`** (0 P0 / 0 P1; Round 1's 1 P0 + 3 P1 all fixed and re-verified).

## 2. Delivered Scope Summary

### Wave 1 — Backend & Migration (`backend/`)
- **Migration**: `backend/alembic/versions/0013_p1_retail_ops_enhancements.py` (`0012_employee_manager_id -> 0013_p1_retail_ops_enhancements`) adds 3 nullable columns on `public.employees`: `work_permit_expiry_date`, `contract_end_date`, `kiosk_pin_hash` (zero Supabase system schema touches, full `downgrade()`).
- **B1 (Work Permit & Contract Expiry Alerts)**:
  - `POST/PATCH/GET /api/v1/employees` expose `work_permit_expiry_date` & `contract_end_date` (`UNSET` sentinel on PATCH preserves omitted fields, explicit `null` clears; GDPR `anonymize()` clears both + `kiosk_pin_hash`; excluded from `OrgChartNodePublic`).
  - `GET /api/v1/dashboard/admin` returns `expiry_alerts_count` & `expiry_alerts` (`<= today + 60 days`, including expired active/on_leave employees) strictly for `owner`/`hr`/`store_manager` (`store_ids ∩ department_scope`), empty for `accountant`/`employee`.
- **B2 (Store-Tablet Kiosk PIN & Cross-Store Clocking)**:
  - Added `PUT /api/v1/employees/{employee_id}/kiosk-pin` (`{ pin: "1234" | null }`, 4–6 ASCII digits or `null` to clear, `owner`/`hr`/in-scope `store_manager`, blocks frozen/terminated). Stored with salted PBKDF2-HMAC-SHA256 (210k) + `hmac.compare_digest`; API exposes only `has_kiosk_pin: bool` (never plaintext/hash).
  - `POST /api/v1/attendance/clock` (`KIOSK`) matches tenant-wide active/on_leave employees by `employee_code` (still binds event to tablet `store_id` & verifies store scope) and enforces `kiosk_pin` (`403` if missing/wrong when PIN configured).
  - `GetAttendanceLedgerUseCase` & `GetAdminDashboardUseCase` aggregate events by RBAC-scoped `employee_id` set (no `event.store_id` truncation of cross-store punch chains).
- **B3 (HR/Owner One-Step Leave Final Approval)**:
  - `ReviewLeaveRequestUseCase` allows `hr`/`owner` to directly approve/reject `pending_manager` requests (`APPROVED`/`REJECTED`) running full month-lock `409`, leave overlap `409`, published shift conflict `409`, and vacation balance `422` checks while recording `hr_reviewed_*` audit fields.
- **B4 (Cross-Store Shift Scheduling & Overnight Manual-Day Entry)**:
  - `POST /api/v1/shifts` accepts optional `store_id`; `store_manager` must pass BOTH `require_store_access(target_store_id)` AND `require_employee_in_scope(employee)` (preventing cross-store unauthorized scheduling & Art. 9 leave conflict leaks). `GetWeeklyScheduleUseCase` merges cross-store support employee rows and exposes employee self cross-store published shifts.
  - `validate_and_build_manual_day_timestamps()` supports overnight shifts (`clock_out_time <= clock_in_time`, next-day early-morning breaks, `<= 16h` cap, DST checks, cross-month closing lock `409`, Art. 34.3 ET 12h inter-shift rest linkage).
- **B5 (Optional Full NIE/NAF Gestoría Export)**:
  - `GET /api/v1/payroll/closings/{id}/gestoria-export?include_full_identifiers=false`: only `owner`/`hr`/`accountant` allowed (`employee`/`store_manager` `403`); `true` decrypts `nie_dni` & `naf` via `PiiCipher` with CWE-1236 sanitization, `Cache-Control: no-store`, and zero PII logs.

### Wave 1.5 — Contracts
- Synced `contracts/openapi.json` & `frontend/src/api/generated/schema.d.ts` via `.\scripts\sync-contracts.ps1`.

### Wave 2 — Frontend (`frontend/`)
- **New Component**: `frontend/src/components/EmployeeCombobox.tsx` (reusable IME-safe fuzzy search by `full_name`/`preferred_name`/`employee_code`).
- **F1**: `ManualDayClockSheet.tsx` + `AttendancePage.tsx` (`defaultDate` auto-fill from blank history rows + `<= 16h` overnight shift UI).
- **F2**: `EmployeeCombobox` across Attendance/Schedule/Leaves; `SchedulePage.tsx` dept filter, target-store timezone UTC conversion (new shifts & copy-last-week-to-draft), and optional `store_id`; `AttendancePage.tsx` month view reads `daily_summaries.planned_minutes` directly.
- **F3**: `AuthContext.tsx` ref + request sequence + `AbortController` + `AbortError` handling (eliminates duplicate `/api/v1/auth/me`, prevents token wipe on abort, validates `preferredTenantId`, clears state on error).
- **F4**: `PayrollPage.tsx` closing `AlertDialog` confirmation + `App.tsx` `navIntent` blocker jump to Attendance (`employeeId` + `localDate`) / Leaves (`employeeId`).
- **F5**: `LeavesPage.tsx` caches `pendingJustificanteLeaveId` to retry medical attachment upload without duplicate `POST /api/v1/leaves`.
- **F-Sync**: B1 expiry date inputs (`EmployeesPage.tsx`, `EmployeeProfilePage.tsx`) + 60-day alert card (`DashboardPage.tsx`); B2 Kiosk PIN input (`KioskPage.tsx`) + PIN manager (`EmployeeProfilePage.tsx`); B5 full-identifier export checkbox (`PayrollPage.tsx`).

## 3. Changed & New Files
- **New**: `AGENTS.md`, `backend/alembic/versions/0013_p1_retail_ops_enhancements.py`, `backend/tests/test_wave1_b1_b2_b4.py` (6), `backend/tests/test_wave1_b3_leave_onestep.py` (22), `backend/tests/test_wave1_b5_gestoria_full_ids.py` (19), `frontend/src/components/EmployeeCombobox.tsx`.
- **Modified Backend (21 files)**: `backend/app/domain/{attendance,dashboard,employees,repositories}.py`, `backend/app/application/{attendance,dashboard,employees,leaves,payroll,scheduling}.py`, `backend/app/infrastructure/{models,repositories}.py`, `backend/app/interfaces/api/{attendance,dashboard,employees,payroll,shifts}.py`, `backend/tests/{test_phase2_employees,test_phase3_attendance,test_phase5e_workflows_and_reports,test_phase5i_org_chart}.py`.
- **Modified Contracts & Frontend (16 files)**: `contracts/openapi.json`, `frontend/src/api/{generated/schema.d.ts,types.ts}`, `frontend/src/app/App.tsx`, `frontend/src/features/{auth/AuthContext,attendance/AttendancePage,attendance/KioskPage,attendance/ManualDayClockSheet,dashboard/DashboardPage,employees/EmployeesPage,employees/EmployeeProfilePage,leaves/LeavesPage,payroll/PayrollPage,schedule/SchedulePage}.tsx`, `frontend/src/i18n/locales/{zh-CN,es-ES}.json`.

## 4. Suggested Next Steps for Codex
1. Optional live browser smoke-check (or rebuild Docker containers if testing locally) and `git commit` for the P1 milestone.
2. **Non-blocking P2 Backlog (from Red Team Round 2/2)**:
   - Batch employee lookup in `payroll.py:_decrypt_identifiers` (eliminate N+1 query).
   - Add per-employee/terminal failed-PIN backoff/rate-limit in `attendance.py` Kiosk branch.
   - Batch `list_by_employee_window` in `scheduling.py:GetWeeklyScheduleUseCase` (and optionally minimize non-vacation `approved_leaves` types for visiting cross-store support employees).
