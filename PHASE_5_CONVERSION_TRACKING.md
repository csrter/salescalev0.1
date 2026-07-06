# Phase 5 — Server-Side Conversion Tracking (Meta CAPI + Google)

Read `CLAUDE.md` first. This phase handles real customer PII (hashed) and
feeds ad platform optimization directly — get the hashing and
deduplication exactly right, since silent errors here degrade every
client's ad performance without an obvious symptom.

## TASKS

1. **Before writing any code in this phase**, look up Meta's current CAPI
   documentation for exact PII hashing/normalization requirements
   (SHA-256, lowercasing, trimming) and Google's current Enhanced
   Conversions / offline conversion import requirements. Do not implement
   from memory — these details matter for event match quality and do
   shift over time. Confirm what you found before implementing.

2. **Meta CAPI**: server-side event sending for each connected client's
   pixel/dataset. Deduplicate against browser-side Pixel events using
   Meta's recommended `event_id` matching. Surface Event Match Quality
   score per client in the dashboard.

3. **Google server-side conversions**: Enhanced Conversions for Leads
   (hashed first-party data matched against ad click data) and/or offline
   conversion import via the Google Ads API for leads converting outside
   the click window.

4. **Click-ID and UTM capture at the lead-capture layer**: reliably
   capture `fbclid`/`fbc` (Meta) and `gclid` (Google) at the point of lead
   submission, on the landing-page/lead-form layer — not just inside the
   ads-platform integration code, since neither CAPI nor Enhanced
   Conversions can match a later server-side event back to the original ad
   click without it. Confirm this writes into the same
   attribution/landing-event table built in Phase 1 alongside the UTM
   parameters already captured there, so click IDs and UTM data live
   together on one record per lead rather than in parallel, disconnected
   systems.

5. **Per-client configuration**: each client has its own pixel/dataset
   (Meta) and conversion action/ID (Google), and potentially its own CRM
   or website as the event source. Build this as a per-client
   configuration, not a single hardcoded event source.

## DEFINITION OF DONE

- A test conversion sent through the app is correctly deduplicated against
  a matching client-side event on both platforms (verify in each
  platform's own event-testing tool, not just in your own logs).
- PII is hashed exactly to each platform's current spec — confirm this
  against the platform's own documentation/validation tool, not just unit
  tests you wrote yourself.
- Click-ID capture works from a real landing-page submission through to a
  server-side event carrying that ID.
- Event Match Quality (Meta) is visible per client in the dashboard.

## BEFORE YOU FINISH

Update `CLAUDE.md`'s Current Status section and commit. Phase 6
(Salescale CRM) depends on leads flowing correctly through the attribution
layer this phase completes, so treat this as a meaningful checkpoint even
though it's no longer the final phase.
