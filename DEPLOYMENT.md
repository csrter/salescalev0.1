# Deploying Salescale (hosted web architecture)

This is the **recommended production architecture** and resolves the P0 blocker
in `RELEASE_CHECKLIST.md`: instead of every desktop app connecting directly to
the database, a single **hosted backend** holds the database credentials, and
clients (the web app) talk to it over HTTPS.

```
Browser ──HTTPS──▶  Web frontend (static)  ──HTTPS──▶  Backend API  ──▶  Supabase Postgres
                    (Vercel/Netlify/nginx)            (FastAPI)          (managed)
```

Only the backend ever holds `DATABASE_URL` and secrets. Nothing sensitive ships
to the browser.

---

## 1. Backend (FastAPI service)

Deploy `backend/` as a container (image built from `backend/Dockerfile`) on any
host — Render, Railway, Fly.io, a VPS, ECS, etc.

**Required environment variables** (set them in the host's dashboard/secrets —
never commit them):

| Var | Value |
|-----|-------|
| `DATABASE_URL` | Supabase **Session pooler** string (`postgresql://postgres.<ref>:<pw>@aws-0-<region>.pooler.supabase.com:5432/postgres`) |
| `JWT_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `TOKEN_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` — **if the database already holds connected ad accounts, reuse the exact key that encrypted them** (changing it makes every stored platform token undecryptable; every connection would need re-authorizing) |
| `FRONTEND_ORIGIN` | the web app's URL, e.g. `https://app.salescale.com` (comma-separate for multiple/preview domains) |
| `APP_BASE_URL` | same as the web app's URL — invite/verify/reset links, social-login returns, and Stripe returns all land here. Without it, invite links point at `localhost:5173` |
| `API_BASE_URL` | the backend's own public URL, e.g. `https://api.salescale.com` — builds the sign-in OAuth redirect URIs |
| `META_REDIRECT_URI` / `GOOGLE_REDIRECT_URI` | `https://<api>/api/connect/{meta,google}/callback` — the connect-flow callbacks default to `localhost:8000` and must be overridden |
| `RESEND_API_KEY` + `EMAIL_DEFAULT_FROM_ADDRESS` | transactional email (team invites, verification, resets). Without a transport, invite links are surfaced to the Admin in the Team UI to share manually — fine for testing, not for a real team |
| `TRUST_FORWARDED_FOR` | `1` when behind a reverse proxy / managed host, so rate limiting sees real client IPs |
| `SUPERADMIN_EMAILS` | your platform-admin email(s), comma-separated |

Optional (enable the corresponding integration): `META_APP_ID`,
`META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_DEVELOPER_TOKEN`, `GOOGLE_LOGIN_CUSTOMER_ID`,
`ANTHROPIC_API_KEY`, `GOOGLE_PLACES_API_KEY`, `ZEROBOUNCE_API_KEY`,
`REQUIRE_EMAIL_VERIFICATION=true` (recommended once email delivery works),
`SMTP_*`. See `.env.example`.

Notes:
- **Do not set `DESKTOP_MODE`** — that's for the Electron shell and opens CORS
  to `*`. Leaving it unset enforces the `FRONTEND_ORIGIN` allowlist.
- **Migrations run automatically on startup** (`alembic upgrade head`), so a new
  database is provisioned on first boot and future schema changes apply on
  deploy. No manual migration step.
- **Health check path:** `GET /api/health` → `{"ok": true}`.
- The container listens on `$PORT` (default 8000).
- **OAuth redirect URIs:** after deploy, the Integrations page (or
  `GET /api/integrations/redirect-uris`) lists the four exact URIs — connect
  and sign-in, per provider — to register on your Google Cloud OAuth client
  and Meta app. A missing entry is the `redirect_uri_mismatch` error.

## 2. Web frontend (static site)

Build `frontend/` and deploy the `dist/` output to any static host (Vercel,
Netlify, Cloudflare Pages, S3+CloudFront, nginx…). Bake in the backend URL:

```bash
cd frontend
VITE_API_URL="https://api.salescale.com" npm run build   # outputs dist/
```

Or build the container from `frontend/Dockerfile`:

```bash
docker build --build-arg VITE_API_URL="https://api.salescale.com" -t salescale-web ./frontend
```

## 3. Wire the two together

- Backend `FRONTEND_ORIGIN` must equal the frontend's URL (CORS).
- Frontend `VITE_API_URL` must equal the backend's URL.
- Put both behind HTTPS (managed hosts do this automatically).

## 4. Run the whole stack locally (production images)

```bash
docker compose up --build
# web  → http://localhost:8080
# api  → http://localhost:8000
```

(`docker-compose.yml` reads secrets from `backend/.env`.)

## VPS deploy (self-managed — Hostinger, DigitalOcean, any Ubuntu box)

Managed platforms (Render/Railway/Fly) handle TLS, firewalling, and patching
for you. A VPS doesn't — you're the host. This section is the full runbook:
harden the box, install Docker, deploy `deploy/docker-compose.prod.yml`
(backend + frontend + Caddy, which gets you free auto-renewing HTTPS with no
certbot/cron), and keep it maintained. Assumes Ubuntu 22.04/24.04 (Hostinger's
default image); commands differ slightly on other distros.

### A. Point DNS at the VPS first

Certificate issuance needs this to resolve *before* you start the stack. At
your DNS provider, add two **A records** pointing at the VPS's public IP:

```
app.yourdomain.com   A   <VPS IP>
api.yourdomain.com   A   <VPS IP>
```

If the domain sits behind Cloudflare, set both to **DNS only** (grey cloud) —
Caddy's HTTP-01 challenge needs to reach the VPS directly on port 80; an
orange-clouded/proxied record breaks it.

**Hostinger-specific:** hPanel has its own VPS-level firewall (separate from
the guest OS's `ufw` below). Check hPanel → VPS → Firewall and make sure a
rule allows 22, 80, and 443 inbound, or the OS firewall config below won't
matter — traffic never reaches the box.

### B. Harden the host

SSH in as `root` (Hostinger emails/shows the initial password) and run:

```bash
# 1. Create a non-root admin user — never operate as root day to day.
adduser deploy
usermod -aG sudo deploy

# 2. Copy your SSH public key to it (run this from YOUR machine, not the VPS):
#      ssh-copy-id deploy@<VPS IP>
#    If you don't have a keypair yet, generate one locally first:
#      ssh-keygen -t ed25519 -C "you@yourmachine"

# 3. Back on the VPS, lock down SSH: key-only, no root login.
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart ssh

# From a NEW terminal (keep the current session open until this works!):
#   ssh deploy@<VPS IP>
# Confirm you can log in and sudo before closing the root session.

# 4. Firewall: only SSH, HTTP, HTTPS. Everything else denied by default.
sudo apt update && sudo apt install -y ufw fail2ban unattended-upgrades
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

# 5. fail2ban (SSH brute-force protection) and unattended security patches —
#    both ship with sane defaults; just enable them.
sudo systemctl enable --now fail2ban
sudo dpkg-reconfigure -plow unattended-upgrades   # choose "Yes"
```

**Docker + ufw gotcha:** Docker manipulates `iptables` directly and can punch
through `ufw` rules for anything published with `ports:` in a compose file —
a container publishing `8000:8000` can be reachable from the internet even
though `ufw` never allowed it. `deploy/docker-compose.prod.yml` sidesteps this
entirely: the backend and frontend use `expose` (container-to-container only,
nothing bound to the host), and Caddy is the *only* service that publishes to
the host, on 80/443. Don't add a `ports:` mapping to backend/frontend for
"quick debugging" and forget to remove it — that's the way this gets bypassed.

If the VPS is small (Hostinger's entry KVM plans start around 1–4GB RAM),
building the frontend image (`npm ci` + `vite build`) can spike memory. Add
swap if you don't have at least 4GB RAM:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### C. Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker deploy
# Log out and back in for the group change to take effect.
```

Cap container log growth (unbounded logs will fill the disk over months):

```bash
echo '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' \
  | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker
```

### D. Deploy the stack

**First, check whether this VPS already runs a reverse proxy.** Hostinger's
"Docker" VPS template (and some other one-click app catalogs) ship with
Traefik pre-installed and already bound to 80/443:

```bash
sudo ss -tlnp | grep -E ':80|:443'
docker ps --format '{{.Names}}\t{{.Image}}'
```

If something's already listening there, use `docker-compose.traefik.yml`
(labels-only — it doesn't touch the existing proxy). If 80/443 are free, use
`docker-compose.prod.yml` (brings its own Caddy). Pick one:

```bash
git clone <your fork/repo URL> salescale && cd salescale

# App secrets (DATABASE_URL, JWT_SECRET, TOKEN_ENCRYPTION_KEY, …) — see the
# required-vars table in step 1 above.
cp .env.example backend/.env
nano backend/.env
chmod 600 backend/.env

# Domains (+ Caddy's cert-notice email, if using the Caddy variant).
cp deploy/.env.example deploy/.env
nano deploy/.env

cd deploy
```

**Fresh VPS, nothing on 80/443 (Caddy owns the ports):**
```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f caddy   # watch cert issuance succeed
```

**Existing Traefik already on 80/443 (labels only, no proxy service of our own):**
```bash
docker compose -f docker-compose.traefik.yml up -d --build
docker logs -f <your-traefik-container-name>   # watch it pick up the two new routers
```
The labels in `docker-compose.traefik.yml` assume entrypoints named
`web`/`websecure` and a cert resolver named `letsencrypt` (Hostinger's
default). If your Traefik's `--entrypoints.`/`--certificatesresolvers.` flags
use different names, check its compose file and adjust
`entrypoints=`/`certresolver=` in the labels to match.

Verify: `curl https://api.yourdomain.com/api/health` → `{"ok": true}`, and
`https://app.yourdomain.com` loads the login screen over a valid cert. Then
follow **OAuth redirect URIs** in step 1 above to register the four callback
URLs on your Meta/Google apps before connecting any ad account or testing
social sign-in.

**Redeploying after a `git pull`:** re-run whichever `up -d --build` command
you used above.

### E. Ongoing

- **Secrets backup:** `backend/.env` exists only on this VPS. If
  `TOKEN_ENCRYPTION_KEY` is lost, every stored platform token becomes
  permanently undecryptable — every client's ad-account connections would
  need re-authorizing. Keep a copy of `backend/.env` in a password manager or
  secrets vault, not just on the disk.
- **Database backups:** the app database lives on Supabase (managed) — backups
  and point-in-time recovery are configured there, not on this VPS. Check
  Supabase's backup settings for your project's tier.
- **Uptime:** point a free monitor (UptimeRobot, Better Uptime, etc.) at
  `https://api.yourdomain.com/api/health`.
- **OS patches:** `unattended-upgrades` handles security patches
  automatically; `sudo apt update && sudo apt upgrade` periodically for the
  rest. Reboot after kernel updates (`sudo reboot`, during a maintenance
  window — this drops the containers briefly; Docker's `restart: unless-stopped`
  brings them back up on boot).
- **Certs:** fully automatic — Caddy renews before expiry. Only re-check DNS
  if a domain's A record ever changes.

## 5. Onboard the team

Once the stack is up: log in (or sign up your org), go to **Team → Invite by
email**, and invite each member. With `RESEND_API_KEY` set the invite email
delivers; members open the link from their own machine, set a password, and
work entirely in the browser — nothing to install, no secrets on their
machines. Seats are metered per plan (starter 5 / pro 15 / agency unlimited),
and Owners can require 2FA org-wide from Security.

## Desktop app (optional)

The Electron app in `electron-app/` runs its **own local backend** and is a
single-user/offline option, not the multi-tenant deployment. For a hosted SaaS,
the **web frontend is the primary client**. If you want the desktop app to use
the hosted API instead of a local backend, build its frontend with
`VITE_API_URL=<backend url>` and remove the `startBackend()` call in
`electron-app/main.js`.

## Security reminder

`DATABASE_URL` and all secrets live **only** on the backend host. Never bundle
them into the web build or a distributed desktop `config.json` — anyone with
the artifact could then reach the whole multi-tenant database directly.
