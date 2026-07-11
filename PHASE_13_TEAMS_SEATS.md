# Phase 13 — Team Members, Invites & Seats

Read `CLAUDE.md` first. This phase depends on the Organization/roles
model from Phase 1 and the transactional email path. Seat limits are
enforced through the Phase 8 entitlement stubs, so this phase is
droppable after Phase 6 whether or not billing is live — if Phase 8
isn't built yet, the stub returns the default tier's limits.

The tenancy model already does the hard part (`organization_id`
scoping everywhere). This phase builds the product surface that lets an
Organization actually operate as a team — and it's a release blocker:
no agency signs up solo.

## PART A — INVITES

1. **Invites table.** `organization_id`, invited email, assigned role,
   inviter user id, hashed single-use token (store the hash, never the
   raw token), status (`pending | accepted | revoked | expired`),
   expiry (7 days). RLS like everything else.

2. **Send flow.** Admin/Owner enters email + role → invite email goes
   out via the transactional path (Phase 9 branded-email rules apply if
   built: sends from the Organization's configured sender when set).
   Rate limit invite sends per org per hour. Resend regenerates the
   token and invalidates the old one; revoke kills it immediately.

3. **Accept flow — two paths.** Existing Salescale user: clicking the
   link while logged in attaches them to the Organization with the
   assigned role. New user: link leads into signup, and acceptance
   completes only after email verification (Phase 12 Part D). The
   invite email address must match the account email that accepts —
   don't let a token be redeemed by a different address.

4. **Edge cases to handle explicitly, not accidentally:** inviting an
   email that's already a member (block with a clear message), inviting
   the same email twice (supersede the old invite), a user belonging to
   multiple Organizations (already supported by the tenancy model —
   verify the org-switcher UX still holds up), and accepting an invite
   to an org whose seats filled up after the invite was sent (block at
   accept time with a message to contact the org admin, don't silently
   over-provision).

## PART B — ROLES & PERMISSIONS

5. **Role set.** `Owner`, `Admin`, `Member`, `Client` (Client already
   exists from Phases 4/6 — do not fork a second client concept; unify
   if anything drifted). Write the permission matrix down as code-level
   truth, roughly:
   - Owner: everything, including billing, deleting the org, and
     transferring ownership.
   - Admin: everything except billing changes, org deletion, and
     Owner management.
   - Member: work with campaigns, CRM, outreach, and reports for
     clients they're allowed to see; no member management, no
     integrations/API-key management, no billing.
   - Client: the existing read-scoped client portal, unchanged.

6. **Enforcement at the data-access layer.** Permission checks live in
   the same layer that enforces `organization_id` scoping — never
   UI-only. Every mutating route asserts role server-side. Add tests
   that hit protected endpoints as each role and assert the matrix.

7. **Last-Owner protection.** An Organization must always have at least
   one Owner: block removing or downgrading the last Owner, and
   implement explicit ownership transfer (Owner promotes another
   member, optionally demotes self) rather than letting it happen as a
   side effect.

8. **Member removal.** Removing a member revokes sessions/tokens for
   that org immediately and must decide what happens to records they
   own (assigned CRM contacts, created campaigns): prompt the remover
   to reassign, defaulting to the removing admin. Never orphan records
   and never delete them.

## PART C — SEATS & USAGE

9. **Seat limits via entitlement stub.**
   `checkEntitlement(org, 'seats')` gates both sending an invite and
   accepting one (both, because seats can fill between the two).
   Pending invites count against the limit to prevent oversubscribing.

10. **Usage visibility.** "X of Y seats used" in the members settings
    view, following the same self-service usage pattern flagged on the
    roadmap (and matching Phase 12's metering displays). When at the
    limit, the invite button explains the limit and links to the
    billing/upgrade page (Phase 8) instead of failing opaquely.

## PART D — AUDIT TRAIL

11. **Log every membership event** — invite sent/revoked/accepted, role
    changed, member removed, ownership transferred — into the existing
    per-action audit log pattern from the ads-management/CRM phases,
    with actor, target, org, and timestamp. This is groundwork for the
    SOC 2 story already on the roadmap; cheap now, expensive later.

## ACCEPTANCE CHECKS

- Full happy path works for both accept flows (existing user, new
  user), with the org switcher behaving for multi-org users.
- Every cell of the permission matrix has a passing server-side test;
  UI hiding alone is nowhere load-bearing.
- The last Owner cannot be removed or demoted by any path, including
  self-service.
- Seats cannot be exceeded via any ordering of invites and accepts.
- A removed member's session is dead immediately, their records are
  reassigned, and every event above appears in the audit log.
- RLS verified on the invites table and any new membership tables.
