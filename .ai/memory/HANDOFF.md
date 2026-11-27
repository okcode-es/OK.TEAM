# okTeam Handoff Snapshot (2026-11-27)

## HR and role isolation follow-up (2026-11-27)

- An accountant with an active or on-leave employee profile may read that profile's own raw leaves and year calendar. An unlinked or frozen account receives an empty raw list and cannot open a personal calendar. Other people's non-vacation absences are redacted in the team calendar and weekly schedule. The existing block on medical file access is preserved.
- Manager directory combines linked employees' operational scope with shared membership stores. The caller can see their own membership. Tenant-wide readers retain their existing directory access.
- Membership mutations serialize on the tenant row and re-read the active caller before authorization. Two sessions using the repository in a disposable PostgreSQL 16 database attempted overlapping changes to two synthetic owners. One committed, one was protected, and one active owner remained.
- Executive summary counts one outstanding announcement if an eligible employee in the report scope still needs to sign. The API regression covers a department with no recipients and a two-recipient notice counted once.
- In Chrome with synthetic data, HR saved an entitlement from 32 to 35 in the employee profile and saw the audit reason after reopening. Department names differing in case or surrounding spaces formed one group and the filtered count showed 2 of 3. At widths 390 and 1440, both languages showed all four KPI titles and the legal text on the login form measured 7.58:1.
- In the same attendance tab, changing the inherited 60-minute break to a 45-minute employee override refreshed net time from 7h00 to 7h15. Restoring inheritance returned it to 7h00.
- All 83 backend tests, Ruff, mypy (64 files), Domain purity (16), TypeScript checks, ESLint and production build passed. The 66-path development OpenAPI and generated TS contract gained `absence_redacted` in the weekly schedule. Independent Red Team passed six relevant API tests and cleared its findings on historical employment status and weekly schedule privacy.
- Non-blocking diagnostics were a Starlette TestClient deprecation, the Vite bundle-size warning and an isolated UI favicon 404. Browser QA used a real FastAPI instance backed by synthetic in-memory repositories. The database concurrency check used a separate PostgreSQL 16 container. No commit, push or deployment was requested.

## Current Git State (2026-11-26)
- Branch `main`; local `origin/main` = `705e048`. Phases 5A–5G are committed.
- Three `AGENTS.md` files are untracked. Phase 01 form changes and this round's memory updates are uncommitted. No commit or push was made in this round.
- `.ai/memory/DECISIONS.md` is unchanged.

## Previous round on 2026-11-26 — verified results
1. **01 Break policy form**: Added recovery for failed policy reads with Chinese/Spanish retry; writes are blocked until a successful read. Isolated Chrome against simulated API verified 403, 404, exception, retry, tenant switch, stale response, save, and restoration inheritance.
2. **02 Dashboard/navigation**: Chinese and Spanish at 1440×900, 1440×500, and 390×844; all 24 KPI navigation checks passed. Three navigation groups and bottom pinning for short windows passed. No console or local HTTP errors. Spanish displays preferred name 林娜 and `RR.HH.`; no Spanish full-name switching. Mobile truncates the Chinese attendance KPI and four Spanish titles.
3. **03 Leave entitlements / assignment — PARTIAL**: Isolated API and PostgreSQL verification: entitlement 30→37; direct assignment of 3 days→34; both identities refreshed; overlapping assignment returned 409. Employee/store/company break calculations yielded 450/420/460 minutes. Browser: employee B used Leave management to change entitlement 30→32, then directly assigned 1 day; the employee profile showed approved, used 1, balance 31. **Profile's Adjust Entitlement entry is blocked**: the required-reason input disappears on entry and cannot be saved; `LeaveEntitlementDialog` defaults `employees=[]`, causing its clearing effect to run on every render. **Attendance profile ledger does not refresh after saving an employee override or restoring inheritance**. The policy is persisted; the ledger reflects it after leaving and re-entering the tab (store 60 min→employee 45 min→restore store yielded 7h00→7h15→7h00 on re-entry). Do not mark 03 fully passed until these are fixed and rechecked.
4. **04 Login browser — visual checks passed; contrast issue open**: Chinese/Spanish desktop (1440×900) and mobile (390×844); all four combinations showed no horizontal overflow and complete copy, forms, and buttons. Desktop SVG wave worked; mobile rendered form-only. Simulated `prefers-reduced-motion` changed wave and fade-in animation to `none`/`0s`. Chrome had no console errors. Estimated contrast for white-page legal fine print was 2.56:1, below 4.5:1; form helper copy was about 4.76:1 and primary button 5.17:1.
5. **Service cleanup**: Isolated 03 services were stopped; demo frontend `5173` and backend `8001` returned 200; PostgreSQL was healthy.

### Current Quality Gates (2026-11-26; reported for this round)
- Backend pytest: 80 passed; one Starlette/httpx deprecation warning.
- Ruff passed; mypy passed on 64 files; Domain purity passed on 16 files.
- `tsc` and ESLint passed.
- Online OpenAPI matches the repository contract: 66 paths.

## Historical Delivery Record (prior handoff, 2026-11-26; not a claim of current re-verification)
### Phases 5A–5D
Workspace Ops, Private Avatars, dual-view Personio-grade light enterprise UI, Employee 360° Profile, GDPR Art. 9 Team Calendar Gantt, Zero-Leak Error Control, UX and i18n polish.

### Phase 5E + Enterprise Login
Five demo accounts (`owner@dragon.es`, `hr@dragon.es`, `manager@dragon.es`, `zhou@dragon.es`, `gestoria@asesoria.es`); self-selectable clock times and `ManualDayClockSheet`; two-stage leave approval (`0008`); management CSV/PDF reports; employee RBAC nav guards; dark sidebar scrollbar; Fluid Split-Screen Login (`LoginFluidBackdrop.tsx`).

### Phases 5F/5G — HR Workbench, Dashboard, Leave Entitlements/Assignment, Break Policies
- **P0 fixes**: Sidebar sticky flex layout with no bottom clipping; single Sign Out in sidebar; accurate `hr@dragon.es` identity (`Elena Chen / 林娜`, `人事主管 / RR. HH.`); `organization` restricted to `owner`; `ManualDayClockSheet` error visibility and open-shift banner.
- **HR dashboard**: Four clickable KPI alert cards (`pending_leaves_count`, `attendance_alerts_count`, `pending_justificantes_count` neutral `0` for accountant, scoped `unacknowledged_announcements_count`); three-group sidebar; Department `<Select>`; announcements empty-state CTA; compact `Celebraciones`; clean `Who's in`; two-tier holiday tiles.
- **HR leaves (`0010`)**: Custom annual entitlements (>=30 natural days, Art. 38 ET floor, immutable adjustment audit triggers) with `LeaveEntitlementDialog`; HR/scoped-manager direct assignment (`POST /api/v1/leaves/assign`, published-shift, balance, and closed-month guarded) with `AssignLeaveSheet`.
- **Three-tier break policies (`0009`, `0010`)**: `employee > store > tenant > system_default`; `fixed_duration` and `fixed_window` (*jornada partida*); `is_paid`; `manual_punch` with server-authoritative auto-end and live countdown versus `auto_deduct`; integrated across ledger, dashboard, payroll closing, and reports. `BreakPolicyForm` in `StoresPage` and `ProfileAttendanceTab`.

### Historical Gates and Local Services (prior handoff, 2026-11-26)
- Red Team Round 2/2: **APPROVE**, 0 P0/P1/P2 remaining.
- `quality-gate.ps1 -Scope all`: Domain purity 16 files/0 violations; Ruff 0; mypy 64 files/0; pytest **80/80**; `tsc --noEmit` 0; ESLint 0; `vite build` passed; **893/893 i18n keys, 0 missing**.
- Historical Chrome DevTools audit: `1440×900`, `zh-CN` and `es-ES`, Dashboard, `AssignLeaveSheet`, Employee 360° Custom Break Policy; 0 console errors/warnings, `overflowX=false`, `asideBottomClipped=false`.
- Historical local Docker endpoints: frontend `http://localhost:5173`; backend `http://localhost:8001` (`/api/v1/health` 200); PostgreSQL 16 `localhost:5433`, migrated to `0010_hr_leave_break_policies`.

## Next
- Committed and pushed 2026-11-27 follow-up fixes (role/concurrency safeguards, report scope fix, HR UI/break-policy fixes, calm 3-column Dashboard KPI strip removing unacknowledged announcements KPI, and `.ai/` rules/skills hardening) to `origin/main`. Ready for the next iteration phase.
