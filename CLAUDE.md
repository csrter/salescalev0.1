# Project: Salescale — Multi-Tenant Ads + CRM SaaS Platform

_(Name confirmed: **Salescale** is the product name for the whole
platform; the CRM module is "Salescale CRM". Earlier planning called this
"Atlas Reach Ads Platform + Salescale CRM" — any lingering references to
that name are stale.)_

Claude Code auto-loads this file at the start of every session in this repo.
Keep it accurate as the build progresses — update it at the end of each
phase rather than letting it go stale.

## WHAT THIS IS

Salescale is a multi-tenant SaaS platform that any marketing agency can
sign up for to manage paid ad campaigns across multiple platforms — Meta,
Google, Snapchat, Reddit, LinkedIn, Microsoft Advertising, and Nextdoor —
for their own clients, plus a built-in CRM, from one place. It replaces
manual work split across each ad platform's own separate manager UI and a
separate CRM.

This is not built around any one vertical. Atlas Reach (a B2B marketing
agency serving HVAC and home-services clients) is the first customer —
"tenant #1" — and its own usage should stay realistic and unspecial-cased,
but nothing in the core product should assume HVAC, home services, or
Atlas Reach's specific workflows. Anything specific to how Atlas Reach
runs its business (its guarantee terms, its qualified-lead definition, its
pipeline stages) is data an organization configures, not something coded
into the product.

## SCOPE

This is a genuine multi-tenant SaaS product: any marketing agency can sign
up, creating their own **Organization**. Organization-to-organization
isolation is a hard requirement — Atlas Reach must never be able to see or
reach another organization's data, and vice versa, with no exceptions for
being tenant #1. Self-serve signup, subscription billing, and org-level
account management are all in scope (see Phase 8).

Three tiers of access exist:
- **Organization** — the tenant. Atlas Reach is one Organization; any
  other agency that signs up is another. Each Organization has its own
  clients, ad accounts, Salescale data, team members, and billing
  subscription, fully isolated from every other Organization.
- **Organization team member** (roles: Owner, Admin, Member — refine
  exact permission differences in Phase 1) — access scoped to their own
  Organization only: its clients, ad accounts, Salescale data, and write
  actions (subject to the confirmation guardrails below). An Organization
  Owner manages billing and team membership; other roles may be more
  limited — define this in Phase 1 rather than giving every team member
  identical permissions by default.
- **Client** — a contact of an Organization's (e.g. Paganelli HVAC is a
  client of Atlas Reach). Scoped to read-only access to their own account
  only within their Organization: their ad performance, metrics, and
  Salescale pipeline. No access to any other client's data, no access to
  Organization-internal fields (see Phase 6), and no write access to ad
  accounts or budgets.

Nothing in the product should special-case Atlas Reach. If a feature only
makes sense "the way Atlas Reach does it," that's a sign it needs to be an
Organization-level setting (e.g. custom pipeline stages, a configurable
performance-guarantee tracker, custom qualified-lead criteria) rather than
a hardcoded assumption.

## PRODUCT GOAL

1. Connect and manage multiple clients' ad accounts across every supported
   platform (Meta, Google, Snapchat, Reddit, LinkedIn, Microsoft
   Advertising, Nextdoor) from one login.
2. View, create, and edit campaigns and their platform-specific
   sub-structures (Meta ad sets, Google ad groups, LinkedIn campaign
   groups, etc.) across all connected accounts, through one interface.
3. Advanced, agency-specific metrics no single platform surfaces natively —
   blended cross-platform CAC/ROAS, funnel-tier performance, creative
   fatigue scores, lead-quality-adjusted CPL, cross-client benchmarking.
4. Customizable dashboard layout per user or per client.
5. Server-side conversion tracking to every connected platform's own
   conversion API equivalent (see `PLATFORMS.md`), with deduplication
   against client-side events on each.
6. UTM tracking as the platform-agnostic source of truth alongside each
   platform's own attribution — capturing UTM parameters at landing time,
   enforcing consistent naming conventions across clients and campaigns,
   and reconciling what each connected platform claims credit for against
   what the UTM data actually shows.
7. A native CRM, called **Salescale CRM**, as part of the same platform:
   leads generated from Meta Instant Forms, Google Lead Form ads, or
   landing-page submissions flow directly into the CRM as contacts/leads,
   arriving with their attribution data (UTM + click ID) already
   attached — no manual re-entry, no separate system to reconcile against.
   The CRM owns a configurable qualified-lead workflow — each Organization
   defines its own qualified-lead criteria and any performance-guarantee
   terms it offers its clients (Atlas Reach's is the 14-Day Trial Sprint;
   another Organization's will differ or may not exist at all) — feeding
   that status directly into the lead-quality-adjusted CPL metric and an
   Organization-configurable guarantee tracker, rather than either being
   hardcoded to one Organization's terms.
8. Self-serve Organization signup, team invites, and flat-tier
   subscription billing (Starter/Pro/Agency — see Phase 8), so a new
   agency can sign up, pick a plan, and start connecting ad accounts
   without Salescale's own team doing anything manually.

## ARCHITECTURE (proposed defaults — confirm/adjust in Phase 1, then treat as fixed)

- Backend: Node.js/TypeScript, or Python/FastAPI if SDK support at build
  time favors it. Whichever is chosen, don't switch mid-project.
- Frontend: React with a genuinely rearrangeable/resizable widget system —
  not a fixed grid pretending to be customizable.
- Database: Postgres, multi-tenant schema — **Organization** is the root
  tenant entity; every other table (clients, ad accounts, campaigns,
  Salescale contacts/deals, etc.) hangs off an `organization_id` and must
  be scoped by it in every query. This is the single most important
  architectural rule in this file — an unscoped query is a cross-tenant
  data leak, not a bug to fix later.
- Auth: individual user logins (Organization team members and Clients)
  are separate from per-client platform connections (one OAuth/API
  credential set per platform per client, scoped under that client's
  Organization). A "client" can have any combination of connected
  platforms — none of them is the required/primary one.
- Billing: Stripe for subscription billing, flat tiers (Starter/Pro/Agency
  — exact feature/limit breakdown defined in Phase 8). Each Organization
  has one subscription. Tier limits (number of clients, ad platform
  connections, team seats, etc.) are enforced server-side, not just hidden
  in the UI — the same principle as the Client-role scoping below.
- **Platform adapter pattern.** With seven platforms in scope, do not
  hardcode platform-specific logic into shared code paths. Define one
  adapter interface every platform implements: connect (OAuth/API auth),
  list/create/edit/pause campaigns, fetch insights, send a server-side
  conversion event. Meta and Google are the first two reference
  implementations (built in Phases 1/2/3/5); Snapchat, Reddit, LinkedIn,
  Microsoft Advertising, and Nextdoor are additional adapters built in
  Phase 7 against the same interface. The dashboard, metrics layer, and
  reporting should consume the adapter interface generically — adding a
  platform should never require touching Phase 3/4's code, only adding a
  new adapter and registering it.
- Target platforms and per-platform specifics (OAuth requirements,
  conversion API equivalents, approval processes, fit notes): see
  `PLATFORMS.md`. Note that `PLATFORMS.md`'s fit commentary is written
  from Atlas Reach's specific vertical (HVAC/home services) — that's
  context for Atlas Reach's own usage, not a constraint on which platforms
  other Organizations can use. Every Organization can connect any
  supported platform regardless of vertical.
- Background jobs: a scheduler/queue polling every connected platform's
  API on an interval. Each platform has independent rate limits and fails
  differently — the job architecture needs per-platform isolation so one
  platform's outage or rate-limit hit doesn't stall polling for the
  others.
- Attribution data model: a landing-event/session table capturing UTM
  parameters (source, medium, campaign, content, term) plus each
  platform's click ID (`fbclid`/`fbc`, `gclid`) at the same capture point,
  tied to the eventual lead record. This is the platform-agnostic layer
  that lets you reconcile what Meta/Google each self-report against what
  actually drove the lead — treat it as core data model, not a bolt-on.
- Salescale CRM data model: contacts, companies, deals/opportunities, a
  pipeline with stages customizable per client (any Organization's clients
  may run a different sales process — this isn't HVAC-specific, every
  Organization's client base will vary), activity log (calls, notes,
  emails), tasks/follow-up reminders, and tags. This lives in the same
  multi-tenant Postgres schema as everything else, scoped by
  `organization_id` like everything else.
- External CRM sync (optional, per client): some clients' nurture
  automation currently lives in an external CRM (e.g., GHL SMS sequences).
  Salescale should be the source of truth for reporting and the
  qualified-lead workflow, with optional two-way sync so existing external
  automation keeps working during a transition rather than requiring a
  hard cutover. Don't assume every client needs this — build it as an
  optional per-client connection, not a required dependency.

## STANDING GUARDRAILS (apply to every phase, not just the phase where they're introduced)

- Never commit or hardcode app secrets, access tokens, developer tokens,
  or client API keys for any platform in source. Environment variables /
  secrets manager only.
- Any action that changes live ad spend, pauses/resumes a campaign, or
  modifies budgets requires explicit UI confirmation before executing.
  No silent writes to a live ad account, ever, on any platform.
- Tenant isolation is a hard requirement at **both** levels: one
  Organization's data must never be reachable from another Organization's
  context, and within an Organization, one client's credentials or data
  must never be reachable from another client's context. Every database
  query touching tenant-scoped tables must filter by `organization_id` —
  treat a missing scope filter as a security bug, not a style issue.
- Every platform's API and conversion-tracking spec changes over time.
  Check current API version and endpoint behavior against that platform's
  live developer documentation before implementing anything
  platform-specific — do not rely on training-data memory for exact
  request shapes, hashing/normalization rules, or permission tiers, for
  any platform.
- Build and test each phase/adapter against Atlas Reach's own Organization
  (as tenant #1, using real or test ad accounts) before wiring in any
  other Organization or client account.
- At the end of each phase: update this file's "Current Status" section
  below, commit with a clear message, and stop for review before starting
  the next phase file.

## CURRENT STATUS

_(Update after each phase — this section is the source of truth for what's
actually built vs. what's still planned.)_

- [x] Phase 1 — Foundation (now includes Organization tenancy). Built
      originally as single-agency, then retrofitted (2026-07-06) for the
      multi-tenant SaaS replan: Organization root entity, self-serve
      signup (`POST /api/orgs/signup` — Atlas Reach is created through
      this same generic flow), Owner/Admin/Member/Client roles
      (owner: everything incl. team; admin: clients/connections/team
      members; member: campaign work only), `organization_id` on every
      tenant table, two-level TenantScope enforced in the data-access
      layer, and org-to-org isolation tests (`test_org_isolation.py`).
- [x] Phase 2 — Core management features (2026-07-06). Create/edit/pause/
      resume for campaigns / ad sets/groups / ads on Meta (Graph API v25.0)
      and Google (Google Ads API v24 via google-ads 31.1.0), both verified
      current against live docs. Guardrail architecture: every
      spend-affecting write is staged as a PendingChange with a before/after
      diff, executes only via an explicit confirm (`/api/manage/changes/
      {id}/execute`), expires after 30 min, and writes an immutable audit
      entry on every attempt — `test_manage_flow.py` proves the flow plus a
      structural test that no unstaged mutating route exists. UI: staged-
      diff confirmation modal, pending-changes tab, queryable audit-log
      tab, Meta creative builder with placement-accurate previews (Meta
      /previews edge), and a Google-only surface (keywords/match types/
      negatives, search-terms review with add-as-negative, PMax asset
      groups). Caveat: end-to-end against *live* ad accounts still needs
      real platform credentials (.env) — flow verified with mocked
      platform calls in tests and a live-failure path in dev.
- [ ] Phase 3 — Advanced metrics layer
- [ ] Phase 4 — Customizable UI
- [ ] Phase 5 — Server-side conversion tracking (CAPI + Google)
- [ ] Phase 6 — Salescale CRM
- [ ] Phase 7 — Additional platform adapters (Snapchat, Reddit, LinkedIn,
      Microsoft Advertising, Nextdoor)
- [ ] Phase 8 — Billing & self-serve onboarding (Stripe, subscription
      tiers, Organization signup)

## PHASE FILES

Run these one at a time, in order, as separate Claude Code sessions or
prompts: `PHASE_1_FOUNDATION.md`, `PHASE_2_CORE_MANAGEMENT.md`,
`PHASE_3_ADVANCED_METRICS.md`, `PHASE_4_CUSTOMIZABLE_UI.md`,
`PHASE_5_CONVERSION_TRACKING.md`, `PHASE_6_SALESCALE_CRM.md`,
`PHASE_7_ADDITIONAL_PLATFORMS.md`, `PHASE_8_BILLING_ONBOARDING.md`. Each
is self-contained but assumes this file's architecture and guardrails as
fixed context. See also `PLATFORMS.md` for per-platform reference details
used across Phases 1, 2, 3, 5, and 7.

Note: Phase 8 (billing/signup) is sequenced last here to match the
existing phase numbering, but its core requirement — the Organization
tenant entity — is actually built in **Phase 1**, not Phase 8. Phase 8
only adds the signup flow, Stripe integration, and tier enforcement on top
of a tenancy model that needs to exist from the start.
