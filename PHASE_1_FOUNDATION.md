# Phase 1 — Foundation

Read `CLAUDE.md` first for product goal, architecture, and standing
guardrails before starting. This phase builds the base every later phase
depends on — get tenant isolation and the data model right here, since
it's expensive to retrofit later.

## TASKS

1. **Propose and confirm the stack.** Before writing code, state your
   chosen backend language/framework and justify it against current SDK
   support for the Meta Marketing API and Google Ads API. Confirm the
   frontend framework and DB choice from `CLAUDE.md`'s defaults, or argue
   for a change if you have a real reason. Stop and present this for
   approval before scaffolding the repo.

2. **Multi-tenant data model.** Design and implement: agency, users,
   clients, platform connections (Meta and/or Google per client), ad
   accounts, campaigns, ad groups/sets, ads, creatives, insights
   (time-series). Model campaigns generically enough that reporting code
   isn't forked per platform later, even though write-side (create/edit)
   logic will differ between Meta and Google.

   Include the core Salescale CRM entities in this same schema now, even
   though the CRM's UI and workflows are built in Phase 6: contacts,
   companies, deals/pipeline-stage, activity log, tasks. Retrofitting a
   CRM data model after ad-side tables are already built and in use is
   more expensive than including it from the start — this is exactly the
   kind of foundational decision this phase exists to get right.

3. **Two-role auth model.** Build both user roles from `CLAUDE.md`'s scope
   section now, not just an Atlas Reach admin role with client access
   added later: **Atlas Reach team** (full access) and **Client** (scoped
   read-only access to their own account only, no ad-account write access,
   no visibility into other clients or Atlas Reach-internal fields).
   Enforce this at the data-access layer, not just by hiding UI elements —
   a client role that merely has hidden buttons but working API access
   underneath is not actually scoped. This is a single-agency platform
   (Atlas Reach only), not a resellable multi-agency product — no
   agency-level self-serve signup or billing is needed.

3. **Attribution/landing-event table.** Alongside the core data model,
   build a table that captures UTM parameters (`utm_source`, `utm_medium`,
   `utm_campaign`, `utm_content`, `utm_term`), referrer, and timestamp at
   the moment a visitor lands on a client's site — this is the same
   capture point Phase 5 will extend to also grab `fbclid`/`gclid`, so
   design it once as a single landing-event capture layer rather than two
   separate mechanisms later. Tie this record to the eventual lead when
   one is submitted.

4. **Attribution/landing-event table.** Alongside the core data model,
   build a table that captures UTM parameters (`utm_source`, `utm_medium`,
   `utm_campaign`, `utm_content`, `utm_term`), referrer, and timestamp at
   the moment a visitor lands on a client's site — this is the same
   capture point Phase 5 will extend to also grab `fbclid`/`gclid`, so
   design it once as a single landing-event capture layer rather than two
   separate mechanisms later. Tie this record to the eventual lead when
   one is submitted.

5. **Meta OAuth flow.** Connect a client's ad account with the correct
   scopes (`ads_management`, `ads_read`, `business_management`). Research
   current Meta App Review requirements for these scopes before building —
   flag to the user that App Review, and Business Verification at higher
   volume, run on Meta's timeline and can't be completed on their behalf.

6. **Google Ads connection flow.** Set up (or confirm) Atlas Reach's
   manager account (MCC), apply for a Google Ads API developer token
   (note Basic vs. Standard access tiers and their quota differences to
   the user), then link each client account under the MCC and run OAuth
   per client.

7. **Secure token storage** for both platforms: encrypted at rest,
   refresh-token handling, and a defined behavior for what happens when a
   client revokes access on either side (don't let the app silently fail —
   surface the disconnected state).

8. **Basic account/campaign browser.** List accounts → campaigns → ad
   sets/groups → ads, pulling live from both APIs, with a platform toggle
   or unified view.

## DEFINITION OF DONE

- A user can log in, connect at least one Meta ad account and one Google
  Ads account (test/owned accounts), and browse the account → campaign →
  ad set/group → ad hierarchy for both, live.
- A test landing-page visit captures UTM parameters into the
  attribution/landing-event table correctly.
- Tenant isolation is verifiable: confirm in writing (and ideally with a
  test) that one client's data cannot be reached through another client's
  session or token.
- The Client role is verifiable as genuinely read-only and genuinely
  scoped: confirm (with a test, not just manual UI checking) that a
  client-role session cannot write to any ad account, cannot query another
  client's data via a direct API call, and cannot reach Atlas
  Reach-internal fields.
- No secrets committed to source — verify before finishing.

## BEFORE YOU FINISH

Update `CLAUDE.md`'s Current Status section, commit with a clear message,
and stop. Don't proceed into Phase 2 in the same session — that's a
deliberate review checkpoint since Phase 2 starts writing to live accounts.
