# Salescale — Session Handoff

Orientation for a fresh Claude Code session (or a new engineer). Read this
first, then `RELEASE_CHECKLIST.md` for the granular release status and
`CLAUDE.md` for the product vision/phase plan.

_Last updated: 2026-07-09 (Phase 2 UI modernization session)._

---

## ⚠️ Read this first

- **Work is committed to a branch, NOT pushed, and `main` is untouched.** All
  of this session's work (25 commits) lives on branch
  **`session/foundation-billing-auth`**. `main` is still at the original clone
  commit `f5c837c`; **nothing has been pushed to any remote.** To promote:
  fast-forward `main` to the branch (or open a PR) when you're happy — ask
  before doing it.
- **This machine has no toolchain.** No system Node, Python ≥3.10, Homebrew, or
  PyInstaller. Each session downloads a standalone **Python 3.11** and **Node
  20** into its (ephemeral, per-session) scratchpad and puts them on `PATH`. A
  new session must re-provision. See "Toolchain" below.
- **Run backend tests with `TZ=UTC`** or ~9 metrics/CRM tests flake (local
  timezone vs. UTC seed data — not a code bug). Full suite: **184 passing**.
- **Do NOT point anything at the live Supabase DB for testing.** Use a throwaway
  SQLite DB (`DATABASE_URL=sqlite:////tmp/x.db`). Earlier cleanups wiped real
  data; the standing rule is throwaway SQLite for all verification.

## What Salescale is

A multi-tenant SaaS for marketing agencies to manage clients' paid ads (Meta +
Google live; Snapchat/Reddit/LinkedIn/Microsoft/TikTok/Pinterest/Nextdoor
scaffolded) plus a native CRM — from one login. Each agency is an
**Organization** (tenant); hard tenant isolation is the #1 rule. Atlas Reach is
tenant #1.

## Architecture

- **Backend** (`backend/`): FastAPI + SQLAlchemy, custom JWT auth (HS256, not
  Supabase Auth). DB is **Supabase Postgres** via psycopg3 (session pooler),
  falling back to local SQLite with no config. Schema owned by **Alembic**,
  auto-migrated on startup (`app/migrations.py`). ~80 endpoints.
- **Frontend** (`frontend/src/`): React + Vite + TypeScript. Design system in
  `App.css` (navy/cobalt, light/dark aware, tenant-brand CSS vars). Logo is an
  inline SVG (`logo.tsx`).
- **Desktop** (`electron-app/`): Electron shell that spawns the packaged backend
  binary (`run.py` → uvicorn on `127.0.0.1:8000`) and loads the built frontend.
  Reads `~/Library/Application Support/salescale-app/config.json` for
  `databaseUrl`, secrets, `superadminEmails`, and the Meta/Google app creds.
- **Hosted web** (production path): `backend/Dockerfile` + `frontend/Dockerfile`
  (nginx) + `docker-compose.yml`. Guide in `DEPLOYMENT.md`.

## Build & run

- **Tests:** `cd backend && TZ=UTC DATABASE_URL=sqlite:////tmp/x.db <venv>/bin/python -m pytest`
  → **184 passing** (22 files). CI: `.github/workflows/ci.yml` (needs a GitHub
  remote to run).
- **Run backend locally:** `uvicorn app.main:app` from `backend/` (reads
  `backend/.env`). Health: `GET /api/health`.
- **Full desktop build:** `./build-macos.sh` (PyInstaller backend from `run.py`
  → `vite build` → `electron-builder --mac --arm64`). Output:
  `electron-app/dist/Salescale-0.1.0-arm64.dmg` (**unsigned** — right-click →
  Open on first launch). The script hardcodes `python3`/`npm`; run with the
  provisioned 3.11/Node 20 on `PATH`.
- **Frontend build gotcha:** Vite 8 uses rolldown; if `vite build` fails on a
  missing native binding, `npm install --no-save @rolldown/binding-darwin-arm64`
  then rebuild.

## Toolchain (re-provision each session)

- **Python 3.11:** download `cpython-3.11.x-aarch64-apple-darwin-install_only`
  from `github.com/astral-sh/python-build-standalone` (latest release), extract
  to scratchpad, `python -m venv`, `pip install -r requirements.txt
  -r requirements-dev.txt` (adds `pyotp`, `pip-audit` used ad hoc).
- **Node 20:** `nodejs.org/dist/latest-v20.x/node-v20.x-darwin-arm64.tar.gz`;
  put `bin/` on `PATH` so `npm`'s `env node` shebang resolves.

## Config & secrets

`backend/.env` (gitignored) currently holds working values for: `DATABASE_URL`
(Supabase session pooler, ref `jtzowohhtrrfzxbchujj`), `JWT_SECRET`,
`TOKEN_ENCRYPTION_KEY` (Fernet), `SUPERADMIN_EMAILS=carterbruns@gmail.com`,
`RESEND_API_KEY`, `EMAIL_DEFAULT_FROM_ADDRESS`, **`META_APP_ID`/`META_APP_SECRET`**
(live, tested), and **`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/
`GOOGLE_DEVELOPER_TOKEN`/`GOOGLE_LOGIN_CUSTOMER_ID`** (live, tested). See
`.env.example` for the full list incl. optional `TWILIO_*` (SMS 2FA) and
`TRUST_FORWARDED_FOR`. **Supabase is empty** — sign up fresh in the app; the
migrations create the schema on first run.

## Built this session (all committed to the branch, tested)

Read the git log on `session/foundation-billing-auth` for the full sequence.
Highlights:

- **Platform registry (Phase 7a):** `backend/app/platforms.py` is the single
  source of truth; insights/change-execution/conversion seams are all
  registry-driven (fixed a Google `else` misroute); generic click-ID capture
  (`LandingEvent.click_ids`); `GET /api/platforms` + a dynamic frontend. All 7
  new platforms registered as STUBs ("coming soon"). Per-platform adapter
  internals (7b) are NOT built — gated on your dev accounts.
- **Meta connected & verified** end-to-end (OAuth → token → ad accounts → live
  campaigns) against a real account. **Google connected** via the **Atlas Reach
  MCC agency model** (`list_manager_child_accounts` — pulled Best Spas Direct +
  Paganelli HVAC). Both work in **Development mode**; going public still needs
  Meta App Review + Business Verification and a Google Standard-access developer
  token (see `PLATFORM_APPROVALS.md`).
- **Desktop OAuth:** opens auth in the system browser, passes the operator
  Meta/Google creds through, and lands on a "return to app" page + refresh on
  focus (`electron-app/`, `api/connect_common.py`).
- **Two-factor auth:** TOTP (authenticator), email, and SMS (Twilio; 503 until
  configured) + 10 single-use backup codes. Two-step login (`/login` →
  challenge → `/login/mfa`). Security settings UI with QR enrollment.
- **Session management:** `user_sessions` table (device list, per-device revoke,
  "log out everywhere"); token carries a `sid`, validated per request.
- **Org 2FA policy:** owner toggle (`require_mfa`) that gates team members to
  enrollment — **enforced server-side** (`deps.mfa_gate` on the app-data
  routers), not just in the UI.
- **Security hardening (multiple passes + a full audit):** single-use reset
  tokens, constant-time login, per-request org-suspension, session revocation,
  social-login email-trust, fail-closed default `JWT_SECRET`, prod security
  headers (nginx CSP/HSTS/…), API docs off in prod, rate limits on every public
  webhook/callback, SSRF egress guard + encrypted external-sync secret, Stripe
  webhook idempotency/ordering, request body-size limit + input caps. **Deps
  clean** (`pip-audit` + `npm audit`); no secrets in git history.

## Migrations (this session)

Alembic head is **`f2b6d90c4a17`**. Chain added this session:
`f1a2b3c4d5e6` (landing click_ids) → `c7e2a1b9d4f8` (token_version +
auth_provider) → `d5f8b3a06c21` (MFA columns) → `e9a4c2b71f30` (org require_mfa
+ user_sessions) → `f2b6d90c4a17` (Stripe idempotency + subscription_event_at).
Any model change needs `alembic revision --autogenerate`; it applies on next
startup. NOT-NULL columns need a `server_default` to apply to non-empty DBs.

## What's left before beta / production

Detailed in `RELEASE_CHECKLIST.md`. The short version:

- **Deploy** backend + frontend to a host + domain (nothing is deployed). The
  desktop ad-OAuth flow and the `APP_BASE_URL` redirects want the hosted web
  build.
- **Meta App Review + Business Verification** and **Google Standard-access
  developer token** to let *other* agencies connect (your own accounts already
  work in dev mode). Long lead time — start early. See `PLATFORM_APPROVALS.md`.
- **Stripe** live keys/products to turn billing on; **Resend** domain
  verification to email addresses other than your own; **Twilio** for SMS 2FA.
- **GDPR/CCPA data export + deletion** (not built) + legal copy.
- **Deferred security hardening** (documented, non-blocking): Postgres RLS
  defense-in-depth; tier caps on ad-connections/conversion-configs/CRM records;
  full inbound-sync replay protection; SSRF IP-pinning.
- Code-signing/notarization (only if shipping the desktop app); backups +
  uptime monitoring.

## In flight / next up

- **Phase 2 (UI modernization): IMPLEMENTED** (7 `ui:` commits, Stages 0–9).
  What landed: `theme.css` (all tokens via CSS `light-dark()`, glass/type/
  spacing/motion scales, tenant-brandable vars) + `theme.ts` (persisted
  light/dark/system toggle, runtime branding → CSS vars); `src/components/`
  (Button/GlassCard/Badge/Field/Skeleton/EmptyState, sortable **DataTable**,
  Toast, **Cmd+K CommandPalette**); collapsible sidebar sections + icon-rail
  collapse; glass topbar/auth/widgets/CRM drawer; skeletons everywhere;
  audit log, Google search terms, CRM lead list, and superadmin org list all
  on DataTable; new **Branding settings page** (`branding.tsx`, Settings →
  Branding, admin-gated) covering white-label name/logo/colors/email + the
  custom-domain claim→TXT→verify flow, live-rethemes via `refreshBranding()`.
  Branding API round-trip was verified against a live backend on throwaway
  SQLite. The brief's "DM inbox (LinkedIn/Instagram)" remains out of scope
  (new feature, not a restyle).
- **Not visually verified in a browser** — the Claude-in-Chrome extension was
  disconnected all session, so verification was `tsc`+`vite build`+oxlint+
  dev-server transforms only. Worth a quick human pass over: login page,
  sidebar collapse, Cmd+K palette, dark-mode toggle, dashboard widgets, CRM.
- **Security×UI overlaps (honored, re-verify in review):** `App.tsx` auth
  gates/`MfaGate`/role nav filters restyled but logically untouched; the
  palette builds commands from the same role-filtered nav list; `creatives.tsx`
  sandboxed `dangerouslySetInnerHTML` preview untouched; branding logo/favicon
  URLs re-guarded client-side (`safeBrandUrl`, http(s) only) on top of the
  backend validator.

## Doc map

- `RELEASE_CHECKLIST.md` — granular, current status of every release item.
- `DEPLOYMENT.md` — hosted deploy (backend + frontend + env vars).
- `PLATFORM_APPROVALS.md` — Meta/Google app + approval steps, scopes, redirect
  URIs.
- `PLATFORMS.md` — per-platform reference for the adapter work.
- `.env.example` — every config var with notes.
- `CLAUDE.md` — product vision + phases (its "Current Status" is partially
  stale; trust this file + `RELEASE_CHECKLIST.md`).
- Auto-memory: `~/.claude/projects/-Users-carter-Desktop-salescale/memory/`
  (Supabase backend, desktop build, backend-tests/TZ gotcha).
