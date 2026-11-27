# okTeam Session State (Hard Limit <= 50 Lines)

- **Current Phase**: Phase 5G Complete — HR profile, role isolation, reporting, `.ai/` hardening & calm 3-column Dashboard KPI strip.
- **Last Updated**: 2026-11-27
- **Active Branch**: `main` (ready to commit & push over `705e048`).
- **Working Tree**: Role isolation & concurrency safeguards, reports, HR UI fixes, break policy recovery, calm 3-column Dashboard KPI strip (removed unacknowledged announcements KPI), `.ai/` rules/skills updates, and regression tests.

## Verification on 2026-11-27
- Accountant raw leave details and personal calendar require the caller's current active/on-leave employee record. Team calendar and weekly schedule redact colleagues' non-vacation reasons, and private medical downloads remain role-guarded.
- Manager directory checks employee store, department and status and intersects peer membership store IDs. PostgreSQL 16 independent transactions verified that concurrent owner changes leave one active owner.
- Management reports count outstanding notices with recipients in scope. Two pending recipients of one notice count once.
- Chrome verified required adjustment reason & 32→35 persistence on reopen, 2-of-3 normalized department filter, login legal text contrast 7.58:1, attendance tab instant refresh (7h00→7h15→7h00), and the redesigned pure-white 3-column Admin Dashboard KPI strip (`1440×900` `zh-CN` & `390×844` `es-ES`, `overflowX=false`, unacknowledged announcements KPI removed).
- Learned patterns persisted to `.ai/rules/DATA_AND_SECURITY.md`, `.ai/skills/okteam-ui-craft/SKILL.md`, `.ai/agents/red-team-gatekeeper.md`, and `.ai/agents/frontend-hydrogen.md`; untracked `AGENTS.md` files removed.
- Gates passed: 83 pytest, Ruff, mypy (64 files), Domain purity (16), frontend typecheck, ESLint, Vite build, and 895/895 i18n keys. OpenAPI (66 paths) and TS contract regenerated; Red Team approved.
- Local Docker services rebuilt and healthy (`localhost:5173`, `localhost:8001/api/v1/health`, Postgres `localhost:5433`).

## Earlier handoff on 2026-11-26 (historical)
- **01 Break policy form**: Failed policy reads retry in Chinese/Spanish; writes blocked until a successful read. Isolated simulated API verified 403, 404, exception, retry, tenant switch, stale response, save, and restoration inheritance.
- **02 Dashboard/navigation**: Chinese/Spanish at 1440×900, 1440×500, 390×844; 24 KPI navigation checks passed. Three navigation groups and short-window bottom pinning work. No console or local HTTP errors. Spanish shows preferred name 林娜 with `RR.HH.`; no Spanish full-name switching. Mobile truncates Chinese attendance KPI and four Spanish titles.
- **03 Entitlements/leave assignment — PARTIAL**: Isolated API/DB: balance 30→37; direct assignment 3 days→34; both identities refreshed; overlap rejected 409. Break calculations employee/store/company = 450/420/460 min. Browser: employee B changed entitlement 30→32 from Leave management; direct assignment 1 day showed approved, used 1, balance 31. Profile's Adjust Entitlement is blocked because required-reason input disappears; `LeaveEntitlementDialog` defaults `employees=[]`, retriggering a clearing effect on each render. Attendance profile ledger stays stale after saving override/restoring inheritance; policy persists, but ledger updates after tab re-entry (store 60 min→employee 45 min→store: 7h00→7h15→7h00).
- **04 Login browser — visual checks passed; contrast issue open**: Chinese/Spanish desktop 1440×900 and mobile 390×844; all four combinations have no horizontal overflow and complete copy/forms/buttons. Desktop SVG wave works; mobile uses form-only view. With `prefers-reduced-motion`, wave and fade-in animations resolve to `none`/`0s`. No Chrome console errors. White-page legal fine print estimated contrast 2.56:1 (below 4.5:1); form helper copy 4.76:1 and primary button 5.17:1.
- **Service cleanup**: Isolated 03 services were stopped; demo frontend 5173 and backend 8001 returned 200; PostgreSQL healthy.

## Earlier quality gates (2026-11-26)
- Backend pytest: 80 passed; one Starlette/httpx deprecation warning.
- Ruff passed; mypy passed on 64 files; Domain purity passed on 16 files.
- `tsc` and ESLint passed; live OpenAPI matches repository contract at 66 paths.

## Historical Baseline (2026-11-26; not re-run this round)
- Phase 5G red-team round 2/2 approved; earlier all-scope gate: 80 pytest, Domain 16, Ruff, mypy 64, tsc/ESLint/build passing; **893/893 i18n keys**.
- Earlier Chrome audit covered 1440×900 only. See `.ai/memory/HANDOFF.md` for historical delivery details and local service ports.
