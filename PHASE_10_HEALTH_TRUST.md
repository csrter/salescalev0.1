# Phase 10 — Call Tracking, Account Health & Client Trust

Read `CLAUDE.md` first. Real dependencies: Phase 1 (Organization/Client
model, attribution table), Phase 2 (campaign/creative management), Phase 3
(metrics), and Phase 6 (Salescale CRM). This phase does **not** require
Phase 4, 5, 7, 8, or 9 — it can run any time after Phase 6, the same way
Phase 9 was made droppable. If Phase 9 (AI insights) already exists by the
time you build this, task 2's account health score and task 1's call data
become good grounding inputs for AI-generated summaries — wire that in as
a bonus, not a requirement of this phase.

This phase adds five features that share a theme: giving an Organization
(and its account managers) a clearer, more trustworthy picture of how each
client relationship is actually doing — not just campaign metrics in
isolation.

## TASKS

1. **Call tracking.** Dynamic number insertion: assign tracking numbers to
   campaigns/UTM sources so inbound calls attribute back to the ad,
   campaign, or keyword that drove them, using the existing
   attribution/landing-event table (Phase 1) as the join point rather than
   building a parallel attribution system. Add call recording and
   transcription — research current telephony/call-tracking API options
   (e.g. Twilio, or a dedicated call-tracking provider) before building
   your own telephony stack; this should be a "buy the phone
   infrastructure, build the integration" decision, not a
   build-everything-yourself one. Feed call outcome (answered, duration,
   and a qualified/unqualified signal if transcribed) into the Salescale
   CRM lead record (Phase 6) through the **same ingestion pattern**
   form-fill leads already use — one pipeline, not two parallel ones.
   Surface call volume and quality per campaign in the metrics layer
   (Phase 3) alongside form-lead volume, so call-driven and form-driven
   CPL are comparable rather than living in two disconnected views. This
   matters more than it might seem for Atlas Reach's own vertical — a
   meaningful share of HVAC/home-services conversions happen by phone, not
   form fill, and a platform that only tracks form leads is blind to a
   real chunk of what's actually driving results.

2. **Account health score.** A composite score per client, computed from
   real inputs already in the system: metrics (Phase 3 — CPL trend,
   budget pacing, ROAS), Salescale CRM lead-quality rate (Phase 6), and
   the call-tracking quality signal from task 1 if built. Make the scoring
   formula transparent and inspectable — a breakdown of what contributed
   to the number and by how much — not a black-box single output. An
   account manager needs to know *why* a client's score dropped, not just
   that it did. Surface this as a sortable list across an Organization's
   whole client book (a triage view), not just a number buried on each
   client's individual page — the point is spotting the client that needs
   attention across a book of 20 clients, not just confirming one client's
   status when you already know to look.

3. **Creative approval workflow.** Client-role users (Phase 1) can review
   pending creative and approve or reject it with comments, extending the
   creative upload/preview work from Phase 2. An ad must not be able to go
   live through any code path while in a "pending approval" state —
   enforce this server-side, consistent with the write-action
   confirmation guardrails in `CLAUDE.md`, not just as a UI warning that a
   direct API call could bypass.

4. **Client satisfaction / NPS.** Scheduled or triggered survey sends
   (e.g. after a qualified-lead milestone, or on a recurring cadence) to
   Client-role contacts, with responses stored against their Salescale CRM
   record (Phase 6). Feed survey results into the account health score
   (task 2) as one more real input, rather than building it as a
   disconnected metric nobody actually looks at day to day.

5. **Data rights (GDPR/CCPA).** Per-contact data export and deletion
   request handling, correctly scoped by `organization_id` (Phase 1) so a
   deletion request against one Organization's contact can never touch
   another Organization's data. Deletion needs to cascade correctly across
   every table a contact appears in — Salescale CRM records,
   attribution/landing-event records, call-tracking records and
   transcripts from task 1. Audit this the same way Phase 9 audited
   branding: don't assume cascade deletes work, verify them, since a
   partial deletion silently leaving data behind is worse for compliance
   purposes than a feature that doesn't exist at all.

## DEFINITION OF DONE

- A test call to a tracking number attributes correctly back to the
  originating campaign/UTM and creates or updates a Salescale lead record
  the same way a form submission already does.
- The account health score is computed, visible in a sortable
  per-Organization client list, and its component breakdown is inspectable
  for any given client — not just a single opaque number.
- A pending creative cannot go live through any code path, including a
  direct API call, while awaiting Client-role approval — verified by test.
- An NPS/satisfaction response updates the account health score without
  any additional manual step.
- A data deletion request against a test contact actually removes their
  data from every table they appear in — verified by directly querying
  the schema afterward, not just checking the primary CRM record is gone.

## BEFORE YOU FINISH

Update `CLAUDE.md`'s Current Status section and commit. If Phase 9's AI
insights are already built, this is a good moment to wire the account
health score and call transcripts in as new grounding inputs for
AI-generated summaries — but treat that as an enhancement to revisit, not
a requirement of finishing this phase.
