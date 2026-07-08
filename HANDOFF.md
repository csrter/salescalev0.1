# Salescale — Session Handoff

Orientation for a fresh Claude Code session (or a new engineer). Read this
first, then `RELEASE_CHECKLIST.md` for the granular status.

---

## ⚠️ Read this first

- **Everything is uncommitted.** The repo is on `main` at the original clone
  commit (`f5c837c`); ~80 files of this session's work live only in the working
  tree. Nothing has been committed or pushed. Commit early if you value it.
- **This machine has no toolchain.** No Node, npm, Python 3.10+, Homebrew, or
  PyInstaller are installed. Each session downloads standalone Node (20.x) and
  Python (3.11) into its scratchpad and puts them on `PATH`. A new session must
  re-provision these (scratchpad dirs are per-session and ephemeral). Requires
  Node ≥ 20.19 / 22 and Python ≥ 3.10.
- **Do NOT truncate the Supabase database.** Earlier test-cleanups wiped real
  data. For verification use a throwaway local SQLite DB
  (`DATABASE_URL=sqlite:////tmp/x.db`), never the live Supabase URL.

## What Salescale is

A multi-tenant SaaS for marketing agencies to manage their clients' paid ads
(Meta + Google, more planned) across platforms, plus a built-in CRM — from one
login. Each agency is an **Organization** (tenant); hard tenant isolation is
the #1 rule. See `CLAUDE.md` for the full product vision and phase plan (its
"Current Status" section is stale — trust `RELEASE_CHECKLIST.md` instead).

## Architecture

- **Backend** (`backend/`): FastAPI + SQLAlchemy. Custom JWT auth (NOT Supabase
  Auth). Database is **Supabase Postgres** via psycopg3 (session pooler);
  falls back to local SQLite with no config. Schema is owned by **Alembic** and
  auto-migrates on startup (`app/migrations.py`). ~60 endpoints.
- **Frontend** (`frontend/`): React + Vite + TypeScript (`frontend/src/`). This
  is the real product UI. (An old static-HTML mock at `frontend/index.html` was
  replaced with the proper Vite entry.) Design system in `App.css` — 2026-SaaS:
  dark sidebar + topbar, light/dark aware.
- **Desktop** (`electron-app/`): Electron shell that spawns the packaged backend
  binary and loads the built frontend. Reads `~/Library/Application Support/
  salescale-app/config.json` for `databaseUrl`, `superadminEmails`, and secret
  passthrough. Single-user/offline option.
- **Hosted web** (recommended for production): `backend/Dockerfile` +
  `frontend/Dockerfile` (multi-stage nginx) + `docker-compose.yml`. Full guide
  in `DEPLOYMENT.md`. The desktop-with-DB-creds model is NOT safe to distribute
  publicly — hosted web is the multi-tenant path.

## Build & run

- **Tests:** `cd backend && pip install -r requirements-dev.txt && pytest`
  → **157 passing** (18 files, incl. tenant-isolation). CI:
  `.github/workflows/ci.yml` (needs a GitHub remote to run).
- **Full desktop build:** `./build-macos.sh` (backend PyInstaller binary from
  `run.py` with Alembic bundled → frontend `vite build` → `electron-builder
  --mac --arm64 --config build/electron-builder.yml`). Output:
  `electron-app/dist/Salescale-0.1.0-arm64.dmg` (**unsigned** — right-click →
  Open on first launch).
- **Frontend build gotcha:** Vite 8 uses rolldown; npm sometimes skips the
  native binding. If `vite build` fails, `npm install --no-save
  @rolldown/binding-darwin-arm64@<version>` then rebuild.
- **Run backend locally:** `uvicorn app.main:app` from `backend/` (reads
  `backend/.env`). Health: `GET /api/health`.

## Config & secrets

`backend/.env` (gitignored) currently holds: `DATABASE_URL` (Supabase session
pooler, project ref `jtzowohhtrrfzxbchujj`), `JWT_SECRET`,
`TOKEN_ENCRYPTION_KEY` (Fernet), `SUPERADMIN_EMAILS=carterbruns@gmail.com`,
`RESEND_API_KEY`, `EMAIL_DEFAULT_FROM_ADDRESS=onboarding@resend.dev`. See
`.env.example` for the full list. **Supabase is currently empty** — sign up
fresh in the app.

## Built this session (all working, tested)

- Fixed the broken build (deps, entrypoint, config) → arm64 DMG builds & runs.
- Wired the real React frontend to the backend; app is functional end-to-end.
- Moved the DB to **Supabase Postgres**; adopted **Alembic** (baseline +
  migrations, auto-migrate on startup).
- **Admin dashboards:** platform super-admin (cross-tenant, via
  `SUPERADMIN_EMAILS` allowlist — the one sanctioned scope bypass) with stats /
  org table / suspend / plan / password-reset / signup chart; plus an org admin
  console (team management).
- **2026-SaaS UI redesign** + add-client flow.
- **Release hardening:** prod secrets, tenant-isolation tests + CI, hosted
  Docker/CORS, rate limiting, limit/offset pagination, guarded Sentry, `pip-
  audit`/`npm audit` (clean).
- **Billing (Stripe):** server-side tier enforcement + checkout/portal/webhook
  (fail-closed 503 without keys) + Billing UI.
- **Auth:** email verification + self-serve password reset (branded HTML email
  via **Resend — delivery verified working**), social login (Google/Meta),
  and a fix for the 401→page-reload login bug.
- **Bring-your-own platform credentials:** each org enters its own Meta/Google
  app creds (encrypted) in an **Integrations** page; connect flows use them,
  falling back to the operator's global app.

## Key decisions & gotchas

- **Ad-platform connect = OAuth with the user's account.** A registered app
  (client ID) always mediates: either the operator's ONE shared app (users
  click-and-authorize, needs Meta App Review + Google standard-access developer
  token) OR each agency's own app (BYO, built). Both supported; global is the
  fallback default. **Google Ads always needs a developer token** — not pure
  OAuth.
- **Migrations:** no Alembic before this session; now there is. Any model
  change needs `alembic revision --autogenerate -m "..."`; it applies on next
  startup. NOT-NULL columns need a `server_default` to apply to non-empty DBs.
- **Desktop OAuth** (social login, ad connect) redirects to `APP_BASE_URL`
  (the web app), so those flows are primarily for the hosted web build.
- `schema.sql` was a stale artifact (uuid ids, Supabase-Auth FK) that caused a
  type conflict — deleted. The ORM/Alembic owns the schema.

## What's left before beta / production

Detailed in `RELEASE_CHECKLIST.md`. The short version — mostly **your accounts**,
not code:

- **Deploy** the backend + frontend to a host + domain (nothing is deployed).
- **Meta app + Google app** (OAuth client + Ads developer token) + approvals —
  long lead time, start early. See `PLATFORM_APPROVALS.md`.
- **Stripe** keys/products to turn billing on.
- **Resend**: verify a domain to email addresses other than your own.
- **GDPR/CCPA data export + deletion** (code, not built) + legal copy (ToS/
  Privacy).
- Code-signing/notarization (only if shipping the desktop app).
- Backups verification + uptime monitoring.

## Doc map

- `RELEASE_CHECKLIST.md` — granular, current status of every release item.
- `DEPLOYMENT.md` — hosted deploy (backend + frontend + env vars).
- `PLATFORM_APPROVALS.md` — Meta/Google app + approval steps, scopes, redirect
  URIs.
- `.env.example` — every config var with notes.
- `CLAUDE.md` — product vision + phases (status section stale).
