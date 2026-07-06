# Phase 6 — Salescale CRM

Read `CLAUDE.md` first. The core Salescale entities (contacts, companies,
deals/pipeline-stage, activity log, tasks) should already exist from Phase
1 — this phase builds the actual workflows and UI on top of that data
model, and wires lead ingestion from the ad platforms into it.

## TASKS

1. **Lead ingestion.** When a lead arrives from a Meta Instant Form,
   Google Lead Form ad, or a landing-page submission, automatically create
   (or update, if the contact already exists) a Salescale contact/lead
   record — with the attribution record from Phase 1 (UTM parameters +
   click ID) attached directly, not looked up separately later. This is
   the core value of building the CRM natively rather than bolting one on:
   no manual re-entry, no reconciliation step between ad platform and CRM.

2. **Pipeline / kanban view.** A drag-and-drop pipeline board per client,
   with stages customizable per client — Atlas Reach's HVAC clients may
   run different sales processes, and the pipeline shouldn't force one
   rigid stage set on everyone.

3. **Qualified-lead workflow.** Implement the actual qualified-lead
   definition and verification process behind the 14-Day Trial Sprint
   guarantee (pull the current definition from the user if it isn't
   already documented in this repo — do not assume a generic definition)
   as a structured status/checklist on the lead record, not a free-text
   tag. Marking a lead qualified here should be the same event that feeds
   the lead-quality-adjusted CPL metric (Phase 3) and the guarantee
   tracker widget (Phase 4) — one status change, multiple places it shows
   up, not three places to update by hand.

4. **Activity and task management.** Notes, call logs, and email activity
   on each contact/lead; task/follow-up reminders assignable to Atlas
   Reach team members managing that client relationship.

5. **Optional external CRM sync.** For any client whose nurture automation
   currently lives in an external CRM (e.g., GHL SMS sequences), build an
   optional two-way sync: Salescale stays the source of truth for
   reporting and qualified-lead status, while status changes sync out to
   keep existing external automation firing. Build this per-client and
   opt-in — don't require it, and don't let its absence block any other
   Salescale feature from working for clients who don't need it.

6. **Client-facing pipeline view.** The Client role (built in Phase 1) gets
   a read-only view of their own pipeline in Salescale: their contacts,
   deal stages, and qualified-lead status. Hide anything Atlas
   Reach-internal from this view — internal notes, activity-log entries
   marked internal-only, and any margin/cost data — by field-level
   filtering on the backend, not just by omitting fields in the client-role
   frontend. A client should not be able to see internal notes by
   inspecting API responses even if the UI doesn't render them.

## DEFINITION OF DONE

- A test lead submitted through a Meta Instant Form (or Google Lead Form,
  or landing page) appears in Salescale automatically, with its UTM and
  click-ID attribution already attached — no manual step required.
- The pipeline board supports drag-and-drop stage changes, with stages
  configurable per client.
- Marking a lead "qualified" updates the lead-quality-adjusted CPL metric
  and the guarantee tracker without any additional manual update.
- If external CRM sync was built: a status change in Salescale correctly
  reflects in the external CRM, and vice versa, without creating duplicate
  records on either side.
- A Client-role login can see their own pipeline read-only, cannot see
  another client's data, cannot see internal-only notes/fields even via
  direct API inspection, and cannot perform any write action.

## BEFORE YOU FINISH

Update `CLAUDE.md`'s Current Status section and commit. This is the last
phase in the current build plan — treat this as a full-system review
point: confirm leads flow end-to-end from ad click → landing page →
Salescale → qualified status → metrics, across both platforms.
