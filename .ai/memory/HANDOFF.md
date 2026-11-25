# Handoff Snapshot — End of Phase 4

- **Date**: 2026-11-25
- **Completed**: Phase 1 (Org/Stores/RBAC) + Phase 2 (Employees/AES-256-GCM PII/Legal Hold) + Phase 3 (Append-Only Attendance/Spanish Labor Engine/3s Kiosk) + Phase 4 (Shift Scheduling Calendar Template 3, Art. 38 ET Leave Management, Private Documents <=60s Signed URLs & Immutable SHA-256 Receipt Ledger, Full-Stack Docker Delivery).
- **Live Local Stack (Running in Docker)**:
  - Frontend: `http://localhost:5173` (`okteam-frontend`, Nginx serving production Vite build with Dev Auth enabled via local gitignored `.env`)
  - Backend: `http://localhost:8001` (`okteam-backend`, FastAPI + Swagger `/api/v1/docs`, local storage mounted to `okteam_docs_data`)
  - Database: `localhost:5433` (`okteam-local-db`, PostgreSQL 16 with migrations `0001`–`0004` applied)
- **Red Team Gatekeeper**: Phase 4 Round 2/2 **PASS** (`P0: None`, `P1: None`).
- **Non-blocking P2 Backlog for Future Polish**:
  1. Normalize 100% non-ASCII filename fallback in `api/documents.py` from `".pdf"` to `"document.pdf"` (RFC 6266 `filename*=UTF-8''...` already serves full Chinese/Spanish names cleanly).
  2. Optionally allow `store_manager` to view/sign their *own* personal `payslip`/`contract` when `doc.employee_id == self_emp.id` while keeping other employees' restricted categories blocked.
  3. Phase 1–3 carry-over P2s: Spanish DNI/NIE Mod-23 & IBAN Mod-97 checksums, `settings_override` propagation in `auth.py`/`deps.py`, partial unique index `(tenant_id, user_id)` on `employees`.
- **Next Suggested Milestone (Phase 5)**: Payroll & Gestoría Export Pack (monthly closed attendance + overtime + approved leaves summary for *Asesoría* in `es-ES` CSV/Excel + automated monthly employee attendance report generation & batch signature workflow) and/or Production EU Supabase & CI/CD pipeline deployment.
