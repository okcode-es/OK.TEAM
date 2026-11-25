# Phase 3 Design Spec: Append-Only Daily Time Tracking (Registro Diario de Jornada) & Kiosk Mode

- **Status**: Approved
- **Date**: 2026-11-25
- **Phase**: Phase 3 (不可变考勤打卡 + 西班牙法定工时/加班红线 + 公用平板 3 秒自动清屏)

## 1. Scope & Non-Goals

### In Scope
1. **Immutable Append-Only Attendance Ledger (`attendance_events`)**:
   - Strictly `INSERT`-only in `public` schema (`0003_phase3_attendance`).
   - Database-level PostgreSQL `BEFORE UPDATE OR DELETE` trigger (`prevent_attendance_event_mutation`) physically rejecting any mutation or deletion of recorded events.
   - Automatic `retention_until = occurred_at_utc + 4 years` (RDL 8/2019 / Art. 34.9 ET).
   - Corrections/retroactive entries appended as new `correction` events referencing `supersedes_event_id` with mandatory `correction_reason` and `corrected_event_type` (`clock_in`, `break_start`, `break_end`, `clock_out`, or `void`), preserving complete audit chain.
2. **Pure Domain Spanish Labor Law Engine (`app/domain/attendance.py`, stdlib only)**:
   - Ledger folding of raw + correction events into chronological work sessions (`clock_in` -> optional `break_start`/`break_end` intervals -> `clock_out`).
   - All durations computed via exact UTC differences (immune to Europe/Madrid & Atlantic/Canary DST 23h/25h transitions); natural calendar day & ISO week boundaries derived via stdlib `zoneinfo.ZoneInfo(store_timezone)`.
   - Six statutory compliance checks outputting structured `ComplianceAlert` items:
     1. **Daily Max (Art. 34.3 ET)**: Adult `> 9.00h/day`, Minor (`< 18` years old on work date via `employee.birth_date`) `> 8.00h/day`.
     2. **Inter-Shift Rest (Art. 34.3 ET)**: `< 12.00h` UTC gap between previous session `clock_out` and next session `clock_in`.
     3. **Continuous Shift Break (Art. 34.4 ET)**: Continuous work `> 6.00h` (Minor `> 4.50h`) without `>= 15 min` (Minor `>= 30 min`) recorded break.
     4. **Weekly Regular Limit (Art. 34.1 ET)**: Weekly worked hours `> 40.00h` (or `> contracted_weekly_hours`) counted as overtime.
     5. **Part-Time Overtime Prohibition (Art. 12.4.c ET)**: `work_schedule_type == part_time` exceeding contracted weekly hours triggers high-severity `PART_TIME_OVERTIME_PROHIBITED`.
     6. **Annual Overtime Cap (Art. 35.2 ET)**: Calendar-year accumulated overtime `>= 60.00h` (warning) and `> 80.00h` (violation `ANNUAL_OVERTIME_EXCEEDED`).
3. **Backend REST API & ITSS Spanish CSV Export**:
   - `POST /api/v1/attendance/clock`: Self clock (`employee` self only) or store `kiosk` clock (authorized `owner`/`hr`/`store_manager` device clocking an active employee of that store by `employee_code`). Blocks `legal_hold`/`terminated`/`anonymized` employees and invalid state transitions (e.g., double `clock_in`, `clock_out` when not clocked in).
   - `POST /api/v1/attendance/corrections`: Append-only correction or retroactive punch (`owner`, `hr`, or `store_manager` within authorized store only; mandatory non-empty `reason`; `accountant` & `employee` get `403`).
   - `GET /api/v1/attendance/ledger`: Filter by `store_id`, `employee_id`, `date_from`, `date_to` returning folded daily sessions, raw immutable event audit trail, hour/overtime totals, and statutory compliance alerts (strictly scoped by tenant, store, and role; `store_manager` blocked from `legal_hold`/unassigned stores; `employee` self only).
   - `GET /api/v1/attendance/export.csv`: Spanish (`es-ES`) ITSS-ready CSV export (company legal name, CIF, store code/name/IANA timezone, employee code/name, local date, clock-in/out local & UTC timestamps, break minutes, net hours, correction flag, compliance alerts) with UTF-8 BOM for Excel compatibility.
4. **Frontend Consoles (Implemented exclusively by Frontend Hydrogen Agent)**:
   - `AttendancePage` (Template 1: edge-to-edge TanStack Table + compact KPI strip + ITSS Spanish CSV export button + right `Sheet` drawer showing immutable event chain & append-only correction form with IME `!isComposing` guard).
   - `KioskPage`: Store public tablet punch terminal with **strict 3-second auto-clear countdown** wiping employee name, code, and punch receipt state after each punch.

### Explicit Non-Goals (Deferred to Phase 4+)
- Shift scheduling calendar (Template 3) and leave request approval workflow — Phase 4.
- Private document 60s signed URLs and SHA-256 monthly timesheet signature receipts — Phase 4.

---

## 2. Database Schema (`0003_phase3_attendance`, `public` schema only)

Table `attendance_events`:
- `id UUID PK`
- `tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT`
- `store_id UUID NOT NULL REFERENCES stores(id) ON DELETE RESTRICT`
- `employee_id UUID NOT NULL REFERENCES employees(id) ON DELETE RESTRICT`
- `event_type VARCHAR(20) NOT NULL` (`CHECK IN ('clock_in', 'break_start', 'break_end', 'clock_out', 'correction')`)
- `occurred_at_utc TIMESTAMPTZ NOT NULL`
- `store_timezone VARCHAR(64) NOT NULL`
- `source VARCHAR(32) NOT NULL` (`CHECK IN ('web', 'kiosk', 'manager_correction')`)
- `recorded_by_user_id UUID NOT NULL`
- `recorded_at_utc TIMESTAMPTZ NOT NULL`
- `supersedes_event_id UUID NULL REFERENCES attendance_events(id) ON DELETE RESTRICT`
- `corrected_event_type VARCHAR(20) NULL` (`CHECK IS NULL OR IN ('clock_in', 'break_start', 'break_end', 'clock_out', 'void')`)
- `correction_reason VARCHAR(300) NOT NULL DEFAULT ''`
- `retention_until TIMESTAMPTZ NOT NULL`
- Indexes:
  - `ix_attendance_events_tenant_emp_time` on `(tenant_id, employee_id, occurred_at_utc)`
  - `ix_attendance_events_tenant_store_time` on `(tenant_id, store_id, occurred_at_utc)`
  - `ix_attendance_events_supersedes` on `(supersedes_event_id)`
- PostgreSQL Immutability Trigger:
  - Function `prevent_attendance_event_mutation()` raising exception `'attendance_events is append-only (RDL 8/2019): UPDATE and DELETE are forbidden'`.
  - Trigger `trg_attendance_events_immutable BEFORE UPDATE OR DELETE ON attendance_events FOR EACH ROW EXECUTE FUNCTION prevent_attendance_event_mutation()`.
  - Clean `downgrade()` drops trigger, drops function, drops indexes, drops table.

---

## 3. Role Permission Matrix (Phase 3)

| Action | `owner` | `hr` | `store_manager` | `accountant` | `employee` |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Self clock (`POST /attendance/clock`, `source=web`) | Own emp record | Own emp record | Own emp record | No (`403`) | Own emp record |
| Kiosk clock (`POST /attendance/clock`, `source=kiosk`) | Any store in tenant | Any store in tenant | Authorized `store_ids` only | No (`403`) | No (`403`) |
| Append correction (`POST /attendance/corrections`) | Any store in tenant | Any store in tenant | Authorized `store_ids` only | No (`403`) | No (`403`) |
| View ledger & alerts (`GET /attendance/ledger`) | All tenant | All tenant | Authorized `store_ids` (active/on_leave only) | All tenant (RO) | Self only |
| Export ITSS Spanish CSV (`GET /attendance/export.csv`) | All tenant | All tenant | Authorized `store_ids` only | All tenant (RO) | Self only |
