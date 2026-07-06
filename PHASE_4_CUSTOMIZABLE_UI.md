# Phase 4 — Customizable UI

Read `CLAUDE.md` first. This phase is primarily frontend — it consumes the
metrics and management APIs built in Phases 2 and 3 rather than adding new
backend integrations.

## TASKS

1. **Rearrangeable dashboard widgets**: addable, removable, resizable, and
   rearrangeable per user, with layouts saved per client view. This needs
   to be a real widget system, not a fixed grid with a "customize" label
   on it.

2. **Minimum widget set**: channel-mix/blended-performance overview,
   spend/pacing charts per platform, funnel-tier comparison tables,
   creative fatigue and Quality Score alerts, a guarantee/goal tracker
   (for clients running performance guarantees, tracking progress against
   the goal regardless of which platform is driving the leads), an
   attribution-discrepancy alert view (surfacing the Phase 3 reconciliation
   flags), and a raw campaign table view per platform for power-user
   editing.

3. **UTM builder tool**: a form/utility (not just a backend enforcement
   rule) where a user picks a client and campaign and gets a correctly
   formatted UTM-tagged URL back, using the standardized naming convention
   from Phase 3 — this is the point where consistent naming actually gets
   enforced, since it's a lot easier to get someone to use a builder than
   to police manually typed URLs after the fact.

4. **Platform filter/toggle** on every view: "Meta only," "Google only,"
   or "blended" — some client conversations are channel-specific, some are
   about total performance, and the UI should support both without
   forcing a page reload or separate view.

5. **Visual identity**: navy/cobalt palette, clean fintech/SaaS aesthetic
   consistent with Atlas Reach's brand. This is client-facing for the
   Client role's views (per `CLAUDE.md`'s two-role model) — it needs to
   look like a real product on both the Atlas Reach admin dashboard and
   the Client dashboard, not just the internal one.

## DEFINITION OF DONE

- A user can add, remove, resize, and rearrange widgets, and the layout
  persists per user/client on reload.
- Every widget respects the platform filter/toggle correctly, including
  the guarantee tracker correctly summing progress across whichever
  platform(s) are contributing.
- The UI is visually consistent with the navy/cobalt fintech aesthetic
  across every screen, not just a landing/dashboard page.

## BEFORE YOU FINISH

Update `CLAUDE.md`'s Current Status section, commit, and stop for review
before Phase 5.
