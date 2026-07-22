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
- [x] Auth/team fix session (post-12): (1) Social sign-in failures no
      longer dead-end — the callback handles user-cancel, exchange
      failures, and the existing-password-account 409 by redirecting to
      the login screen with ?login_error=<reason> (Login shows it once
      and strips the URL). The classic "Error 400: redirect_uri_mismatch"
      is an OAuth-app registration gap: connect and sign-in use DIFFERENT
      callback paths on the same app; GET /api/integrations/redirect-uris
      + the card at the top of the Integrations page now list all four
      URIs verbatim with copy buttons so operators can register them.
      (2) Invites work without an email transport: send/resend responses
      carry invite_link when delivery isn't configured/failed (dev,
      desktop) so the Admin shares it out-of-band — shown once in the
      Team UI (adm-secret pattern), never stored, never in list responses
      (DB keeps only the token hash). (3) Network-level platform failures
      (DNS/refused/timeout) normalize into MetaApiError / GoogleApiError /
      PlacesError inside the service layer, so an unreachable API surfaces
      through existing handling instead of a bare 500. Tests in
      test_teams_invites / test_social_auth / test_integrations /
      test_connect_flows.
- [x] CRM completeness + house outreach + live-refresh fix (post-deploy
      session): (1) Contact gained city/state columns (migration
      a4e1c9d3f27b, nullable — prod-safe) and a company_name read/write
      field resolved from Company (get-or-create org-scoped,
      case-insensitive, services/crm.get_or_create_company; batch
      resolution avoids N+1). CSV import targets extended with
      city/state/company/full_name (full_name splits on first
      whitespace server-side, explicit first/last columns win; per-row
      in-request company dedupe). Import dialog auto-detects
      First/Last/Full name, Email, Phone, City, State, Business name
      via a normalized-header synonym table, and accepts JSON files
      (top-level array, or first array under
      contacts/leads/rows/data/records). (2) Lead deletion: DELETE
      /api/crm/contacts/{id} + POST /api/crm/contacts/bulk-delete
      (require_admin, ≤500 ids, cross-org ids silently skipped),
      cascade via services/crm.delete_contact (activities, tasks, tags,
      deals; detaches historical landing/conversion/verification/
      outreach refs), audit_log contact.deleted per row. Drawer got a
      two-step "Delete lead" confirm + an "Edit info" form
      (identity + city/state/business name — PATCH already existed);
      lead list got checkboxes + bulk-delete bar + City/State/Business
      name columns. Notes already existed (Activity timeline).
      (3) Outreach for the agency itself: the house client is now a
      selectable target in all three outreach pickers ("My agency
      (house CRM)", frontend-only — backend always accepted any
      org-scoped client_id); test proves prospect import + sequences
      under the house client. (4) The production "Live refresh failed
      (NetworkError)" on the campaign browser: GoogleApiError /
      MetaApiError / PlacesError escaping a router became bare 500s
      that bypass CORSMiddleware, which browsers report as an opaque
      NetworkError — main.py now registers global 502 handlers with a
      readable detail; Google Ads reads got a 45s per-RPC deadline
      (READ_TIMEOUT_SECONDS — the library default is ~unbounded, so a
      stalled gRPC call outlived the browser fetch); frontend api()
      got a 75s AbortSignal.timeout. Tests 283 → 292
      (test_crm_contacts.py with dedicated cc_org fixtures,
      test_platform_error_surfacing.py, house-outreach test in
      test_outreach.py). Deployed to the VPS (commit 6facf9a).
- [x] Cold-email Outreach module (agency-level, BYO mailbox): a full
      SMTP/IMAP send+receive module parallel to the IG Outreach module,
      gated the same way (require_team read, require_admin config,
      client role fully locked out). Every org connects its OWN mailbox
      (IMAP+SMTP creds, Fernet-encrypted, never returned to the client)
      — Salescale's own domain never appears as a sender; Atlas Reach's
      reference deploy is a self-hosted docker-mailserver on the VPS for
      carter@atlasreach.io (deploy/MAILSERVER.md, deploy/docker-compose.
      mailserver.yml — isolated compose project, Rspamd tuned so replies
      aren't lost, ClamAV off to fit the box's RAM). Models/migrations
      (backend/app/models/email_outreach.py, b1e7d4c9a025 +
      c2f8e5a1b307): EmailAccount, EmailCampaign, EmailStep,
      EmailEnrollment, EmailThread, EmailMessage (the append-only send
      ledger — audit trail + open/unsubscribe tokens), EmailSuppression,
      EmailWarmupPeer. services/email_transport.py wraps stdlib smtplib/
      imaplib with a hard wall-clock deadline (_run_with_deadline) around
      every connect+auth attempt — socket `timeout=` bounds connect()/
      recv() but NOT getaddrinfo(), so an unreachable/mistyped host would
      otherwise hang a request-handling thread forever; found via live
      browser verification, not the mocked test suite, and is now itself
      regression-tested. services/email_outreach_send.send() is the ONE
      gateway (mirrors services/outreach_send.py): ordered guards
      (account active → org suppression → email_verification.
      assert_can_email, both skipped for kind="warmup" → warmup-ramped
      daily cap) → CAN-SPAM footer + List-Unsubscribe/One-Click headers +
      per-message tokens → stdlib MIME send → EmailMessage + EmailThread
      upsert. services/email_campaigns.py is the enrollment engine
      (mirrors services/outreach_sequences.py): send-window/day gating
      in the campaign's own timezone (zoneinfo), campaign + account daily
      caps, stop-on-reply/bounce/unsubscribe (unsubscribe exits ALL of a
      contact's active campaigns org-wide, not just the current thread —
      compliance guardrail #9). services/email_personalize.py renders
      {{first_name|fallback}}-style tokens plus a Claude-generated
      {{ai_snippet}} grounded ONLY in the contact/org's own CRM data
      (guardrail #7; metered via AiUsage feature="outreach_personalize",
      cached on EmailEnrollment.ai_snippets so re-render never re-bills;
      any AI failure renders empty and never blocks a send).
      services/email_warmup.py ramps effective_daily_cap over a 4-week
      schedule and runs synthetic warmup sends between an org's OWN
      warmup-enabled mailboxes (never cross-tenant) tagged
      X-Salescale-Warmup so the IMAP sync routes them out of the real
      inbox. entitlements.py gained an email_sends tier limit (starter
      1,000/mo, pro 10,000, agency 100,000). Frontend: a team-only
      "Email" nav item (frontend/src/email_outreach.tsx) with Dashboard
      (KPIs incl. the 5%-bounce deliverability red line, per-day volume,
      mailbox health strip), Campaigns (config + step editor + preview +
      house-CRM enroll with a risky/skipped receipt), Inbox, Accounts
      (connect/test/warmup toggle), Suppression. Tests 292 → 334
      (test_email_outreach.py, test_email_campaigns.py, dedicated
      ce_org/cc_org fixtures). Click-through verified live on the alt-dev
      stack (account connect, campaign create/steps/preview/enroll/
      activate, inbox, suppression) — surfaced and fixed 4 bugs the
      mocked tests couldn't catch: the DNS-hang transport issue above;
      GET/detail campaign serialization nested stats under a "stats" key
      instead of the flat fields the frontend/analytics contract used
      (backend/app/api/email_outreach.py _campaign_out); the step editor
      saved 0-indexed positions against a backend that requires 1-indexed
      (matching EmailEnrollment.current_position's convention) — fixed
      in the frontend's saveSteps/previewEmailStep call sites; the
      Audience tab's enrollment list didn't re-fetch after a fresh enroll
      (its effect only keyed off campaign.id, not the enrolled count).
      Follow-up fix (same session, before any deploy): EmailAccount
      originally had ONE shared username/password for both SMTP and IMAP —
      broken for the real intended setup (Amazon SES SMTP credentials to
      send, a self-hosted mail.atlasreach.io IMAP mailbox to receive
      replies, which are necessarily different logins). Split into
      smtp_username/smtp_password_encrypted +
      imap_username/imap_password_encrypted throughout (model, the
      unreleased migration edited in place, email_transport.py,
      schemas, the account router, tests). Frontend connect dialog now has
      independent SMTP/IMAP credential fields behind a "Same login as
      SMTP" checkbox (checked by default — most orgs use one mailbox for
      both legs) plus an SES host hint. Re-verified live: the SMTP leg
      actually reached the real email-smtp.us-east-1.amazonaws.com:587 and
      got a proper 535 auth rejection on fake IAM creds, proving the SES
      path end-to-end; the IMAP leg to a not-yet-provisioned host timed
      out safely at 25s instead of hanging, and the backend stayed
      responsive to other requests throughout. Tests still 334 passing.
      DEPLOYED to production (2026-07-12): migrations b1e7d4c9a025 +
      c2f8e5a1b307 applied cleanly on the live Supabase DB (all 8 email_*
      tables + split-credential columns verified present), backend/
      frontend images rebuilt and recreated on the VPS, /api/email-
      outreach/* routes live and auth-gated (401 without a token), the
      background scheduler (email_campaigns.run_due + email_warmup.run_due
      + email_outreach_sync.sync_due) running on its 60s tick. NOTE:
      backend/.env has no ANTHROPIC_API_KEY, so {{ai_snippet}}
      personalization is silently disabled (renders empty, never blocks a
      send) until a key is added — every other send path works without it.
      Reaching Elastic Email SMTP (smtp.elasticemail.com:2525/587) and the
      mail.atlasreach.io:993 IMAP hairpin both verified from INSIDE the
      prod backend container. Remaining before a live send: Elastic domain
      verification (user-side DNS: merge SPF include, add DKIM/tracking/
      DMARC + the mail-subdomain MX at Porkbun) and the user connecting the
      mailbox in the Email → Accounts dialog.
- [x] VPS mail server bring-up (receive leg, LIVE): docker-mailserver
      14.0.0 running on the VPS as the isolated "mailserver" compose
      project, hosting carter@mail.atlasreach.io — receive-only; sending
      goes through the org's ESP (Elastic Email for Atlas Reach).
      Verified end-to-end: external SMTP→port 25→Rspamd→Dovecot INBOX,
      then read back over external IMAPS 993 with the real LE cert.
      Hard-won specifics (all in deploy/MAILSERVER.md + compose
      comments): TLS comes from the pre-existing Traefik's acme.json —
      a mail-cert-helper whoami router in docker-compose.traefik.yml
      triggers issuance, and ONLY the acme.json FILE may be bind-mounted
      :ro (DMS writes extracted certs to /etc/letsencrypt/live/);
      hostname==mail domain puts the host in $mydestination and 550s
      every virtual mailbox — config/user-patches.sh (the guaranteed
      end-of-boot hook; a postfix-main.cf override alone did NOT apply)
      pins mydestination to localhost only, verified persistent across
      recreate; ENABLE_AMAVIS/SPAMASSASSIN must be 0 — the DMS default
      runs them alongside Rspamd and Amavis quarantines-and-BOUNCES
      inbound mail (observed live on a header check; bouncing prospect
      replies is the one unacceptable failure mode), Rspamd tags-to-Junk
      instead; POSTMASTER_ADDRESS points at the hosted mailbox, never
      the bare domain (Porkbun forwarder bounces). Postfix rejects
      null-MX sender domains — test with a real sender domain.
      Bare-domain MX stays Porkbun forwarding forever; only the mail.*
      subdomain is this box's. Remaining, user-side: Porkbun MX record
      for the mail subdomain, Elastic Email domain verification (merge
      SPF include into the EXISTING bare-domain TXT — a second SPF
      record is a permerror), then connect the mailbox in Salescale
      (needs the cold-email module deployed to prod first).
- [x] Multi-provider AI (post-cold-email): grounded insights AND cold-email
      personalization now route their single model call through one dispatch
      (services/ai_provider.complete) that speaks Anthropic (default), OpenAI,
      or Gemini. Provider is operator-selected via settings.ai_provider, each
      with its own key + default model (openai_model gpt-4o, gemini_model
      gemini-2.0-flash — env-overridable); per-provider SDKs import lazily so a
      missing package only errors when that provider is actually selected, and
      personalization still fails open on any error. An Organization may BYO its
      own key for the active provider (IntegrationCredential, resolved BYO-first
      with the operator env key as fallback — anthropic/openai/gemini added to
      integration_creds.KEY_PROVIDERS, so the existing /api/lead-finder/
      providers endpoint stores them). Grounding is unchanged and still done by
      the caller before dispatch, so tenant isolation (guardrail #7) holds for
      every provider; AiUsage metering records the resolved model and prices it
      from ai_provider.PRICING (extended for the OpenAI/Gemini models). The
      call seam kept its (system, user_content, max_tokens) signature so the
      test suite's monkeypatch still applies — 334 tests still pass. NOTE on
      "OAuth from Claude": there is no consumer-OAuth path for a backend to call
      the Claude API on a user's behalf; server auth is an API key (or WIF), so
      personalization uses a server-held key per the BYO/operator resolution
      above. DEPLOYED to production (2026-07-12): backend/frontend rebuilt on
      the VPS with openai 2.45.0 + google-genai + anthropic 0.116.0 in the
      image; dispatch module loads and all three providers are selectable.
      Active provider stays anthropic until an operator sets AI_PROVIDER +
      the matching key in backend/.env.
- [x] 2FA "remember this device" (post-multi-provider-AI): a login on a
      device that recently passed a 2FA challenge can skip the challenge on
      future logins for settings.mfa_remember_device_days (default 30).
      New TrustedDevice model (migration a8f2c4d9e6b3): user_id + sha256
      token_hash (unique, indexed — same lookup-by-hash pattern models/
      team.py uses for invite tokens; O(1) lookup instead of a bcrypt scan
      since this is checked on every login) + user_agent/ip/expires_at/
      last_used_at/revoked. services/trusted_devices.py mirrors services/
      sessions.py's shape (remember/verify/list_for_user/revoke_one/
      revoke_all). Wire-up in api/auth.py: POST /login checks an incoming
      X-Device-Token header BEFORE issuing a challenge — a live grant (org
      policy permitting) skips straight to session creation, same as the
      no-MFA path; POST /login/mfa takes remember_device: bool and, on a
      successful code, mints a grant and returns its raw token once as
      TokenResponse.device_token (never re-derivable from the stored hash).
      Explicitly listable/revocable (GET/DELETE /api/auth/trusted-devices),
      and wiped alongside every other account-wide credential reset —
      logout-all, password reset, and MFA disable — since a device-trust
      grant is itself a standing credential. New org policy column
      Organization.allow_remember_device (default true; PUT /api/orgs/me/
      allow-remember-device, owner-only) — an agency handling compliance-
      sensitive clients can force a 2FA code on every login regardless of
      device. Frontend: a "Remember this device for 30 days" checkbox on
      the 2FA challenge screen (App.tsx Login); the device token lives in
      localStorage under its OWN key ("device_token"), separate from
      "session" — a plain sign-out clears the session but deliberately
      leaves the device token in place (that's the whole point: logging
      back in on the same browser should skip the challenge), while
      "log out everywhere" clears it locally AND revokes every grant
      server-side. New "Remembered devices" panel in security.tsx (mirrors
      the existing Active-sessions panel: list + per-device "Forget"), plus
      the org-policy toggle alongside "Require two-factor for all team
      members". Tests: 4 new cases in test_mfa.py (skip-on-valid-token,
      revoke, wiped by logout-all/disable, org-policy-off suppresses
      minting) — 334 → 338 passing. Verified live end-to-end in the browser
      on the alt3 stack (a real TOTP enroll hit a pre-existing, unrelated
      dev-env gap — TOKEN_ENCRYPTION_KEY unset on that throwaway stack 500s
      any Fernet-encrypted-secret path — so verification used email 2FA
      instead, which needs no Fernet key): logged in, checked "remember this
      device" at the challenge, confirmed the device_token landed in
      localStorage and the row appeared correctly in the Remembered-devices
      panel (right IP/UA/expiry), then did a NORMAL sign-out + fresh
      email+password login and watched it go straight into the app with
      zero 2FA prompt. DEPLOYED to production (2026-07-12), migration
      applied cleanly to the live Supabase DB alongside the Lead Finder
      profile-enrichment deploy below.
- [x] Lead Finder profile enrichment (post-2FA session): the in-house
      lead pipeline now fills owner name/title, owner direct/mobile line,
      work email, company description, estimated annual revenue and
      headcount — WITHOUT scraping (guardrail 6 holds). Two sources:
      (1) the business's own site's meta/og description
      (enrichment.discover_site_description, same polite single-page
      crawler posture), and (2) a new ProfileProvider adapter tier in
      services/enrichment.py — ApolloProvider reference impl (org
      enrichment + owner-title people search + match), BYO org key ONLY
      ("apollo" in KEY_PROVIDERS, no operator fallback — people-data
      ToS). Mobile/revenue physically cannot come from crawling; a
      licensed provider is the only compliant source, so without a key
      those fields simply stay empty. Schema (migration b6d1f3a8c5e2):
      contacts.mobile_phone + companies.description/estimated_revenue/
      employee_count; RESERVED_CONTACT_FIELD_KEYS extended.
      enrich_and_verify applies profiles fill-blanks-only (a typed-in
      name/number is never overwritten; the business-name placeholder in
      first_name IS replaced by the real owner), owner email lands as
      the TOP candidate (source provider:apollo) and still goes through
      the verification gate. Serialization: mobile + company_* fields
      are ContactOutTeam-only (never client-portal); _company_names
      batches the firmographics. PATCH/POST contacts accept
      mobile_phone. Frontend: Lead Finder gained the previously-missing
      BYO provider-keys card (admin-only, google_places/apollo/hunter/
      zerobounce — the endpoints existed since Phase 12, no UI did);
      drawer shows mobile/firmographics/description + Edit info gained
      Mobile; lead list gained Mobile + Est. revenue optional columns.
      Tests 338 → 342. Verified live on alt2 (key save flow, drawer
      render, mobile PATCH round-trip). DEPLOYED to production
      (2026-07-12): migrations a8f2c4d9e6b3 (2FA remember-device, above)
      + b6d1f3a8c5e2 applied cleanly to the live Supabase DB, backend/
      frontend rebuilt on the VPS, /api/lead-finder/providers confirmed
      live (401 without a token, not 404). NOTE: a Fernet key generated
      for local alt2 verification was briefly committed into
      .claude/launch.json — caught before push by the auto-mode
      classifier, scrubbed from history via amend since the branch had
      never been pushed. `feature/ui-revamp` still has no configured
      push credential on this Mac (no gh CLI, no HTTPS credential
      helper, SSH key not registered on GitHub) — the branch is deployed
      but not yet backed up to GitHub; push it manually when convenient.
- [x] Pitch-target position + SPA cache fix (same-day follow-up):
      (1) contacts.job_title (migration c9e4a7b2d8f1, nullable) — the
      decision-maker's role, previously buried in source_detail.owner_title,
      is now a first-class field: filled by enrichment fill-blanks-only,
      editable in the drawer (Position), CSV-import target with header
      auto-detect (title/position/role/designation), optional lead-list
      column, and it leads the drawer identity line ("Owner · Desert Air
      HVAC · Scottsdale, AZ"). OWNER_TITLES extended with marketing
      leadership (CMO/VP marketing/marketing director/manager/GM) and
      Apollo people results are re-ranked by that priority order
      (enrichment._rank_pitch_target) instead of trusting provider order.
      Tests 342 → 343. (2) frontend/nginx.conf: index.html had NO
      Cache-Control, so browsers heuristically cached the SPA document and
      kept users on pre-deploy bundles for hours (surfaced as "provider-key
      entry not working" — the card simply wasn't in the user's cached
      bundle; the feature itself was fine). `expires -1` on
      `location = /index.html` emits Cache-Control: no-cache without
      dropping the inherited security headers; hashed /assets stay
      immutable+1y. BOTH DEPLOYED to production (2026-07-12), migration
      applied cleanly, health green.
- [x] Email outreach: editable campaigns + functional warmup + personalization
      correctness (same-day session, research-driven — two Opus research agents
      mapped the module and compiled 2025–26 warmup vendor consensus first):
      (1) EDITABILITY — PUT /steps is now an UPSERT-IN-PLACE (EmailStepIn.id;
      ids kept → enrollment ai_snippets caches survive edits, in-flight
      enrollments continue at their position number) and works while ACTIVE
      (edits affect future sends only; two-pass negative-position parking
      avoids transient unique(campaign,position) violations). Activate gained
      a real status guard (draft/paused only); POST /campaigns/{id}/archive
      added (state existed, no setter — mailbox deletion was impossible).
      Step saves also validate personalization tokens (unknown → 422 listing
      them; custom.* checked against the org's field defs).
      (2) WARMUP — was fully inert (schemas dropped warmup_enabled, started_at
      never written, UI toggle no-oped). Now: EmailAccountPatch carries
      warmup_enabled/warmup_target_daily; disabled→enabled stamps
      warmup_started_at (re-enable restarts the ramp). Engine rewritten to the
      vendor consensus (services/email_warmup.py): warmup volume ramps 5 →
      min(40, target) linearly over 28 days, weekdays only, spread over an
      08–18 UTC window with deterministic ±25% hash jitter and peer ROTATION;
      after day 28 maintenance never stops (20% of cold cap, floor 10).
      ~35% of received warmup mail gets a THREADED auto-reply (strongest
      placement signal; deterministic on Message-ID hash, loop-safe via
      X-Salescale-Warmup-Depth < 2). IMAP sync gained warmup hygiene
      (email_transport.warmup_inbox_hygiene): junk-folder RESCUE of warmup
      mail (moved to INBOX, charged to the sender pair's junk_count) +
      mark-\Seen opens. EmailWarmupPeer gained sent/received/junk counters
      (migration d7f3b9c1e4a6). effective_daily_cap(account, db) halves while
      bounce_rate_7d > 2% (auto-throttle). TWO NUMBERS per industry
      convention: warmup_progress (deterministic 0–100, days/28) and
      warmup_health (100 − bounce/junk/delivery penalties, None till ≥5
      sends) — surfaced in _account_out + analytics accounts; frontend
      Accounts tab renders a 0–100% progress bar ("N% warmed · week X of 4" /
      "fully warmed") + health badge, dashboard strip shows "warmup N%".
      (3) PERSONALIZATION — values now smart-cased ("john"→"John",
      "o'brien"→"O'Brien", "MESA"→"Mesa", 2-letter states upper; mixed-case
      values and emails/custom.* untouched), a tidy pass removes emptied-token
      artifacts ("Denver, ." → "Denver.", doubled spaces, blank-line runs),
      and KNOWN_TOKENS/unknown_tokens() backs the save-time 422. Campaign
      editor shows a live-edit notice on active campaigns + a two-step
      Archive button. Tests 343 → 359 (test_email_warmup.py — NOTE it defines
      its own org fixture; importing test_email_campaigns' module-scoped
      cc_org would re-run the same signup and collide). Verified live on alt2:
      warmup toggle → started stamp → bar at 0%/50%/"week 3 of 4", ramped cap
      (0 of 20 / 0 of 60 today), live step edit on an active campaign with
      stable ids, archive → activate 409, and preview rendering
      "Hi John O'Brien … Mesa, AZ" from all-lowercase CRM data.
      DEPLOYED to production (2026-07-12): migration d7f3b9c1e4a6 applied
      cleanly to the live Supabase DB, health green. Deploy incident worth
      remembering: the first rollout CRASH-LOOPED (exit 3, traceback
      swallowed by stdout buffering) because ~30k macOS AppleDouble files
      (._*, 163-byte null-padded resource forks) had been scattered through
      ~/salescale on the VPS by a Mac-made `tar czf` tarball extracted there
      at 11:22 UTC (NOT by the git-archive deploy flow — those archives are
      clean; likely a manual re-extract of the old salescale-deploy.tar.gz).
      The image build baked them in and alembic parsed ._<migration>.py as
      Python → "source code string cannot contain null bytes". Fixed by
      purging ._*/.DS_Store on the VPS AND excluding both patterns in
      backend/ + frontend/.dockerignore so tree junk can never enter an
      image again. Never use plain macOS `tar czf` for anything that lands
      on the VPS — git archive only (or COPYFILE_DISABLE=1).
      SECOND casualty of the same 11:22 UTC extraction, surfaced as "login
      NetworkError": ~/salescale/backend/.env was OVERWRITTEN by the local
      dev copy (birth 11:22:19, then hand-edited 11:38), losing the
      prod-only lines FRONTEND_ORIGIN/API_BASE_URL. The next container
      recreate picked it up → CORSMiddleware no longer allowed
      app.salescale.lol → every response blocked by the browser (classic
      allow-credentials-without-allow-origin signature; diagnosis: 401
      probes with an Origin header, comparing endpoints). Restored both
      lines (backup kept as backend/.env.bak-<hhmm>) and recreated the
      backend; verified TOKEN_ENCRYPTION_KEY still decrypts stored
      IntegrationCredential rows (the clobbering file shared the same key,
      so no re-connects needed). Reminder: extraction of anything over
      ~/salescale MUST come from `git archive` (which contains no .env) —
      the deploy flow never touches .env by design.
- [x] 2FA-email fallback (same-day fix): real 2FA/reset/invite mail from
      Atlas Reach silently died because resolve_sender returns the branded
      sales@atlasreach.io and Resend 403s unverified domains (send_email
      never raises by design — the failure was invisible). services/email.py
      send_email now retries ONCE with the platform default sender when the
      branded sender fails; EmailLog records whichever sender actually
      delivered. All five call sites are account-lifecycle mail, never
      client-facing branded surfaces, so the fallback can't leak vendor
      branding into white-labeled client mail. Tests 362 (3 new in
      test_email_delivery.py). DEPLOYED to production (2026-07-12, ef2e35f).
      Permanent fix is user-side: verify atlasreach.io at resend.com/domains,
      after which the branded sender just works and the fallback never fires.
- [x] Lead Finder filters + >20 results (same-day session): Text Search
      pagination lands — places.search_text now takes min_rating/open_now/
      page_token and returns (results, next_page_token); FieldMask includes
      nextPageToken (IDs-Only SKU — doesn't raise the billed tier). Search
      accepts max_results 20/40/60 (Google's documented ceiling is 60 across
      3 pages) and loops pages server-side with cross-page place-id dedupe;
      a mid-pagination Places failure keeps already-billed pages instead of
      502-ing. METERING IS NOW PER PAGE: each page is a separately billed
      Google request, so lead_finder_searches gained pages_fetched
      (migration e5a9c2f7b4d8) and lead_finder_usage sums it — a 60-result
      search honestly costs 3 of the monthly quota, the UI select says so
      ("60 results · 3 searches"), and when the remaining quota can't cover
      the request the search clamps to what's left and returns
      quota_clamped=true (surfaced as a "Partial results" alert) instead of
      402-ing. Server-side filters: minRating (0.5-cadence snap) + openNow,
      page-invariant per Google's pageToken rules. Frontend (leadfinder.tsx):
      results-count + min-rating selects on the search form, and Lead
      Finder's OWN client-side filter bar over results — category (from
      result types), rating, has phone, has website, hide already-in-CRM —
      with filtered counts ("N results (of M)") and filter-aware select-all;
      import cap raised to 60. places.py gained a PLACES_TEXT_SEARCH_URL env
      override (local stub verification only, never set in deployments) —
      verified live on alt2 against a 2-page mock: 40-result search fetched
      2 pages → usage 2/40, 4.0+ server filter returned only ≥4.0 results,
      filter bar + clear-filters behavior correct. Tests 362 → 366.
      DEPLOYED to production (2026-07-12, 79ca196): migration e5a9c2f7b4d8
      applied to the live Supabase DB (alembic current = e5a9c2f7b4d8 head),
      backend/frontend rebuilt, /api/health ok, search endpoint live and
      auth-gated. Deploy-runbook correction: the compose file on the VPS is
      deploy/docker-compose.traefik.yml (under the deploy/ subdir), not
      repo-root docker-compose.traefik.yml.
- [x] Warmup tab + research-tuned fast-warmup strategy (same-day session,
      two-Opus-agent research pass first — vendor consensus + provider-side
      reputation physics): (1) NEW dedicated "Warmup" tab in the Email module
      (admin-only) — one card per mailbox with a proper toggle Switch (new
      shared primitive components/ui.tsx Switch + .switch styles in ui.css,
      visually-hidden-label support), hero progress bar, week/stage line,
      health badge, "Warmup today X of Y", ramped-cap line, lifetime
      exchanged counters (sent/confirmed/junk-rescued from EmailWarmupPeer,
      new email_warmup.warmup_totals), and a warmup_target_daily inline
      editor. Accounts cards slimmed to a one-line warmup summary + "Manage
      warmup" link (panel switch via onGoWarmup prop). _account_out gained
      warmup_volume_today / warmup_sends_today / warmup_totals /
      warmup_blended_ready. (2) ENGINE updates encoding the research: weekend
      warmup volume is now WEEKEND_RATIO (40%) of the weekday figure instead
      of zero — the hard weekday/weekend cliff reads as scripted and a full
      stop "resets momentum" (run_warmup_tick's weekday gate removed; the
      budget itself is weekend-reduced); BLENDED_READY_DAY=10 +
      warmup_blended_ready() — from day 10 the UI badges "blended-ready —
      start low-volume real sends", per the strongest research finding:
      post-MPP, real replies from real prospects are the signal providers
      still fully credit, so low-volume real sending from ~day 10 warms
      FASTER than warmup-only to 100%. Research verdicts worth keeping:
      21-day floor/28-day target is corroborated across vendors (14-day
      claims are marketing); never double volume day-over-day (Gmail
      421-4.7.28 throttling); scale horizontally (2–3 inboxes/domain,
      20–50/day each) not per-inbox; domain age ~2–4 weeks is unbuyable
      wall-clock (SEM-FRESH blocklists); Google Postmaster v2 (Oct 2025)
      killed reputation tiers → binary compliance + spam rate; warmup POOLS
      are being actively neutralized by Google (GMass shut down, Apollo
      dropped fake engagement) — our same-org peer exchange is volume pacing
      + threaded replies, not a cross-tenant pool, and stays. Tests 366 → 367
      (weekend-reduction + blended-ready cases; weekend tick test now expects
      sends). Verified live on alt2 ON A SUNDAY: budgets showed 2 and 9
      (exactly 40% of weekday 5/22), blended-ready badge on the day-14
      mailbox, switch toggles round-trip with toasts, re-enable restarts ramp
      at day 1, Accounts summary line + Manage warmup link render.
- [x] SMS outreach module (agency-level, BYO Twilio, opted-in numbers only):
      a full SMS send module parallel to the cold-email module, gated the same
      way (require_team read, require_admin config, client role locked out).
      COMPLIANCE-FIRST by design — SMS carries TCPA statutory damages per text,
      so the framework enforces consent before volume. Models/migration
      (backend/app/models/sms_outreach.py, f3c7d9e2a1b5): SmsAccount,
      SmsCampaign, SmsStep, SmsEnrollment, SmsMessage (append-only send ledger
      + monthly meter), SmsSuppression; plus contacts gained
      sms_opt_in/at/source (the TCPA consent record — a phone number alone is
      never enough; the where/when is the proof). THE CONSENT GATE:
      services/sms_consent.assert_can_sms/sendable is the one shared check
      every send routes through (requires recorded opt-in AND not suppressed;
      STOP always beats opt-in) — plus E.164 normalize_phone so a formatted
      number and its canonical form can't diverge in the suppression ledger.
      THE SEND GATEWAY: services/sms_send.send() is the ONE choke point
      (ordered guards: account active → suppression → consent gate → quiet
      hours [TCPA 8am–9pm recipient-local via the campaign send window,
      default 11–20 America/New_York keeps continental-US inside the window] →
      account + campaign daily caps); thin httpx Twilio client (no SDK, BYO
      creds Fernet-encrypted + never serialized), Twilio 21610 → carrier
      suppression row so our ledger converges with Twilio Advanced Opt-Out,
      CTIA sender-id + "Reply STOP to opt out" appended to step 1 only.
      services/sms_campaigns.py is the enrollment engine (mirrors
      email_campaigns): enroll buckets every contact through the consent gate
      (skipped: no_number/no_consent/suppressed/already/duplicate/not_found),
      run_due advances/completes/window-defers, STOP/reply exits handled in
      the inbound webhook (synchronous, not a sync tick). Public
      signature-validated Twilio webhooks (api/sms_webhooks.py): inbound
      STOP/HELP/reply — a STOP suppresses the number, clears sms_opt_in on
      every matching contact, and exits ALL of the contact's active SMS
      enrollments org-wide (compliance guardrail #9); plus delivery-status
      callbacks. entitlements.sms_sends tier limit (starter 500 / pro 5000 /
      agency 50000) with usage/enforce pair. CONTACT/LEAD INTEGRATION (the
      requested piece): CSV import carries consent — a whole-file
      sms_opt_in_all "these all opted in on our website" attestation OR a
      per-column sms_opt_in target (truthy cells), each recording
      source="csv_import:website_attested"; manual opt-in/revoke on contact
      create/patch; consent fields are ContactOutTeam-only. Frontend
      (src/sms_outreach.tsx, team-only "SMS" nav item): Dashboard (KPIs +
      leading "only opted-in contacts are textable" banner), Campaigns
      (config + step editor w/ char/segment counter + preview + house-CRM
      enroll with a skipped-bucket receipt), Messages (per-contact
      conversation view — SMS has no threads), Accounts (Twilio connect:
      Account SID + auth token + from-number-OR-messaging-service, test
      button, A2P 10DLC reminder, and each card shows the two per-account
      webhook URLs with copy buttons — inbound is REQUIRED for STOP handling),
      Suppression. Framework built by the primary session; engine + campaign
      routes + frontend + tests handed to Sonnet 5 agents against a pinned
      contract, then verified: 379 tests pass (367 + 12 new
      test_sms_outreach.py, own sms_org fixture), tsc clean, live click-through
      on alt2 (nav, dashboard, accounts, connect dialog all render). One real
      production gap the mocked tests couldn't catch, caught + fixed:
      python-multipart was missing (requirements.txt) — Starlette's
      request.form() hard-requires it, so every form-encoded Twilio webhook
      would have 500'd in prod. DEPLOYED to production (2026-07-12, with the
      Sendblue integration below; migration f3c7d9e2a1b5 applied cleanly,
      routes live and auth-gated). Go-live also needs, user-side:
      the org's Twilio account connected in SMS → Accounts, A2P 10DLC brand +
      campaign registration in the Twilio console, and the per-account inbound
      webhook URL pasted into the Twilio number config (required for STOP).
- [x] SMS second provider — Sendblue (iMessage/SMS): the SMS module is now
      multi-provider via an adapter seam. SmsAccount.provider
      ("twilio"|"sendblue") selects the transport; sms_send._provider_send /
      verify_credentials dispatch on it (_sendblue_send POSTs
      api.sendblue.co/api/send-message with sb-api-key-id / sb-api-secret-key
      headers — for Sendblue account_sid holds the API Key ID and the
      encrypted token holds the API Secret Key; _verify_sendblue probes GET
      /api/evaluate-service). Sendblue returns 2xx with the message object
      even on some failures, so the message's own status/error_code is the
      source of truth (ERROR/DECLINED or nonzero error_code → failed).
      Sendblue's webhooks carry NO documented signature header, so
      authenticity uses a per-account webhook_token (secrets.token_urlsafe,
      new column, migration f3c7d9e2a1b5 edited in place — safe since SMS was
      never deployed) in dedicated URL routes
      (/api/sms/webhooks/sendblue/inbound|status/{account_id}/{token},
      hmac.compare_digest); Twilio routes keep X-Twilio-Signature. Inbound/
      status handling refactored into provider-agnostic helpers
      (_process_inbound/_apply_status). Sendblue has no 21610 equivalent and
      no auto-STOP, so our STOP-keyword parse + opted_out-flag handling honors
      opt-outs; a status callback reporting opted_out also converges the
      suppression ledger. sendblue_base_url is env-overridable (docs show both
      .co and .com). Frontend: connect dialog gained a provider Segmented
      selector (create-time) that relabels fields (API Key ID / API Secret
      Key), hides Messaging Service SID, requires the Sendblue number, swaps
      the A2P warning for a Sendblue consent note; account cards show a
      provider badge + the correct tokened webhook URLs; smsWebhookUrls is
      provider-aware. Tests 379 → 385 (5 Sendblue cases). Verified live on
      alt2 (provider selector flips the dialog to "Connect Sendblue" with
      relabeled fields). DEPLOYED to production with the SMS module
      (2026-07-12).
- [x] Bug fix (found by the SMS engine agent mirroring email_campaigns):
      email_campaigns._next_valid_send_time returned the stale original
      `after` in its in-window branch instead of the loop's advanced day —
      invisible when send_window_start > 0 (default) but with start == 0 could
      schedule a send on a disallowed day. Fixed + regression test. The SMS
      copy shipped already-fixed.
- [x] CRM lists + bulk edit + org SMS opt-in default + enroll-by-list
      (post-SMS session, two Sonnet agents against a pinned contract,
      independently re-verified): (1) Organization.sms_opt_in_default —
      org-level standing attestation that intake funnels collect SMS consent
      upstream; sms_consent.apply_org_default() stamps every NEW contact at
      all four creation sites (POST /contacts, CSV import as fallback under
      an explicit attestation, lead_ingest, lead_finder) with source
      "org_default:pre_opted_funnel", never overwriting an existing record;
      STOP/suppression still wins at send time. Owner-only toggle (PUT
      /api/orgs/me/sms-opt-in-default, mirrors allow-remember-device) +
      Switch card under the SMS dashboard's compliance banner (renders for
      admins, disabled unless owner — isOwner threaded App→SmsOutreachView).
      Atlas Reach's pre-existing contacts were backfilled the same day via
      backend/scripts/backfill_sms_optin_atlas_reach.py (user-run on prod,
      dry-run then --write; source "agency_attested:pre_existing_crm_consent").
      (2) CRM contact lists: ContactList/ContactListMember mirror Tag/
      ContactTag (client-scoped, unique name per client, org-scoped +
      TenantScope); CRUD under /api/crm/lists, bulk add/remove (≤1000,
      cross-org ids silently skipped, idempotent), list_id EXISTS-filter on
      GET /contacts composing with cf_filter/verification, delete_contact
      cascades membership. Frontend: list filter select + Manage lists in
      the lead list, "Add to list" on the bulk bar (pick-or-create) and in
      the contact drawer. (3) Bulk edit: update_contact's field application
      extracted to _apply_contact_update; POST /api/crm/contacts/bulk-update
      {contact_ids ≤500, fields: ContactUpdateIn} → {updated, skipped},
      all-or-nothing on custom-field validation errors; bulk-bar "Edit"
      dialog applies one field per pass (city/state/company/position/
      sms_opt_in/custom fields — identity fields deliberately excluded).
      (4) Enroll-by-list: EmailEnrollIn/SmsEnrollIn gained list_id (exactly-
      one-of validator); both enroll endpoints resolve the scoped list and
      feed the existing enroll_contacts in ≤500 slices, merging receipts;
      both EnrollDialogs gained an Audience select (whole-list mode:
      "Enroll list (N)") + "Select all (N shown)" over the filtered rows
      (>500 selection blocks with a use-a-list hint). Migration
      a1b7e3f9c2d6 (down_revision f3c7d9e2a1b5). Tests 385 → 404
      (test_crm_lists.py, own lc_org fixture). Verified live on alt2:
      list create/filter (member counts), bulk city edit persisted
      ({"updated":2}), toggle → new contact born opted-in with the
      org_default source, email enroll-by-list returned {"enrolled":2}.
      DEPLOYED to production 2026-07-12 (with the personalization upgrade
      below).
- [x] Personalization upgrade (email + SMS) — Clay-style features + hard
      failsafes (two Sonnet agents, pinned contract PERSONALIZE_HANDOFF):
      (1) shared engine (services/email_personalize.py): four new grounded
      tokens job_title/company_description/company_revenue/company_employees
      (from the Phase-12/enrichment columns; _company_facts replaces
      _company_name); single-level {{#if token}}…{{else}}…{{/if}}
      conditionals evaluated before substitution; deterministic spintax
      {{spin:a|b|c}} — sha256(contact.id + block text) picks the variant, so
      re-renders are idempotent per contact while variants spread across the
      audience (template-fingerprint/deliverability play); brace-depth
      scanner (_iter_spin_blocks) because nested {{tokens}} inside variants
      break a naive non-greedy regex. (2) SMS AI personalization:
      SmsStep.ai_instructions + SmsEnrollment.ai_snippets (migration
      b3f8a1d47c92), grounded one-sentence ≤15-word prompt, same
      outreach_personalize metering, enrollment-cached, preview uncached;
      SMS_KNOWN_TOKENS += ai_snippet/job_title (+ custom.* via custom_keys).
      (3) Failsafes: clean_ai_snippet output guard (strips quote-wrapping;
      discards URLs/template-syntax/over-length → "", still fail-open);
      save-time 422s extended to unknown #if names, unclosed #if, <2-variant
      spin (both step APIs, shared _unknown_tokens_against); send-time
      guards — email exits enrollment render_error on blank/leftover-{{
      (unsubscribe_url literal exempt, the gateway resolves it), SMS exits
      render_empty/render_error/too_long (MAX_RENDERED_SEGMENTS=3, GSM-7
      160/153 math — cost-blowout guard for runaway custom fields/AI text).
      Frontend: SMS step editor gained the collapsed AI-instructions block
      (mirrors email's), both TOKENS_HINTs list the new tokens + #if/spin
      syntax, segment note "counted before personalization; capped at 3".
      Tests 404 → 432 (test_personalize.py 21 unit + engine-exit cases).
      Known test-isolation note: module-scoped org fixtures leave due
      enrollments other tests' run_due() can pick up — new tests exit their
      enrollments or filter captured sends by recipient. DEPLOYED to
      production 2026-07-12.
- [x] SMS per-campaign compliance-footer toggle (same-day follow-up): the
      CTIA sender-id + "Reply STOP to opt out" suffix on an SMS campaign's
      first message (services/sms_send.apply_compliance_suffix) is now
      opt-out per campaign via SmsCampaign.include_compliance_footer
      (migration c4d9e6a2f815, default true — every existing/new campaign
      keeps current behavior unless explicitly changed). For contacts who
      already know they'll hear from you (past clients, warm follow-ups),
      an org can turn the reminder text off; STOP handling itself is
      completely unaffected either way — sms_consent/suppression never
      reads this flag, it only controls whether the CTIA text is appended.
      Threaded through SmsCampaignIn/Patch, create/PATCH/preview endpoints,
      and the gateway's send() (derives include_footer from the campaign
      instead of always-true). Frontend: ConfigForm gained a Switch with an
      explicit compliance-risk note (carrier filtering) rather than a
      silent toggle. Test added
      (test_compliance_footer_defaults_on_and_can_be_disabled_per_campaign).
      Tests 432 → 433. Verified live on alt2: default-on preview showed the
      full "OrgName: ... Reply STOP to opt out" text, toggled-off preview on
      a second campaign showed the bare personalized body, PATCH round-trip
      confirmed via network inspection. DEPLOYED to production 2026-07-12.
- [x] SMS individual messenger (same-day follow-up): one-off, single-contact
      texting — a live 1:1 conversation, not a campaign. New
      POST /api/sms/compose {account_id, contact_id, body} (require_team)
      routes through the SAME sms_send.send() gateway as campaigns (full
      consent/suppression/cap guards apply), kind=SMS_KIND_MANUAL (the
      constant existed, unused, since the SMS framework's first cut).
      Manual sends never carry the CTIA compliance footer regardless of the
      per-campaign toggle above — repeating "OrgName: ... Reply STOP" on
      every text in an ongoing conversation isn't the "first message of a
      program" the convention is for (send()'s include_footer is now
      `campaign is not None and campaign.include_compliance_footer`, was
      previously true-by-default for campaign=None, an untested/unused path
      before this feature); STOP handling is unaffected either way. Also
      skips the campaign send-window (mirrors how the email module's manual
      replies already behave) but keeps the per-account daily cap and the
      same MAX_RENDERED_SEGMENTS=3 guard as campaign sends (422 over cap).
      Frontend: SMS → Messages gained "New message" (pick number + house-CRM
      contact + body) and a reply composer at the bottom of the open
      conversation (infers the send-from number from that conversation's
      own message history). Tests: test_compose_sends_one_off_with_no_
      compliance_footer, _blocked_without_consent, _rejects_over_segment_cap,
      _cross_org_contact_404s. Tests 433 → 437.
      REAL BUG CAUGHT DURING LIVE VERIFICATION (not new, pre-existed the
      whole Messages tab): api/sms_outreach.py's _message_out() never
      serialized `contact` or `sent_at`/`received_at` even though the
      frontend's SmsMessage type and MessagesPanel always expected them —
      every conversation silently collapsed into one "Unknown contact"
      bucket (the grouping key was `m.contact?.id ?? "unknown"`, always
      "unknown"). Fixed by having _message_out accept an optional contact
      (reusing the existing _contact_stub) and deriving sent_at/received_at
      from created_at by direction; list_messages/list_conversations now
      batch-resolve contacts. Regression-tested (asserts contact.id/
      first_name and sent_at on the compose response). Verified live on
      alt2: sent two manual messages to the same contact (flipped a fake
      Twilio account to "active" in dev-alt2.db to get past the account
      gate — actual delivery correctly failed with Twilio auth error 20003,
      proving the guard order, not a real send), both appeared in the right
      order under the contact's real name/number, not "Unknown contact".
      DEPLOYED to production 2026-07-12.
- [x] SMS read tracking, both directions (same-day follow-up, researched via
      Sendblue's public docs — confirmed their status webhook reports a
      "READ" value for iMessage read receipts, keyed by message_handle,
      alongside SENT/DELIVERED/ERROR/DECLINED): (1) Outbound — Sendblue/
      iMessage read receipts. SMS_MSG_READ status + sms_messages.read_at
      (migration d1e5f3b7a924); _apply_status (shared by both providers)
      now handles status "read" → row.status=read, read_at=now. Twilio
      never sends this status so the branch is simply unreachable on that
      provider — no per-provider special-casing needed, the existing
      provider-agnostic webhook handler already covers it. (2) Inbound —
      our own unread/read state, since SMS has no thread model to hang a
      per-thread unread flag on (unlike email's EmailThread.unread). Reused
      the SAME read_at column dual-purpose by direction: outbound = when
      the recipient read it (Sendblue), inbound = when OUR team marked the
      conversation read. New POST /api/sms/messages/mark-read {contact_id}
      (require_team, contact is the conversation key) sets read_at on every
      unread inbound row for that contact. Frontend: conversation list gets
      an email-style "new" Badge + bold name while unread; opening a
      conversation (click, or the auto-selected default one on load) calls
      mark-read and refreshes; message bubbles show "Read {time}" under
      status for any outbound message with a read receipt. Tests:
      test_sendblue_read_receipt_sets_status_and_read_at,
      test_twilio_status_webhook_ignores_unknown_status (regression —
      needed its own unique provider_sid via a fresh _twilio_send
      monkeypatch, not the captured_sends fixture's shared hardcoded
      "SM_test_sid" literal, which collides across this module-scoped
      org's many other tests once looked up by sid+org),
      test_mark_read_only_clears_inbound_unread_for_that_contact (outbound
      rows untouched, idempotent second call), test_mark_read_cross_org_
      contact_404s. Tests 437 → 441. Verified live on alt2: fired a real
      Sendblue-shaped status webhook (message bubble showed "read · Read
      just now"), simulated a signed Twilio inbound reply (unread), then
      confirmed opening the Messages tab auto-called mark-read and
      persisted read_at server-side. DEPLOYED to production 2026-07-12.
- [x] Clay-style personalization round 2 (research → write → QA; two Sonnet
      agents vs pinned contract CLAY_HANDOFF, research pass on Clay's docs
      first): (1) AI RESEARCH FIELDS ("Claygent-lite") — org-defined research
      prompts (ResearchFieldDef, unique key per org, cap via entitlement stub
      research_fields starter 5/pro 15/agency ∞, hard ceiling 20) answered
      per-contact by services/research.py: ONE AI call per missing field,
      grounded ONLY in CRM/enrichment facts + the contact's own website text
      (enrichment.fetch_site_text — homepage + /about, robots-honored, same
      crawler posture/kill switch, 6000-char cap; guardrail 6 holds). Answers
      land on contacts.research JSON as {value, confidence, source_url,
      researched_at} — cached (skip unless force), metered as AiUsage
      feature="outreach_research", fail-open per field, TEAM-ONLY in
      serialization. Rendered via {{research.<key>}} tokens in email AND SMS
      templates (validated at step-save like custom.*), and fed into
      ai_snippet grounding. API: /api/crm/research-fields CRUD (delete scrubs
      the key from every contact, custom-fields pattern) + POST
      /api/crm/research/run (≤200 ids, BackgroundTask). UI: manager card in
      CRM setup next to FieldManager, "Run AI research" on the lead-list bulk
      bar + in the campaign Review tab. (2) AUDIENCE PREVIEW + QA — Clay's
      table-first QA plus the native approval flag Clay lacks: POST
      /campaigns/{id}/preview-batch renders the step for every active
      enrollment (paged ≤50; AI snippets generate once and CACHE on the
      enrollment — same spend as sending, just earlier) with issues[] flags
      (leftover_tokens/blank_body/no_first_name/not_sendable:<reason> incl.
      suppression + verification); PUT/DELETE /enrollments/{id}/override
      stores per-step {subject, body} the engine sends VERBATIM (literal
      {{unsubscribe_url}} still resolved by the gateway; blank-body still
      guarded); POST /campaigns/{id}/qa approve/unapprove/exclude (exclude =
      exit_reason qa_excluded). EmailCampaign.require_approval: engine defers
      unapproved enrollments +1h (held, never skipped); approving schedules
      via _next_valid_send_time. UI: 4th "Review" tab in the campaign editor
      (paged DataTable, issue/QA badges, per-row Approve/Edit/Exclude, bulk
      approve, require-approval Switch). (3) ORG OUTREACH CONTEXT + AI
      CONTROLS — Organization.outreach_context JSON (company_description/
      icp/offer/tone_guide, 2000-char caps) via GET/PUT /api/orgs/me/
      outreach-context (require_admin write), injected into email + SMS
      snippet grounding and research grounding; EmailCampaign.ai_tone/
      ai_example (few-shot) appended to the snippet prompt (email only).
      UI: "AI writing context" card on the Email dashboard (admin-only),
      collapsed "AI writing" section in campaign config. Migration
      b2e6f1a9c4d7 (down_revision d1e5f3b7a924). Tests 441 → 456
      (test_research.py rf_org, test_email_qa.py qa_org). Verified live on
      alt2: field create → {{research.services_offered}} hint, bulk run
      queued 200, context card PUT 200, Review tab rendered both enrollments
      personalized ("Hi John O'Brien … Desert Air HVAC"), Approve badge,
      hand-edit override round-trip ("Edited" badge), require-approval
      Switch PATCH 200. DEPLOYED to production (2026-07-13 UTC, bdb8b25
      deploy). NOTE: alt2 has no AI key, so
      research/snippet paths verified fail-open; prod needs ANTHROPIC_API_KEY
      (or a BYO provider key) before research fields return values.
- [x] AI provider key in Integrations (same-day follow-up): the BYO AI keys
      (anthropic/openai/gemini — endpoints existed since multi-provider AI,
      no UI did) now have an "AI provider" card on the Integrations page:
      active provider + model, per-provider status badges (Your key /
      Platform key / Not configured), and a prominent "No key — AI features
      off" warning badge when nothing resolves. Key writes are OWNER-ONLY,
      enforced server-side (_require_owner_for in api/lead_finder.py's
      shared PUT/DELETE /providers endpoints — lead-data providers stay
      admin-manageable); non-owner admins see statuses read-only. New GET
      /api/integrations/ai-provider (require_admin) returns
      {active, model, providers[]} via ai_provider.active_provider/
      active_model + integration_creds.key_source. Frontend:
      AiProviderKeysCard in integrations.tsx (mirrors the Lead Finder
      provider-row pattern), Integrations gained isOwner prop (App.tsx),
      .mg-ai-provider-* styles in manage.css. Tests 456 → 459
      (owner save/status/remove round-trip; admin 403 on all three AI
      providers but still 200 on google_places). Verified live on alt2:
      card renders "No Key — AI Features Off", owner Add key → PUT 200 →
      "Your Key" badges, Remove → back to warning state. DEPLOYED to
      production (2026-07-13 UTC, bdb8b25 deploy).
- [x] Warmup functionality audit (same-day): proved the warmup pipeline
      end-to-end with ZERO mocks — scratchpad harness (warmup_e2e.py) ran a
      local aiosmtpd sink (implicit TLS + AUTH, self-signed cert trusted via
      SSL_CERT_FILE; aiosmtpd gotcha: implicit-TLS sessions never set
      session.ssl, so AUTH is only advertised with auth_require_tls=False)
      and drove email_warmup.run_warmup_tick against the alt2 dev DB through
      the REAL gateway + smtplib transport: tick 1 sent 2 (one per mailbox,
      peer-rotated), tick 2 paced by the jitter gap sent 1, messages carried
      X-Salescale-Warmup/Depth headers, on_warmup_received recorded receipt
      and fired a threaded "Re:" auto-reply (In-Reply-To correct, depth 1),
      peer-pair sent/received counters advanced, failed-send → account
      status=error guard observed live. Scheduler wiring confirmed
      (main.py _email_outreach_scheduler → email_warmup.run_due;
      email_campaigns.register_hooks binds on_warmup_received/junk at
      import). 17 warmup unit tests green. ONE functional gap found + fixed:
      an org with <2 active warmup-enabled mailboxes gets ZERO exchange
      volume silently ("0 of N today" forever) — the Warmup tab now shows a
      warn Alert explaining peer exchange needs a second mailbox (cap ramp
      still applies). THIS IS PROD'S CURRENT STATE: Atlas Reach has one
      mailbox — warmup exchange is inert until a second mailbox is connected
      with warmup on (user-side). DEPLOYED to production (2026-07-13 UTC,
      bdb8b25 deploy); since resolved user-side — three mailboxes
      connected with warmup on.
- [x] Desktop app repair + flaw audit (2026-07-12, post-DMG-fix): the
      running desktop app was silently talking to a July-10 zombie dev
      server on port 8000 (throwaway /tmp e2e SQLite — explained the
      "verify email" banner, CRM "Not Found", stuck Email skeletons) while
      its own bundled backend died at startup on a stale Supabase password
      in userData config.json (the VPS .env is the only current copy;
      local backend/.env is ALSO stale — flagged, not touched). Fixed:
      zombie killed, config.json synced from the VPS via ssh-pipe (never
      displayed), resendApiKey/emailFromAddress added (2FA email was
      silently dropped without a transport), appBaseUrl + env.API_BASE_URL
      set. Booting the desktop backend applied the additive b2e6f1a9c4d7
      migration to the live DB (checked additive-only first; the pending
      deploy's alembic step will no-op). Then a 19-agent Sonnet workflow
      audit (5 dimensions, adversarial verify — 19 confirmed, 0 refuted)
      + fixes: (1) SCHEDULER GATE — desktop_mode now runs NO background
      schedulers (config.run_schedulers(), DESKTOP_RUN_SCHEDULERS=1
      opt-out for standalone installs; tests/test_desktop_mode.py): two
      instances polling one DB could double-send email/SMS/DMs since the
      due-row scans have no cross-process claim (server-side hardening
      with FOR UPDATE SKIP LOCKED left as a flagged follow-up — only
      matters if the VPS ever runs >1 replica). (2) ELECTRON SHELL —
      single-instance lock, dock-reactivate no longer double-spawns the
      backend (was orphaning the first: the exact zombie class above),
      spawn-error + unexpected-exit dialogs with a stderr tail (backend
      death was invisible), setWindowOpenHandler routes target=_blank to
      the system browser (was dead), and config.json gained a generic
      "env" object merged into the backend env — closes the
      passthrough-allowlist class (API_BASE_URL, TWILIO_*, AI keys…)
      forever. (3) PYINSTALLER — anthropic/openai/google-genai are
      imported lazily so the binary had NO AI SDKs; collect_all'd in
      main.spec (+ multipart for Starlette form parsing); binary grew
      ~9MB, presence verified via pyi-archive_viewer. (4) WEB-ORIGIN —
      social sign-in buttons hidden on desktop (window.salescale
      .isDesktop; the OAuth callback needs a web origin and dead-ends).
      Cold-email unsubscribe/List-Unsubscribe URLs building from a
      localhost api_base_url can't happen from desktop anymore (no
      schedulers = no sends) and env.API_BASE_URL is set anyway. KNOWN
      REMAINING (all flagged as task chips or user-side): SMS webhook URL
      cards render localhost URLs on desktop (configure Twilio from the
      web app); dynamic port + own-backend handshake chip; scheduler
      claim hardening chip; APP_BASE_URL is UNSET ON THE VPS TOO — prod
      password-reset/verify/invite email links point at localhost:5173
      (user-side: add APP_BASE_URL=https://app.salescale.lol to the VPS
      backend/.env and recreate). Tests 459 → 462, tsc clean, DMG rebuilt
      (148MB) + contents verified (backend hash, new main.js, frontend
      asar). DEPLOYED to production with the 2026-07-13 UTC bdb8b25 deploy.
- [x] Warmup timezone (2026-07-12, same-day): the warmup engine's
      08:00–18:00 send window, weekend reduction, AND daily-budget midnight
      now follow a per-mailbox IANA `warmup_timezone` (migration
      924b1e025dc1, NULL = UTC = old behavior; org-configurable, no
      tenant special-casing per guardrails). The fixed-UTC window was
      1am–11am for a Phoenix org — synthetic mail in the local night reads
      as scripted. Local midnight for the budget counter is load-bearing:
      a Phoenix window is 15:00–01:00 UTC (straddles UTC midnight), and a
      UTC-midnight reset would hand out a second daily budget mid-window.
      Engine: email_warmup.account_local() (invalid zone degrades to UTC,
      never stalls), per-sender window check inside run_warmup_tick
      (window check moved from tick-level to account-level), local-date
      hash picks. API: EmailAccountPatch.warmup_timezone (ZoneInfo-
      validated, 422 on garbage), in _account_out. UI: timezone input per
      mailbox on the Warmup tab (campaign-timezone input pattern). Tests
      459→462→465 (test_desktop_mode.py 3 + warmup tz cases in
      test_email_warmup.py incl. the straddle-midnight budget case).
      Atlas Reach wants America/Phoenix on its three mailboxes —
      DEPLOYED to production (2026-07-13 UTC): migration 924b1e025dc1
      applied to the live Supabase DB, backend/frontend rebuilt, health
      green, new routes auth-gated; all three Atlas Reach mailboxes set
      to America/Phoenix (user-approved SQL) — first warmup sends land
      8am–6pm Phoenix.
- [x] SMS render failsafes (2026-07-13): a lead with no usable first_name
      greets by its BUSINESS NAME instead (deterministic — no AI; beats any
      explicit |fallback since a named greeting is the point), proper-cased
      via the shared _smart_case plus an acronym guard (sms_campaigns.
      business_case — "DESERT AIR HVAC LLC" → "Desert Air HVAC LLC"; only
      true acronyms like LLC/HVAC/PLLC stay upper, Inc/Co stay title-case).
      A blank {{city}} referenced by the template is AI-INFERRED once from
      the lead's OWN facts (business name, website domain, phone area
      codes, state, the Lead Finder search query — guardrail 7 holds;
      sms_campaigns.infer_city_failsafe via the ai_provider dispatch,
      metered through the existing _record_usage, max_tokens=16) and
      written back fill-blanks-only to contacts.city so it's cached and
      human-correctable in the CRM. Output guard _clean_city rejects
      hedges/sentences/digits/UNKNOWN → field stays blank and the existing
      render_empty/tidy guards apply; every AI failure fails open (no key,
      cap, timeout — a send is never blocked on AI). Both hooks live in
      sms_campaigns.render_body (the single choke point: engine sends AND
      preview). No schema change, no new tokens. SMS step-editor hint
      documents the failsafes. Tests 465 → 467 (failsafe render + fail-open
      cases; note the module's earlier compliance-footer test owns
      from_numbers +14805550301/302 — pick unused numbers, the sms_accounts
      (org, from_number) unique index bites across the module-scoped DB).
      DEPLOYED to production (2026-07-13 UTC, 425cb95).
- [x] Bulk re-enrich (2026-07-13, same-day): POST /api/crm/contacts/enrich
      (require_team, VerifyContactsIn ≤500 ids, TenantScope 404 on any
      cross-org id, BackgroundTask → lead_finder.enrich_and_verify) + an
      "Enrich contact info" button on the lead-list bulk bar. Closes the
      backfill gap: enrichment (owner name/title/MOBILE via the org's
      Apollo key, site discovery, verification) previously ran ONLY at
      import time, so the 80 Atlas Reach leads imported before the Apollo
      key was connected had no owner/mobile data and no way to get it.
      Fill-blanks-only as always. Tests 467 → 468. DEPLOYED to production
      (2026-07-13 UTC, c7762b2): health green, endpoint live + auth-gated.
- [x] Enrichment status card (2026-07-13, same-day): enrichment was
      fire-and-forget with zero visible state — now every enrich_and_verify
      run (bulk re-enrich, Lead Finder import, CSV import) writes an
      EnrichmentJob row (models/lead_finder.py, migration e4e04c133222:
      status/phase/total/processed/updated_at heartbeat per contact —
      per-contact commits also make the pipeline itself incremental, a
      mid-run failure keeps completed contacts). GET /api/crm/enrich/jobs
      (require_team) serves the last 10 runs with elapsed, pace-based
      eta_seconds (elapsed/processed × remaining), and a server-derived
      "interrupted" status when a running job's heartbeat is >180s old
      (backend restarted mid-run). Frontend: EnrichmentStatusCard between
      the board and lead list in crm.tsx (team-only, hidden until the org
      has ever enriched) — Processing badge, progress bar, "Enriching lead
      N of M… about Xm Ys remaining", verifying-phase line, failure reason,
      history of prior runs; polls every 4s only while a job is running and
      refetches instantly when the bulk Enrich button queues (window event
      ENRICH_QUEUED_EVENT — no prop drilling). job.error keeps
      type(e).__name__ when str(e) is empty (cryptography.InvalidToken
      stringifies to "" — found live when alt2's scrubbed
      TOKEN_ENCRYPTION_KEY couldn't decrypt stale IntegrationCredential
      rows; cleared from dev-alt2.db). Tests 468 → 469. Verified live on
      alt2: real 3-lead run (card appeared mid-flight then completed), a
      simulated 20/80 job rendered the bar at 25% + "about 5m 10s
      remaining" (ETA math verified), running-job-wins-hero display fix.
      DEPLOYED to production (2026-07-13 UTC, 0972fa9): migration
      e4e04c133222 applied to the live Supabase DB, health green, endpoint
      live + auth-gated.
- [x] Parked-enrollment re-arm fix, SMS + email (2026-07-13): found
      checking "is SMS working" in prod — the CPA campaign had 31 ACTIVE
      enrollments with next_run_at NULL, permanently invisible to run_due.
      Both engines park enrollments (next_run_at = None) when a tick
      catches the campaign paused or the account disconnected, and the
      code comments promised "reconnect flow re-arms" — but no re-arm
      existed anywhere, in either module: pause→resume (or account
      error→reconnect) stranded the audience forever. Fix:
      sms_campaigns.rearm_parked/rearm_account + email_campaigns twins
      (schedule at the campaign's next valid send window, never
      mid-quiet-hours), called from both activate endpoints, the SMS
      account /test endpoint, and the email account reprobe/test paths.
      Sends themselves verified working in prod: Sendblue account active,
      both messages ever sent were delivered, 181 opted-in textable
      contacts, 0 suppressions. Tests 469 → 471 (park→reactivate→re-armed
      in both modules, + the SMS account-test re-arm path). DEPLOYED to
      production (2026-07-13 UTC, 572f338), and the 32 stranded prod
      enrollments re-armed with user approval (CPA x31 due 16:00 UTC =
      9am Phoenix, test2 x1 due 18:00 UTC) — verified scheduled.
- [x] iMessage channel — BlueBubbles provider (dev) + Sendblue (prod)
      (2026-07-13): iMessage added as a THIRD provider ("bluebubbles") in
      the existing SMS Outreach module, next to twilio/sendblue — NOT a
      new module/engine/tables (Sendblue already sent iMessage; this adds
      the self-hosted BlueBubbles dev/prototype path + finishes channel
      health for both). Extend-don't-redesign: zero engine changes, no new
      tables, no RLS changes (isolation stays the app-layer TenantScope on
      the already-org-scoped sms_* tables). Migration f9a3c7e1b6d4 adds 3
      nullable columns: sms_accounts.relay_url + min_send_spacing_seconds,
      sms_messages.service. services/sms_send.py: _bluebubbles_send (POST
      {relay}/api/v1/message/text?password=, chatGuid iMessage;-;<e164>,
      returns data.guid) + _verify_bluebubbles (GET /api/v1/ping) wired
      into _provider_send/verify_credentials ahead of the twilio default;
      a provider-agnostic min-spacing THROTTLE in the gateway send()
      (anti-detection pacing — BlueBubbles runs through a real Mac/Apple ID,
      so machine-gun sends get the ID flagged): only AUTOMATED campaign sends
      are paced (a human's 1:1 inbox reply, campaign=None, is never
      throttled), a violation returns the SPACING code which the engine
      reschedules to last_send + spacing×random(1.0–1.8) via
      next_spacing_time (jittered so the cadence isn't robotic, NOT the old
      coarse +1h), and bluebubbles accounts default to a 60s spacing at
      creation (BLUEBUBBLES_DEFAULT_SPACING_SECONDS; operator can override,
      incl. 0); naive/aware datetime coerced so it can't 500 on SQLite vs
      Postgres. Plus channel_health(db,account)
      → {status: healthy|degraded|blocked, ...} from account status + a
      25-row outbound sample (green-bubble/SMS fallback = degraded via the
      new service column; ≥50% failed = blocked). NEW api/imessage_webhooks
      .py (registered in main.py): POST /api/webhooks/imessage/bluebubbles/
      {account_id} — shared-secret auth (X-Salescale-Webhook-Secret header,
      injected by the VPS relay, OR ?secret= query since BlueBubbles' own
      webhook config can only set a static URL), normalizes BlueBubbles'
      {type:new-message|updated-message} payload, ignores isFromMe echoes,
      threads inbound to CRM by phone with HOUSE-CRM new-lead fallback
      (sms_opt_in stays False — inbound is not consent), STOP suppresses,
      updated-message → delivered/read; plus /api/webhooks/imessage/
      sendblue/{inbound,status}/{account_id}/{token} thin aliases that
      delegate to the canonical sms_webhooks handlers (status alias also
      captures service/was_downgraded). sms_webhooks._process_inbound gained
      optional create_missing/service params (defaults keep Twilio/Sendblue
      behavior identical); services/crm.get_or_create_house_client extracted
      (race-safe, mirrors api/orgs GET /house-client). Frontend
      (sms_outreach.tsx + api.ts): "BlueBubbles (dev)" provider in the
      Accounts dialog (Relay URL / Server password / iMessage number / min
      seconds; account_sid sent null), a channel-health badge on account
      cards, and the bluebubbles inbound-webhook URL card. VPS relay
      (deploy/imessage-relay/): Caddyfile (auto-TLS, reverse_proxy to the
      reverse-SSH tunnel port), launchd plist (Mac) + systemd unit (Linux)
      for autossh, firewall.sh (ufw allowlist 443 → Salescale backend IP),
      and a README runbook for the user-side manual steps. Tests 471 → 486
      (test_imessage_outreach.py, own im_org fixture, _bluebubbles_send
      monkeypatched). tsc clean. NOT deployed (no live BlueBubbles server /
      relay yet — Sendblue iMessage already works in prod today). Remaining
      user-side for the dev path: provision the relay VPS + DNS + SSH key,
      install BlueBubbles on a Mac, apply the relay configs, then connect
      the account in SMS → Accounts (provider BlueBubbles).
- [x] iMessage relay actually stood up + real random pacing RANGE
      (2026-07-13, same-day follow-up): the dev BlueBubbles path went from
      "not deployed" to live end-to-end, reusing the existing prod VPS
      (2.25.75.95) rather than a second box. DNS (imessage-relay.salescale.
      lol → 2.25.75.95), autossh reverse tunnel (Mac :1234 → VPS loopback
      127.0.0.1:12345, launchd-managed for auto-reconnect/reboot survival),
      and a Traefik FILE-PROVIDER route added alongside the existing
      docker-provider routes (auto-TLS via the same letsencrypt resolver) —
      confirmed live end-to-end (https://imessage-relay.salescale.lol/api/
      v1/ping reaches BlueBubbles on the Mac; api.salescale.lol health
      unaffected). CONFIRMED SSH GOTCHA (corrected in the README + relay
      configs): `restrict,permitlisten="127.0.0.1:12345"` does NOT work on
      live OpenSSH 9.6 — ssh(1) sends the literal string "localhost" (not
      "127.0.0.1") as the listen host when `-R` omits an explicit bind
      address, and `restrict`'s bundled `no-port-forwarding` is NOT
      overridden by `permitlisten` (server reports "Server has disabled
      port forwarding" even with a matching permitlisten). Working pattern:
      `-R 12345:localhost:1234` (bare port) paired with
      `no-agent-forwarding,no-X11-forwarding,no-pty,permitlisten="localhost:12345"`
      (explicit individual restrictions, NOT the `restrict` shorthand).
      PACING CORRECTED (user-requested): the anti-detection guard was a
      floor×1.0-1.8x-jitter model (60s default → 60-108s actual gaps,
      unbounded upward drift) — changed to a literal uniform-random RANGE.
      Migration a3d7c1f8e942 adds sms_accounts.max_send_spacing_seconds
      (nullable); services/sms_send.next_spacing_time picks
      random.uniform(min, max) when both bounds are set, falling back to
      the old floor*jitter only when max is left null (backward-compat for
      any account configured before the range existed). BlueBubbles default
      at account creation (only when NEITHER bound is given) is now the
      literal 20-45s window the user asked for
      (BLUEBUBBLES_DEFAULT_SPACING_MIN/MAX_SECONDS), replacing the old 60s
      floor constant. API: AccountIn/AccountPatch gained
      max_send_spacing_seconds + a 422 if max < min. Frontend: a second
      "Max seconds between sends" field alongside Min (sms-form-row
      two-column layout, reusing the existing class). Tests 486 → 490 (max-
      below-min 422, range-default assertion, sampled-gap-in-range check,
      floor-fallback-when-max-unset regression). DEPLOYED to production
      2026-07-13 (migrations f9a3c7e1b6d4 + a3d7c1f8e942 both applied
      cleanly on the live Supabase DB in this session).
- [x] Gemini model retirement fix + SMS city AI-inference disabled
      (2026-07-13, same-day follow-up): live-diagnosed (one prod test call,
      user-authorized, output redacted) why AI personalization went blank
      right after the Gemini flip above — Google retired gemini-2.0-flash
      2026-06-01 (HTTP 404), so every Gemini-routed call failed and was
      silently swallowed by the by-design fail-open try/except around every
      AI call site. Usage/entitlement/key-resolution all checked out fine
      (0/200 monthly queries, key resolved). Fixed: default gemini_model →
      gemini-2.5-flash (same price-performance tier 2.0-flash held, not the
      pricier gemini-3.5-flash frontier tier; pricing $0.30/$2.50 per 1M
      in/out confirmed against Google's own pricing page), PRICING table
      updated (old entry kept so historical AiUsage rows still price
      correctly). DEPLOYED — config-only, no migration. SEPARATE follow-up
      the same day: the SMS city AI-inference failsafe (infer_city_failsafe,
      only fires when {{city}} is referenced AND contact.city is blank)
      proved inconsistent per-lead in practice even after the model fix
      (one lead's preview worked, others didn't — root cause not fully
      pinned down, rate-limiting on the BYO Gemini key was the leading
      hypothesis but unconfirmed) — user chose to unblock a live campaign
      rather than keep debugging it. services/sms_campaigns.render_body no
      longer calls infer_city_failsafe at all: {{city}} is now a PLAIN
      contact.city field lookup, same as {{state}} — blank renders blank,
      zero AI dependency, zero failure modes. infer_city_failsafe/
      _clean_city/_CITY_TOKEN_RE/_CITY_FAILSAFE_SYSTEM are kept defined but
      uncalled (a one-line change to re-enable, not a rebuild) — read
      render_body's docstring before re-enabling. Two tests updated to
      match (business-name-greeting test split from the now-removed AI-city
      assertion; the fails-open test's now-vacuous _call_model monkeypatches
      removed, _clean_city's own guard behavior kept as a standalone unit
      test). Tests 490 → 491. DEPLOYED — config/code only, no migration.
      Email's {{city}} was never AI-dependent (always a plain field lookup)
      so this only affects SMS.
- [x] Generic unhandled-exception catch-all, CORRECTLY this time (2026-07-13,
      same-day, two-pass): user hit "NetworkError when attempting to fetch
      resource" on CSV CRM import — the symptom this codebase already
      diagnosed once (bare 500s emitted outside CORSMiddleware read by
      browsers as an opaque NetworkError), but that prior fix only
      special-cased three platform-API exception types (Google/Meta/
      PlacesError) for live-refresh paths, leaving every other router (incl.
      CSV import's loop — several unguarded calls: get_or_create_company,
      sms_consent.record_opt_in/apply_org_default, the Contact(...)
      construction; only custom_fields_svc has a try/except, CustomFieldError
      only) exposed to the same gap.
      FIRST PASS (wrong, corrected same day): added
      app.add_exception_handler(Exception, ...) mirroring the platform-error
      pattern. Passed a dedicated test AND manual verification — but the
      user reported the identical browser error afterward. ROOT CAUSE OF THE
      MISS: Starlette's Starlette.build_middleware_stack() specifically
      special-cases a handler keyed on the base Exception class (or the
      literal int 500) into ServerErrorMiddleware, which wraps OUTSIDE every
      user middleware including CORSMiddleware — so that handler's response
      NEVER passes back through CORS and never gets Access-Control-Allow-
      Origin, regardless of what the handler returns. The platform-specific
      handlers work because they're keyed on SPECIFIC exception types, which
      Starlette instead routes through ExceptionMiddleware — INSIDE
      CORSMiddleware. Confirmed live via the browser console:
      "Cross-Origin Request Blocked ... Access-Control-Allow-Origin missing.
      Status code: 500" — proving the handler DID run (produced a 500) but
      the response was CORS-less. The first-pass test didn't catch this
      because Starlette's TestClient doesn't enforce or simulate CORS at
      all — it only checks status/body, which were both already correct.
      CORRECT FIX: real HTTP middleware (@app.middleware("http")), registered
      in main.py BEFORE app.add_middleware(CORSMiddleware, ...) so it ends up
      wrapped BY CORS instead of wrapping ServerErrorMiddleware around it —
      catches everything in a try/except around call_next(request), logs
      the full traceback at ERROR, returns the same generic safe 500 (never
      raw exception text). The old add_exception_handler(Exception, ...)
      registration was removed (redundant — provided no real CORS benefit
      even as a fallback). Tests corrected to actually assert
      access-control-allow-origin is present on the response (with a real
      Origin header on the request, mimicking a browser) — added to BOTH the
      new middleware's test AND retrofitted onto the existing platform-error
      test, confirming that pre-existing mechanism was always correct.
      Tests 492 (unchanged count — existing tests strengthened, not added).
      DEPLOYED — code only, no migration. Lesson for next time: a test that
      only checks status code + body cannot catch a CORS-header regression;
      TestClient must be given a real Origin header and the response
      header itself asserted. NOTE: still a safety net only — a bad CSV row
      still 500s the whole import request rather than landing in the
      per-row `failed` list the way CustomFieldError already does; worth
      hardening if a specific reproducible bad row recurs.
- [x] Generic landing-page form webhook (2026-07-13): a third-party-form-tool
      counterpart to the existing Meta/Google native lead-form webhooks, for
      clients whose landing pages use Webflow, WPForms, Elementor, Typeform,
      Zapier/Make, or a plain HTML form — anything that can POST to a URL.
      Reuses LeadFormConfig with platform="landing_page", but unlike meta/
      google there's no external console to hold a shared secret, so the key
      is server-generated (POST /api/clients/{id}/lead-forms/landing-page/
      rotate, admin-only) and folded into the URL path
      (/api/webhooks/landing-form/{client_id}/{key}) — the one auth
      mechanism every such tool supports without custom headers; PATCH
      .../landing-page toggles enabled without rotating. The client's tool
      controls its own field names, so api/lead_webhooks.py matches incoming
      keys via a case/punctuation-insensitive synonym table (email/phone —
      one required — first/last/full name, city, state, company, job title,
      message, utm_source/utm_medium/utm_campaign/utm_content/utm_term/
      gclid/fbclid/fbp), same "meet the data where it is" posture as the CSV
      import header auto-detect; unrecognized fields land in source_detail
      for audit (capped), a recognized "message" becomes an Activity note on
      the contact. Ingests through the same lead_ingest.upsert_contact used
      by every other capture path (source="landing_page_webhook", dedupe by
      email/phone, fill-blanks-only) and gets a LandingEvent for attribution
      parity when the payload carries UTM/click-id evidence, matching the
      Google lead-form webhook's gclid handling. Frontend: a new card in
      CRM setup's "Native lead-form ingestion" section (LeadFormRouting in
      frontend/src/crm.tsx) — Generate/Rotate/Disable, with the accepted
      field names documented inline. Tests 493 (14 in test_crm.py, one new:
      generate→ingest incl. synonym mapping, company auto-link, message→
      note, attribution, resubmission update, missing-identity 400, disable
      rejects, rotate invalidates the old URL). Verified live end-to-end on
      alt2: generated a real URL from the UI, POSTed a realistic third-party
      payload via curl exactly as a form tool would, confirmed the contact
      (name split, phone, city, auto-created company, source, gclid
      attribution) and the activity note through the API, then confirmed
      wrong-key and no-identity-field both reject.
- [x] Lead SMS notifications (2026-07-13): text-the-team alerts the moment a
      lead arrives in real time (native Meta/Google lead-form webhooks, the
      JS-tracked landing-page embed, and the generic landing-page webhook —
      never bulk CSV/Lead Finder imports), reusing the existing SMS Outreach
      module's connected account/provider transport instead of new send
      infrastructure. NOT lead outreach: the recipient is an ops phone number
      the org itself configures (Organization.notify_new_leads +
      lead_notification_phones, migration b8e2f4a916c7), never a CRM Contact,
      so services/sms_send.send_notification() is a new, deliberately
      separate gateway path that skips the TCPA consent/suppression gate
      built for texting prospects (services/sms_consent) — reusing send()
      itself would have been wrong, since it hard-requires a Contact +
      opted-in consent record that doesn't apply to an agency's own ops
      alert. Logs to the same append-only SmsMessage ledger with a new
      kind="notification" and contact_id=None, through whichever provider
      (Twilio/Sendblue/BlueBubbles) the org's first ACTIVE SmsAccount uses —
      no separate "default account" concept existed, so this is the first
      caller of "pick one" (first by created_at). services/lead_notify.py is
      the one new hook point, called at all 4 real-time lead-creation call
      sites right alongside the existing push_contact_update(event="lead.
      created") call — found and fixed a real commit-ordering bug while
      wiring it in: 3 of the 4 sites (Google lead-form webhook, the generic
      landing-page webhook, /api/track/lead) already called db.commit()
      BEFORE their `if created:` block, so a notification's SmsMessage row
      added after that point would never persist (silently dropped on
      request teardown, since get_db never auto-commits) — fixed by moving
      each site's commit to after the notify call. notify_new_lead() itself
      never commits/rolls back the session (the caller's own commit, right
      after, covers it) and swallows every exception — a Twilio outage must
      never cost the lead that was just successfully created. API:
      GET/PUT /api/orgs/me/lead-notifications (require_team read, require_
      admin write; phones normalized to E.164 + deduped via the same
      sms_consent.normalize_phone the consent gate uses, cap 10, full
      replace). Frontend: LeadNotificationsCard on the SMS Dashboard next to
      the existing sms_opt_in_default card (admin-only, Switch + add/remove
      phone rows, a hint when the org has no connected SMS account yet).
      Tests 493 → 498 (test_sms_outreach.py, own ln_org fixture per test —
      notify_new_lead picks the org's first active account, so a shared
      module-scoped org would make "which account sent it" nondeterministic
      across a file with dozens of accounts already in it): settings
      roundtrip + validation, a real /api/track/lead lead triggering a
      captured send with the right account/body, resubmission not
      re-notifying, off-by-default, no-active-account silently skips, and a
      simulated provider outage that fails the SmsMessage row but still
      201s the lead. Verified live end-to-end on alt2: toggled the switch and
      saved a phone number through the real UI, then posted a real lead via
      curl — it hit the connected account's actual Twilio API (real
      "Authentication Error - invalid username" on the dev account's fake
      creds, proving the live-network path) and logged a failed
      kind="notification" row while the lead itself still created
      successfully (201), confirming the never-blocks-lead-creation
      guarantee under a real failure, not just the mocked test.
      SAME-DAY FOLLOW-UP (user-requested): account selection now prefers the
      org's BlueBubbles account over any other connected provider (a real
      iMessage from a personal number reads as a human ping, not a shortcode
      blast) — falls back to the first other active account for orgs with no
      BlueBubbles connected, so the feature still works everywhere. Test
      added proving BlueBubbles wins even when a Twilio account was
      connected first (_twilio_send wired to explode if mis-dispatched).
      Tests 498 → 499. DEPLOYED to production (2026-07-13, migration
      b8e2f4a916c7 applied cleanly on the live Supabase DB).
- [x] Per-client lead SMS notifications (2026-07-13, same-day follow-up):
      the agency's own ops alert (above) is org-wide; this adds a per-client
      counterpart so a client's own contact (e.g. the business owner) can
      ALSO get texted when one of THEIR leads arrives. No new columns —
      mirrors the existing external_sync convention exactly: stored in
      client.metric_settings["lead_notifications"] ({"enabled", "phones"}),
      admin-managed via GET/PUT /api/clients/{id}/lead-notifications (same
      normalize/dedupe/cap-10 validation as the org-level endpoint). services/
      lead_notify._recipient_phones combines both sources — org-wide numbers
      (if org.notify_new_leads) + this client's numbers (if its own toggle is
      on) — deduped, so a number configured in both places is texted once;
      either source alone is sufficient to fire, neither is required for the
      other to work. Frontend: ClientLeadNotifications card in the client's
      CRM setup panel (frontend/src/crm.tsx), next to LeadFormRouting —
      same Switch + add/remove phone-row UI as the org-level SMS-dashboard
      card. Tests 499 → 501 (settings roundtrip + validation, and org+client
      combining with a deduped overlapping number). Verified live on alt2:
      saved a client-level number through the real CRM-setup UI, confirmed
      via the API. DEPLOYED to production (2026-07-13) — no migration
      (metric_settings already existed on clients).
- [x] Editable lead-notification message + contacts.zip (2026-07-13,
      same-day follow-up, user-requested exact format): the SMS body was a
      hardcoded one-liner; now an admin-editable {{token}} template
      (Organization.lead_notification_template, migration d2a6f8c1b3e5 —
      NOTE the first-picked revision id collided with the existing
      c9e4a7b2d8f1 job_title migration and had to be regenerated), shared by
      both org-wide and per-client recipients (services/lead_notify.
      render_notification_body + KNOWN_TOKENS = name/first_name/last_name/
      phone/email/brand/zip/source; unknown_tokens() 422s at save time,
      mirroring the SMS/email step-editor's own token validation). Default
      template is the exact format requested:
      "*NEW LEAD*\nName: {{name}}\nPhone: {{phone}}\nBrand: {{brand}}\n
      Email: {{email}}\nZip Code: {{zip}}" — {{brand}} is the client's name.
      Blank values render as empty (never "None"); a blank/omitted template
      on save resets to the default. contacts.zip is a new first-class field
      (mirrors city/state, same migration) since the template needed
      somewhere to read it from — populated fill-blanks-only from the JS
      landing embed (LeadSubmissionIn.zip, previously only forwarded to
      dispatch_conversion and discarded), the generic landing-page webhook
      (its synonym mapper already parsed "zip" but discarded it — now
      wired through), and Google's ZIP_CODE/POSTAL_CODE lead-form column
      (new addition to _GOOGLE_COLUMNS handling, kept out of the
      **fields-spread into upsert_contact since zip isn't a core-identity
      kwarg there). Also wired zip into: ContactCreateIn/UpdateIn +
      ContactOutPublic (client-portal visible, same level as city/state),
      CSV import target + frontend header-synonym auto-detect, drawer
      "Edit info" + bulk-edit field list, and an optional lead-list column.
      API: GET/PUT /api/orgs/me/lead-notifications gained message_template +
      default_template. Frontend: LeadNotificationsCard (SMS Dashboard)
      gained a monospace textarea + "Reset to default"; ClientLeadNotifications
      (CRM setup) explicitly notes the template is shared/set on the org card.
      Tests 501 → 503 (default/custom template rendering incl. blank-value
      case, unknown-token rejection, zip round-trip through create/PATCH/CSV
      import/Google webhook, and the full default-template text asserted
      byte-for-byte against a real /api/track/lead submission). Verified
      live on alt2: typed a custom template through the real Dashboard UI,
      saved, confirmed via API; Reset to default restored the exact
      requested format; a real lead posted with phone/zip rendered
      byte-for-byte as "*NEW LEAD*\nName: Uniq Person\nPhone: 4805559877\n
      Brand: Paganelli HVAC\nEmail: ...\nZip Code: 85009" up through the
      real send dispatch (only the actual Twilio network call failed, on
      that stack's known unrelated TOKEN_ENCRYPTION_KEY mismatch for a
      pre-existing seeded account — not a code path this session touched).
      DEPLOYED to production (2026-07-13): migration d2a6f8c1b3e5 applied
      cleanly to the live Supabase DB.
- [x] Desktop app repair, take 2 (2026-07-15): the desktop-app "Failed to
      fetch" symptom traced back to a real repo bug, not just staleness —
      electron-app/main.js had been silently DELETED (no replacement) by
      commit 66c9b08 three days earlier, despite that commit's message
      describing features added to it; package.json's "main" still pointed
      at the missing file, so any rebuild since would have failed outright.
      The installed app was running a stale pre-deletion main.js against an
      increasingly out-of-date packaged backend — its bundled Alembic
      scripts predated ~15 migrations already applied directly to
      production, so `alembic upgrade head` crashed (exit 3) before the
      backend ever bound port 8000, and every frontend fetch to
      localhost:8000 failed. Fixed by reconstructing main.js (recovered
      pre-deletion version + the audit's described feature set: single-
      instance lock, no backend double-spawn on dock reactivate, spawn-
      error/unexpected-exit dialogs with an output tail, target=_blank to
      the system browser, config.json "env" merge) and doing a full rebuild
      (PyInstaller backend + frontend + electron-builder DMG). Verified live:
      installed app spawns the backend, binds 8000, /api/health 200.
- [x] Dashboard timeframe + account/campaign spend filter, click/impression
      drill-down metrics, keyword bid/match-type/pause editing (2026-07-15):
      the dashboard previously had no timeframe control at all (every widget
      silently got the backend's 30-day default) and no way to narrow spend
      to a specific ad account or campaign; clicks/impressions/CTR/CPC were
      already computed in the metrics layer but never returned to the
      frontend. Added a Today/7d/30d/90d/Custom timeframe control (native
      date inputs for custom — no calendar-picker primitive exists yet) and
      an account/campaign multi-select filter (checkboxes at both levels,
      lazy-loaded campaigns), both persisted per user+client via new
      dashboard_layouts.filters (migration c4e8f1a6b9d3, same one-JSON-blob
      Phase 4 pattern as `widgets`, which was relaxed to nullable so a
      filters-only save doesn't fabricate an empty-dashboard value).
      /api/metrics/blended + /spend-daily gained account_ids/campaign_ids
      params; insights_daily gained account_external_id (nullable,
      populated going forward) since entity_external_id+client_id
      under-determines the account for a client with 2+ accounts on one
      platform — campaign filtering instead matches the campaign_id already
      carried in InsightDaily.raw. _matches_entity_filter unions the two
      picks rather than intersecting (a real bug caught during self-review:
      checking a whole account AND a campaign under a different, unchecked
      account would otherwise zero out that campaign's rows). Overview
      widget gained Impressions/Clicks/CTR/CPC KPIs + a range-aware spend
      label; Channel mix table gained the same columns per platform.
      Keywords: the existing Google-only keyword panel (add/remove only)
      gained inline bid/match-type editing and pause/resume, reusing the
      stage->confirm->execute guardrail as-is — keywords join asset_group as
      entity types with no local cache table. fetch_keywords now also
      returns cpc_bid_micros; the add-keyword form gained a bid field
      (google_ads_api.add_keyword already accepted one, just never threaded
      through from the payload). Tests 519 → 526 (test_metrics.py
      entity-filter/union-fix + campaign-filter cases; new
      test_keyword_management.py, its own dedicated Google org fixture since
      the shared `seeded` fixture is Meta-only). Verified live on alt2
      (custom range + account filter render and persist across navigation,
      Overview KPIs render with correct empty-state formatting) — no ad
      accounts connected in that dev DB, so keyword editing itself was
      verified via a full HTTP-level test (stage → diff → execute →
      google_ads_api.update_keyword called with the right args) rather than
      click-through. DEPLOYED to production 2026-07-15: migration
      c4e8f1a6b9d3 applied cleanly to the live Supabase DB, backend/frontend
      rebuilt on the VPS, health green, new routes (/api/dashboard/filters,
      /api/metrics/blended's new params, /api/manage/changes) confirmed
      live + auth-gated (401 without a token). Same build also shipped to
      the desktop app (see the repair entry above).
- [x] Desktop app: backend-readiness handshake (2026-07-15, same-day
      follow-up): user reported a fresh "Failed to fetch" on the desktop app
      right after the repair above — root cause was a startup RACE, not a
      regression: the packaged backend takes ~5s real time (PyInstaller
      unpack + Alembic migration check) to bind :8000, but the Electron
      window loads its file:// bundle and the React app fires its first API
      calls almost instantly, with no handshake between the two. This was a
      known-but-unfixed gap flagged in an earlier session's desktop audit
      ("dynamic port + own-backend handshake chip"). createWindow() now
      opens immediately with a lightweight inline loading page and polls
      /api/health (200ms interval, 30s timeout) before loading the real
      frontend; a genuine timeout surfaces the same error-dialog + output-
      tail pattern as the existing crash dialogs. Polling helper unit-tested
      in isolation (no listener / delayed listener / 503-then-200) against a
      plain Node http server. Verified live: port 8000 confirmed NOT
      listening immediately post-launch (the exact window the race
      exploited), then healthy on the expected ~5-9s timeline. Rebuilt +
      reinstalled locally; nothing to redeploy on the webapp side (Electron-
      shell-only change).
- [x] UI smoothness pass + logo-cobalt rebrand + outreach functional
      hardening + Lead Finder owner-first (2026-07-16, three-Opus-agent
      audit→fix session; interrupted mid-run by a session limit and resumed —
      two stale-test/crash artifacts of the interruption were repaired before
      resuming). NO new migrations; NOT yet deployed to prod.
      (1) THEME — purple is gone; tokens follow the logo (navy #0f2147 /
      cobalt #2b62e0 / white): theme.css --accent light-dark(#2b62e0,#6d95f2)
      + strong/soft literals + chart-1; sidebar active pill/icon and auth
      checkmarks re-keyed to accent-derived color-mix (App.css had literal
      purples that ignored the token swap); avatar gradients →
      accent/accent-strong; branding.tsx white-label fallbacks; auth_email.py
      button hex; DESIGN.md synced. White-label contract (6 frozen names)
      unchanged.
      (2) UI SMOOTHNESS (agent, 10 audited fixes, frontend-only): per-view
      code-splitting via React.lazy (entry 268 kB; crm/email/sms/dashboard/…
      lazy chunks, per-host Suspense so a loading chunk never blanks
      siblings); workspace views KEPT MOUNTED after first visit (hidden
      view-host divs; polling gated on an `active` prop and re-fires on
      re-activation) so tab switches are instant and state survives;
      useWidgetData holds stale data during refetch (.widget-refetching
      opacity dip) instead of skeleton-flashing; keepEqual ref-stability +
      React.memo list rows for the three inbox/convo lists; 220ms debounced
      CRM filters + 250ms outreach search; @starting-style entrance on view
      switch; .btn:active press feedback; dashboard widgets fetch once
      (filtersReady gate, no default-then-refetch); ClientDetail focus
      refetch only while a connect flow is pending (oauthPending ref) or
      30s-throttled; progress fills (CRM enrich, email warmup) animate
      transform: scaleX not width. DataTable row memoization deliberately
      skipped (needs every caller's column defs stabilized — flagged, not
      worth the blast radius).
      (3) EMAIL functional fixes (agent, 7): CAN-SPAM identity block
      (org name + postal address) now appended even when the body renders
      {{unsubscribe_url}} inline (was dropped entirely — compliance gap);
      generate_ai_snippet contract is now None=transient-failure(retry,
      never cached) vs ""=ran-but-empty(cacheable) in BOTH email+SMS engines
      (a missing key/cap/timeout no longer permanently kills an enrollment's
      personalization); preview-batch issues[] gained ai_snippet_empty and
      GET /analytics gained ai_configured (org-BYO-aware) in both modules —
      surfaced as a warn Alert on both Dashboards ("AI personalization is
      off", wired by the main session; api.ts types updated); nested {{#if}}
      is a save-time 422 (was killing enrollments at send); outreach AI
      calls (email/SMS snippets, research, the parked city failsafe) now
      route through ai_provider.resolve_outreach() — a cheap-model tier
      (claude-haiku-4-5 / gpt-4o / gemini-2.5-flash, ai_outreach_model etc.
      env-overridable, PRICING already covered) while insights keep the full
      model; Gemini calls pass thinking_budget=0 (2.5-flash defaults to
      burning thinking tokens — pure cost on one-sentence snippets; guarded
      for older google-genai); account reconnect/test now REVIVES
      enrollments stranded in error status (rearm_account sums rearm_parked
      + new _revive_errored; campaign-reactivate deliberately does not —
      a broken mailbox isn't fixed by reactivating a campaign) in both
      modules.
      (4) SMS functional fixes (agent, 4 + a crash): daily/campaign cap
      queries count sent+delivered+read (delivery receipts were removing
      rows from the cap counter — silent overspend risk); the 3-segment cap
      is measured on the body WITH the compliance footer via the real
      apply_compliance_suffix; ai_configured/ai_snippet_empty surfacing as
      above; _account_out gained last_inbound_at + inbound_webhook_stale
      (non-Twilio account, active, ≥20 lifetime sends, zero inbound ever =
      the STOP-capture webhook was never registered — the one dangerous
      misconfig, since Sendblue/BlueBubbles have no 21610-style self-heal);
      plus the interruption artifact fixed by the main session: preview
      500'd on None.strip() after the contract change.
      (5) LEAD FINDER OWNER-FIRST (main session): imports should land the
      OWNER as the contact with the business name secondary. New
      lead_finder.extract_owner_from_site() — one grounded AI extraction
      over the business's OWN site text (enrichment.fetch_site_text posture;
      guardrail 6 holds), STRICT-JSON prompt, hallucination guard requires
      every returned name part to appear verbatim in the site text,
      AI-resolution checked BEFORE the crawl so keyless orgs never pay the
      fetch, metered AiUsage feature="lead_owner_extract", fail-open. Runs
      in enrich_and_verify AFTER the Apollo path, only while the contact is
      still the business-name placeholder (typed-in names never touched);
      fills first/last/job_title. Frontend: lead-list "Lead" cell renders
      person primary + company_name as a secondary ellipsized line (skipped
      while placeholder so the name never duplicates), Lead Finder copy
      updated.
      Tests 531 passing (519 → 531; the "2 failed" at session pickup were
      stale assertions against the deliberate new contracts, not
      regressions); tsc clean; vite build confirms the chunk split; live
      click-through on alt2 (cobalt everywhere incl. login/nav/avatars,
      instant tab switches, owner-first lead cell "john o'brien / Desert Air
      HVAC", both AI-off banners rendering, zero console errors). Impeccable
      findings triaged: warmup + enrich progress bars converted to scaleX;
      pre-existing --ease-spring token and the tiny guarantee progress-fill
      width transition left as documented/contained design choices.
- [x] "Schematic" UI identity (2026-07-16, same-day follow-up to the
      cobalt rebrand; user-directed: "fully unique, not the typical AI SaaS
      UI"): the whole product re-registered as a precision technical
      document / instrument panel — navy+cobalt read as blueprint coloring.
      Frontend-only (+ index.html font preloads); NO backend changes, NO
      migrations, NOT yet deployed. Spec: DESIGN.md v3 header (supersedes
      v2's visual register; v2 information-design rules stand). Core (main
      session): IBM Plex Sans/Mono self-hosted (public/fonts + styles/
      fonts.css, ~140KB, loaded before theme.css); theme.css pivot — radii
      2/3/4/6px, shadow-xs/sm=none (hairlines separate resting surfaces;
      floats keep shadows), glass→"vellum" (92–97% opacity, 6px blur),
      --grid-line/--grid-size drafting-grid canvas (drawn by base.css body,
      brand-derived so tenants repaint), --tick-ink/--tick-len corner
      registration marks, --tracking-caps 0.08em, --ease-spring→crisp
      quint-out (no overshoot anywhere); ui.css primitives — .card corner
      ticks, .kpi mono engraved label + tabular mono readout (hero gradient
      text REMOVED), .badge square mono uppercase stamp, thead mono
      captions + td.align-right mono tabular, toast/confirm-dialog side-tab
      borders → tone on the full hairline (the "AI SaaS tell" killed
      everywhere; clients.css .cl-tree-panel side-tab + .cl-avatar gradient
      too). Three-Opus-agent fan-out on a pinned contract (scratchpad
      SCHEMATIC_CONTRACT.md): shell/auth agent — nav pill squared to
      accent-16% wash + accent hairline, mono section heads, sidebar-foot
      mono "title block" org label, mono breadcrumb-current, auth aurora →
      night-blueprint sheet (header gradient + drafting grid + ONE drawn
      registration crosshair, @keyframes aurora deleted), login card flat
      vellum + corner ticks, Plex preloads in index.html (App.css
      deliberately untouched — its shell/auth rules are dead layer(legacy)
      duplicates; live selectors are shell.css/auth.css); workspace agent —
      dashboard widget titles = numbered mono drawing labels (CSS counters
      on .dash-grid), guarantee progress → scaleX + mono numerals, kanban
      lane hairline rules, drawer dotted label→value leaders (pure CSS
      dt::after) + corner ticks, enrichment card hairline + mono percent
      readout; outreach/settings agent — numbered "01 /" section labels
      IDENTICALLY in eml/sms/or scopes (counter + mono overline + hairline
      rule fill), campaign step editors = procedure sheets (square mono
      step chips, ruled heads, dotted connector rail on .eml-steps/
      .sms-steps wrappers — className-only TSX hooks), inbox unread =
      inset accent edge + mono NEW stamp, TOKENS_HINT → mono spec-sheet
      blocks, settings .set-plan aurora + branding-preview gradient →
      tokens. Frozen white-label contract, light-dark() mechanism, chart
      palette, and all component behavior untouched; changed-file grep = 0
      literal colors outside theme.css. tsc clean, vite build green
      (chunk split intact), live click-through on alt2 in BOTH themes
      (light "drafting paper" + dark "night blueprint": login crosshair
      sheet, CRM lanes/stamps/mono captions, campaign procedure steps +
      spec-sheet token hints all verified; zero console errors). Aurora
      tokens remain defined in theme.css but unused on shipped surfaces —
      candidates for deletion in a future sweep.
      DEPLOYED 2026-07-16 (b6c2701), web + desktop, together with the
      2026-07-16 outreach-hardening commit 187a077 (both were undeployed):
      no migrations pending (alembic head c4e8f1a6b9d3 unchanged), git
      archive → VPS, backend/frontend images rebuilt, /api/health ok,
      app.salescale.lol serves the Schematic bundle (Plex @font-face +
      woff2 200s + --grid-line confirmed via curl), new analytics routes
      auth-gated. DESKTOP: font-path fix first (b6c2701 — public/fonts +
      absolute /fonts/ URLs break under file://; moved to src/assets/fonts
      through Vite's pipeline → emitted url(./<hash>.woff2) relative, loads
      from web AND file://; static preloads dropped, font-display swap
      covers first paint); PyInstaller backend + electron-builder DMG
      (141MB, Resources/backend/main hash-matched, 7 woff2 in asar),
      launch-verified from the packaged app (own backend bound :8000,
      health ok, alembic no-op against live Supabase) with the Schematic
      UI rendering under file:// against real prod data (mono readouts,
      numbered widget labels, grid, ticks — screenshot-verified), then
      installed to /Applications and re-verified. DMG artifact:
      electron-app/dist/Salescale-0.1.0-arm64.dmg.
      SAME-DAY FOLLOW-UP — desktop Google Ads sync fix: the dashboard's
      "Synced with issues: google: Specified service GoogleAdsService"
      does not exist in Google Ads API v24" is google-ads-python's
      CLIENT-SIDE get_service() error (mismatched quote is verbatim from
      their client.py), raised when the versioned service module fails to
      IMPORT — not a Google server/version problem. google-ads loads
      google.ads.googleads.v{N}.* dynamically, so PyInstaller never
      bundled them (same class as the 07-12 AI-SDK gap; prod container
      imports v24 fine, web was never affected). main.spec now
      collect_submodules('google.ads.googleads.v24') — bump alongside
      library upgrades, google.ads.googleads.client._DEFAULT_VERSION is
      the authority (lib 31.1.0 = v24; requirements.txt leaves google-ads
      unpinned and the Docker layer cache means prod only picks up new
      libs when requirements.txt changes). Binary 55→59MB, DMG 148MB,
      1,711 v24 modules verified in the PYZ. Proven END-TO-END on the
      installed app via CDP (relaunched with --remote-debugging-port,
      Runtime.evaluate fetch with the app's own session; port closed +
      normal relaunch after): POST /api/insights/sync returned google
      ok:true with real row counts for both Google-connected clients.
- [x] Email campaigns: multi-mailbox sending pool with rotation
      (2026-07-16, user-requested "cycle through inboxes to maximize sends
      per campaign and account"). NOT yet deployed (adds migration
      e7b4a9d2c6f1). Model: email_campaign_accounts join table (campaign →
      N mailboxes, position = rotation/display order, unique per pair) +
      email_enrollments.account_id (nullable, indexed) — the STICKY
      per-contact mailbox. Rotation is PER-CONTACT, not per-message, by
      design: reply-in-thread follow-up steps must send from the same
      mailbox or the conversation breaks for the recipient; the pin is
      written only on the first SUCCESSFUL send (before that, every
      attempt re-picks, so nobody gets pinned to a mailbox that never
      landed a send). Picker (email_campaigns._pick_account): among ACTIVE
      pool mailboxes, most remaining capacity today (warmup-ramped
      effective_daily_cap − gateway.sends_today) — equal caps alternate
      round-robin, a half-warmed mailbox contributes exactly its ramp,
      total throughput = sum of the pool. Engine: process_enrollment
      resolves pinned account (inactive → park, reconnect re-arms) or
      picks (no active mailbox → park; all active capped → defer 1h,
      CAP_REACHED cadence); rearm_account finds campaigns via legacy
      account_id OR the pool table (test proves revive through a
      NON-primary pool member); campaign.account_id kept as pool[0]
      (legacy mirror). Migration backfills one pool row per campaign and
      pins only enrollments with thread_id (already-conversing); never-
      sent stay NULL to rotate. API: EmailCampaignIn/Patch gained
      account_ids (1-10, deduped, org-scoped-validated; account_id still
      accepted → one-mailbox pool); PATCH account_ids allowed while
      ACTIVE (pinned contacts keep their sender even if unchecked —
      documented in _set_campaign_pool; legacy account_id swap keeps its
      pause-first 409); activate requires ≥1 ACTIVE pool mailbox; account
      DELETE guard + cleanup extended to pool rows and enrollment pins.
      Frontend: New-campaign dialog + Config tab replace the single
      mailbox select with a checkbox pool (≥1 enforced) + rotation hint.
      Tests 531 → 536 (rotation 2/2 distribution + step-2 pins match
      step-1 senders per contact + all complete; caps 1+2 absorb 3
      contacts with zero deferrals; all-capped defers unpinned without
      pinning; non-primary pool member error parks only its contacts and
      /test revives them via the pool-table lookup; API contract incl.
      account_id mirror + PATCH-while-active). Verified live on alt2:
      create dialog pool, Config-tab PATCH round-trip (account_ids grew
      to both mailboxes via the real UI), zero console errors.
      DEPLOYED 2026-07-16 (d192fca), web + desktop: web first so
      migration e7b4a9d2c6f1 applied through the standard container-boot
      flow (docker exec alembic current = e7b4a9d2c6f1 head on live
      Supabase), health green, campaigns route auth-gated, fresh bundle
      served; then PyInstaller backend + DMG rebuilt (148MB, binary
      hash-matched), installed to /Applications, launched — desktop boot
      alembic no-op'd (already at head), /api/health ok, app renders
      logged-in against the live org.
- [x] CSV import bad-row isolation (2026-07-16): user hit "An unexpected
      error occurred" on CSV CRM import. Root cause: a cell longer than a
      Postgres column cap (zip String(20), state 64, phone 50, city 120,
      name 150…) raised DataError at the per-row db.flush(), which sat
      OUTSIDE the loop's only try (CustomFieldError-only) → aborted the whole
      import transaction → generic 500. NEVER reproduced in tests because
      SQLite silently ignores String length caps; only Postgres (prod)
      enforces them — the exact "works in tests, 500s in prod" trap the
      generic-500 catch-all lesson warned about. Fix (api/crm.py
      import_contacts): each row's DB writes (Contact build, company resolve,
      validate_and_merge, add, flush) run in db.begin_nested() — any row
      exception (DataError/IntegrityError/anything) rolls back just that
      savepoint, records the row in `failed`, and the good rows still commit;
      company ids cached only AFTER a row's savepoint releases so a
      rolled-back row can't leave a stale cache id. test_platform_error_
      surfacing's catch-all-CORS test was repointed off CSV import (now
      guarded) onto contact-create (still calls get_or_create_company
      unguarded) so the generic-500-with-CORS guarantee stays covered.
      Tests 536 → 537 (test_crm_contacts.test_csv_import_bad_row_isolated_
      not_500 injects the flush-time error SQLite can't produce). DEPLOYED
      2026-07-16 (c31fbf1) web + desktop, backend-only (no migration, no
      frontend change): VPS backend rebuilt + recreated, health green,
      alembic still e7b4a9d2c6f1; desktop PyInstaller backend + DMG (148MB,
      hash-matched) reinstalled + launch-verified.
- [x] Gemini default + owner-selectable AI provider/model (2026-07-16):
      user asked to make Gemini the default and let users pick the active
      model. (1) settings.ai_provider default anthropic → gemini (operator
      global fallback). (2) Organizations gained owner-selectable ai_provider
      + ai_model columns (migration f1c3e9a7b2d4, nullable = inherit operator
      default; an explicit ai_model applies to BOTH insights and outreach for
      that org). services/ai_provider: active_provider/active_model/resolve/
      resolve_outreach are now org-aware via a shared _resolve(db, org,
      default_model_fn) — org override wins, else operator default; new
      SELECTABLE_MODELS registry is the per-provider dropdown menu AND the
      save-time whitelist (every entry also in PRICING so metering never
      falls back to DEFAULT_PRICE). API: PUT /api/integrations/ai-provider
      (require_owner) sets provider+model (400 on unknown provider/model,
      model=null resets to the provider default); GET returns active +
      model + org_selected + available{provider:[models]}. Frontend: the
      Integrations "AI provider" card gained provider pills (Gemini/
      Anthropic/OpenAI, active = accent-wash disabled) + a model dropdown
      (owner-editable, default marked, mono), both calling the PUT and
      showing a toast; non-owners see it read-only. Tests 537 → 541
      (test_integrations: default-is-gemini + model-menu, owner selects
      provider/model, unknown-model 400, owner-only gate; test_personalize:
      default-is-gemini + org-override split replacing the old anthropic-
      default cheap-model test; test_email_campaigns/test_sms_outreach
      outreach-model assertions updated haiku → gemini-2.5-flash for the new
      default). Verified live on alt2 end-to-end: card shows Gemini active,
      clicking the Anthropic pill persisted (active=anthropic, model=
      claude-opus-4-8 default, org_selected=true, model menu re-keyed to
      Claude models), reset returns to gemini/gemini-2.5-flash; zero console
      errors. DEPLOYED 2026-07-16 (a7c1e26) web + desktop: migration
      f1c3e9a7b2d4 applied to live Supabase via the container-boot flow
      (alembic current = f1c3e9a7b2d4 head), health green, endpoint
      auth-gated; desktop PyInstaller backend + DMG (148MB, hash-matched)
      reinstalled + launch-verified (boot alembic no-op'd, already at head).
      NOTE: prod still has no AI key in backend/.env, so AI features stay
      off until an operator/owner adds a Gemini key (Integrations → AI
      provider card, or GEMINI_API_KEY on the VPS) — the default flip just
      changes WHICH provider a key is expected for.
- [x] Branding mailing-address field (2026-07-16): user couldn't activate a
      cold-email campaign — "Set your organization's mailing address
      (Branding)" — despite having "set the email". Root cause: the
      Branding page had NO mailing-address input at all; the org.branding
      .mailing_address the activation gate reads (api/email_outreach.py:955)
      was unsettable. The user had set email_from_address (sender), a
      different field. Backend already accepted+stored mailing_address
      (BrandingIn + PUT /api/orgs/me/branding do org.branding = payload);
      three frontend gaps fixed: OrgBranding type lacked mailing_address,
      save()'s PUT body OMITTED it (so it'd be wiped on every save), and
      there was no input. Added a "Mailing address" field to the Branded
      email section with a CAN-SPAM footnote (not client-facing), threaded
      into save(), and guarded product_name's input value against null
      (pre-existing console warning on the same page). Frontend-only — no
      backend change, no migration. Verified on alt2: the exact save() body
      persists the address (PUT 200, stored), field renders in the tree
      with its footnote; the activation gate reads the same field so it now
      passes once set. DEPLOYED 2026-07-16 (d04c445) web (frontend image
      rebuilt/recreated, app 200) + desktop (DMG 148MB rebuilt, installed,
      health ok). LATENT FLAG (not fixed — out of scope, touches
      entitlements): PUT /api/orgs/me/branding is gated by
      _require_white_labeling, currently a `True` stub so harmless, but when
      the entitlement flip wires tiers this compliance field (needed by
      every cold-email tier) would be trapped behind the white-label
      paywall — decouple mailing_address from that gate as part of the
      Stripe/entitlements pass.
- [x] View sent cold emails in the Email inbox (2026-07-16): user couldn't
      see sent cold emails in the Inbox tab. Diagnosed on prod: 99 outbound
      email_messages but 0 threads — all 99 were WARMUP (correctly threadless
      /excluded), i.e. no real campaign sends had happened yet, and the inbox
      (which lists EmailThreads) was framed reply-only (empty state "Replies
      to your campaigns show up here"). Every campaign/manual send DOES upsert
      a thread (email_outreach_send send() line ~277, to_contact != None), so
      sent cold emails already land in the inbox — the gap was surfacing +
      filtering them. Added: GET /api/email-outreach/inbox `filter` param
      (all | awaiting | replied) driven by the thread's existing
      last_inbound_at (awaiting = sent, no reply yet; replied = has inbound);
      inbox "All / Sent / Replied" Segmented control; per-thread Sent
      (neutral) / Replied (ok) badge (.eml-thread-foot); filter-aware empty
      states; EmailThread type + listEmailThreads gained last_inbound_at /
      filter. Backend param + frontend only — NO migration. Tests 541 → 542
      (test_email_outreach.test_inbox_filter_awaiting_vs_replied: two sent
      threads, one gets a synced reply → All shows both, awaiting excludes
      the replied, replied returns only it). Verified live on alt2 with two
      seeded sent threads (one replied+unread, one awaiting): All showed both
      with correct badges, Sent filter narrowed to the unanswered one, the
      sent message body opened in the thread pane; seed cleaned up after.
      NOTE surfaced during diagnosis: the running alt2 uvicorn has no
      --reload, so a backend edit needs a preview restart before live API
      checks reflect it (pytest already covered the logic). DEPLOYED
      2026-07-16 (62d3e7f) web (backend+frontend rebuilt/recreated, health
      green, filter endpoint auth-gated) + desktop (PyInstaller backend +
      DMG 148MB, installed, health ok).
- [x] "Q2 CPA campaign isn't sending" — three cascading bugs (2026-07-16,
      diagnosed live on prod). (1) TIMEZONE: the campaign's timezone was
      saved as "MST", which zoneinfo can't load (abbreviations aren't IANA
      keys + absent from slim-container tzdata), so email_campaigns._tz()
      swallowed the error and fell back to UTC — the 8am-5pm window was
      evaluated as 8-17 UTC (=1am-10am Mountain), already closed, so all 87
      enrollments parked to the next UTC window (the following Monday), never
      sending. New services/timezones (normalize() maps MST/EST/PST/… →
      IANA city zones + validates; resolve() is the runtime lookup that also
      rescues abbreviations already stored, no migration). email + SMS _tz()
      use it (SMS: also protects TCPA quiet-hours from wrong-zone eval).
      Email + SMS campaign create/patch now VALIDATE + canonicalize the
      timezone (422 on garbage) instead of silently storing an unloadable
      one. (2) CASCADING MAILBOX-DISABLE: after re-arming, sends started but
      several contacts had two emails comma-joined ("a@x.com, b@x.com" from
      CSV import) → SMTP 501; the gateway treated EVERY send failure as a
      mailbox auth problem and set account.status="error", so ONE bad
      address disabled all 3 pool mailboxes and parked the other 80
      enrollments. Fix: new email_transport.EmailRecipientError (recipient-
      refused: send_message `refused` dict + SMTPRecipientsRefused); gateway
      pre-flight rejects a malformed/multi-address recipient before SMTP and
      catches EmailRecipientError separately → marks the contact email
      invalid + returns BLOCKED (engine exits that enrollment like a bounce,
      no retry) while leaving the mailbox ACTIVE. Only true mailbox
      auth/connection errors still flip account.status. (3) ROOT DATA/IMPORT
      BUG: CSV import stored a multi-address email cell verbatim. Import now
      takes the first valid address as `email` and keeps all in
      candidate_emails; the 19 existing such prod contacts were cleaned up
      the same way (first address, both kept as candidates, verification
      reset). Tests 542 → 549 (test_timezones; email campaign tz
      validation; malformed-recipient isolation keeps mailbox active +
      SMTP-refused → BLOCKED; CSV multi-address split). PROD OUTCOME: fixed
      the campaign's timezone → America/Phoenix (its Arizona zone, matching
      the warmup mailboxes), re-armed all 87 — the campaign then sent
      cleanly: 77 delivered, 76 advanced to the follow-up step, ~11
      bad-address contacts exited as bounced, all 3 mailboxes stayed
      ACTIVE throughout. DEPLOYED 2026-07-16 (timezone ab63334, recipient
      isolation bbacef2, CSV split 13bed7c) web + desktop (DMG 148MB
      rebuilt, installed, health ok). Note: the ~11 exited bad-address
      contacts in Q2 CPA are now cleaned/deliverable but NOT re-enrolled —
      re-enrolling them is the user's call.
- [x] Inbox thread-open crash fix (2026-07-17): clicking an email in the
      Email → Inbox tab blanked the whole view to the bare drafting-grid
      background. Root cause: GET /email-outreach/threads/{id}/messages
      returns {thread, messages} (has since the module was first built), but
      api.ts's listEmailThreadMessages was typed AND used as a bare
      EmailMessage[] — so setMessages got the wrapper object and the render's
      messages.map(...) threw, crashing the React subtree to just the
      Schematic canvas. Never surfaced until now because until the Q2 CPA
      campaign actually sent, the org had ZERO real threads to click (warmup
      messages are threadless/excluded), so nobody ever opened one. Fix:
      listEmailThreadMessages now unwraps .messages. Frontend-only, no
      backend change, no migration. Verified live on alt2 by seeding one
      sent thread + message: clicking it now opens the thread pane with the
      subject, contact line, message bubble ("sent · just now"), and reply
      composer — zero console errors (only pre-existing controlled-input
      warnings). Seed cleaned up after. DEPLOYED to production 2026-07-17
      (bd91810) web (frontend image rebuilt/recreated, app + api 200) +
      desktop (frontend rebuilt, DMG 155MB rebuilt reusing the unchanged
      backend binary, installed to /Applications, launch-verified backend
      :8000 health 200).
- [x] SMS "not sending" diagnosis — BlueBubbles chat-creation + SMS routing
      (2026-07-17): user's active "CPA OUTREACH" campaign (BlueBubbles/iMessage
      account, +16232967782, relay imessage-relay.salescale.lol) was NOT
      dead — the scheduler picked it up every tick and fired sends, but every
      one 500'd. Diagnosed live on prod: the relay ping/verify + server/info
      were healthy (private_api:true, helper_connected, iMessage signed in as
      salescale@icloud.com), but message/text returned
      {"message":"Message Send Error","error":{"message":"Chat does not
      exist!"}} — because services/sms_send._bluebubbles_send targeted an
      EXISTING chat guid (iMessage;-;<num>) and a cold prospect has never been
      messaged from that Mac, so no chat exists. Reproduced the exact 500.
      SECOND finding (the bigger one): swept handle/availability across the
      26 active recipients — only 2 are iMessage-registered; 24 are plain cell
      numbers. iMessage physically can't reach them without SMS Text Message
      Forwarding on the host Mac. FIXES (backend-only, no migration, web
      deploy only — desktop runs no schedulers so SMS sending is server-side):
      (1) _bluebubbles_send now resolves each recipient's service via
      handle/availability/imessage (iMessage if registered, else SMS; defaults
      iMessage on lookup failure) and builds the chat guid with that service;
      (2) on the first message it falls back from message/text to chat/new
      (_bluebubbles_create_chat_send, service-aware) which CREATES the
      conversation and sends the opener — so first-contact no longer fails;
      threaded follow-ups still reuse the now-existing chat; (3) surfaces the
      relay's specific nested error.message instead of the generic "Message
      Send Error". Verified live: resolve returns SMS for a non-iMessage
      prospect and iMessage for a registered number. Tests: 2 new unit tests
      (create-chat-on-missing, non-iMessage→SMS routing, specific-error) —
      test_imessage_outreach 20→22, sms suites 74 pass. DEPLOYED to production
      2026-07-17 (a15b57f chat-creation, then service/SMS routing) — backend
      rebuilt/recreated on the VPS, /api/health 200. USER-SIDE PREREQUISITE
      for the 24 SMS numbers: enable "Text Message Forwarding" on the Mac via
      a paired iPhone (Settings → Messages → Text Message Forwarding → allow
      carter's MacBook), iPhone online w/ cellular — until then SMS-service
      sends fail (chat/new can't send SMS without forwarding). The 2
      iMessage-capable prospects now send on the next tick; the errored/parked
      SMS enrollments are NOT re-armed yet (would re-fail pre-forwarding) —
      re-arm once forwarding is confirmed. Caveat noted to user: bulk SMS via
      a personal iPhone's number is far less robust than the connected
      Sendblue account (carrier spam-flagging / TCPA) — Sendblue remains the
      alternative if iMessage+forwarding underperforms.
- [x] {{state}} personalization "not working" — missing lead geo, derived from
      area code (2026-07-17): the SMS/email render engines resolve {{state}}
      from contacts.state correctly; the real cause was DATA — all 31 CPA
      OUTREACH leads (and 177 of 281 org-wide) had state blank, so the token
      rendered empty and "tax advisory in {{state}}." collapsed to "in.".
      These leads carried only a name + phone (empty source_detail — not even
      Lead Finder's stored address/query), so the one reliable geo signal is
      the phone AREA CODE. New services/area_codes.py: NANP US area-code -> 2-
      letter-state map + state_for_phone() (strips +1, ignores toll-free/non-
      US). Wired into lead_finder.enrich_and_verify fill-blanks-only (no
      provider key needed, so the existing bulk "Enrich" button + auto-post-
      import enrichment both backfill state; a human/enrichment value always
      wins). Backfilled the live org via backend/scripts/backfill_state_from_
      area_code.py (dry-run then --write): 177 contacts filled (86 AZ home
      base + a sane nationwide spread), zero overwrites. Verified live: all 31
      CPA enrollments now render "Hey is this Paul? Saw you guys do tax
      advisory in NC." Caveat: area code -> state is best-effort (number
      portability), fill-blanks-only and correctable in the CRM. Tests +2
      (test_area_codes.py). DEPLOYED to production 2026-07-17 (backend
      rebuilt/recreated, /api/health 200) — backend-only, no migration.
- [x] Public privacy policy + Meta Instant-Form lead auto-import enablement
      (2026-07-19): (1) Static, un-authenticated privacy policy at /privacy
      (frontend/public/privacy.html + nginx `= /privacy` route — served
      directly by nginx, not the SPA, so external reviewers reach it without
      login; covers Meta/Google-API data handling + GDPR/CCPA deletion). LIVE
      https://app.salescale.lol/privacy (commit a46e114). (2) Meta lead
      auto-import turned on: META_SCOPES += leads_retrieval, pages_show_list,
      pages_read_engagement, pages_manage_metadata (667875b);
      META_WEBHOOK_VERIFY_TOKEN set in prod .env so the existing app-level
      leadgen webhook (/api/webhooks/meta/leadgen) verifies (handshake
      verified live); Meta connect now auto-subscribes every managed Page to
      the leadgen field AND writes a per-page LeadFormConfig routing leads to
      that client — services/meta_leadgen.subscribe_client_pages (best-effort,
      tenant-safe: never hijacks a page already routed to another org/sibling
      client), wired into api/connect_meta callback + new
      meta_api.fetch_pages_with_tokens / subscribe_page_leadgen (42539da).
      All DEPLOYED (backend rebuilt/recreated on the VPS). REMAINING
      user-side: PUBLISH the Meta app (Meta's own banner: NO leadgen webhook
      delivers — even for admins — until the app is published), App Review /
      Advanced Access for leads_retrieval + pages perms, register the
      app-level Webhooks→Page→leadgen callback (verify token above), and
      reconnect each client's Meta so the new-scope token + auto-subscribe
      fire. Diagnosis: the app was merely UNPUBLISHED (dev mode), NOT
      Meta-restricted (Graph API showed no restrictions); the invalid_grant
      error the user attributed to Meta was actually a revoked GOOGLE refresh
      token. NOT built yet — Salescale server-side auto-upload of Google
      offline conversions: flagged in the same Atlas Reach client-ops thread
      (client's conversion action "Submit lead form (Page load thank-you)" is
      WEBPAGE_CODELESS / non-uploadable, so every manual + API upload 400s
      "not set up for uploading conversions"; resolved client-side by
      creating an UPLOAD_CLICKS offline action id 7690364876 on customer
      5170352227, fed by a connected Google Sheet). Wiring the Phase-5
      pipeline (per-client Google ConversionConfig → action 7690364876 + a
      dispatch call on the landing_page_webhook capture path) to auto-fire
      future leads is the remaining product task. Client also double-fires
      conversions to a foreign Google conversion ID 18292295114 alongside
      its own 18291942873 — client-side tag cleanup.
- [x] SMS auto-enroll new leads (2026-07-19): a new lead arriving for a
      client can now be auto-enrolled into that client's SMS
      qualifying-questions campaign the moment it lands — the "speed to
      lead" automation the SMS module was missing (enrollment was manual /
      house-CRM-by-list only; only the team ALERT fired on arrival). New
      SmsCampaign.auto_enroll_new_leads (migration c7e1a9f3b2d8, additive
      non-null server_default false). services/lead_autoenroll.py is the
      outreach counterpart to lead_notify: called at the SAME four
      lead-creation sites (api/leads.py /api/track/lead; api/lead_webhooks
      Meta leadgen / Google lead-form / generic landing-page), right after
      notify_new_lead, best-effort (never commits/rolls back, never blocks
      lead creation). It finds ACTIVE campaigns where client_id ==
      contact.client_id AND auto_enroll_new_leads, and routes the single
      contact through sms_campaigns.enroll_contacts — so the TCPA consent
      gate (sms_consent.sendable) STILL holds: a lead with no recorded SMS
      opt-in is skipped, never force-texted (org sms_opt_in_default is what
      makes inbound form leads eligible; STOP/suppression always win).
      SmsCampaign already had a nullable client_id; the API now REQUIRES it
      when auto_enroll is on (create + patch both 422 otherwise, and patch
      refuses to clear the client while the flag is on) since the trigger
      needs to know whose leads flow in. api/sms_outreach _campaign_out +
      create/patch carry the field; SmsCampaignIn/Patch gained it. Frontend
      (sms_outreach.tsx ConfigForm): the campaign Config tab gained a Client
      picker (was never surfaced — client_id existed on the model but no UI
      set it) + an "Auto-enroll new leads for this client" Switch, disabled
      until a client is chosen, with a client-name-aware hint; api.ts gained
      listClients + client_id/auto_enroll_new_leads on the SMS types. Tests
      549 → 556 (test_sms_outreach.py: end-to-end landing-webhook → trigger
      → enroll → run_due send; consent-skip when unconsented; the
      client-required guard on create + patch). Verified live end-to-end on
      a local stack (real login, created a client-scoped campaign, Client
      picker + toggle round-trip → GET confirmed client_id + auto_enroll
      true). DEPLOYED to production 2026-07-19 (66ad118): git archive → VPS,
      backend+frontend rebuilt/recreated, migration c7e1a9f3b2d8 applied to
      the live Supabase DB (alembic current = c7e1a9f3b2d8 head), /api/health
      ok, /api/sms/campaigns auth-gated. REMAINING (user-side, guided
      in-app): create + activate the two qualifying-question campaigns
      (Best Spas Direct, Paganelli HVAC) in SMS → Campaigns — scope each to
      its client, add the 2-step qualifying sequence, flip auto-enroll on,
      pick the send-from number, activate. Nothing texts a lead until those
      are activated. NOTE: the compliance footer on step 1 prepends the
      AGENCY org name (SMS accounts are org-level) — the client name lives in
      the body so the lead knows who it's from.
- [x] Org + client default timezone (2026-07-19): first-class timezone
      settings at the agency (app) level and per client, so campaign
      send-window / TCPA quiet-hours default to the right local time instead
      of typing an IANA name per campaign. New columns organizations.timezone
      + clients.timezone (migration d3b8f1a4c920, additive nullable; NULL =
      inherit). services/timezones.campaign_default(client_tz, org_tz,
      fallback) resolves the chain (each candidate normalized, so a stored
      abbreviation still yields a real IANA key). A NEW SMS campaign inherits
      client → org → "America/New_York"; a NEW email campaign (no client tier)
      inherits org → "UTC"; an explicit timezone in the create request still
      wins. Changing a setting NEVER rewrites existing campaigns (new-only) —
      SmsCampaignIn/EmailCampaignIn.timezone became Optional (None = inherit,
      resolved in the create endpoints). Endpoints: PUT /api/orgs/me/timezone
      (require_admin) + PUT /api/clients/{id}/timezone (require_admin), both
      IANA-validated by the existing _valid_campaign_timezone (null clears);
      OrganizationOut + ClientOutTeam now carry timezone. Frontend: an
      "Organization timezone" card on the Branding settings page (self-
      contained OrgTimezoneCard — there's no separate org-settings page; it's
      NOT branding), a "Client timezone" card in the client's CRM setup panel
      (crm.tsx, next to lead-notifications), and the SMS campaign Config
      applies the SELECTED client's timezone to the campaign on pick (keyed tz
      input re-mounts to show it). api.ts gained setOrgTimezone/
      setClientTimezone/getClient + timezone on Org/Client. Tests 556 → 560
      (org+client tz endpoints incl. abbreviation-normalize + invalid-422 +
      null-clear; SMS client>org>default inheritance + explicit-wins; email
      org>UTC inheritance). Verified live on the local stack (Branding org tz
      save → America/Phoenix persisted; client tz endpoint round-trip;
      selecting Paganelli in SMS Config auto-applied its America/Phoenix,
      persisted on the campaign). DEPLOYED to production 2026-07-19 (2277c16):
      git archive → VPS, backend+frontend rebuilt/recreated, migration
      d3b8f1a4c920 applied to the live Supabase DB (alembic current =
      d3b8f1a4c920 head), /api/health ok, PUT /api/orgs/me/timezone auth-gated.
- [x] SMS "Send at all hours (24/7)" toggle (2026-07-20): a per-campaign
      Config toggle (frontend/src/sms_outreach.tsx ConfigForm) for immediate
      replies to inbound leads at any hour — sets send_window_start=0 /
      send_window_end=24 / send_days=[0..6] and hides the window/day pickers.
      NO backend/schema change: sms_send.in_send_window already reads
      0 <= hour < 24 on all days as always-open, and SmsCampaignIn/Patch
      already allow end=24; the only gap was the UI hour dropdowns topping out
      at 11pm (HOURS = 0..23), so a full day wasn't expressible. Toggle-off
      restores a standard 8am–9pm Mon–Fri window. The consent gate
      (sms_consent) and STOP/suppression are UNCHANGED and still enforced —
      this only lifts the quiet-hours SEND WINDOW, which is defensible for a
      direct response to a consumer-initiated inbound lead (not cold sends);
      the UI note says as much. Test test_24_7_window_is_always_open
      (in_send_window True at 2am for 0–24/all-days, False for 8–21/Mon–Fri).
      Tests 560 → 561. Verified live on the local stack (toggle on → window/day
      controls collapse → campaign persists 0–24/all-7-days/America/Phoenix).
      DEPLOYED to production 2026-07-20 (4826738): frontend rebuilt/recreated
      on the VPS (frontend-only + a backend test — no migration), /api/health
      ok, app serving the new bundle.
- [x] Two-way lead-reply relay over BlueBubbles (2026-07-20): when enabled
      (Organization.lead_relay_enabled + lead_relay_phone, migration
      e2f9c1a7d4b6), an inbound lead reply on the org's BlueBubbles number is
      forwarded to the operator's phone, labeled with the lead's name + a reply
      CODE (the lead number's last 4); the operator replies starting with that
      code ("1234 on my way") and services/lead_relay.py routes the rest back
      to that lead through BlueBubbles. TAG-based routing (user's choice over
      sticky last-lead) resolves the lead directly — no stored active-
      conversation state, unambiguous with concurrent leads. Forward →
      sms_send.send_notification (operator's own number, no consent gate);
      reply to the lead → NEW sms_send.send_reply (skips the opt-in gate since
      the lead texted first, still honors STOP/suppression + account cap,
      records kind=manual on the lead). Wired into sms_webhooks._process_inbound:
      a text FROM the relay phone is an operator command (routed, never treated
      as a lead/STOP/enrollment), every other genuine (non-STOP) reply is
      forwarded. BlueBubbles-only (account.provider gate); Twilio/Sendblue
      inbound untouched. All best-effort — a relay failure never breaks the
      inbound webhook. Config: GET/PUT /api/orgs/me/lead-relay (require_admin;
      enabling requires a phone, normalized to E.164 so the webhook recognizes
      the operator). Frontend: a "Forward lead replies to my phone" card on the
      SMS Dashboard (admin), warns when no BlueBubbles account is connected.
      Tests 561 → 564 (config validation incl. enable-without-phone 422; full
      forward→tagged-reply round-trip with the lead-linked outbound; no-code
      reply gets a help nudge, texts no lead). Verified live on the local stack
      (card renders, phone saves + normalizes through the real endpoint, toggle
      reflects the enabled state). DEPLOYED to production 2026-07-20 (0b661e5):
      migration e2f9c1a7d4b6 applied to the live Supabase DB, backend+frontend
      rebuilt/recreated, /api/health ok, /api/orgs/me/lead-relay auth-gated.
      USER-SIDE to go live: a connected BlueBubbles account, the operator phone
      (480-720-7351) reachable by the BlueBubbles Mac (iMessage, or SMS Text
      Message Forwarding for a non-iMessage number), and the BlueBubbles
      inbound webhook delivering (already set up for the CPA campaign).
- [x] Per-client lead-notification message template (2026-07-20): a client
      can override the org-wide lead-alert template for its OWN leads;
      resolution chain is client → org → built-in default
      (services/lead_notify.resolve_template, mirrors the timezone
      campaign_default chain). One body per lead still goes to all recipients
      (org ops numbers + the client's own) — a client customizing its template
      customizes the alert for all of that client's leads. Stored next to the
      client's phones in client.metric_settings["lead_notifications"]
      ["template"] — NO migration (JSON bag; Organization.lead_notification_
      template already existed). GET/PUT /api/clients/{id}/lead-notifications
      now carry message_template (the client's own, null → inherit) +
      default_template (the org template it falls back to, or the built-in),
      token-validated at save against the same {{token}} set as the org
      (lead_notify.unknown_tokens → 422). Frontend: the client's "Lead SMS
      notifications" card (crm.tsx ClientLeadNotifications) gained a "Use a
      custom message for this client" toggle → monospace template editor +
      "Reset to org template" (inherits + shows the org template when off);
      org card (sms_outreach.tsx) copy clarified as the per-client default.
      Tests: test_sms_outreach client roundtrip updated for the new response
      keys + new override test (client template wins, unknown-token 422,
      clear→falls-back-to-org). Verified: tsc clean, prod vite build clean,
      78 tests pass across test_sms_outreach + test_crm in a throwaway
      container off the prod image (local Python venv is unprovisioned/broken,
      so no live browser preview this session — the client card is a
      structural mirror of the shipped org card). DEPLOYED to production
      2026-07-20 (4c360c3): git archive → VPS, backend+frontend rebuilt/
      recreated, /api/health ok, endpoint live + auth-gated (401), no
      migration. Also this session (prod ops, no code): pushed 2 failed
      Best Spas Direct lead-notification texts (transient BlueBubbles 502)
      back through lead_notify; confirmed BlueBubbles SMS-send readiness
      (server private_api on, service-resolution works — green-bubble SMS
      needs iPhone Text Message Forwarding, now enabled user-side); and
      backfilled the missed Meta Instant Form lead (Ljubinka Voljevica,
      602-503-1923, Paganelli) via the real _ingest_meta_lead path — created,
      notified, auto-enrolled, first qualifying SMS delivered. ROOT CAUSE of
      the missed Meta lead: the app-level leadgen webhook isn't fully live
      (Meta app not published / callback not registered) — our side (LeadForm
      Config + leads_retrieval scope) is correct; publishing the Meta app +
      registering the webhook is the remaining user-side fix so Meta leads
      auto-import going forward.
- [x] iOS PWA — installable client web app (2026-07-21): the web frontend
      is now an installable Progressive Web App, so clients can add the
      client-role portal to their iOS home screen (Safari → Share → Add to
      Home Screen) and run it full-screen — no Apple Developer account, App
      Store, or review. frontend/public/manifest.webmanifest (standalone,
      navy theme, logo-derived icons: apple-touch-icon 180 + pwa-192/512 +
      maskable-512, all generated from the Salescale mark), apple-mobile-web-
      app meta tags + theme-color + apple-touch-icon in index.html, and a
      dismissible iOS-Safari-only "Add to Home Screen" hint
      (frontend/src/InstallHint.tsx — gated on iOS Safari && !standalone,
      mounted in main.tsx, themed via the existing CSS tokens). nginx.conf
      serves .webmanifest as application/manifest+json. NO service worker by
      design (live server-backed dashboard needs no offline cache; also
      avoids the Electron base:'./' build conflict). Inherits the existing
      client-role portal scoping + white-label branding. Verified live in the
      browser (manifest served, head tags in DOM, banner renders correctly +
      stays hidden on desktop, zero console errors) and DEPLOYED to production
      (2026-07-21, 950c7a1): frontend-only (no backend, no migration), git
      archive frontend/ → VPS, frontend image rebuilt/recreated,
      /manifest.webmanifest 200 application/manifest+json, apple-touch-icon
      200, app + backend health green. Follow-up: per-agency branded manifest
      (host-aware, like /api/branding/resolve) for white-label install icons.
      SEPARATELY this session (NOT deployed; untracked in mobile/): a native
      Expo/React Native iOS companion app was built — team app (leads + CRM +
      ad-analytics dashboard) with a client-role Dashboard-only mode, logo
      icons, and full EAS / TestFlight / unlisted-App-Store setup
      (mobile/DISTRIBUTION.md). It awaits an Apple Developer Program account
      ($99/yr, required for any iOS distribution to other devices) before it
      can ship; the PWA above is the no-Apple-account path for clients.
- [x] UI cleanup pass — navigation, clutter, mobile drawer (2026-07-21):
      a "cleaner + easier to navigate" pass on the shell, within the existing
      Schematic design system (tokens only, no re-architecture). (1) NAV
      REGROUP (App.tsx nav array): new "Outreach" section (Outreach/Email/SMS,
      previously buried in "Workspace"), "Ads" renamed "Activity" (Pending
      changes/Audit log); "Workspace" now Clients/CRM/Lead Finder. (2) TOPBAR
      DECLUTTER: dropped the redundant org-name breadcrumb crumb (org already
      in the sidebar footer); moved the Comfortable/Dense density Segmented out
      of the topbar into the sidebar footer beside the theme toggle (new
      DensityControl + .side-density, passed showDensity to Sidebar). (3)
      LIGHTER BACKDROP: calmed the drafting grid — --grid-size 24→32px,
      --grid-line 6/7%→3.5/4.5% (theme.css). (4) NARROW-WIDTH NAV: the old
      ≤760px "wrapping unlabeled icon row" is replaced by a proper off-canvas
      DRAWER (shell.css) — a topbar hamburger (new Menu icon) toggles the
      full labeled sidebar over a scrim (.nav-scrim, z-drawer 70/69), closed by
      navigate/scrim-tap/leaving-the-breakpoint. React tracks isNarrow via
      matchMedia and suppresses the desktop `side-collapsed` class while narrow
      so the drawer always shows labels. Fixed a real bug: a STALE legacy
      App.css @media(max-width:760px) block (a duplicate of the old shell.css
      rules) was still setting `.nav-item span{display:none}`, hiding the
      drawer's labels — removed it (shell.css owns responsive now). Verified
      live on the alt2 stack at desktop + 600px (drawer open/close, nav-closes-
      drawer, no console errors), tsc -b + vite build clean. DEPLOYED to
      production (2026-07-21, d53fbd7): frontend-only, git archive frontend/ →
      VPS, frontend image rebuilt/recreated, app 200, new CSS bundle carries
      the drawer classes, backend health green. Note: two pre-existing
      layout-transition lint findings in the legacy App.css/shell.css (sidebar
      width-collapse animation) were left as-is — intentional, predate this.
- [x] Mobile PWA shell v1 (2026-07-21, follow-up to the mobile design-spec
      artifact session — the spec itself lives at the claude.ai artifact
      "Salescale Mobile — PWA Design Spec"): the ≤760px experience moves from
      drawer-adapted desktop to native-mobile primitives, per the spec's
      phase-1 scope. (1) BOTTOM TAB BAR (App.tsx mobileTabs + .tabbar in
      shell.css): role-aware — team gets Home(clients)/CRM/Email/SMS/More,
      client role gets Home/Activity(audit)/Security/More; "More" opens the
      existing drawer (long-tail nav + theme/logout in its footer), with
      active-state = 2px accent top-rule + accent ink (Schematic selected
      state), mono uppercase labels, vellum surface, ≥48pt targets,
      env(safe-area-inset-bottom). Gated by the existing isNarrow matchMedia
      + display:none >760px; role gating reuses the same isTeam flags as the
      nav array. (2) CLIENT-SCOPE STRIP (.scope-strip): accent-washed strip
      under the topbar naming the drilled-into client (mono "CLIENT SCOPE"
      stamp + name + ✕ exit) whenever a TEAM member is inside one client's
      data on mobile — the tenant-isolation cue; clients never see it.
      (3) SAFE AREAS: topbar/sidebar/content pick up env(safe-area-inset-*)
      at ≤760px (previously only InstallHint did, despite viewport-fit=cover).
      (4) INSTALL PROMPT upgrade (InstallHint.tsx): dismissal is now a
      14-day SNOOZE (legacy "1" values read as expired), and Chromium
      Android gets a real install path — beforeinstallprompt is captured
      (mini-infobar suppressed) and the nudge grows an Install button that
      fires the native dialog; iOS keeps the Share→Add coach copy.
      (5) PER-TENANT MANIFEST: GET /api/branding/manifest (api/branding.py)
      builds the PWA manifest from resolve_for_host — verified custom
      domains get the agency's product_name + header_start chrome color,
      everyone else the neutral Salescale manifest (never leaking that a
      domain is claimed); frontend nginx now PROXIES /manifest.webmanifest
      to it (Host forwarded, docker-DNS resolver variable so nginx starts
      without the backend, error_page fallback to the static manifest baked
      in the image). Icons stay the static defaults — per-tenant install
      ICONS need server-side image generation: flagged follow-up. Tests
      565 → 568 (test_manifest.py: default host, verified-custom-domain
      branding, unverified-domain-stays-neutral). tsc + vite build clean;
      verified live on alt2 at mobile width (tab bar renders + switches,
      More→drawer→scrim-close, scope strip appears on drill-down and clears
      on navigate, zero console errors; manifest endpoint curl-verified).
      Toolchain note: both local venvs had died with their old session-
      scratchpad base interpreters — standalone CPython 3.11.15 now lives
      PERSISTENTLY at ~/.local/salescale-toolchain/python and backend/venv
      is rebuilt against it (survives future sessions). Known pre-existing
      at 375px, NOT this change: the client-detail dashboard filter row
      overflows horizontally (page can h-scroll) — worth a wrap pass later.
      DEPLOYED to production 2026-07-21 (web: backend+frontend rebuilt, no
      migration; desktop unaffected — tab bar is narrow-viewport-only and
      schedulers/nginx don't ship in the DMG).
- [x] CRM import overhaul — full automap + dedupe/upsert + normalization
      (2026-07-21, two-Opus-agent build against a pinned contract, then live-
      verified + one real bug caught and fixed). Backend (api/crm.py import_
      contacts, schemas.py CsvImportIn, no migration — source_detail/custom_
      fields JSON already exist): (1) DEDUPE/UPSERT — new `mode` param
      (create | update | create_or_update; API default "create" for back-
      compat, frontend SENDS create_or_update). Per-request client-scoped
      prefetch of lower(email)→contact and normalize_phone→contact maps; row
      match order email→phone→mobile; fill-blanks update (system field set
      only when existing empty, company only when unlinked, custom keys with
      an existing value dropped, extra emails merged into candidate_emails,
      sms opt-in never downgraded, notes always appended). Inserts register
      into the maps so an in-file duplicate row collapses onto the first.
      Response is now a superset: {imported(=created+updated), created,
      updated, unchanged, skipped, failed, created_fields, skipped_fields,
      verification_queued}. (2) NORMALIZATION at ingest — phone/mobile_phone
      → E.164 via sms_consent.normalize_phone (≥7 digits, else stripped raw,
      never lose data); state full-name → 2-letter (50 states + DC dict);
      length pre-check against the Postgres caps BEFORE the DB, error names
      the CSV COLUMN not the target (the SQLite-passes/Postgres-500s trap).
      (3) NEW TARGETS — `website` → Company.domain (urlparse host, strip
      scheme/path/www, fill-blanks) and `notes` → internal Activity(note).
      (4) validate_and_merge now enforce_required=False on import (a bulk
      backfill must not be blocked by form-required fields). (5) New-field
      cap is a SOFT-SKIP (skipped_fields) instead of a mid-import 402 abort.
      (6) RE-IMPORT IDEMPOTENCY (the caught bug): inline "new field" creation
      now REUSES an existing active def whose normalized label matches (via
      _norm_field_label, alnum-lowercase) instead of minting lead_score_2 on
      every pass — a stale client re-sending a column as "new" no longer
      duplicates the field. (7) Provenance: created contacts get source_detail
      {"import_file": file_name}; ONE AuditLogEntry per run (action=
      "contacts.imported", diff encoded as conforming DiffRowOut rows — a
      dict diff 500s the audit-log serializer, which is typed List[DiffRowOut]).
      Frontend (crm_custom.tsx CsvImportDialog): full automap by default —
      HEADER_SYNONYMS expanded (dedicated mobile_phone target split out of
      phone; new website/notes/sms_opt_in targets; billing/shipping/mailing
      city/state/zip; account/firm/brand, etc.), dot-path last-segment
      fallback (address.city → city), unmatched columns now default to "new"
      custom field (inferType-seeded) instead of "skip" (all-empty/at-ceiling
      still skip); parseJson recursively flattens nested objects to dot-paths
      (depth 3, scalar arrays comma-joined); import-mode Segmented + SMS-opt-in
      attestation checkbox + editable new-field labels + 3-row preview;
      BATCHED submit (500-row chunks, later batches remap to created custom
      keys, failed[].row offset to original); result panel with the full
      count line + created/skipped-fields + a client-side "Download failed
      rows" CSV. THE CAUGHT BUG (live, not by tests): re-importing a file that
      created fields re-mapped those columns to "new" and duplicated the defs,
      because the dialog trusted a stale activeDefs prop; fixed BOTH ends — the
      backend reuse guard above (durable) + the dialog now fetches fresh active
      defs on open (api /api/crm/custom-fields, falls back to the prop). Tests
      568 → 581 (test_crm_contacts.py +13 incl. the reuse-idempotency test).
      tsc + vite build clean; grep gate clean. Verified live on alt2: messy
      CSV (full-name split, UPPER email, multi-address cell → candidate_emails,
      (480)/dashed/bare phones → E.164, Arizona→AZ, website→domain, opt-in,
      notes→activity, in-file dup collapsed) AND nested JSON (address.city,
      company.website, mobile, scalar-array tags→custom) both automapped and
      upserted correctly; re-import showed Created 0 · Unchanged N with the
      three columns mapped to custom:<key> and zero new defs. DEPLOYED to
      production 2026-07-21 (backend+frontend rebuilt, no migration).
- [x] SMS reply-triggered steps, response branching & richer tracking
      (2026-07-21): three user-requested campaign upgrades, all
      extend-don't-redesign on the existing engine. DEPLOYED to
      production 2026-07-21 (b5f2d90), web + desktop: migration
      a9c3f6e1d8b2 applied cleanly to the live Supabase DB via the
      container-boot flow (alembic current = a9c3f6e1d8b2 head), backend/
      frontend rebuilt on the VPS, /api/health ok, SMS routes auth-gated,
      fresh bundle served; post-deploy prod state verified read-only —
      zero log errors, the 3 active campaigns intact, 29 scheduled
      enrollments untouched, 0 enrollments in the new awaiting state
      (correct: no reply steps exist yet), and the 63 parked-active
      enrollments all belong to PAUSED campaigns (hvac outreach 62 /
      test2 1 — expected pause behavior, predates this deploy; the 31
      error-status ones are the known pre-existing BlueBubbles
      SMS-forwarding strandings). Desktop: PyInstaller backend + DMG
      rebuilt (148MB, backend hash-matched, new sms_outreach chunks in
      the asar), installed to /Applications, launch-verified (own
      backend bound :8000, health 200, boot alembic no-op'd).
      (1) TIMED REPLY-AFTER-RESPONSE:
      SmsStep gained trigger ("schedule" = today's drip | "reply") and
      wait_minutes (added to wait_days — finer-than-day delays for BOTH
      trigger types); a reply step fires wait after THE LEAD'S REPLY.
      Enrollments gained the awaiting-reply park state
      (awaiting_reply_since set + next_run_at NULL — distinct from the
      paused-park so rearm_parked/reactivate can never force-fire a step
      nobody replied to; explicitly filtered there) plus last_reply_at/
      last_reply_body (latest reply; replied_at stays the FIRST — the
      stats definition). The inbound webhook routes every genuine
      (non-STOP/HELP) reply through NEW sms_campaigns.handle_reply: a
      reply-triggered step at/after the current position schedules
      wait after the reply (landed in the send window — quiet hours
      hold; 24/7 campaigns respond immediately), deliberately skipping
      pending schedule steps ("stop the drip, respond"); with no reply
      step left, exit_on_reply behaves exactly as before. The engine
      parks at a reply step (webhook is the only scheduler of one) and
      clears last_reply_body on send so a later reply step waits for the
      lead's NEXT message. BlueBubbles/iMessage inbound inherits all of
      it (shared _process_inbound). (2) RESPONSE BRANCHING: reply steps
      carry branches [{label, keywords, body}] + ai_branching — at send
      time the reply is matched deterministically first (word-boundary,
      case-insensitive, branch order = priority; "no" can't fire on
      "know"), then ONE cheap grounded AI classification when keywords
      miss and ai_branching is on (classify_reply via resolve_outreach,
      metered, output guarded to exact labels, fail-open) — no match
      sends the step's default body_template. Branch bodies use the
      same token grammar and are 422-validated at save exactly like
      step bodies; branches on a schedule step 422 (schema validator).
      Preview accepts sample_reply and returns branch_label (the same
      select_branch as the engine). (3) TRACKING: fixed a real stats
      bug — a Sendblue/iMessage read receipt moved a row sent→read and
      thereby REMOVED it from the sent/delivered counts
      (_campaign_stats + _analytics_accounts now use inclusive status
      tuples like the cap counters); campaign stats gained read +
      read_rate (read/delivered — honest "opened", iMessage-only),
      replies (total inbound linked to the campaign) alongside replied
      (unique enrollments), and awaiting_reply; inbound SmsMessage rows
      are stamped with campaign/enrollment/step attribution keyed off
      the contact's most recent outbound campaign message (so a lead
      replying AFTER the sequence completed still counts + gets
      replied_at); per-step funnel (sent/delivered/read/failed/replies)
      on the full campaign serialization; analytics by_day gained read
      (bucketed on read_at day). Frontend (sms_outreach.tsx):
      step editor gained the On-a-schedule / After-they-reply Segmented,
      value+unit delay input (minutes/hours/days), branches editor
      (label + comma keywords + response, AI-match Switch), per-step
      stats line; campaigns table + dashboard gained Read/Replies
      columns + KPIs + a Read chart series; Audience tab shows
      "awaiting reply" badges, "when they reply" next-send, and a Last
      reply column; Config gained the previously-unsurfaced
      exit_on_reply Switch with reply-step-precedence copy; PreviewDialog
      gained the sample-reply input + matched-branch badge. Tests
      581 → 589 (reply-step full loop incl. +30min scheduling + inbound
      attribution + consumed-reply clearing; default-body fallback;
      word-boundary unit; AI-branch wiring; exit-on-reply preserved;
      rearm-skips-awaiting; branch-misuse 422s; read-receipt/replies
      stats). Verified live on alt2 (migration applied on boot; REAL
      signed Twilio webhook against the running server scheduled the
      reply step exactly +45min, branch body sent on tick, stats/UI all
      confirmed — campaigns table, step editor, audience badges render;
      zero console errors). NOTE: alt2's TOKEN_ENCRYPTION_KEY scratchpad
      file had been purged again (any Fernet path 500s) — regenerated at
      the path .claude/launch.json expects; dev-alt2.db secrets encrypted
      under older keys no longer decrypt.
- [x] SMS reply catch-up for pre-feature repliers (2026-07-21, same-day
      follow-up): leads who replied BEFORE a campaign had reply handling
      (enrollments exited "replied" under the old stop-on-reply behavior, or
      completed then replied) are terminal — a reply step added later could
      never reach them through the webhook path. New sms_campaigns.
      catch_up_past_replies + POST /api/sms/campaigns/{id}/catch-up-replies
      (require_admin, dry_run param): re-activates each eligible enrollment
      at the first applicable reply step, primes last_reply_body with the
      lead's ACTUAL most recent inbound text (branch matching answers what
      they really said), schedules at the next valid window (their reply's
      delay has already elapsed). Deliberately explicit, never automatic on
      step-save (guardrail #2 spirit — texting past repliers is a confirmed
      admin action with a receipt): the Audience tab auto-runs a DRY RUN when
      the campaign has a reply step and shows an Alert with the real count +
      a two-step confirm. Safeties: idempotent (any enrollment with a
      reply-step send on record skips "already_responded" — never
      double-texts), consent/suppression re-checked per lead at queue time
      AND again at send time, opted_out/manual/failed exits never
      resurrected, monthly send quota enforced like enroll. Tests 589 → 591
      (full retro flow: drip reply exits → reply step added → dry-run
      no-mutation → catch-up → branch-matched send from historical reply
      text → completed → second run queues 0; revoked-consent skip +
      no-reply-step response). DEPLOYED to production 2026-07-21 with the
      main reply-steps deploy flow (web + desktop).
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