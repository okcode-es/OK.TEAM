# okTeam Session State (Hard Limit <= 50 Lines)

- **Current Phase**: P1 Retail High-Frequency UX & Compliance Enhancements Complete (B1–B5, F1–F5, F-Sync) — Committed & Pushed to `main`.
- **Last Updated**: 2026-11-28
- **Active Branch**: `main` (clean working tree; root `AGENTS.md` included for Codex handoff).
- **Alembic Head**: `0013_p1_retail_ops_enhancements` (applied & downgrade/upgrade verified on local Postgres `localhost:5433`).

## Verification on 2026-11-28 (P1 Retail Ops — Red Team Round 2/2 PASS: 0 P0 / 0 P1)
- **Wave 1 Backend (`B1–B5` + `0013_p1_retail_ops_enhancements.py`)**:
  - **B1**: Nullable `work_permit_expiry_date` & `contract_end_date` on `employees` (cleared on GDPR `anonymize()`, excluded from `OrgChartNodePublic`); 60-day expiry alerts on `GET /api/v1/dashboard/admin` scoped to `owner`/`hr`/`store_manager` (`store_ids ∩ department_scope`), hidden from `employee`/`accountant`.
  - **B2**: Nullable salted PBKDF2-SHA256 `kiosk_pin_hash` (`PUT /api/v1/employees/{id}/kiosk-pin`, `has_kiosk_pin` boolean only, zero hash/PIN leak); tenant-wide Kiosk cross-store clocking by `employee_code` (recording tablet `store_id`, `403` on missing/wrong PIN); ledger & dashboard aggregate full cross-store punch chains without `event.store_id` truncation.
  - **B3**: `ReviewLeaveRequestUseCase` allows `hr`/`owner` direct one-step approval/rejection on `pending_manager` leaves with full month-lock/overlap/shift-conflict/balance validation and HR audit trail.
  - **B4**: `POST /api/v1/shifts` optional `store_id` (enforcing both `require_store_access` & `require_employee_in_scope` for `store_manager`), cross-store rows merged into weekly schedule & employee self-view; `validate_and_build_manual_day_timestamps()` supports overnight shifts (`clock_out_time <= clock_in_time`, `<= 16h`, cross-month lock, Art. 34.3 ET 12h rest).
  - **B5**: `GET /api/v1/payroll/closings/{id}/gestoria-export?include_full_identifiers=true` decrypts `NIE/DNI` & `NAF` via `PiiCipher` with CWE-1236 escaping & `Cache-Control: no-store` strictly for `owner`/`hr`/`accountant` (`employee`/`store_manager` `403`, zero PII logs).
- **Wave 1.5 Contracts**: Synced `contracts/openapi.json` & `frontend/src/api/generated/schema.d.ts`.
- **Wave 2 Frontend (`F1–F5` + `F-Sync`)**:
  - `ManualDayClockSheet` `defaultDate` prefill & `<= 16h` overnight support; reusable IME-safe `EmployeeCombobox`; `SchedulePage` dept filter, target-store timezone UTC conversion & copy-last-week-to-draft; `AttendancePage` month view uses `daily_summaries.planned_minutes`; `AuthContext` ref + `AbortController` + seq guard fixes double `/auth/me` & tenant switch races; `PayrollPage` closing `AlertDialog` + blocker `navIntent` jump + full-ID export toggle; `LeavesPage` `pendingJustificanteLeaveId` retry fix; B1 expiry alerts/fields & B2 Kiosk PIN UI.
- **Quality Gates (ALL PASS)**: Domain purity 17/0, Ruff 0, Mypy 67/0, **Pytest 146/146 (+47 new)**, `tsc --noEmit` 0, ESLint 0, Vite build PASS (1826 modules), **1099/1099 i18n keys**.
- **Codex Handoff**: Root `AGENTS.md` added; see `.ai/memory/HANDOFF.md` for full file list & non-blocking P2 backlog.
