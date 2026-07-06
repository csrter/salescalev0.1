# Project: Atlas Reach Ads Platform + Salescale CRM

Claude Code auto-loads this file at the start of every session in this repo.
Keep it accurate as the build progresses — update it at the end of each
phase rather than letting it go stale.

## WHAT THIS IS

A multi-tenant web app for Atlas Reach, a B2B marketing agency, to manage
Meta and Google Ads campaigns for multiple HVAC contractor clients from one
place — replacing manual work split across Meta Ads Manager, Google Ads UI,
and Business Suite.

## SCOPE

This is a single-agency platform: Atlas Reach plus Atlas Reach's own
clients. It is not being built as a resellable multi-tenant SaaS product
for other agencies to sign up for — don't add agency-level self-serve
signup, billing, or agency-to-agency isolation. The multi-tenancy that
matters is client-to-client isolation within Atlas Reach, which is already
a hard requirement below.

Two user roles exist from the start:
- **Atlas Reach team** — full access across all clients, all ad accounts,
  all Salescale data, all write actions (subject to the confirmation
  guardrails below).
- **Client** — scoped to their own account only. Read access to their own
  ad performance, metrics, and Salescale pipeline. No access to any other
  client's data, no access to Atlas Reach-internal fields (see Phase 6),
  and no write access to ad accounts or budgets — this is a visibility
  role, not a management role.

## PRODUCT GOAL

1. Connect and manage multiple clients' Meta ad accounts AND Google Ads
   accounts from one login.
2. View, create, and edit campaigns/ad sets/ads (Meta) and
   campaigns/ad groups/ads (Google) across all accounts.
3. Advanced, agency-specific metrics neither platform surfaces natively —
   blended cross-platform CAC/ROAS, funnel-tier performance, creative
   fatigue scores, lead-quality-adjusted CPL, cross-client benchmarking.
4. Customizable dashboard layout per user or per client.
5. Server-side conversion tracking to both platforms — Meta Conversions API
   (CAPI) and Google Enhanced Conversions/offline conversion import — with
   deduplication against client-side events.
6. UTM tracking as the platform-agnostic source of truth alongside each
   platform's own attribution — capturing UTM parameters at landing time,
   enforcing consistent naming conventions across clients and campaigns,
   and reconciling what Meta/Google each claim credit for against what the
   UTM data actually shows.
7. A native CRM, called **Salescale**, as part of the same platform: leads
   generated from Meta Instant Forms, Google Lead Form ads, or landing-page
   submissions flow directly into Salescale as contacts/leads, arriving
   with their attribution data (UTM + click ID) already attached — no
   manual re-entry, no separate system to reconcile against. Salescale
   owns the qualified-lead workflow (the specific definition/verification
   process behind the 14-Day Trial Sprint guarantee) and feeds that status
   directly into the lead-quality-adjusted CPL metric and the guarantee
   tracker, rather than those metrics depending on an external CRM's tags.

## ARCHITECTURE (proposed defaults — confirm/adjust in Phase 1, then treat as fixed)

- Backend: Node.js/TypeScript, or Python/FastAPI if SDK support at build
  time favors it. Whichever is chosen, don't switch mid-project.
- Frontend: React with a genuinely rearrangeable/resizable widget system —
  not a fixed grid pretending to be customizable.
- Database: Postgres, multi-tenant schema — one agency (Atlas Reach), many
  clients, each client with one or more ad accounts across platforms.
- Auth: Atlas Reach team logins are separate from per-client platform
  connections (Meta OAuth token; Google Ads OAuth token + developer
  token). A "client" can have a Meta account, a Google account, or both —
  neither platform is the required/primary one.
- Background jobs: a scheduler/queue polling both the Meta Marketing API
  and Google Ads API on an interval. The two platforms have independent
  rate limits and don't fail the same way — handle them separately.
- Attribution data model: a landing-event/session table capturing UTM
  parameters (source, medium, campaign, content, term) plus each
  platform's click ID (`fbclid`/`fbc`, `gclid`) at the same capture point,
  tied to the eventual lead record. This is the platform-agnostic layer
  that lets you reconcile what Meta/Google each self-report against what
  actually drove the lead — treat it as core data model, not a bolt-on.
- Salescale CRM data model: contacts, companies, deals/opportunities, a
  pipeline with stages customizable per client (each HVAC client may run a
  slightly different sales process), activity log (calls, notes, emails),
  tasks/follow-up reminders, and tags. This lives in the same multi-tenant
  Postgres schema as everything else — Salescale is a module of this
  platform, not a separate system bolted on.
- External CRM sync (optional, per client): some clients' nurture
  automation currently lives in an external CRM (e.g., GHL SMS sequences).
  Salescale should be the source of truth for reporting and the
  qualified-lead workflow, with optional two-way sync so existing external
  automation keeps working during a transition rather than requiring a
  hard cutover. Don't assume every client needs this — build it as an
  optional per-client connection, not a required dependency.

## STANDING GUARDRAILS (apply to every phase, not just the phase where they're introduced)

- Never commit or hardcode Meta app secrets, access tokens, developer
  tokens, or client API keys in source. Environment variables / secrets
  manager only.
- Any action that changes live ad spend, pauses/resumes a campaign, or
  modifies budgets requires explicit UI confirmation before executing.
  No silent writes to a live ad account, ever.
- Tenant isolation is a hard requirement: one client's credentials or data
  must never be reachable from another client's context.
- Meta's Marketing API/CAPI and the Google Ads API change their specs over
  time. Check current API version and endpoint behavior against each
  platform's live developer documentation before implementing anything
  API-specific — do not rely on training-data memory for exact request
  shapes, hashing/normalization rules, or permission tiers.
- Build and test each phase against one real (Atlas Reach's own) ad account
  on each platform before wiring in any client account.
- At the end of each phase: update this file's "Current Status" section
  below, commit with a clear message, and stop for review before starting
  the next phase file.

## CURRENT STATUS

_(Update after each phase — this section is the source of truth for what's
actually built vs. what's still planned.)_

- [x] Phase 1 — Foundation _(2026-07-06: Python/FastAPI + React/TS + Postgres
  stack approved and built. Multi-tenant schema incl. Salescale CRM entities
  and landing_events attribution table; two-role auth enforced at the
  data-access layer (TenantScope) and verified by 17 passing tests; Meta
  OAuth (Marketing API v25.0, direct Graph calls) and Google Ads OAuth
  (API v24, official google-ads lib) with Fernet-encrypted token storage and
  disconnected-state surfacing; live account→campaign→ad set/group→ad
  browser API + React UI. Live-account verification against real Meta/Google
  test accounts still requires user-supplied app credentials — see SETUP.md
  for the App Review / developer-token steps only the user can do. Dev runs
  on SQLite (no Docker/Postgres on this machine); deploy targets Postgres.)_
- [ ] Phase 2 — Core management features
- [ ] Phase 3 — Advanced metrics layer
- [ ] Phase 4 — Customizable UI
- [ ] Phase 5 — Server-side conversion tracking (CAPI + Google)
- [ ] Phase 6 — Salescale CRM

## PHASE FILES

Run these one at a time, in order, as separate Claude Code sessions or
prompts: `PHASE_1_FOUNDATION.md`, `PHASE_2_CORE_MANAGEMENT.md`,
`PHASE_3_ADVANCED_METRICS.md`, `PHASE_4_CUSTOMIZABLE_UI.md`,
`PHASE_5_CONVERSION_TRACKING.md`, `PHASE_6_SALESCALE_CRM.md`. Each is
self-contained but assumes this file's architecture and guardrails as
fixed context.
