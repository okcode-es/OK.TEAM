# okTeam HR

Enterprise HR, Spanish labor compliance (RDL 8/2019, Estatuto de los Trabajadores), shift scheduling, Art. 38 ET leave management, and private SHA-256 compliance document ledger for Chinese-owned businesses in Spain (`zh-CN` / `es-ES`).

## Architecture

- **Backend**: FastAPI + Clean Architecture (`domain` pure Python stdlib, `application`, `infrastructure` async SQLAlchemy + PostgreSQL 16, `interfaces/api`).
- **Frontend**: React + TypeScript + Vite + Tailwind CSS + shadcn/ui + TanStack Table.
- **Contracts**: OpenAPI 3.1 exported to `contracts/openapi.json` and generated to `frontend/src/api/generated/schema.d.ts`.

## Local Development

### 1. Environment

```bash
cp .env.example .env
```

Set `DATABASE_URL`, `PII_ENCRYPTION_KEY`, `STORAGE_SIGNING_SECRET`, and Supabase Auth variables (or `DEV_AUTH_ENABLED=true` + `VITE_DEV_AUTH_ENABLED=true` + `SUPABASE_JWT_SECRET` for local dev without Supabase).

### 2. Backend

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8001
```

### 3. Frontend

```bash
cd frontend
npm ci
npm run dev
```

### 4. Quality Gates

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\quality-gate.ps1 -Scope backend
powershell -ExecutionPolicy Bypass -File .\scripts\quality-gate.ps1 -Scope frontend
```

## Docker Compose (Local Full-Stack with PostgreSQL 16)

```bash
docker compose --profile local-db up -d --build
```

- PostgreSQL 16: `localhost:${LOCAL_DB_PORT:-5433}`
- Backend API: `http://localhost:${BACKEND_PORT:-8001}` (local private documents persisted in `okteam_docs_data` volume)
- Frontend UI: `http://localhost:${FRONTEND_PORT:-5173}`

Run migrations inside the backend container if starting from a fresh DB:

```bash
docker compose exec backend alembic upgrade head
```

## EU Supabase Production Setup

1. Provision an **EU-region** Supabase project (PostgreSQL 16 + Auth + Private Storage Bucket `compliance-documents`).
2. Set production environment variables:
   - `APP_ENV=production`
   - `DEV_AUTH_ENABLED=false` and `VITE_DEV_AUTH_ENABLED=false`
   - `DATABASE_URL=postgresql+asyncpg://...`
   - `SUPABASE_URL`, `SUPABASE_JWT_AUDIENCE=authenticated`, `SUPABASE_SERVICE_ROLE_KEY` (backend-only)
   - `STORAGE_BACKEND=supabase`, `SUPABASE_STORAGE_BUCKET=compliance-documents`
   - `PII_ENCRYPTION_KEY` (32-byte base64url AES-256-GCM key)
   - `STORAGE_SIGNING_SECRET` (random 32+ byte secret)
   - `PUBLIC_API_BASE_URL` and `CORS_ORIGINS` set to your HTTPS domain(s)
3. Run `alembic upgrade head` against the production database, then deploy `backend` and `frontend`.
