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
| `TOKEN_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` — **if the database already holds connected ad accounts, reuse the exact key that encrypted them** (changing it makes every stored platform token undecryptable; every connection would need re-authorizing) |
| `FRONTEND_ORIGIN` | the web app's URL, e.g. `https://app.salescale.com` (comma-separate for multiple/preview domains) |
| `APP_BASE_URL` | same as the web app's URL — invite/verify/reset links, social-login returns, and Stripe returns all land here. Without it, invite links point at `localhost:5173` |
| `API_BASE_URL` | the backend's own public URL, e.g. `https://api.salescale.com` — builds the sign-in OAuth redirect URIs |
| `META_REDIRECT_URI` / `GOOGLE_REDIRECT_URI` | `https://<api>/api/connect/{meta,google}/callback` — the connect-flow callbacks default to `localhost:8000` and must be overridden |
| `RESEND_API_KEY` + `EMAIL_DEFAULT_FROM_ADDRESS` | transactional email (team invites, verification, resets). Without a transport, invite links are surfaced to the Admin in the Team UI to share manually — fine for testing, not for a real team |
| `TRUST_FORWARDED_FOR` | `1` when behind a reverse proxy / managed host, so rate limiting sees real client IPs |
| `SUPERADMIN_EMAILS` | your platform-admin email(s), comma-separated |

Optional (enable the corresponding integration): `META_APP_ID`,
`META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_DEVELOPER_TOKEN`, `GOOGLE_LOGIN_CUSTOMER_ID`,
`ANTHROPIC_API_KEY`, `GOOGLE_PLACES_API_KEY`, `ZEROBOUNCE_API_KEY`,
`REQUIRE_EMAIL_VERIFICATION=true` (recommended once email delivery works),
`SMTP_*`. See `.env.example`.

Notes:
- **Do not set `DESKTOP_MODE`** — that's for the Electron shell and opens CORS
  to `*`. Leaving it unset enforces the `FRONTEND_ORIGIN` allowlist.
- **Migrations run automatically on startup** (`alembic upgrade head`), so a new
  database is provisioned on first boot and future schema changes apply on
  deploy. No manual migration step.
- **Health check path:** `GET /api/health` → `{"ok": true}`.
- The container listens on `$PORT` (default 8000).
- **OAuth redirect URIs:** after deploy, the Integrations page (or
  `GET /api/integrations/redirect-uris`) lists the four exact URIs — connect
  and sign-in, per provider — to register on your Google Cloud OAuth client
  and Meta app. A missing entry is the `redirect_uri_mismatch` error.

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

## 5. Onboard the team

Once the stack is up: log in (or sign up your org), go to **Team → Invite by
email**, and invite each member. With `RESEND_API_KEY` set the invite email
delivers; members open the link from their own machine, set a password, and
work entirely in the browser — nothing to install, no secrets on their
machines. Seats are metered per plan (starter 5 / pro 15 / agency unlimited),
and Owners can require 2FA org-wide from Security.

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
