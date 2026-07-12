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