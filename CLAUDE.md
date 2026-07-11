# CLAUDE.md — Salescale

Persistent project context, auto-loaded by Claude Code. Everything in
this file is fixed context and standing guardrails for every session.
Where this file and the actual codebase disagree on implementation
details, the codebase is the source of truth — read it before assuming.

## WHAT SALESCALE IS

Salescale is a multi-tenant B2B SaaS platform for marketing agencies:
ads management with real write access across platforms, a native CRM,
server-side conversion tracking, outreach tooling, and white-label
client portals — one product, per-agency branded. Tagline:
"Revolutionizing the way you run ads." Brand: navy/cobalt, glassmorphic,
typography-first (Stripe/Linear/Ramp reference class).

Atlas Reach (a performance marketing agency serving home-service
contractors, expanding multi-vertical) is tenant #1 and dogfoods
everything. Nothing in the product may special-case Atlas Reach: if a
feature only makes sense "the way Atlas Reach does it," it must become
an Organization-level setting (custom pipeline stages, configurable
guarantee tracker, custom qualified-lead criteria), never a hardcoded
assumption.

## CURRENT STATE — READ THIS FIRST

The platform is BUILT through Phase 11. This is no longer a greenfield
spec; it is a working product approaching release. Every session
operates on an existing codebase with an existing design system.
Practical consequences:

- Do not scaffold, re-architect, or "improve" existing subsystems while
  executing a phase file. Extend the patterns that exist.
- The UI has been through a full glassmorphic navy/cobalt modernization
  pass with tenant-theme-awareness via CSS variables. New surfaces must
  use the existing design tokens/components — no hardcoded colors, no
  parallel component sets.
- A security audit pass has been completed. Do not casually touch
  auth, token storage, webhook verification, or RLS policies outside a
  task that explicitly targets them.
- Stripe billing (Phase 8) is built but NOT live. All tier gating
  currently flows through entitlement-check stubs
  (`checkEntitlement(org, <limit>)`) returning default limits. New
  features must gate through the same stubs — never hardcode limits —
  so the live flip is a single change.

## SCOPE & ROLES

The root tenant entity is the **Organization** (an agency). Every
domain table carries `organization_id`, enforced at the data-access
layer — never UI-only — with Postgres Row-Level Security as an
additional enforcement layer. Cross-tenant access is the single worst
class of bug this product can have; treat any new table without
org-scoping + RLS as a blocking defect.

Roles:
- **Organization team** — Owner, Admin, Member. Scoped to their own
  Organization: its clients, ad accounts, CRM data, and write actions
  (subject to the write guardrails below). Owner manages billing and
  membership. The full permission matrix is formalized in Phase 13.
- **Client** — a contact of an Organization (e.g. Paganelli HVAC for
  Atlas Reach). Read-only portal scoped to their own account: their ad
  performance, metrics, and pipeline. No other client's data, no
  Organization-internal fields, no write access to ad accounts or
  budgets. Client-facing surfaces render the Organization's white-label
  branding (Phase 9), never Salescale's, when configured.

## STANDING GUARDRAILS (apply in every session)

1. **Tenant isolation** — org-scoping + RLS on every table, verified,
   as above.
2. **Write-action confirmation** — no code path may take an ad live,
   change a budget, or spend money without an explicit confirmation
   step enforced server-side. A pending-approval creative (Phase 10)
   must be unpublishable through any path, including direct API calls.
3. **Secrets server-side only** — platform tokens encrypted at rest,
   refresh handling in place, revoked access surfaced to the user
   rather than failing silently. No secrets in client bundles, ever.
4. **Adapter pattern for ad platforms** — one common interface;
   Meta and Google are the reference implementations. New platforms are
   adapters (Phase 7). Platform-specific logic must not leak into
   shared code.
5. **Entitlements through the stub** — see Current State. Also expose
   self-service usage visibility ("X of Y used") for any metered thing.
6. **No scraping of Instagram, Facebook, or any Meta surface, ever, in
   any form or feature flag.** Salescale's Instagram Outreach module
   depends on Meta App Review approval; scraping endangers that
   approval and every existing Meta integration. Lead data comes from
   licensed APIs (Google Places), the target business's own public
   website, or a provider the Organization connects with its own key.
7. **AI insights ground in real computed data** for the Organization
   asking — never free-generated numbers, never across tenant
   boundaries.
8. **Audit logging** — membership, write actions, and CRM changes log
   actor/target/org/timestamp in the established per-action audit
   pattern. This is the SOC 2 groundwork; keep it consistent.
9. **Compliance posture** — GDPR/CCPA export/deletion (Phase 10) must
   cascade to every table a contact touches; verify by query, not
   assumption. No cold-outreach feature may bypass consent and opt-out
   requirements.

## PRODUCT CAPABILITIES (built, Phases 1–14)

1. Multi-tenant foundation: Organization tenancy, roles, Meta + Google
   OAuth, encrypted token storage, account/campaign browser.
2. Core ad management: real write access (create/edit/pause campaigns,
   ad sets, ads) on Meta and Google behind the confirmation guardrail.
3. Advanced metrics: computed metrics layer plus UTM-vs-platform
   attribution reconciliation.
4. Customizable UI: per-user dashboard/view preferences.
5. Server-side conversion tracking: Meta CAPI + Google Enhanced
   Conversions.
6. Salescale CRM: leads from Meta Instant Forms, Google lead forms, and
   landing pages flow in with attribution (UTM + click ID) attached;
   pipeline with Organization-configurable stages and qualified-lead
   criteria.
7. Additional platform adapters (Snapchat, Reddit, LinkedIn, Microsoft
   Advertising, Nextdoor) — adapter code per build order; live status
   depends on each platform's developer-access approvals (external).
8. Stripe billing + self-serve onboarding, flat tiers
   (Starter/Pro/Agency) — built, awaiting live activation.
9. White-labeling (custom domain, branding, zero-vendor-branding audit,
   branded transactional email) + AI insights on the Claude API.
10. Client trust: call tracking attributed to campaign/UTM, composite
    account health score, client creative-approval workflow, NPS,
    GDPR/CCPA export and deletion.
11. Full SaaS design-system pass (delivered via the security-audit +
    UI-modernization effort; treat as complete).
12. Lead Finder & email verification: Google Places business search by
    vertical + geography with per-org monthly metering, own-site /
    BYO-provider enrichment, verification pipeline with
    `verification_status` on contacts and a shared outreach gate, plus
    the verified-email action gate on invites/connections.
13. Teams & seats: invite flow, multi-org memberships + switcher,
    tier-gated seats, membership audit events.
14. Custom CRM fields: per-Organization typed field definitions, JSONB
    values with GIN indexing, filtering/sorting, CSV import mapping,
    per-field client visibility. Plus the house CRM (org-level prospect
    pipeline) as a post-14 addition.

Related module, specced separately (not a numbered phase): the
**Outreach** module — fully automated Instagram outreach on the
official Meta Graph API (trigger engine, sequence engine,
messaging-window awareness, unified inbox, CRM sync) plus a LinkedIn
assisted-send queue (full LinkedIn automation is out of scope — ToS).
Build proceeds against dev-mode API; go-live gates on Meta App Review.

## REMAINING WORK

All numbered phases (1–14) are built. What's left, in order: Stripe
live activation + the entitlement flip, the Outreach module build
(dev-mode now, go-live gated on Meta App Review), and the release gate
— see the unchecked items at the bottom of STATUS.

## STATUS

- [x] Phase 1 — Foundation
- [x] Phase 2 — Core management features
- [x] Phase 3 — Advanced metrics layer
- [x] Phase 4 — Customizable UI
- [x] Phase 5 — Server-side conversion tracking (CAPI + Google)
- [x] Phase 6 — Salescale CRM
- [x] Phase 7 — Additional platform adapters (code; platform approvals
      pending externally)
- [x] Phase 8 — Billing & onboarding (built — NOT live; stubs active)
- [x] Phase 9 — White-labeling & AI insights
- [x] Phase 10 — Call tracking, account health & client trust
- [x] Phase 11 — Design system pass
- [x] Phase 13 — Team members, invites & seats: email invites with
      hashed single-use tokens, multi-org memberships + org switcher,
      seat metering (pending invites reserve seats; accept re-checks),
      last-Owner protection + explicit ownership transfer, member
      removal with immediate session kill + open-task reassignment,
      membership audit log. Notes: membership truth lives in
      organization_memberships; User.organization_id/role is the
      ACTIVE-org mirror (services/team.py owns that invariant — keep
      every membership change going through it). Invite-signup marks
      email_verified (token possession proves the inbox). Isolation on
      the new tables is the app-layer TenantScope pattern like every
      other table; Postgres RLS stays globally deferred (HANDOFF.md).
- [x] Phase 14 — Custom CRM fields: per-Organization typed field
      definitions (text/number/select/multi_select/date/boolean/url) on a
      single `custom_fields` JSONB bag on contacts, GIN-indexed
      (jsonb_path_ops, Postgres-only; plain JSON on SQLite dev/test).
      Validation/coercion, collision protection (reserved-key list next
      to the Contact model), rename-is-label-only, archive-vs-hard-delete
      (delete scrubs the key from every contact's JSONB in a FastAPI
      background task), select-option remap-or-keep (409 → prompt), and
      per-org active-definition cap through the entitlement stub
      (`custom_fields` tier limit + hard ceiling 100). All value writes go
      through services/custom_fields.validate_and_merge — the single
      data-access layer for the API, the contact form, and CSV import.
      `visible_to_clients` (default false) filters values out of
      client-role reads/filters at the data layer. Filtering & sorting
      join the contact list query (build_filter_clauses/build_sort);
      per-user list-column choice reuses the Phase 4 preference pattern
      (crm_list_preferences table + /api/dashboard/crm-columns). CSV
      import (/api/crm/contacts/import) maps columns → system/custom
      targets with inline field creation and per-row error reporting.
      Frontend: FieldManager in CRM setup, custom-field render/edit in the
      contact drawer + new-contact form, custom columns + filter bar +
      CSV import dialog in the lead list (src/crm_custom.tsx).
- [x] House CRM (post-Phase-14 addition): the agency's own prospect
      pipeline, surfaced as a top-level team-only "CRM" nav item. Modeled
      as one synthetic Client row per org flagged `clients.is_house`
      (migration d4e8f2a9b1c3, partial unique index — one per org), so
      the entire existing CRM (board, contacts, deals, custom fields,
      CSV import) runs against it unchanged. GET /api/orgs/me/house-client
      (require_team) gets-or-creates it, race-safe via the unique index +
      IntegrityError re-read. House clients are excluded from the client
      roster (api/clients), the client-cap entitlement count, and all
      admin counts/lists; they never have a portal user, so client-role
      access is impossible through TenantScope's client pin. Frontend:
      "crm" tab in App.tsx (Workspace section, show: isTeam) lazily
      resolves the house id on first open and mounts the existing CrmView.
      Tests: backend/tests/test_house_crm.py. Feeds the Outreach module
      and Phase 12 Lead Finder (house CRM is where found leads land).
- [x] Phase 12 — Lead Finder & email verification: Google Places (New)
      Text Search behind /api/lead-finder (services/places.py, explicit
      FieldMask — request only displayed/stored fields; per Google's
      caching policy only place IDs + query text are ever stored, never
      result payloads). Per-org MONTHLY metering for searches and
      verifications via two ledger tables (lead_finder_searches,
      email_verifications — the AiUsage pattern), TIER_LIMITS entries +
      usage/enforce pairs in services/entitlements.py, self-service view
      at /api/lead-finder/usage. Import creates org-scoped contacts
      (source=lead_finder, source_external_id=place_id → idempotent
      re-import, search query kept on source_detail for attribution) plus
      linked Company rows; dedupe marking is org-wide on normalized phone
      digits / website domain / casefolded name (lead_finder.OrgCrmIndex
      → "already in your CRM" inline, never silent skips). Enrichment
      (services/enrichment.py): polite own-site crawler (robots.txt,
      honest UA, ≤5 conventional contact paths, fast timeouts, kill
      switch) + EnrichmentProvider adapter, Hunter reference impl — BYO
      org key ONLY, no operator fallback (their ToS). Verification
      (services/email_verification.py): VerificationProvider adapter,
      ZeroBounce batch reference impl, NullProvider→unknown when no key;
      contacts gained verification_status/verified_at/candidate_emails
      (migration f7a2c8d4e9b1); changing a contact's email resets the
      verdict; runs automatically post-import (BackgroundTasks
      enrich→verify, quota-respecting), as a CSV-import bulk action
      (verify flag) and via POST /api/crm/contacts/verify. THE OUTREACH
      GATE: email_verification.sendable()/assert_can_email() is the one
      shared check — every future email-send feature routes audiences
      through it (invalid excluded, risky warned), never re-implements.
      BYO keys (google_places/zerobounce/hunter) in IntegrationCredential
      via /api/lead-finder/providers, write-only + encrypted, resolved
      key-first with operator env fallback (integration_creds.resolve_key
      / KEY_PROVIDERS). Part D existed from Phases 1/13 (24h token,
      rate-limited resend, login gate); added require_verified_email on
      invite send / member add / connect starts — active when
      require_email_verification is on, closing the sessions-issued-
      before-the-flip hole. Frontend: Lead Finder tab (leadfinder.tsx,
      team-only, Workspace section; imports land in the house CRM),
      verification badge + column + filter in the CRM lead list, drawer
      verify/re-verify button, CSV-import verify checkbox. Tests:
      backend/tests/test_lead_finder.py + gate test in test_auth_email.py
      (Lead Finder tests use their own org — the seeded Atlas Reach org's
      contact counts feed the metrics suite's assertions). Guardrail 6
      holds: licensed Places API, the business's own site, BYO providers —
      zero Meta-surface contact anywhere in the pipeline.
- [x] Connect-flow hardening (post-12 fix session): the Google MCC /
      Meta Business Manager connect no longer dumps every visible ad
      account onto the one client being connected. Discovery is decoupled
      from attachment (services/ad_accounts.py): the OAuth callback
      auto-attaches only when exactly ONE new account is visible;
      otherwise nothing attaches and the Admin assigns accounts in the
      "Manage accounts" picker on the client's connections card
      (GET/POST /api/connect/{platform}/accounts — live discovery, never
      cached; same-org attachments annotated by client, other-org
      attachments shown unavailable and never named). PATCH
      /api/ad-accounts/{id} moves an account between clients and cascades
      the denormalized client_id (cached campaign hierarchy, PENDING
      changes only — executed ones are history, insight rows via
      hierarchy external ids, quality snapshots incl. best-effort live
      asset-group resolution) — the repair path for accounts that landed
      on the wrong client. Both callbacks now handle user-cancel
      (?error=..., missing code) and platform API failures with a
      branded error page instead of a 422/500; same-org already-attached
      accounts are skipped rather than 409-aborting the whole connect
      (cross-org still refuses). Attach/reassign write audit_log entries
      (entity_type=ad_account). Frontend: AccountPicker dialog
      (components/AccountPicker.tsx) with checkbox attach + "Move here",
      wired into ClientDetail. Tests: backend/tests/test_connect_flows.py
      (dedicated Connect Co org — the isolation/metrics suites assert
      over Atlas Reach's account counts).
- [ ] Stripe live activation + entitlement flip (after 12–14, so real
      limits land everywhere in one pass)
- [ ] Outreach module build (dev-mode) — go-live gated on Meta App
      Review (external clock)
- [ ] Release gate: RLS audit on all new tables, live-card billing test
      end-to-end, one full Atlas Reach dogfood week
      (scrape → verify → outreach → CRM → campaign)

## PHASE FILES

All numbered phase files (1–14) are complete. They and `PLATFORMS.md`
remain in the repo as reference for the patterns they established.
Remaining work is the unnumbered items above: Stripe live activation +
entitlement flip, the Outreach module build (gated on Meta App Review),
and the release gate.

## BEFORE FINISHING ANY SESSION

Update this file's STATUS section to reflect what was completed, note
anything left half-done explicitly rather than silently, and commit.