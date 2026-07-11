# Phase 12 — Lead Finder & Email Verification

Read `CLAUDE.md` first. This phase depends on the Salescale CRM
(Phase 6) existing, since found leads land there as contacts. It does
NOT depend on Phases 7–11 — like Phases 9 and 10, tier gating is done
through the entitlement-check function stubs, so this phase is
droppable any time after Phase 6.

This phase gives every Organization a built-in lead sourcing pipeline:
find businesses by vertical + geography, enrich them with contact
emails, verify those emails, and land them in the CRM ready for
outreach. None of the reporting-only competitors have this, and
GoHighLevel's equivalent is weak — build it like a headline feature,
not a utility.

## HARD GUARDRAIL — NO PLATFORM SCRAPING

Do not implement any scraping of Instagram, Facebook, or any Meta
surface, in any form, under any feature flag. Salescale's Instagram
Outreach module depends on a Meta App Review approval, and tying the
same app (or same company) to ToS-violating data collection puts that
approval — and every existing Meta integration from Phases 1/2/5 — at
risk. All lead data in this phase comes from licensed APIs (Google
Places), the target business's own public website, or a licensed data
provider the Organization has its own account with. If a task in this
file could be read two ways, pick the reading that is not scraping.

## PART A — LEAD FINDER (GOOGLE PLACES)

1. **Search interface.** A Lead Finder view where an Organization team
   member enters a business category / keyword and a geography
   ("HVAC contractors", "Scottsdale AZ", radius or city-level). Results
   show business name, category, phone, website, rating, and address.

2. **Google Places integration.** Use the current Google Places API
   (Text Search + Place Details) — check the current API version,
   pricing model, and field-mask billing before building, since Google
   bills per request AND per field requested. Request only the fields
   the feature actually displays/stores. Cache Place results
   server-side within Google's allowed caching policy (confirm the
   current policy — historically place IDs are cacheable indefinitely,
   most other fields are not) rather than assuming.

3. **Per-Organization metering.** Places requests cost real money.
   Meter searches per Organization per month, expose usage in the same
   self-service usage view pattern as seats/clients, and gate monthly
   search volume by tier through the entitlement stub
   (e.g. `checkEntitlement(org, 'lead_finder_searches')`). Never let
   one tenant's usage be invisible to billing.

4. **Dedupe before import.** When results are imported, dedupe against
   existing CRM contacts in that Organization (normalized phone,
   website domain, and name+address match — not just exact string
   equality). Show "already in your CRM" inline on results instead of
   silently skipping.

5. **Import as CRM leads.** Selected results become CRM contacts with
   `source = lead_finder`, the search query stored for attribution, and
   the standard `organization_id` scoping + RLS like every other table.
   Imported leads enter whatever default pipeline stage the
   Organization has configured for new leads.

## PART B — ENRICHMENT

6. **Website email discovery.** For each imported business with a
   website, crawl that business's own site (homepage, /contact, /about,
   common contact paths only — not a general web crawl) for published
   contact emails. Respect robots.txt, set an honest user agent, rate
   limit politely, and time out fast. Store discovered emails as
   candidate contact emails pending verification (Part C), never as
   verified.

7. **Licensed provider adapter (optional per Organization).** Define a
   small enrichment-provider interface (input: business domain/name;
   output: candidate contacts) and ship one reference implementation
   against a licensed provider's API (Hunter, Apollo, or similar —
   confirm current API terms; some prohibit resale/multi-tenant use, so
   the Organization must connect THEIR OWN API key, BYO-key style, not
   a shared Salescale key). Keep it an adapter so the provider is
   swappable, same philosophy as the ad-platform adapters in Phase 7.

8. **Data minimization.** Store business contact data only (name, role,
   business email/phone). Do not build storage for personal data beyond
   what outreach needs. Enriched data inherits the Organization's
   GDPR/CCPA export-and-delete handling from Phase 10 if that phase is
   built; if not, leave a clearly marked TODO hook where deletion would
   cascade.

## PART C — EMAIL VERIFICATION (LEADS)

9. **Verification provider adapter.** Same adapter pattern: interface
   in, one reference implementation against a bulk verification API
   (NeverBounce, ZeroBounce, or similar — confirm current pricing and
   batch endpoints). Do NOT hand-roll SMTP-handshake verification
   in-app; it gets Salescale's own infrastructure IPs flagged.

10. **`verification_status` on contacts.** Enum:
    `unverified | valid | risky | invalid | unknown`, plus
    `verified_at` timestamp. Surface it as a badge in contact list and
    detail views, filterable like any other field.

11. **Pipeline placement.** Verification runs automatically at the end
    of the Lead Finder → enrichment flow, and as a bulk action on CSV
    import (Phase 6 import flow) and on any manually selected contact
    set. Meter verifications per Organization by tier, same pattern as
    task 3.

12. **Outreach gate.** Any email-sending feature (current or future
    sequences) must exclude `invalid` by default and warn on `risky`.
    Make this a shared check, not per-feature copy-paste, so the
    Outreach module inherits it for free.

## PART D — ACCOUNT EMAIL VERIFICATION (AUTH)

13. **Signup confirmation.** If the Phase 1/8 auth flow doesn't already
    verify user email addresses, add it now: single-use token link,
    24-hour expiry, resend with rate limiting, and unverified accounts
    blocked from inviting members or connecting ad accounts. Send via
    the existing transactional email path (Phase 9's branded-email
    rules apply if that phase is built).

## ACCEPTANCE CHECKS

- A search for a vertical+geo returns real Places results, imports
  selected businesses as org-scoped CRM leads, dedupes correctly, and
  the whole run appears in that Organization's usage metering.
- Enrichment discovers emails only from the target business's own site
  or the Organization's own connected provider key.
- Every imported lead ends the pipeline with a verification status, and
  a contact marked `invalid` cannot be enrolled in an email send.
- RLS verified on every new table (`lead_finder_searches`,
  enrichment/verification job tables): one Organization can never see
  another's searches, quotas, or results.
- Zero code paths touch Instagram/Facebook data collection.
