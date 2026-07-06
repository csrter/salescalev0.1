# Phase 7 — Additional Platform Adapters

Read `CLAUDE.md` and `PLATFORMS.md` first. This phase adds Snapchat,
Reddit, LinkedIn, Microsoft Advertising, and Nextdoor as adapters
conforming to the interface established by the Meta and Google
implementations in Phases 1/2/3/5. If that adapter interface doesn't
already cleanly abstract campaign CRUD, insights, and conversion sending
across Meta and Google, fix the interface before adding a fifth
implementation on top of a shaky one.

## RECOMMENDED BUILD ORDER

Build in this order — easiest/highest-fit first, so early wins validate
the adapter pattern before tackling the less mature APIs:

1. **Microsoft Advertising** — closest structurally to the existing Google
   adapter; likely the fastest to build and a strong fit for Atlas Reach's
   home-services clients.
2. **Nextdoor** — high fit for home services, but confirm current API
   maturity and access requirements first (per `PLATFORMS.md`) before
   committing to a timeline; this one may have more unknowns going in.
3. **LinkedIn** — well-documented API, but don't reuse the Meta/Google
   audience-targeting abstraction for LinkedIn's B2B targeting fields (job
   title, seniority, company size) — these need their own model.
4. **Snapchat** — campaign structure loosely mirrors Meta's, which should
   make mapping onto the existing adapter pattern relatively
   straightforward.
5. **Reddit** — build last; per `PLATFORMS.md` this is the least mature
   API of the five, and by this point the adapter pattern will be
   well-proven, making it easier to absorb Reddit's rough edges in
   isolation rather than debugging the pattern and Reddit's quirks at the
   same time.

## TASKS (per platform, repeat for each)

1. OAuth/API auth flow, following that platform's current documentation
   for developer access, tokens, and any app-review-equivalent process.
   Flag any approval step that runs on the platform's own timeline to the
   user, the same way Meta App Review and Google's developer token tiers
   were flagged in Phase 1.
2. Implement the adapter interface: list/create/edit/pause campaigns
   (and that platform's equivalent sub-structures), fetch insights, send a
   server-side conversion event.
3. Extend the attribution/landing-event table's click-ID capture (Phase 1)
   to include that platform's click identifier, if one exists.
4. Register the adapter so it appears automatically in the dashboard's
   platform filter (Phase 4), the blended/channel-mix metrics (Phase 3),
   and Salescale lead ingestion (Phase 6) — without needing new code in
   those phases' files. This is the actual test of whether the adapter
   pattern was built correctly in Phase 1.
5. Update `PLATFORMS.md` with anything discovered during the build that
   differs from what was documented going in — this file should stay a
   living reference, not a snapshot of assumptions made before building.

## DEFINITION OF DONE

- Each of the five platforms can be connected, browsed, and used to
  create/edit/pause a test campaign on a real (Atlas Reach's own) account.
- A lead attributed to each platform (where that platform drives leads)
  flows into Salescale the same way Meta/Google leads already do, with
  whatever attribution data that platform provides attached.
- Adding each adapter required no changes to Phase 3's metrics code or
  Phase 4's dashboard code beyond registration — if it did, the adapter
  interface has a leak worth fixing before moving to the next platform.
- `PLATFORMS.md` is updated to reflect what was actually true at build
  time, not left as the pre-build assumptions.

## BEFORE YOU FINISH

Update `CLAUDE.md`'s Current Status section and commit — ideally with one
commit per platform adapter rather than one giant commit for all five, so
a problem with one platform doesn't block review of the others.
