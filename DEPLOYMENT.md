# Deploying Salescale (hosted web architecture)

This is the **recommended production architecture** and resolves the P0 blocker
in `RELEASE_CHECKLIST.md`: instead of every desktop app connecting directly to
the database, a single **hosted backend** holds the database credentials, and
clients (the web app) talk to it over HTTPS.

```
Browser ──HTTPS──▶  Web frontend (static)  ──HTTPS──▶  Backend API  ──▶  Supabase Postgres
                    (Vercel/Netlify/nginx)            (FastAPI)          (managed)
```

Only the backend ever holds `DATABASE_URL` and secrets. Nothing sensitive ships
to the browser.

---

## 1. Backend (FastAPI service)

Deploy `backend/` as a container (image built from `backend/Dockerfile`) on any
host — Render, Railway, Fly.io, a VPS, ECS, etc.

**Required environment variables** (set them in the host's dashboard/secrets —
never commit them):

| Var | Value |
|-----|-------|
| `DATABASE_URL` | Supabase **Session pooler** string (`postgresql://postgres.<ref>:<pw>@aws-0-<region>.pooler.supabase.com:5432/postgres`) |
| `JWT_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `TOKEN_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `FRONTEND_ORIGIN` | the web app's URL, e.g. `https://app.salescale.com` (comma-separate for multiple/preview domains) |
| `SUPERADMIN_EMAILS` | your platform-admin email(s), comma-separated |

Optional (enable the corresponding integration): `META_APP_ID`,
`META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_DEVELOPER_TOKEN`, `GOOGLE_LOGIN_CUSTOMER_ID`,
`ANTHROPIC_API_KEY`, `SMTP_*`. See `.env.example`.

Notes:
- **Do not set `DESKTOP_MODE`** — that's for the Electron shell and opens CORS
  to `*`. Leaving it unset enforces the `FRONTEND_ORIGIN` allowlist.
- **Migrations run automatically on startup** (`alembic upgrade head`), so a new
  database is provisioned on first boot and future schema changes apply on
  deploy. No manual migration step.
- **Health check path:** `GET /api/health` → `{"ok": true}`.
- The container listens on `$PORT` (default 8000).

## 2. Web frontend (static site)

Build `frontend/` and deploy the `dist/` output to any static host (Vercel,
Netlify, Cloudflare Pages, S3+CloudFront, nginx…). Bake in the backend URL:

```bash
cd frontend
VITE_API_URL="https://api.salescale.com" npm run build   # outputs dist/
```

Or build the container from `frontend/Dockerfile`:

```bash
docker build --build-arg VITE_API_URL="https://api.salescale.com" -t salescale-web ./frontend
```

## 3. Wire the two together

- Backend `FRONTEND_ORIGIN` must equal the frontend's URL (CORS).
- Frontend `VITE_API_URL` must equal the backend's URL.
- Put both behind HTTPS (managed hosts do this automatically).

## 4. Run the whole stack locally (production images)

```bash
docker compose up --build
# web  → http://localhost:8080
# api  → http://localhost:8000
```

(`docker-compose.yml` reads secrets from `backend/.env`.)

## Desktop app (optional)

The Electron app in `electron-app/` runs its **own local backend** and is a
single-user/offline option, not the multi-tenant deployment. For a hosted SaaS,
the **web frontend is the primary client**. If you want the desktop app to use
the hosted API instead of a local backend, build its frontend with
`VITE_API_URL=<backend url>` and remove the `startBackend()` call in
`electron-app/main.js`.

## Security reminder

`DATABASE_URL` and all secrets live **only** on the backend host. Never bundle
them into the web build or a distributed desktop `config.json` — anyone with
the artifact could then reach the whole multi-tenant database directly.
