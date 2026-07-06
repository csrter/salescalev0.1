# Phase 9 — White-Labeling & AI Insights

Read `CLAUDE.md` first. This phase's real dependencies are Phase 1
(Organization/Client model), Phase 3 (metrics), Phase 4 (dashboard), and
Phase 6 (Salescale CRM) — not Phase 7 or Phase 8. It's fine to run this
phase right after Phase 6, before additional platform adapters (Phase 7)
or billing/self-serve signup (Phase 8) exist. A few tasks below note where
they'd normally tie into Phase 8's tier model and give a lighter-weight
version to build now instead, so nothing here is blocked waiting on it.
Wherever Phase 8 lands later, wire the real tier logic into the
abstraction points flagged below rather than reworking this phase's code.

This phase is also where Salescale stops looking like an internal tool
and starts looking like a product other agencies would actually pay for —
treat the white-labeling half especially carefully, since it's the
difference between "usable" and "sellable" for every Organization that
isn't Atlas Reach.

## PART A — WHITE-LABELING

1. **Custom domain per Organization.** An Organization can point their own
   domain/subdomain (e.g. `portal.theiragency.com`) at their instance,
   with automated SSL provisioning (Let's Encrypt or your hosting
   platform's domain API — check current support before building a
   custom cert-management flow from scratch). Confirm current domain
   verification requirements (DNS TXT record, CNAME) against whatever
   hosting/CDN provider you're using.

2. **Branding customization.** Per-Organization logo, color palette, and
   favicon, applied consistently across the Client-facing views built in
   Phase 4 and Phase 6. Decide (and confirm with the user) whether
   Organization-team-facing admin views are also rebranded, or stay
   Salescale-branded internally — agencies may not care about branding
   their own internal tool, only what their clients see, but don't assume
   this without asking.

3. **Zero-vendor-branding audit.** Every hardcoded "Salescale" reference,
   logo, or favicon in any Client-facing surface (dashboard, login page,
   emails, PDF/exported reports if built) needs to be conditional on the
   Organization's branding config, not hardcoded. Do an actual audit pass
   at the end of this phase — string-search the codebase for the product
   name and verify every hit outside of Salescale's own marketing/admin
   surfaces is correctly conditional.

4. **Branded transactional email.** Client invites, notifications, and
   any scheduled reports should send from the Organization's own
   configured domain/from-address if they've set one up, falling back to
   a neutral default if they haven't — never send client-facing email
   with "Salescale" as the visible sender for an Organization that's
   configured their own branding.

5. **Branded login/signup page per Organization**, reachable via their
   custom domain, for both their own team and their clients.

6. **Tier gating, built as a stub if Phase 8 doesn't exist yet.** If
   billing/tiers (Phase 8) are already built, propose which tier(s) get
   white-labeling (custom domain is commonly an Agency-tier-only feature
   industry-wide, with basic logo/color customization sometimes available
   lower down), confirm with the user, and enforce it server-side. If
   Phase 8 hasn't been built yet, make white-labeling available to every
   Organization for now, but put the check behind a single
   entitlement/permission function (e.g. `canUseWhiteLabeling(org)`)
   rather than leaving it unconditionally on throughout the codebase —
   that function can return `true` unconditionally today and get wired to
   real tier data later without touching every call site.

## PART B — AI INSIGHTS

7. **Grounded metric explanations.** A natural-language "explain this"
   feature on key metrics/widgets (e.g. "why did CPL increase this
   week") that calls the Claude API server-side. Ground every response in
   actual computed values already produced by Phase 3's metrics layer —
   pass the real numbers into the prompt or have the model call a
   function/tool that fetches them, rather than letting it free-generate
   from a raw data dump. A generated explanation that states a number not
   traceable back to Phase 3's own computed metrics is a correctness bug,
   not a style issue.

8. **Auto-generated report summaries.** A short natural-language executive
   summary generated from an Organization's own metrics (Phase 3) and
   Salescale CRM data (Phase 6) — e.g. for the client-facing reporting
   flow. Same grounding requirement as task 7: every claim in the summary
   needs to trace back to a real computed number, not an invented one.

9. **Tenant isolation for AI features — non-negotiable.** Every AI
   insight call must be scoped to the requesting Organization's own data
   only. Test this adversarially, not just functionally: attempt a prompt
   that tries to get the model to reference or compare against another
   Organization's data, and confirm the underlying data-fetching layer
   never had access to it in the first place — the AI feature should be
   architecturally incapable of a cross-tenant leak, not merely instructed
   not to do one.

10. **Usage limits and cost control, tier-ready but not tier-dependent.**
    Implement a usage cap and cost-tracking now regardless of whether
    Phase 8 exists yet: a per-Organization counter for AI queries/summaries
    and a configurable limit (a single global default is fine before
    Phase 8 exists). Monitor actual Claude API cost per Organization so
    pricing stays sustainable as usage scales. Structure the limit-check
    the same way as task 6 — one function Phase 8 can later wire to real
    subscription-tier limits — rather than hardcoding a number inline
    wherever the AI feature is called. Flag to the user if any proposed
    limit looks likely to run at a loss once real usage data exists.

11. **Data handling disclosure.** Since AI insights send Organization
    data to the Claude API (a third-party processor from each
    Organization's perspective), document what data leaves the system
    boundary for this feature and surface it in whatever terms/privacy
    documentation exists — don't let this be a silent implementation
    detail if bigger agency customers later ask about data handling
    during a security review.

## DEFINITION OF DONE

- A test Organization can configure a custom domain and see it live,
  with zero Salescale branding visible anywhere in that Organization's
  Client-facing surfaces — verified by the audit in task 3, not just
  spot-checking a couple of pages.
- Logo/color customization is reflected consistently across every
  Client-facing view, not just the main dashboard.
- The AI "explain this metric" feature produces a response for a real
  test account, and every specific number in that response is verified
  against Phase 3's actual computed metric — not just plausible-sounding.
- An adversarial test attempting to make the AI insight feature reference
  another Organization's data fails, because the underlying query never
  had access to it — not because the model politely declined.
- Tier gating for both white-labeling and AI insights goes through a
  single entitlement-check function per feature (not scattered inline
  checks) — verified by test if Phase 8 already exists, or verified as a
  clean stub ready to wire up if it doesn't yet.

## BEFORE YOU FINISH

Update `CLAUDE.md`'s Current Status section and commit. This phase is a
good point to step back and use the product end-to-end as if you were
looking at it through a brand-new agency's eyes — the white-labeling work
especially is easy to get 90% right and miss the one hardcoded logo
reference that breaks the illusion. If Phase 8 hasn't been built yet,
that review happens against a test Organization created directly rather
than through a real signup flow — the branding/isolation checks matter the
same either way.
