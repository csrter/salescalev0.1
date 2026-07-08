# Platform go-live: Meta & Google approvals

The integration **code** is built and was verified against live docs on
2026-07-06 (Meta Graph API v25.0, Google Ads API v24). What stands between it
and production is **external approval + credentials** — these take days to
weeks, so start them early. This doc lists exactly what each platform needs.

Set all credentials as **backend** environment variables (see `.env.example`);
never ship them to the client.

---

## Meta (Facebook / Instagram Ads)

**What the code uses:** OAuth `dialog/oauth` → token exchange; Graph API for
campaign read/manage; Conversions API (`POST /{dataset_id}/events`).
- Scopes requested: `ads_management, ads_read, business_management`
  (`app/services/meta_api.py`).
- Conversions API: `app/services/meta_capi.py`.

**To go live:**
1. Create an app at **developers.facebook.com** (Business type) and add the
   **Marketing API** product.
2. Complete **Business Verification** for your Meta Business.
3. **App Review** for **Advanced Access** to `ads_management`, `ads_read`,
   `business_management` — with a screencast of the OAuth + management flow.
   (Standard/dev access only works for users with a role on the app, fine for
   testing.)
4. For the Conversions API, each client connects a **dataset/pixel**; set
   `META_WEBHOOK_VERIFY_TOKEN` if you use lead webhooks.
5. Set the OAuth **redirect URI** to your hosted backend:
   `https://<api-domain>/api/connect/meta/callback`.

**Env vars:** `META_APP_ID`, `META_APP_SECRET`, `META_REDIRECT_URI`,
`META_API_VERSION` (default `v25.0`), `META_WEBHOOK_VERIFY_TOKEN`.

---

## Google Ads

**What the code uses:** OAuth2 (`adwords` scope) → refresh token; the official
**`google-ads`** Python SDK (GAQL + `upload_click_conversions`) for management
and **Enhanced Conversions for Leads**.
- `app/services/google_ads_api.py`, `app/services/google_conversions.py`.
- `google-ads` is imported lazily and is now in `requirements.txt` — it's
  **required** for any Google feature (heavy; pulls gRPC).

**To go live:**
1. Create a **Google Cloud project**; configure the **OAuth consent screen**.
   The `https://www.googleapis.com/auth/adwords` scope is **sensitive**, so the
   consent screen needs **verification** before external users can connect.
2. Apply for a **Google Ads API developer token** (in a manager/MCC account).
   It starts at **Basic access** — apply for **Standard access** for
   production volume.
3. Create an **OAuth 2.0 Client** (Web); set the redirect URI to
   `https://<api-domain>/api/connect/google/callback`.
4. **Enhanced Conversions for Leads** must be enabled on each managed account
   (the code checks `enhanced_conversions_for_leads_enabled`).

**Env vars:** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_DEVELOPER_TOKEN`, `GOOGLE_LOGIN_CUSTOMER_ID`, `GOOGLE_REDIRECT_URI`.

---

## Before you flip it on

- **Redirect URIs** in both platforms must match the hosted backend exactly
  (https). Update them when the domain changes.
- **Test with your own ad accounts first** (dev/standard access) — verify
  connect → pull insights → send a test conversion end-to-end — before
  submitting for review or onboarding other agencies.
- Phase 7 platforms (Snapchat, Reddit, LinkedIn, Microsoft, Nextdoor) are
  **not built** yet — see `PHASE_7_ADDITIONAL_PLATFORMS.md`.
- `TOKEN_ENCRYPTION_KEY` must be set (already done in `.env`) or storing the
  platform OAuth tokens fails.
