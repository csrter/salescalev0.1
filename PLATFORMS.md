# Platform Reference

Per-platform specifics for adapter implementation. Verify every detail
below against each platform's current developer documentation before
implementing — API names, scopes, and conversion-tracking mechanics shift
over time, and this table is a starting map, not a spec to code against
blindly.

## Fit note (read this before building Phase 7)

The commentary below reflects Atlas Reach's own client base — HVAC and
home-services contractors — as the first Organization using this
platform. Since this is a multi-tenant SaaS, every Organization can
connect and use any supported platform regardless of their vertical; this
section is context for how Atlas Reach itself will likely use each
platform, not a constraint on the product.

- **Snapchat** — younger, visual-first audience. Weak fit for HVAC/home
  services lead-gen specifically; better fit if Atlas Reach's other
  ventures (e.g. Ryft Dynamics / e-commerce, per broader account context)
  ever move onto this platform.
- **Reddit** — niche-community, high-intent but research-heavy audience.
  Weak-to-moderate fit for HVAC; could work for niche B2B/SaaS positioning
  if Atlas Reach's multi-vertical expansion goes that direction. Reddit's
  ad platform and API are also younger/less mature than Meta or Google's —
  expect more rough edges.
- **LinkedIn** — strong fit for B2B/SaaS lead-gen, weak fit for HVAC
  contractor acquisition specifically (consistent with the earlier
  recommendation against LinkedIn ads for HVAC clients at this stage).
  Worth building given Atlas Reach's SaaS-vertical expansion plans.
- **Microsoft Advertising (Bing)** *(recommended addition)* — strong fit
  for home services. Search intent mirrors Google Ads, often at lower CPCs
  with less competition, and Bing's user base skews toward exactly the
  homeowner demographic that searches for HVAC/plumbing/electrical
  services. Very close to a "free win" alongside the existing Google
  integration since the API and campaign structure are similar.
- **Nextdoor** *(recommended addition)* — strong fit, arguably the best
  fit of anything on this list besides Meta/Google. Nextdoor is
  hyper-local and neighborhood-based, which is close to ideal targeting
  for home-service contractors who serve a specific service radius.
- **TikTok** *(optional, not currently in scope)* — worth a mention but
  not included by default: younger audience, weaker fit for HVAC lead-gen,
  but a large and fast-growing ad platform if Atlas Reach's other verticals
  ever call for it. Add as an adapter later using the same pattern if it
  becomes relevant — no architectural reason not to.

## Meta

- API: Marketing API (Graph API).
- Auth: OAuth + app review for `ads_management`/`ads_read`; Business
  Verification required at higher volume.
- Conversion API: Conversions API (CAPI), server-side, event_id dedup
  against Pixel.
- Click ID: `fbclid` / `fbc` cookie.
- Status: already scoped in Phases 1/2/3/5 as first reference
  implementation.

## Google

- API: Google Ads API.
- Auth: Developer token (Basic vs. Standard access tiers) issued to a
  manager account (MCC), OAuth per linked client account.
- Conversion tracking: Enhanced Conversions for Leads, offline conversion
  import via API.
- Click ID: `gclid`.
- Status: already scoped in Phases 1/2/3/5 as second reference
  implementation.

## Snapchat

- API: Snapchat Marketing API.
- Auth: OAuth 2.0; developer/business account required; ad account access
  granted per Business Manager-equivalent structure.
- Conversion tracking: Snap Conversions API (server-side, similar
  event-based model to Meta's CAPI).
- Click ID: Snap's own click-identifier parameter — confirm current name
  and capture mechanics against Snap's docs at build time.
- Notes: campaign structure loosely mirrors Meta's (Campaign → Ad Squad →
  Ad), which may make the adapter easier to map from the Meta
  implementation than from Google's.

## Reddit

- API: Reddit Ads API.
- Auth: OAuth 2.0; requires a Reddit Ads account and API access approval.
- Conversion tracking: Reddit Conversions API (server-side).
- Click ID: confirm Reddit's current click-tracking parameter at build
  time — this is a newer API surface and has had more changes than Meta's
  or Google's.
- Notes: expect a less mature API than Meta/Google — build with more
  defensive error handling and don't assume feature parity (e.g. bulk
  operations, granular reporting) will match the bigger platforms.

## LinkedIn

- API: LinkedIn Marketing API (Campaign Manager).
- Auth: OAuth 2.0; requires a LinkedIn Marketing Developer Platform
  application and access approval, tied to a Company Page / ad account.
- Conversion tracking: LinkedIn Conversions API (server-side).
- Click ID: confirm LinkedIn's current click-identifier mechanics against
  their docs — differs from the `fbclid`/`gclid` pattern.
- Notes: campaign structure (Campaign Group → Campaign → Ad) and targeting
  concepts (job title, seniority, company size) don't map onto
  Meta/Google's audience model — don't force LinkedIn's B2B targeting
  fields into a shared "audience" abstraction built around consumer
  platforms.

## Microsoft Advertising (Bing)

- API: Microsoft Advertising API (formerly Bing Ads API).
- Auth: OAuth 2.0 + a Microsoft Advertising developer token.
- Conversion tracking: offline conversion import, UET (Universal Event
  Tracking) tag for site-side tracking.
- Click ID: `msclkid`.
- Notes: campaign/ad group/keyword structure closely mirrors Google Ads —
  this adapter should be one of the faster ones to build given the
  Google adapter already exists as a reference, though the two APIs are
  not identical and shouldn't be assumed interchangeable.

## Nextdoor

- API: Nextdoor Ads API / business advertising platform — confirm current
  API availability and access requirements at build time, as Nextdoor's
  self-serve ad API maturity has historically lagged the larger platforms.
- Auth: confirm current OAuth/access model against Nextdoor's developer
  documentation.
- Conversion tracking: confirm current server-side conversion support —
  may be less mature than Meta/Google/Snap equivalents.
- Notes: hyper-local, neighborhood-based targeting is the entire value
  proposition here — don't force Nextdoor's geographic targeting model
  into a generic "audience" abstraction built for demographic/interest
  targeting on other platforms.
