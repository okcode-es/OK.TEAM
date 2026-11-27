# okTeam Session State (Hard Limit <= 50 Lines)

- **Current Phase**: P0 Compliance & UX Defect Fixes Complete (B1–B4, F1–F4) — following `c66cc07` (`GEMINI.md` / ADR-016) & `a384b7e` (Phase 5I).
- **Last Updated**: 2026-11-27
- **Active Branch**: `main` (zero Alembic/OpenAPI contract changes).
- **Alembic Head**: `0012_employee_manager_id` (applied to local Postgres `localhost:5433`).

## Verification on 2026-11-27 (P0 Compliance & UX Fixes — Red Team PASS: 0 P0 / 0 P1)
- **Backend (`backend-hydrogen`, B1–B4)**:
  - **B1** (`backend/app/domain/attendance.py`): Art. 34.3 ET 12h inter-shift rest check skips same-day split shifts (`prev_session.local_date == curr_session.local_date`), preserving overnight/cross-year/DST validation.
  - **B2** (`backend/app/domain/attendance.py`): Weekly overtime accumulates chronologically per ISO week `(iso_year, iso_week)` and attributes incremental overtime minutes to the triggering `session.local_date`, preventing cross-month / cross-year double counting.
  - **B3** (`backend/app/application/payroll.py`): `_evaluate_store_month_readiness` aggregates attendance events & leaves by `tenant_id` + `employee_id in store_emp_ids` (no `store_id` drop) using each event's `store_timezone` for cross-store / cross-timezone closing accuracy.
  - **B4** (`pdf_generator.py`, `deps.py`, `pyproject.toml`, `uv.lock`, `Dockerfile`): Switched `get_pdf_renderer()` to `ReportLabAttendancePdfRenderer` (`reportlab>=4.2.0` + `fonts-noto-cjk`/`fonts-wqy-zenhei`), auto-detects Linux/Windows CJK fonts (fallback Helvetica), preserves `redact_health=True` GDPR masking, and adds dual footer signature boxes (`Firma y sello de la Empresa` / `Firma del Trabajador / Recibí`).
  - **Regression Tests**: Added split-shift, cross-month/cross-year overtime, cross-store/cross-timezone payroll closing, and CJK/redacted/signature PDF tests (`99/99 pytest` PASS).
- **Frontend (`frontend-hydrogen`, F1–F4)**:
  - **F1** (`AttendancePage.tsx`): `effectiveBreakMinutes` reads `summary.total_break_minutes` directly (no double-counting auto-deducted breaks).
  - **F2** (`AttendancePage.tsx`): Accountant `team-calendar` redaction check aligned to `entry.display_type === 'absence_redacted'`.
  - **F3** (`AttendancePage.tsx`): `correction-supersedes` allows chaining/voiding active corrections while filtering superseded & `void` records.
  - **F4** (`App.tsx`, `KioskPage.tsx`, `kioskLock.ts`, `AuthContext.tsx`, `zh-CN.json`, `es-ES.json`): Fullscreen standalone Kiosk without `AppShell`, 4-digit PIN lock/unlock modal, salted PBKDF2/SHA-256 `localStorage` persistence bound to `tenantId+userId+role` with synchronous render-time F5 refresh & cross-tab lock enforcement, cleared on `signOut`.
- **Quality Gates (ALL PASS)**: Domain purity 17/0, Ruff 0, Mypy 67/0, **Pytest 99/99**, `tsc --noEmit` 0, ESLint 0, Vite build PASS, **1054/1054 i18n keys**.
- **Red Team Notes (3 non-blocking P2)**: Custom range PDF header width tuning; `KioskPage` `loadStores` effect dependency trim; unlock modal retry cooldown.

## Prior Baseline (Phase 5I — `a384b7e`)
- Interactive Org Chart (`Organigrama`), Phase 5H Time Management, `GEMINI.md` / ADR-016 rules all intact.
- Local Docker Services: `localhost:5173` (200), `localhost:8001/api/v1/health` (200), Postgres `localhost:5433` (rev `0012`).
