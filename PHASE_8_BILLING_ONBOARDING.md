# Phase 8 — Billing & Self-Serve Onboarding

Read `CLAUDE.md` first. The Organization tenant entity and three-tier auth
model should already exist from Phase 1 — this phase adds the public-facing
signup flow, Stripe subscription billing, and tier-based feature
enforcement on top of that foundation. If Phase 1's Organization model
isn't cleanly multi-tenant already, fix that before building a signup flow
that creates more of them.

## TASKS

1. **Define the tiers.** Propose a concrete Starter/Pro/Agency breakdown
   before building anything — what's gated per tier (number of clients,
   number of connected ad platforms per client, number of team seats,
   Salescale contact/deal limits, access to advanced metrics or specific
   platform adapters) and price points. Present this for approval; don't
   guess and build silently, since these numbers are business decisions,
   not engineering ones.

2. **Stripe integration.** Set up Stripe products/prices matching the
   approved tiers. Each Organization has exactly one active subscription.
   Implement the checkout flow (Stripe Checkout or Elements), webhook
   handling for subscription lifecycle events (created, updated, canceled,
   payment failed), and Stripe's customer billing portal for
   self-service plan changes and payment method updates — don't build a
   custom billing-management UI when Stripe's hosted portal already
   covers most of it.

3. **Self-serve Organization signup.** A new agency can sign up, create
   their Organization, choose a tier, complete checkout, and land in a
   working (empty) instance of the product — no manual provisioning by
   Salescale's own team required. Include team invites (email-based,
   scoped to the new Organization, respecting the Owner/Admin/Member roles
   from Phase 1).

4. **Tier enforcement, server-side.** Every tier limit (client count, seat
   count, platform-connection count, etc.) must be enforced at the point
   of creation/action, not just displayed in the UI as a soft warning. A
   request that would exceed a limit should fail server-side with a clear
   error, independent of what the frontend does or doesn't prevent.

5. **Trial or freemium handling, if applicable.** Decide (and confirm with
   the user) whether there's a free trial period, and if so, what happens
   automatically at trial end (downgrade to a limited state vs. hard
   lockout vs. grace period) — don't leave this undefined and let it
   become a support problem later.

6. **Migrate Atlas Reach cleanly.** Atlas Reach's existing data (built and
   tested as Organization #1 through Phases 1–7) should already fit this
   model without a special migration, since Phase 1 built it as a genuine
   Organization from the start rather than a special-cased "the" tenant.
   Verify this is actually true rather than assuming it — if Atlas Reach
   needs any kind of data migration to fit the tiered billing model
   cleanly, that's a sign something in Phase 1 was special-cased and
   should be fixed here rather than patched around.

## DEFINITION OF DONE

- A brand-new agency (test account, not Atlas Reach) can sign up cold,
  pick a tier, pay via Stripe test mode, and land in a working empty
  Organization — no manual steps by Salescale's own team.
- Exceeding a tier limit (e.g. adding a client beyond the plan's cap)
  fails server-side with a clear message, verified by test, not just by
  checking the UI hides the option.
- Stripe webhooks correctly handle at least: successful subscription
  creation, a plan upgrade/downgrade, a canceled subscription, and a
  failed payment — confirm each with Stripe's test event tooling rather
  than only the happy path.
- Atlas Reach's Organization, set up in Phase 1, has a real subscription
  in this billing system with no special-cased exemptions from the tier
  rules other Organizations follow.

## BEFORE YOU FINISH

Update `CLAUDE.md`'s Current Status section and commit. This phase makes
the product a real SaaS business, not just a real SaaS architecture — treat
this as the checkpoint before considering the product launch-ready.
