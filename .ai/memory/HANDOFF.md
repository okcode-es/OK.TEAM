# okTeam Handoff Snapshot (2026-11-26)

## Completed & Live Locally (Uncommitted on `main`)
1. **Phases 5A–5D**: Workspace Ops, Private Avatars, Dual-View Personio-Grade Light Enterprise UI, Employee 360° Profile, GDPR Art. 9 Team Calendar Gantt, Zero-Leak Error Control, UX & i18n Polish.
2. **Phase 5E + Enterprise Login**: 5 Demo Accounts (`owner@dragon.es`, `hr@dragon.es`, `manager@dragon.es`, `zhou@dragon.es`, `gestoria@asesoria.es`), Self-Selectable Clock Times + `ManualDayClockSheet`, Two-Stage Leave Approval (`0008`), Management Reports (CSV/PDF), Employee RBAC Nav Guards, Dark Sidebar Scrollbar, Fluid Split-Screen Login (`LoginFluidBackdrop.tsx`).
3. **Phase 5F & 5G (HR Workbench Polish + Dashboard Upgrade + HR Leave Entitlements/Direct Assign + 3-Tier Break Policies — Red Team Round 2/2 APPROVE)**:
   - **P0 Fixes**: Fixed sidebar sticky flex layout (zero bottom clipping), single Sign Out in sidebar only, accurate `hr@dragon.es` (`Elena Chen / 林娜`, `人事主管 / RR. HH.`), `organization` restricted to `owner`, `ManualDayClockSheet` error visibility + open-shift banner.
   - **HR Dashboard**: 4 clickable KPI alert cards (`pending_leaves_count`, `attendance_alerts_count`, `pending_justificantes_count` neutral `0` for `accountant`, scoped `unacknowledged_announcements_count`), 3-group sidebar, Department `<Select>`, announcements empty CTA, compact `Celebraciones`, clean `Who's in`, 2-tier holiday tiles.
   - **HR Leaves (`0010`)**: Custom annual entitlements (`>= 30` natural days Art. 38 ET floor, immutable adjustment audit triggers) + `LeaveEntitlementDialog`, HR/Manager direct leave assignment (`POST /api/v1/leaves/assign`, published shift & balance & closed-month guarded) + `AssignLeaveSheet`.
   - **3-Tier Break Policies (`0009` + `0010`)**: `employee > store > tenant` precedence, `fixed_duration` & `fixed_window` (*jornada partida*), `is_paid`, `manual_punch` (with server-authoritative auto-end & live countdown) vs `auto_deduct`, integrated across ledger, dashboard, payroll, and reports + `BreakPolicyForm` in `StoresPage` & `ProfileAttendanceTab`.
   - **Verified Gates**: Red Team Round 2/2 APPROVE, `quality-gate.ps1 -Scope all` PASS (16 domain files pure, ruff 0, mypy 64 files 0, **80/80 pytest**, `tsc` 0, `eslint` 0, `vite build` PASS), **893/893 i18n keys (0 missing)**, Chrome DevTools `1440×900` `zh-CN`/`es-ES` screenshots (`0` console errors/warnings).

## Live Local Endpoints
- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8001` (`/api/v1/health` 200 OK)
- Local Postgres 16: `localhost:5433` (migrated to `0010_hr_leave_break_policies`)
