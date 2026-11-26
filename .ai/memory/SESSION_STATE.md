# okTeam Session State (Hard Limit <= 50 Lines)

- **Current Phase**: Phase 5G — HR Workbench Fixes, Dashboard Upgrade, HR Leave Entitlements/Direct Assign & 3-Tier Custom Break Policies (Complete, Red Team Approved, Browser Verified, Docker Live)
- **Last Updated**: 2026-11-26
- **Active Branch**: main (Phases 5A–5G + Fluid Split-Screen Login complete locally; awaiting user review/push)

## Latest Delivered (Stage A Backend + Stage B Frontend + Red Team Round 2/2 APPROVE)
- **P0 Workbench & RBAC Fixes**:
  - Fixed Deep Navy `#0B1120` sidebar layout (`h-screen sticky top-0 flex flex-col`, `flex-1 min-h-0 overflow-y-auto sidebar-scroll`, fixed bottom footer — zero clipping); removed duplicate Sign Out from top header.
  - Fixed `hr@dragon.es` role/name display (`Elena Chen` / `林娜`, `人事主管 / RR. HH.`, `organization` route & nav restricted to `owner` only; zero email-prefix greeting).
  - Fixed `ManualDayClockSheet` error visibility (above `<SheetFooter>` with `scrollIntoView`), Europe/Madrid local date, proactive open-shift banner, and emerald success feedback.
- **HR Dashboard 4 Upgrades (`DashboardPage.tsx`, `dashboard.py`)**:
  - Top 4 clickable KPI alert cards (`pending_leaves_count`, `attendance_alerts_count`, `pending_justificantes_count` forced `0` for `accountant`, `unacknowledged_announcements_count` strictly scoped to `store_ids ∩ department_scope` + filters).
  - 3-group sidebar (`日常管理 / 合规与行政 / 企业配置`), Department `<Select>` dropdown, announcements empty-state CTA, compact `Celebraciones`, clean `divide-y` `Who's in`, 2-tier mini calendar holiday tiles.
- **HR Leave Entitlements & Direct Assign (`0010_hr_leave_break_policies`)**:
  - Annual vacation entitlements (`>= 30` statutory Art. 38 ET floor, carryover, comp-off, append-only `leave_entitlement_adjustments` protected by PostgreSQL triggers) + single/bulk `LeaveEntitlementDialog`.
  - Direct HR/Owner/scoped Store Manager leave assignment (`POST /api/v1/leaves/assign`, auto-`approved`, checks closed month, overlap, published shifts, balance) + `AssignLeaveSheet` in `LeavesPage` & `ProfileLeavesTab`.
- **3-Tier Custom Break Policies & Auto-End (`0009`, `0010`)**:
  - Enterprise / Store / Employee inheritance (`employee > store > tenant > system_default`), supporting `fixed_duration` & `fixed_window` (*jornada partida*), `is_paid`, `manual_punch` (with server-authoritative `auto_end_manual_break` + live 1s countdown) vs `auto_deduct`, integrated across ledger, dashboard, payroll closing, and reports.

## Verified Gates (All Passed)
- **Red Team Gatekeeper**: Round 2/2 **APPROVE** (0 P0, 0 P1, 0 P2 remaining).
- **Quality Gate (`quality-gate.ps1 -Scope all`)**: Domain purity (16 files, 0 violations), Ruff 0, Mypy (64 files) 0, **Pytest 80/80**, `tsc --noEmit` 0, ESLint 0, `vite build` PASS, **893/893 i18n keys (0 missing)**.
- **Chrome DevTools Visual Audit (Round 1 PASS)**: `1440×900` `zh-CN` & `es-ES` for HR Dashboard, `AssignLeaveSheet`, and Employee 360° Custom Break Policy — `0` console errors/warnings, `overflowX=false`, `asideBottomClipped=false`.
- **Live Docker**: `okteam-local-db:5433` (healthy, rev `0010`), `okteam-backend:8001` (healthy), `okteam-frontend:5173` (running).

## Next Action
- User reviews Phase 5G live at `http://localhost:5173` and verified screenshots; await approval to `git commit` & `push` to `origin/main` or begin Phase 6.
