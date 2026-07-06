# Phase 2 — Core Management Features

Read `CLAUDE.md` first. Confirm Phase 1 is actually complete (check its
Definition of Done, not just its checkbox) before starting — this phase
writes to live ad accounts, so the foundation needs to be solid.

## TASKS

1. **Create/edit/pause/resume** campaigns, ad sets/groups, and ads,
   writing back to Meta via the Marketing API and to Google via the Google
   Ads API. Keep these as two backend integrations behind one UI — don't
   let Google-specific concepts (keywords, match types, Quality Score,
   Search vs. Performance Max vs. Display) leak into Meta's data model or
   vice versa.

2. **Budget and bid management** on both platforms, with guardrails per
   `CLAUDE.md`: confirm before any write action that changes live spend,
   and log every change — who made it, on which platform, when. This audit
   trail is a real agency requirement, not a nice-to-have; clients ask
   "why did spend change" and someone needs an answer.

3. **Creative upload and preview** matching each platform's actual ad
   placements. Meta and Google's Search/Display/PMax formats look nothing
   alike — a generic preview isn't good enough to catch formatting issues
   before a client sees them live.

4. **Google-specific management surface**: keyword management (match
   types, negative keywords), Search terms review, Performance Max asset
   groups. These have no Meta equivalent — don't force them into a
   Meta-shaped UI.

## DEFINITION OF DONE

- A user can create, edit, pause, and resume a campaign on both platforms
  through the app, and the change is reflected live on Meta/Google when
  checked directly.
- Every write action that touches live spend requires an explicit
  confirmation step in the UI — verify there is no code path that writes
  budget/status changes without one.
- An audit log exists and is queryable: who changed what, on which
  platform, when.
- Creative previews are visually accurate to real placements on both
  platforms, not generic mockups.

## BEFORE YOU FINISH

Update `CLAUDE.md`'s Current Status section, commit, and stop for review
before Phase 3.
