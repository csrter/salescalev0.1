# Salescale — Release Checklist

Status legend: `[ ]` not started · `[~]` partial · `[x]` done.
Grounded in the actual state of the repo as of the pre-release review.
Items are ordered by priority. **P0 blocks any public release.**

---

## P0 — Release blockers (architecture & security)

- [x] **Deployment architecture — hosted web (built & verified).** Chose the
  hosted path: the FastAPI backend runs as a service holding the DB
  credentials; the **web frontend** (static build) talks to it over HTTPS with
  `VITE_API_URL`; nothing sensitive ships to the client. Done: production
  `backend/Dockerfile` (correct module, `$PORT`, non-root, auto-migrate on
  boot), multi-stage `frontend/Dockerfile` + nginx SPA config, multi-origin
  CORS allowlist (verified it admits the configured web origin and rejects
  others), rewritten `docker-compose.yml`, and a turnkey `DEPLOYMENT.md`.
  Verified hosted-mode signup against Supabase and a web build baking in the
  hosted URL. **Remaining (needs your accounts):** pick a host (Render/Fly/
  Railway/VPS) + a static host (Vercel/Netlify/…), set the env-var secrets
  there, and point a domain — all steps are in `DEPLOYMENT.md`. The desktop
  app stays as an optional single-user/offline client.
- [~] **Set production secrets; never ship them in the client.**
  - [x] Generated a strong `JWT_SECRET` and a Fernet `TOKEN_ENCRYPTION_KEY`
    into `backend/.env`; the desktop app can carry them via `config.json`;
    `.env.example` documents generation; startup warns on the dev default.
    (Connecting Meta/Google now works — the missing Fernet key was silently
    breaking it.)
  - [ ] Still to do for a **hosted** deploy: set these as server env vars
    (never in the client bundle) and rotate the ones committed to your
    local `.env` if that machine isn't trusted.
- [x] **Adopt a migration system (Alembic).** Done: baseline migration
  captures all 30 tables; the existing Supabase DB is stamped; startup now
  runs `alembic upgrade head` (bundled into the packaged binary), so any
  database — fresh or existing — self-heals to the current schema. Workflow:
  `alembic revision --autogenerate -m "..."` then it applies on next start.
- [x] **Verify & enforce tenant isolation.** Extended the suite with
  `backend/tests/test_admin.py` (super-admin gate, cross-tenant reads,
  suspend/reset, owner-only member edits, cross-org 404s). Full suite: **121
  passing**. CI runs it on every push (see P2). Keep extending as new
  endpoints land.
- [ ] **Code-sign + notarize the macOS app** (Apple Developer ID). The DMG
  is currently unsigned; Gatekeeper blocks normal users. (Moot if you go
  web-only.)

## P1 — Core product gaps (needed for a real paid product)

- [~] **Billing & tier enforcement (Phase 8).** [x] Server-side tier limits
  enforced (clients + team seats, 402 on overage; agency = unlimited) — tested.
  [x] Stripe checkout / customer-portal / webhook endpoints built (fail-closed
  503 when unconfigured); the webhook handler syncs plan/status and is
  unit-tested; `organizations` gained Stripe columns via a migration. [ ] To
  go live: create Stripe products/prices, set `STRIPE_SECRET_KEY`,
  `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO/AGENCY`, and register the webhook.
  [x] Frontend **Billing** tab (owner-only) built — plan/status, Upgrade
  (→ checkout redirect), Manage billing (→ portal), and a "not configured"
  state when Stripe keys are absent.
- [~] **Auth completeness.** [x] Rate-limiting added on signup (10/hr/IP) and
  login (20/5min/IP) — verified (429 after the limit); note it's per-process,
  so move to Redis for a global limit across multiple hosted instances.
  [x] Email verification on signup + self-serve password reset built
  (purpose-bound expiring tokens, no account enumeration, rate-limited),
  delivered via the existing email service; a `require_email_verification`
  flag gates login when you want it on. Tested end-to-end (7 tests reading the
  real token from the dev email log). [x] Frontend built — `?verify=`/`?reset=`
  links open verify/reset screens, a "Forgot password?" flow on login, and an
  email-verification banner with resend. [x] **Real delivery via Resend** —
  the email service now sends through the Resend API when `RESEND_API_KEY` is
  set (SMTP fallback otherwise); tested with a mocked transport. Set the key +
  a verified sender domain and flip `REQUIRE_EMAIL_VERIFICATION=true` to
  enforce. [ ] Optional CAPTCHA.
- [x] **Social login (Sign in with Google / Meta).** New OAuth sign-in:
  `/api/auth/oauth/{provider}/start|callback` + `/api/auth/me`, find-or-create
  provider users (email pre-verified), and "Continue with Google/Meta" buttons
  on the login screen. Reuses the ad OAuth apps (extra redirect URIs);
  fail-closed 503 until configured. Tested (start/callback/session, 8 tests).
- [x] **Bring-your-own platform credentials.** Each Organization now supplies
  its own Meta app + Google Ads OAuth client/developer token (encrypted at
  rest) via an **Integrations** page; the connect flows use the org's creds
  (falling back to the operator's global app if set). So connecting the API is
  the agency's responsibility, not the operator's. Tested (6 tests, org-scoped,
  admin-gated, verified end-to-end in the packaged app).
- [~] **Platform integrations & approvals (long lead time).** [x] Audited the
  Meta/Google code (built + doc-verified 2026-07-06: Graph v25.0, Google Ads
  v24) and **fixed a real gap** — `httpx` (used by every Meta/Google call) was
  missing from `requirements.txt`, which would crash the connect flows in a
  clean prod image; also pinned `google-ads`. [x] Wrote `PLATFORM_APPROVALS.md`
  with the exact Meta App Review + Google developer-token/OAuth-consent steps,
  scopes, and env vars. [ ] External + yours: submit the approvals, set the
  credentials, and prove connect → insights → conversions with a real account.
  Phase 7 platforms still unbuilt.
- [ ] **Resolve the ~42 TODO/FIXME markers** in `backend/app` and
  `frontend/src`; triage which are release-blocking.
- [ ] **Update CLAUDE.md "Current Status"** — all 10 phases are still marked
  incomplete though much is built; make it reflect reality.

## P2 — Operations & compliance

- [ ] **Hosting/infra:** managed backend host, HTTPS + reverse proxy, process
  manager/auto-restart, health checks. Replace desktop CORS `*` with the
  real hosted origin(s).
- [~] **Observability:** [x] Guarded Sentry error tracking (activates when
  `SENTRY_DSN` is set) + a structured log format; uvicorn's per-request access
  log covers request logging. [ ] Still yours: set the DSN and add uptime
  monitoring + alerting on your host.
- [ ] **Backups & DR:** confirm Supabase automated backups and run a test
  restore; define a data-retention policy.
- [x] **CI/CD:** added `.github/workflows/ci.yml` — runs the backend test
  suite (incl. tenant isolation) and the frontend typecheck/build on every
  push/PR. Needs a GitHub remote to actually execute; backend job verified
  green locally (121 passing).
- [ ] **Legal/compliance:** Terms of Service, Privacy Policy, DPA, cookie
  consent, and GDPR/CCPA data export + deletion (Phase 10, not built). You
  store lead/contact PII and ad data — this is not optional.
- [x] **Supply-chain hygiene:** ran `pip-audit` (app deps clean; only the base
  image's build-time `setuptools` flagged — hardened the Dockerfile to upgrade
  it) and `npm audit` (**0 vulnerabilities**). `.dockerignore` keeps `.env` out
  of images. Deleted the stale `schema.sql`.

## P3 — Quality & polish

- [ ] **Desktop auto-update** (`electron-updater`) so fixes ship without a
  manual reinstall. (Or a proper web deploy pipeline if going web.)
- [ ] **Frontend finish pass:** apply the new design system to the CRM and
  dashboard-widget internals; global loading/error/toast handling; fix the
  low-contrast client-detail header in dark mode; accessibility pass.
- [~] **Performance/scale:** [x] Added limit/offset pagination — the
  cross-tenant `/api/admin/organizations` (unbounded by design) plus `clients`
  and CRM `tasks`; CRM contacts/activities, audit-log and landing-events
  already had caps. [ ] Still to do: DB index review on tenant-scoped queries,
  and frontend "load more" for the few user-facing lists (backend caps are the
  safety net today).
- [ ] **Onboarding UX:** remove the manual `config.json` step; guide the user
  to connect their first platform.
- [ ] **Docs & support:** admin runbook, support process, status page.

---

### Recommended sequence

Decide **P0 #1 (hosted vs desktop)** first — it determines whether signing,
auto-update, and CORS even matter, and where secrets live. Then secrets +
migrations + tenant-isolation CI, then billing + auth + platform approvals
(start the approvals early; they take weeks), then ops/compliance, then polish.
