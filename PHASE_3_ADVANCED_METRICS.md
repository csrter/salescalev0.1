# Phase 3 — Advanced Metrics Layer

Read `CLAUDE.md` first. This phase is read/compute-only — no new write
paths to live ad accounts — so it's lower-risk than Phase 2, but the
metric definitions need to be right since Atlas Reach will report these
numbers to clients.

## TASKS

Build metrics beyond what either platform's native UI surfaces on its own,
computed from Marketing API + Google Ads API insights data:

1. **Cross-platform blended CAC and blended ROAS** per client — combined
   Meta + Google spend and conversions into one number.

2. **Channel-mix reporting**: share of a client's leads and spend from
   Meta vs. Google, and CPL comparison between them.

3. **Funnel-tier performance**: cost-per-lead and volume by
   Cold/Warm/Hot tier on Meta, and by campaign/keyword intent tier on
   Google (e.g., branded vs. non-branded Search).

4. **Creative fatigue score** (Meta) and **ad-strength/Quality-Score trend
   tracking** (Google) — computed and flagged automatically, not left for
   someone to eyeball on a chart.

5. **Lead-quality-adjusted CPL** across both platforms — cost per lead
   weighted by whether it was later marked "qualified" in Salescale (built
   in Phase 6). Design this metric now against the Salescale data model
   from Phase 1 as the native source, with an optional fallback path for
   any client whose lead-quality data still lives in an external CRM
   during a transition period — don't hardcode the assumption that
   Salescale is populated from day one for every client. Tag each lead
   with its source platform so quality is comparable channel-to-channel.

6. **Cross-client benchmarking**: how one client's CPL/ROAS compares to
   the agency's book of business in the same vertical, per platform and
   blended.

7. **Attribution reconciliation**: compare what each platform
   self-reports (Meta/Google claiming credit for a conversion) against
   what the UTM/landing-event data actually shows for that lead. Flag
   discrepancies — e.g., Meta attributing a lead that the UTM trail shows
   arrived via organic search, or a lead with no UTM data at all landing
   on a platform-attributed conversion. This is the check that catches
   platforms over-crediting themselves, which happens more than agencies
   often realize.

8. **Standardized UTM builder/enforcement**: a shared naming-convention
   tool so `utm_campaign`/`utm_content` values stay consistent across
   clients and campaigns rather than drifting into inconsistent ad-hoc
   naming — inconsistent UTMs quietly break every metric above that
   depends on them.

## DEFINITION OF DONE

- Every metric above is computed and displayable for at least one real
  connected account per platform.
- Attribution reconciliation correctly flags at least one deliberately
  introduced test discrepancy between platform-reported and UTM-based
  attribution.
- Metric definitions are documented in code/comments clearly enough that
  someone auditing a client report six months from now can trace exactly
  how a number was derived — these will end up in client-facing reports,
  so ambiguity here becomes a client-trust problem later.
- The CRM integration for lead-quality data is built against the
  Salescale data model as the native source, with a pluggable interface
  for any client still on an external CRM during transition — not
  hardcoded to one CRM's API shape.

## BEFORE YOU FINISH

Update `CLAUDE.md`'s Current Status section, commit, and stop for review
before Phase 4.
