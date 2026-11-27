# okTeam Session State (Hard Limit <= 50 Lines)

- **Current Phase**: Phase 5I Complete & Pushed (`a384b7e`) — Interactive Organization Chart (`Organigrama`) + Phase 5H Time Management + `GEMINI.md` / ADR-016 Token-Efficiency Rules.
- **Last Updated**: 2026-11-27
- **Active Branch**: `main` (pushed `a384b7e`, plus local rule updates `GEMINI.md` / `DECISIONS.md` / `okteam-ui-craft/SKILL.md`).
- **Alembic Head**: `0012_employee_manager_id` (applied to local Postgres `localhost:5433`).

## Verification on 2026-11-27 (Phase 5I Interactive Org Chart)
- **Backend (Red Team Round 2/2 APPROVE — 0 P0 / 0 P1 / 0 P2)**:
  - Migration `0012_employee_manager_id`: nullable self-referential `employees.manager_id` (`ON DELETE SET NULL`), `ck_employees_no_self_manager`, `ix_employees_tenant_manager`.
  - Pure domain `backend/app/domain/org_chart.py`: stdlib-only iterative O(N) `build_org_chart` with 3-color deterministic cycle breaking, exact `direct_reports_count`/`total_reports_count`, and `root_ids`/`unassigned_ids` partitioning.
  - `GET /api/v1/organization/org-chart` (`organization.py`): read-only for all active tenant roles (including `employee`); strict 12-field `OrgChartNodePublic` (`ConfigDict(extra="forbid")`) with zero sensitive HR/PII fields; `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`; resilient per-employee avatar signing.
  - `PATCH /api/v1/employees/{id}` preserves omitted `manager_id` via `UNSET` + `model_fields_set` (explicit `null` clears) and rejects cycles/inactive/cross-tenant managers (422); GDPR `anonymize()` clears `manager_id` on anonymized employee & active subordinates.
- **Frontend**:
  - `frontend/src/api/types.ts`: generated `OrgChartNodePublic` & `OrgChartResponse` aliases (zero hand-written DTOs).
  - `OrgChartView.tsx` + `orgChartLayout.ts` + `orgChartExport.ts` + `OrgChartPublicDrawer.tsx`: light `#F3F5F9` dot-grid pan/zoom canvas (`25%`–`200%`), pure white cards with customizable fields & `direct (total)` expand/collapse pill, anti-explosion 5-col compact grid (`> 5` unexpanded reports) with `#CBD5E1` orthogonal connectors, collapsible "未分配汇报线" group, top `role="toolbar"` (IME-safe search/locate, customize, highlight, focus branch, filter, SVG + 2× Retina PNG export), floating bottom-right control bar (`−/%/+`, Fit, Locate Me, Expand/Collapse level, Home), and role-graded Public Card Drawer (`查看完整人事档案` strictly hidden for `employee`/`accountant`).
  - `EmployeesPage.tsx` & `EmployeeProfilePage.tsx`: semantic `tablist` (`员工列表 | 组织架构`), `employee` defaults to `组织架构` with public-only directory tab, optional `manager_id` selector for HR/Owner.
- **Quality Gates (ALL PASS)**: Domain purity 17/0, Ruff 0, Mypy 67/0, **Pytest 95/95**, `tsc --noEmit` 0, ESLint 0, Vite build PASS, **1037/1037 i18n keys**, OpenAPI & `schema.d.ts` synced.
- **Browser Audit (Chrome DevTools, 0 console errors)**: Verified `hr@dragon.es` & `zhou@dragon.es` (`employee`) at `1440×900` (`zh-CN`) and `390×844` (`es-ES`, `overflowX=false`); verified 5-col compact grid, unassigned group, search/locate, Locate Me, HR full-profile jump, and employee full-profile button hidden.
- **Local Docker Services**: `localhost:5173` (200), `localhost:8001/api/v1/health` (200), Postgres `localhost:5433` (rev `0012`).

## Routes for Visual Spot-Check
- Sidebar section `employees` (`组织与员工` / `Organización`) -> Tab **`组织架构` / `Organigrama`**:
  - Desktop `1440×900` as HR (`hr@dragon.es`): 5-column compact grid under Carlos Manager, toolbar (Search/Customize/Highlight/Focus/Filter/Export PNG+SVG), Public Drawer + `查看完整人事档案`.
  - Desktop `1440×900` as Employee (`zhou@dragon.es`): defaults to `组织架构`, `定位到我`, Public Drawer with NO full-profile button, public-only `员工列表`.
  - Mobile `390×844` (`es-ES`): `Apps -> Organización -> Organigrama` (`overflowX=false`, full-width search bar).
- Prior Phase 5H routes: `attendance` (`时间管理 -> 考勤记录`), `leaves`, `payroll`.
