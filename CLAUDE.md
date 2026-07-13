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