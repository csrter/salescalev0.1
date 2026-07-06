# Setup & Runbook — Phase 1

## Prerequisites

- Node 18+ (present) and Python 3.12 (installed via `uv`; the backend venv
  at `backend/.venv` already uses it).
- Postgres 17 runs natively via Postgres.app (`~/Applications/Postgres.app`,
  data dir `~/Library/Application Support/Postgres/var-17`) with a
  `salescale`/`salescale` role and database already created — matching the
  default `DATABASE_URL` in `.env.example`. Start/stop:

  ```bash
  PGBIN=~/Applications/Postgres.app/Contents/Versions/17/bin
  "$PGBIN/pg_ctl" -D ~/Library/"Application Support"/Postgres/var-17 start   # or stop
  ```

  Opening Postgres.app once and enabling "Start on login" makes this
  automatic. `docker-compose up db` remains an alternative when Docker
  Desktop is running. If `DATABASE_URL` is unset, dev falls back to SQLite.

> **Intel-mac note:** `cryptography` is capped `<49` in requirements.txt —
> 49+ ships no prebuilt wheels for Intel macs and tries a full Rust/OpenSSL
> source build.

## Backend

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp ../.env.example ../.env   # then fill it in (see below)
.venv/bin/python -m scripts.seed --email you@atlasreach.com --password '...'
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Tests (tenant isolation, client-role scoping, UTM capture):

```bash
.venv/bin/python -m pytest tests/ -q
```

Migrations: dev uses `create_all` on startup. For Postgres, generate the
initial migration once a Postgres `DATABASE_URL` is set:
`alembic revision --autogenerate -m "initial"` then `alembic upgrade head`.

## Frontend

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```

## Required secrets (.env — never committed)

| Variable | Where it comes from |
|---|---|
| `JWT_SECRET` | generate: `openssl rand -hex 32` |
| `TOKEN_ENCRYPTION_KEY` | generate: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `META_APP_ID` / `META_APP_SECRET` | Meta app at developers.facebook.com (Business type app) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth client (Web) in Google Cloud Console |
| `GOOGLE_DEVELOPER_TOKEN` | Google Ads MCC → Tools → API Center |
| `GOOGLE_LOGIN_CUSTOMER_ID` | Atlas Reach MCC customer ID (digits only) |

Add the redirect URIs to each platform app config:
- Meta → Valid OAuth Redirect URIs: `http://localhost:8000/api/connect/meta/callback`
- Google → Authorized redirect URIs: `http://localhost:8000/api/connect/google/callback`

## Platform-side steps only you can do (on their timeline, not ours)

**Meta (Marketing API v25.0)**
- With no App Review, the app runs at **Standard Access**: it can only see
  ad accounts owned by/associated with the app's own business — enough to
  test against Atlas Reach's own ad account, which is exactly the Phase 1
  plan.
- Before connecting *client* ad accounts you must pass **App Review** for
  `ads_management`, `ads_read`, `business_management` (Advanced Access),
  which also requires **Business Verification** of Atlas Reach. Meta
  controls the timeline; start it early.
- Long-lived user tokens (~60 days) are stored; when one expires or a
  client revokes access, the connection flips to `disconnected` in the UI
  and needs a reconnect.

**Google Ads (API v24)**
- You need a **manager account (MCC)** for Atlas Reach; the developer
  token comes from the MCC's API Center.
- New developer tokens start at **Basic Access** (limited daily operations
  and account caps). Apply for **Standard Access** once you're past
  testing — Basic is fine for Phase 1 against your own account.
- Each client's Google Ads account should be **linked under the MCC**
  (client accepts the link invitation), then the OAuth connect flow in the
  app grants API access per client.
- If Google doesn't return a refresh token on reconnect, remove the app at
  myaccount.google.com/permissions and reconnect (the app requests
  `prompt=consent` to avoid this, but account settings can override).

## Landing-page tracking snippet

Embed on each client's site (replace `CLIENT_ID` and the API host). It
captures UTMs + click IDs once per session at landing time:

```html
<script>
(function () {
  var KEY = "ar_session";
  var sessionKey = localStorage.getItem(KEY);
  if (!sessionKey) {
    sessionKey = (crypto.randomUUID ? crypto.randomUUID() :
      String(Date.now()) + Math.random().toString(16).slice(2));
    localStorage.setItem(KEY, sessionKey);
  } else {
    return; // already captured this session's landing
  }
  var q = new URLSearchParams(location.search);
  fetch("https://YOUR-API-HOST/api/track/landing", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      client_id: "CLIENT_ID",
      session_key: sessionKey,
      landing_url: location.href,
      utm_source: q.get("utm_source"),
      utm_medium: q.get("utm_medium"),
      utm_campaign: q.get("utm_campaign"),
      utm_content: q.get("utm_content"),
      utm_term: q.get("utm_term"),
      referrer: document.referrer || null,
      fbclid: q.get("fbclid"),
      gclid: q.get("gclid"),
      user_agent: navigator.userAgent
    })
  });
})();
</script>
```

## Definition-of-Done status

- Login → connect Meta + Google → browse account/campaign/ad-set/ad
  hierarchy: **built**; needs your real test accounts + app credentials to
  exercise live (see platform steps above).
- UTM capture into `landing_events`: **verified by test**
  (`tests/test_attribution.py`).
- Tenant isolation: **verified by test** (`tests/test_tenant_isolation.py`)
  — cross-tenant reads return 404 through list, direct-id, nested, and
  query-param paths.
- Client role genuinely read-only/scoped: **verified by test**
  (`tests/test_client_role.py`) — writes 403, OAuth start 403,
  `internal_notes` absent from the schema clients are serialized with.
- No secrets in source: `.env` is gitignored; only `.env.example`
  placeholders are committed.
