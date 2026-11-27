# okTeam Handoff Snapshot (2026-11-27 — Phase 5I + 5H)

## Phase 5I: Interactive Organization Chart (`Organigrama`) + `employees.manager_id`

### 1. Backend Deliverables (`backend/`, Alembic head `0012_employee_manager_id`)
- **Migration & Persistence (`0012_employee_manager_id.py`, `models.py`, `repositories.py`)**:
  - Added nullable `employees.manager_id` FK (`employees.id`, `ON DELETE SET NULL`), `ck_employees_no_self_manager`, and `ix_employees_tenant_manager` (`tenant_id, manager_id`).
  - Updated `SqlAlchemyEmployeeRepository.anonymize()` to clear `model.manager_id = None` and bulk-clear `manager_id` on active subordinates in the same tenant (GDPR Art. 17).
- **Pure Domain (`backend/app/domain/org_chart.py`, `backend/app/domain/employees.py`)**:
  - Added `Employee.manager_id` and iterative `validate_no_manager_cycle(...)`.
  - Added stdlib-only `OrgChartNode`, `OrgChartResult`, and iterative O(N) `build_org_chart(...)` with 3-color deterministic cycle breaking, BFS + reverse-topological `direct_reports_count`/`total_reports_count`, and `root_ids`/`unassigned_ids` partitioning.
- **Application & API (`backend/app/application/org_chart.py`, `backend/app/interfaces/api/organization.py`, `employees.py`, `main.py`)**:
  - Added `GET /api/v1/organization/org-chart` (`operation_id="getOrganizationOrgChart"`) open read-only to all active tenant roles (`owner`, `hr`, `store_manager`, `accountant`, `employee`).
  - Enforced strict 12-field GDPR projection `OrgChartNodePublic` (`ConfigDict(extra="forbid")`: `id`, `full_name`, `avatar_url`, `job_title`, `department_id`, `department_name`, `store_id`, `store_name`, `work_email`, `manager_id`, `direct_reports_count`, `total_reports_count`) and `OrgChartResponse` (`ConfigDict(extra="forbid")`) with `Cache-Control: no-store` & `X-Content-Type-Options: nosniff`.
  - Updated `PATCH /api/v1/employees/{id}` with `UNSET` sentinel + `model_fields_set` so omitting `manager_id` preserves existing reporting lines while explicit `null` clears it; validates same-tenant active manager and rejects cycles (422).
  - Updated dev-only `backend/scripts/seed_demo_accounts.py` with realistic reporting lines (`EMP-003` -> `EMP-004` Carlos Manager -> 7 direct reports, plus standalone `EMP-010`).
- **Tests, Contracts & Red Team**:
  - Added `backend/tests/test_phase5i_org_chart.py` (95/95 total pytest passing).
  - Synced `contracts/openapi.json` and `frontend/src/api/generated/schema.d.ts`.
  - **Red Team Gatekeeper Round 2/2**: `APPROVE` (0 P0 / 0 P1 / 0 P2).

### 2. Frontend Deliverables (`frontend/`)
- **Generated Types (`frontend/src/api/types.ts`)**: Aliased `OrgChartNodePublic` & `OrgChartResponse` from `schema.d.ts` (zero hand-written DTOs).
- **Layout & Export Engines (`orgChartLayout.ts`, `orgChartExport.ts`)**:
  - Pure TypeScript deterministic layout engine supporting multi-root trees, filter ancestor bridge nodes, anti-explosion 5-column compact grid (`> 5` direct reports with none expanded), crisp `#CBD5E1` orthogonal connectors, and collapsible unassigned reporting-line section.
  - Zero-dependency programmatic UTF-8 SVG & 2× Retina PNG export (`#F3F5F9` background).
- **Interactive Org Chart & Public Drawer (`OrgChartView.tsx`, `OrgChartPublicDrawer.tsx`)**:
  - Top `role="toolbar"` with IME-safe search & locate, customize card fields, highlight by department/store, focus branch (`我的直属团队` / selected node / reset), store+department filter, and PNG/SVG export.
  - `#F3F5F9` dot-grid pan/zoom canvas (`25%`–`200%`), pure white cards with `direct (total)` expand/collapse capsule, bottom-right floating control bar (`−/%/+`, Fit, Locate Me, Expand/Collapse level, Home).
  - Role-graded right-side Public Card Drawer showing strictly public fields, clickable direct manager & direct reports, and cobalt `查看完整人事档案` button visible ONLY to `owner`, `hr`, or in-scope `store_manager` (never rendered for `employee`/`accountant`).
- **Navigation & HR Manager Editing (`EmployeesPage.tsx`, `EmployeeProfilePage.tsx`, `zh-CN.json`, `es-ES.json`)**:
  - Renamed sidebar nav `employees` to `组织与员工 / Organización`, accessible to all roles; ordinary `employee` defaults directly to `组织架构` and sees a public-only directory under `员工列表`.
  - Added optional `直属主管 / Responsable directo` selector for HR/Owner in Create/Edit Employee flows.

### 3. Verification & Visual Spot-Check Routes
- **Quality Gates**: `scripts/quality-gate.ps1 -Scope all` PASS (Domain purity 17/0, Ruff 0, Mypy 67/0, Pytest 95/95, `tsc --noEmit` 0, ESLint 0), Vite build PASS, **1037/1037 i18n keys**.
- **Local Services**: Rebuilt & running (`http://localhost:5173` 200, `http://localhost:8001/api/v1/health` 200, Postgres `localhost:5433` rev `0012`).
- **Routes for Browser Visual Spot-Check**:
  1. `http://localhost:5173` -> Sidebar `组织与员工 / Organización` -> Tab `组织架构 / Organigrama` as HR (`hr@dragon.es`): expand Carlos Manager (`7 (7)`) to verify 5-column compact grid, unassigned group (`未分配汇报线`), Search/Customize/Highlight/Focus/Filter/Export PNG+SVG, and Public Drawer -> `查看完整人事档案`.
  2. `http://localhost:5173` -> Dev Login as Employee (`zhou@dragon.es`) -> Sidebar `组织与员工`: verify default `组织架构` tab, `定位到我`, Public Drawer with zero full-profile button, and public-only `员工列表`.
  3. Mobile `390×844` (`es-ES`): `Apps -> Organización -> Organigrama` (`overflowX=false`, clean subtitle & full-width search bar).
  4. Phase 5H routes (also in working tree): `attendance` (`时间管理 -> 考勤记录`), `leaves`, `payroll` (`月度关账`).
