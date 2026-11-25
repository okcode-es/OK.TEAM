# Phase 2 Design Spec: Employee HR Profiles, Spanish Contracts & Encrypted PII

- **Status**: Approved
- **Date**: 2026-11-25
- **Phase**: Phase 2 (员工核心档案 + 西班牙合同基础 + NIE/IBAN/NAF 加密 + 4 年留存冻结与匿名化)

## 1. Scope & Non-Goals

### In Scope
1. **Employee Core HR Records (`employees`)**: Tenant-scoped employee directory bound to a verified `primary_store_id`, optional link to `user_id`,工号 (`employee_code`), legal full name + Chinese preferred name, contact info, and `birth_date` (supporting Phase 3 minor `<= 8h/day` protection).
2. **Spanish Statutory Contract Fields**:
   - `contract_type`: `indefinido`, `temporal`, `fijo_discontinuo`, `practicas`.
   - `work_schedule_type`: `full_time`, `part_time` (Art. 12.4.c ET — prohibits regular overtime).
   - `contracted_weekly_hours`: `0 < h <= 40.00` (`< 40.00` strictly enforced for `part_time`).
   - `annual_vacation_days`: statutory floor `>= 30` natural days (Art. 38 ET).
3. **Application-Layer PII Encryption (`AES-256-GCM`)**:
   - Fields: `NIE/DNI`, `IBAN`, `NAF` (*Número de Afiliación a la Seguridad Social*).
   - Per-encryption random 96-bit nonce + AAD bound to `"{tenant_id}:{employee_id}:{field_name}"`.
   - Tenant-scoped `HMAC-SHA256` blind index (`nie_dni_blind_index`) using a domain-separated derived key for duplicate NIE/DNI detection without storing or querying plaintext.
   - Masked strings (`nie_dni_masked`, `iban_masked`, `naf_masked`) stored for safe list rendering.
4. **Role-Based Decryption & Data Minimization**:
   - `GET /api/v1/employees` and `GET /api/v1/employees/{id}` NEVER return plaintext PII.
   - `GET /api/v1/employees/{id}/sensitive` returns decrypted PII with `Cache-Control: no-store` strictly to `owner`, `hr`, `accountant`, and `employee` (self only). **`store_manager` is strictly forbidden (`403`)**.
5. **GDPR × Spanish Labor Law 4-Year Legal Hold & Anonymization**:
   - Zero physical `DELETE`.
   - `POST /api/v1/employees/{id}/terminate` transitions to `legal_hold`, sets `termination_date` and `retention_until = max(termination_date_eod_utc, now_utc) + 4 years`.
   - `POST /api/v1/employees/{id}/anonymize` irreversibly wipes PII only when `now_utc >= retention_until`; blocks with `422` if statutory 4-year retention has not elapsed.
6. **Frontend Consoles (`shadcn/ui` + `okteam-ui-craft`)**:
   - `EmployeesPage` (Template 1: TanStack Table + right `Sheet` create drawer + store/status filters + IME guard).
   - `EmployeeProfilePage` (Template 2: two-column profile cards + on-demand 15s auto-masking sensitive PII viewer + terminate/anonymize workflows).

### Explicit Non-Goals (Deferred to Phase 3+)
- Append-only attendance clock-in (*Registro de Jornada*) and kiosk 3s auto-clear — Phase 3.
- Shift scheduling calendar and 60s signed private files / SHA-256 receipt ledger — Phase 4.

---

## 2. Database Schema (`0002_phase2_employees`, `public` schema only)

Table `employees`:
- `id UUID PK`, `tenant_id UUID FK(tenants.id) NOT NULL`, `primary_store_id UUID FK(stores.id) NOT NULL`, `user_id UUID NULL` (no cross-schema FK to `auth`).
- `employee_code VARCHAR(32) NOT NULL`, `full_name VARCHAR(160) NOT NULL`, `preferred_name VARCHAR(120) NOT NULL DEFAULT ''`, `email VARCHAR(255) NOT NULL DEFAULT ''`, `phone VARCHAR(40) NOT NULL DEFAULT ''`, `birth_date DATE NULL`.
- `contract_type VARCHAR(32) NOT NULL`, `work_schedule_type VARCHAR(20) NOT NULL`, `contracted_weekly_hours NUMERIC(4,2) NOT NULL`, `annual_vacation_days INTEGER NOT NULL DEFAULT 30`, `hire_date DATE NOT NULL`, `termination_date DATE NULL`.
- `nie_dni_enc TEXT NULL`, `nie_dni_masked VARCHAR(32) NULL`, `nie_dni_blind_index VARCHAR(64) NULL`.
- `iban_enc TEXT NULL`, `iban_masked VARCHAR(40) NULL`.
- `naf_enc TEXT NULL`, `naf_masked VARCHAR(32) NULL`.
- `employment_status VARCHAR(20) NOT NULL DEFAULT 'active'` (`active`, `on_leave`, `terminated`, `legal_hold`, `anonymized`).
- `retention_until TIMESTAMPTZ NULL`, `anonymized_at TIMESTAMPTZ NULL`, `created_at TIMESTAMPTZ NOT NULL`, `updated_at TIMESTAMPTZ NOT NULL`, `deleted_at TIMESTAMPTZ NULL`.
- Partial unique indexes:
  - `uq_employees_tenant_code_active` on `(tenant_id, upper(employee_code)) WHERE deleted_at IS NULL`
  - `uq_employees_tenant_nie_blind_active` on `(tenant_id, nie_dni_blind_index) WHERE nie_dni_blind_index IS NOT NULL AND deleted_at IS NULL`

---

## 3. Role Permission Matrix (Phase 2)

| Action | `owner` | `hr` | `store_manager` | `accountant` | `employee` |
| :--- | :---: | :---: | :---: | :---: | :---: |
| List / View employees (masked) | All tenant | All tenant | Assigned `store_ids` only | All tenant (RO) | Self only (`user_id == self`) |
| Create / Update employee | Yes | Yes | No (`403`) | No (`403`) | No (`403`) |
| Decrypt `/sensitive` (`NIE/IBAN/NAF`) | Yes | Yes | **No (`403`)** | Yes | Self only |
| Terminate (4y Legal Hold) / Anonymize | Yes | Yes | No (`403`) | No (`403`) | No (`403`) |
