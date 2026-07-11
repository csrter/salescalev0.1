# Salescale — Session Handoff

Orientation for a fresh Claude Code session (or a new engineer). Read this
first, then `CLAUDE.md` for product vision/guardrails/phase status,
`DEPLOYMENT.md` for the deploy runbook, and `RELEASE_CHECKLIST.md` for
granular release status.

_Last updated: 2026-07-11 (Phase 12 + connect/auth fixes + production deploy)._

---

## ⚠️ Read this first

1. **SALESCALE IS LIVE IN PRODUCTION** at `https://app.salescale.lol` /
   `https://api.salescale.lol`, running against the **real Supabase
   database — the same one local dev's `backend/.env` points at.** Two
   consequences:
   - Never test against `backend/.env`'s `DATABASE_URL`. The pytest suite
     pins its own throwaway SQLite automatically; anything manual gets
     `DATABASE_URL=sqlite:////tmp/x.db`.
   - **A deployed migration applies to the live DB on container start**
     (`alembic upgrade head` runs at boot). Treat schema changes as
     production changes now. New NOT-NULL columns need a `server_default`
     to apply to a non-empty DB.

2. **Branch `feature/ui-revamp` holds 48 unpushed commits and exists ONLY on
   this Mac.** `origin/main` (github.com/csrter/salescalev0.1) is still at
   `f5c837c` (2026-07-07); no remote branch exists for this work. The VPS has
   a code *snapshot* (no git history). Losing this laptop loses the history —
   pushing the branch is the single highest-value 10-second action available.

3. **Run backend tests with `TZ=UTC`**
   (`cd backend && TZ=UTC .venv/bin/python -m pytest -q`) or ~9 metrics tests
   flake on timezone math. Full suite: **283 passing** as of `8f71709`.

4. **Secrets:** production `backend/.env` lives only at
   `deploy@2.25.75.95:~/salescale/backend/.env` (mode 600). If
   `TOKEN_ENCRYPTION_KEY` is ever lost, every stored ad-platform token is
   permanently undecryptable — all client ad-account connections would need
   re-authorizing. **Back that file up to a password manager.** Not yet done
   as of this writing.

## What Salescale is

Multi-tenant B2B SaaS for marketing agencies: ads management with real write
access (Meta + Google live, 7 more platforms scaffolded as adapters), native
CRM + house CRM, server-side conversion tracking, Lead Finder (Google
Places) + email verification, Instagram outreach module (built, go-live gated
on Meta App Review), white-label client portals. Each agency is an
**Organization** (tenant); tenant isolation is the #1 rule; Atlas Reach is
tenant #1 and dogfoods everything. Full detail + standing guardrails:
`CLAUDE.md`.

## Product state

**All numbered phases (1–14) are built** — per-phase notes in `CLAUDE.md`
STATUS. Landed this week (all on `feature/ui-revamp`):

| Commit | What |
|--------|------|
| `9ff0d87` | Phase 12 — Lead Finder (Google Places, per-org monthly metering, org-wide dedupe, house-CRM import) + email verification (ZeroBounce adapter, `verification_status` on contacts, the shared outreach gate `email_verification.sendable()`) + own-site enrichment + BYO provider keys |
| `fb81469` | Connect fix — a Google MCC / Meta BM login no longer dumps every visible ad account onto one client. Discovery ≠ attachment (`services/ad_accounts.py`); per-client "Manage accounts" picker; `PATCH /api/ad-accounts/{id}` reassign with full client_id cascade; OAuth callbacks handle cancel/API errors with a branded page instead of 4xx/500 |
| `4f704c9` | Auth/team fix — social sign-in failures redirect to login with `?login_error=` reason; Integrations page lists the 4 exact OAuth redirect URIs; invites without an email transport return `invite_link` to the Admin (shown once, never stored/listed); network-level platform errors normalize into `MetaApiError`/`GoogleApiError`/`PlacesError` |
| `1b5143e` `f401640` `8f71709` | Deployment: completed env-var runbook; VPS hardening guide; Caddy stack (`deploy/docker-compose.prod.yml`) and Traefik-reuse stack (`deploy/docker-compose.traefik.yml`) |

**Remaining roadmap** (see CLAUDE.md): Stripe live activation + entitlement
flip → Outreach go-live (Meta App Review + Business Verification, plus a
Google Standard-access developer token for *other* agencies to connect — see
`PLATFORM_APPROVALS.md`; long lead times, external clocks) → release gate
(RLS audit, live-card billing test, Atlas Reach dogfood week).

## Production environment (deployed 2026-07-11)

| Thing | Where |
|-------|-------|
| Web app | `https://app.salescale.lol` |
| API | `https://api.salescale.lol` — health: `GET /api/health` → `{"ok":true}` |
| VPS | Hostinger KVM, `2.25.75.95`, Ubuntu, 7.8GB RAM |
| SSH | `ssh deploy@2.25.75.95` — key-only (key: this Mac's `~/.ssh/id_ed25519`); root login + password auth disabled |
| Host security | `ufw` (22/80/443 only), `fail2ban`, `unattended-upgrades`, Docker log rotation capped via `/etc/docker/daemon.json` |
| Code | `~/salescale` on the VPS — tar snapshot of `8f71709`, **not a git clone** |
| Stack | `~/salescale/deploy/docker-compose.traefik.yml` → containers `deploy-backend-1`, `deploy-frontend-1` (no host ports; Traefik reaches them over Docker networking) |
| TLS / routing | **Pre-existing Traefik** (`traefik-traefik-1`, host network, config `/docker/traefik/`) discovers our containers via labels; owns Let's Encrypt certs (issued 2026-07-11, auto-renew). We did NOT deploy Caddy here — `docker-compose.prod.yml` is the variant for a clean box. |
| DNS | Porkbun — A records `app`/`api` → `2.25.75.95`. Porkbun's parking wildcard (`*.salescale.lol` → pixie.porkbun.com) remains; harmless, exact A records win. |
| Database | Supabase Postgres (session pooler) — **shared with local dev** |
| Email | Resend (key carried over from dev config) — invites/verification/resets deliver |
| OAuth | All four redirect URIs registered and verified live on the Meta app + Google OAuth client: `https://api.salescale.lol/api/{connect,auth/oauth}/{meta,google}/callback` |

**Careful:** the VPS also runs Carter's other apps (`hermes-webui`,
`openclaw`, `9router` — compose projects under `/docker/`) behind the same
Traefik. Never stop/prune containers broadly; scope every docker command to
the `deploy` compose project.

**Redeploy procedure** (no git remote on the VPS — ship a snapshot from the
Mac). The archive contains only tracked files, so the VPS's `backend/.env` /
`deploy/.env` are never overwritten:

```bash
# on the Mac, repo root, work committed:
git archive --format=tar.gz -o /tmp/salescale-deploy.tar.gz HEAD
scp /tmp/salescale-deploy.tar.gz deploy@2.25.75.95:~/
ssh deploy@2.25.75.95 'tar xzf ~/salescale-deploy.tar.gz -C ~/salescale && rm ~/salescale-deploy.tar.gz \
  && cd ~/salescale/deploy && docker compose -f docker-compose.traefik.yml up -d --build'
rm /tmp/salescale-deploy.tar.gz
```

**Logs / status:**

```bash
ssh deploy@2.25.75.95
cd ~/salescale/deploy
docker compose -f docker-compose.traefik.yml ps
docker compose -f docker-compose.traefik.yml logs backend --tail 100
docker logs traefik-traefik-1 --since 10m     # routing / cert issues
```

## Not done yet (roughly in order)

1. **Push `feature/ui-revamp` to GitHub** (warning #2).
2. **Back up the VPS `backend/.env`** to a password manager (warning #4).
3. **Flip `REQUIRE_EMAIL_VERIFICATION=true`** in the VPS `backend/.env` once a
   real invite email is confirmed delivered, then redeploy. It's off by
   default so fresh deploys aren't blocked by broken email — but email works
   here now.
4. **Uptime monitor** on `https://api.salescale.lol/api/health` (UptimeRobot
   or similar). Not set up.
5. **Stripe live activation + entitlement flip** — tier gating all flows
   through `services/entitlements.py` stubs; Phase 8 billing is built, not live.
6. **Outreach go-live** — code built (`9d01f16`); blocked on Meta App Review.
7. **Release gate** — RLS audit on new tables, live-card billing test, one
   full Atlas Reach dogfood week.
8. Desktop social sign-in can't complete its round-trip (the callback
   redirects the session token to `APP_BASE_URL`, which the file:// Electron
   UI can't receive). Password login works on desktop. Fix would be a
   custom-protocol handoff — only if desktop social login is ever wanted; the
   hosted web app is the primary client now.

## Dev environment notes

- **Backend:** FastAPI + SQLAlchemy 2 + Alembic (linear chain, auto-run at
  startup), venv at `backend/.venv` (a second `backend/venv` exists and is
  what the VPS-era launch configs reference — both work).
- **Frontend:** React + Vite + TS, Deep Cobalt design system (`DESIGN.md`) —
  tokens only in `theme.css`, shared primitives in `src/components/`, view
  CSS per-prefix under `src/styles/views/`.
- **Tests:** `cd backend && TZ=UTC .venv/bin/python -m pytest -q` → 283.
  Suites that create org data use dedicated org fixtures (`lf_org`,
  `connect_org`) — never seed into Atlas Reach; metrics/isolation suites
  assert over its counts.
- **UI verification:** `.claude/launch.json` has `backend-alt3`/`frontend-alt3`
  (8030/5203, `dev-alt3.db` — copy `dev-alt2.db` to create; gitignored,
  delete after). Login `uitest@example.com` / `housecrm-verify-1`.
  Preview-pane gotchas (sandboxed servers can't read `.env` → set
  TOKEN_ENCRYPTION_KEY inline for connection-bearing endpoints; dialog
  screenshots render black — verify via read_page/JS) are in Claude's
  project memory.
- **Desktop build:** `./build-macos.sh` (PyInstaller backend → vite build →
  electron-builder) → `Salescale-0.1.0-arm64.dmg`, unsigned. If `vite build`
  fails on a missing rolldown native binding:
  `npm install --no-save @rolldown/binding-darwin-arm64`.

## Doc map

- `CLAUDE.md` — product vision, standing guardrails, per-phase STATUS
  (kept current; update it + commit before ending any session).
- `DEPLOYMENT.md` — full deploy runbook: env vars, VPS hardening, Caddy vs
  Traefik-reuse stacks, team onboarding.
- `RELEASE_CHECKLIST.md` — granular release items (predates this week; the
  deploy items in it are now done).
- `PLATFORM_APPROVALS.md` — Meta App Review / Google developer-token steps.
- `PLATFORMS.md` — per-platform adapter reference.
- `.env.example` — every config var with notes.
- Auto-memory: `~/.claude/projects/-Users-carter-Desktop-salescale/memory/`
  (Supabase, tests/TZ, connect flows, Phase 12, UI stack).
