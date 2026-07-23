# iMessage (BlueBubbles) VPS relay — Workstream R

Manual setup runbook. **Nothing in this directory is executed for you** —
these are config files and scripts the operator (Carter) runs by hand on
real infrastructure. This session did not SSH into or configure any host;
it only produced the files below.

## Why this exists

Salescale's SMS module already has a `bluebubbles` provider
(`backend/app/models/sms_outreach.py`, `backend/app/services/sms_send.py`)
that sends iMessages by POSTing to a **BlueBubbles Server** REST API. The
problem: BlueBubbles Server only runs on a Mac (it drives Messages.app
through the Private API), and that Mac has **no public IP** — its REST API
only listens on `localhost:1234` by default. Salescale's backend, running
on the VPS, can't reach a `localhost` port on a machine across the
internet.

The fix is a small relay:

```
Salescale backend (VPS)                 Relay VPS                    Mac (BlueBubbles Server)
┌──────────────────┐   HTTPS (443)   ┌────────────────┐   loopport   ┌─────────────────────┐
│ services/         │ ───────────►   │ Caddy           │ ◄──────────  │ autossh              │
│ sms_send.py        │   firewalled   │ (auto-TLS)      │   (12345)   │ -R 12345:localhost:  │
│ _bluebubbles_send   │   to backend  │       │         │             │        1234          │
│ _verify_bluebubbles │   IP only     │       ▼         │             │        │              │
└──────────────────┘                 │ 127.0.0.1:12345 │             │        ▼              │
                                       └────────────────┘             │ BlueBubbles Server    │
                                                                       │ REST API :1234        │
                                                                       └─────────────────────┘
```

1. The Mac opens a **reverse SSH tunnel** to the relay VPS (`autossh -R
   12345:localhost:1234 relay@<vps>`) — the Mac dials out, so no inbound
   port needs to be opened on the Mac or its home router.
2. That tunnel lands on `127.0.0.1:12345` on the relay VPS — nothing else
   can reach it, by construction (SSH remote-forward binds loopback unless
   `GatewayPorts` is on, which it must stay off — see "GatewayPorts" below).
3. **Caddy** on the relay VPS terminates public HTTPS for a subdomain
   (auto-provisioned Let's Encrypt cert) and reverse-proxies straight to
   `127.0.0.1:12345`.
4. The relay's public port is **firewalled to only Salescale's backend
   egress IP** — nobody else on the internet can reach it even though it's
   a public HTTPS endpoint.
5. Salescale's backend talks to `https://imessage-relay.<domain>` exactly
   as if it were talking to BlueBubbles directly — `relay_url` on the
   `SmsAccount` row is this URL, and BlueBubbles' own `?password=...` query
   param (already attached by `services/sms_send.py`) is the app-level auth
   on top of the transport.

**What this relay is NOT for:** the *inbound* leg (BlueBubbles → Salescale,
i.e. new iMessages / read receipts arriving as a webhook) goes **directly**
from the Mac to Salescale's public API — the Mac already has a normal
outbound path to the internet for that, no relay or tunnel needed. See
"Inbound webhooks" near the end for why, and for the optional exception.

## Live deployments (what production actually runs)

Production reuses the existing Traefik VPS (`2.25.75.95`) — NOT a dedicated
relay VPS and NOT Caddy. Both relays are Traefik **file-provider** routes in
`/docker/traefik/dynamic/` on that host (option 1 of "Reusing the existing
VPS" below); the `Caddyfile` here is reference-only for the dedicated-VPS
topology. Two relays exist, one per BlueBubbles Mac:

| Hostname | Tunnel port | Mac | Tunnel user | Tunnel job |
|---|---|---|---|---|
| `imessage-relay.salescale.lol` | `127.0.0.1:12345` | Carter's MacBook (SIP off, Private API on) | `deploy@` | LaunchAgent `com.salescale.bluebubbles-tunnel` |
| `imsg.atlasreach.io` | `127.0.0.1:8443` | AWS EC2 Mac `i-0f2a35cae939b4f66` (us-east-2, mac2-m2pro.metal, IP not elastic) | `imsgtunnel@` | LaunchDaemon `/Library/LaunchDaemons/com.salescale.imsgtunnel.plist` (needs explicit `HOME` env — LaunchDaemons don't inherit it) |

EC2 Mac caveats: **SIP cannot be disabled on EC2 Mac instances** (no
Recovery Mode), so BlueBubbles' Private API is structurally unavailable
there — `services/sms_send._bluebubbles_method` probes
`/api/v1/server/info` per send and downgrades to `method: apple-script`
automatically. The `imsgtunnel` key is restricted to
`no-agent-forwarding,no-X11-forwarding,no-pty,permitlisten="127.0.0.1:8443"`
(the plist requests an explicit `-R 127.0.0.1:8443` bind, so `permitlisten`
matches `127.0.0.1`, not `localhost`). `GatewayPorts` on the VPS is the
default `no`. Neither relay is IP-allowlisted (see the same-VPS firewall
note below — on the shared box the ufw rule is inert); BlueBubbles'
`?password=` auth is the gate.

## Files in this directory

| File | Runs on | Purpose |
|---|---|---|
| `Caddyfile` | Relay VPS | Public HTTPS termination + reverse proxy to the tunnel's loopback port. |
| `com.salescale.bluebubbles-tunnel.plist` | **The Mac** (launchd) | Keeps the reverse SSH tunnel open. **Use this one on macOS.** |
| `autossh-bluebubbles.service` | A Linux host (systemd) | Same tunnel, systemd form. Reference only — macOS doesn't use systemd. Only relevant if you ever run the tunnel from a Linux box instead of the Mac directly. |
| `firewall.sh` | Relay VPS | ufw rules restricting 443 to Salescale's backend IP; iptables fallback commented at the bottom. |

---

## Setup, in order

### 0. Decide: dedicated relay VPS, or reuse the existing Traefik VPS?

**Recommended: a small dedicated VPS just for this relay** (the cheapest
tier of any provider is plenty — this proxies a handful of short-lived API
calls, not real traffic). Reasons to prefer this over reusing
`2.25.75.95` (the existing Salescale/Traefik/mailserver box, per
`DEPLOYMENT.md` / `MAILSERVER.md`):

- **Port 443 is already owned by Traefik** on that box (host network,
  `docker-compose.traefik.yml`). Caddy can't also bind 443 there without
  either (a) Traefik terminating TLS and handing plaintext to Caddy — which
  drops the "Caddy does its own auto-TLS" property this workstream asks
  for — or (b) an SNI-based TCP passthrough router in Traefik so Caddy
  keeps doing its own ACME, which works but is genuinely more moving parts
  for a feature this small. See "Reusing the existing VPS" below if you
  still want to go this route.
- **Blast-radius isolation.** This relay's only job is proxying to a
  personal Mac's iMessage client. Keeping its SSH key, its firewall rule,
  and its one open service separate from the box running the paying
  product's database access and client-facing app means a mistake here
  (or BlueBubbles itself, which is explicitly a "dev/prototype provider"
  per the code comments) can't touch anything else.
- **Simplicity.** A brand-new small VPS running only Caddy + sshd needs
  none of the compose-file / Traefik-router / acme.json-sharing steps
  `MAILSERVER.md` had to work through for the mail server case — you just
  install Caddy, drop in the `Caddyfile`, and open port 443.

If you'd rather not stand up a second VPS, see **"Reusing the existing
VPS"** below — it's fully supported, just requires one extra Traefik-side
step this README documents but does not perform for you (no existing files
are edited by this workstream).

### 1. Provision the relay VPS

Any small Ubuntu VPS works (Hostinger/DigitalOcean/Vultr/etc., 1 vCPU /
512MB–1GB RAM is overkill-safe). Note its public IPv4 address — you'll need
it for the DNS record in step 2. Update it and install prerequisites:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl ufw
```

### 2. DNS — A record for the relay subdomain

On whichever DNS provider is authoritative for the domain you're using
(Porkbun for `atlasreach.io`/`salescale.lol` per the existing runbooks, or
wherever this domain actually lives):

```
Type  Host / Name          Value              TTL
A     imessage-relay       <relay VPS IP>     600
```

i.e. `imessage-relay.atlasreach.io → <relay VPS IP>` (**CHANGE ME** to your
real subdomain/domain — update it in `Caddyfile` too). Wait for propagation
(`dig +short imessage-relay.atlasreach.io`) before starting Caddy — it
needs the record live to complete the HTTP-01 challenge on first start.

### 3. Generate + install the restricted SSH key (Mac → relay VPS)

On the Mac (BlueBubbles machine):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_bluebubbles_relay -C "mac-bluebubbles-relay-tunnel" -N ""
```

On the relay VPS, create a dedicated, unprivileged user for the tunnel —
**never use root or an existing admin account for this**:

```bash
sudo adduser --disabled-password --gecos "" relay
sudo mkdir -p /home/relay/.ssh
sudo chown relay:relay /home/relay/.ssh
sudo chmod 700 /home/relay/.ssh
```

Add the **public** half of the key (`~/.ssh/id_ed25519_bluebubbles_relay.pub`
from the Mac) to `/home/relay/.ssh/authorized_keys` on the VPS, but with a
restriction prefix — this is the single most important security line in
this whole setup, because it's what turns "an SSH key that can log in" into
"a key that can ONLY forward port 12345, nothing else":

```
no-agent-forwarding,no-X11-forwarding,no-pty,permitlisten="localhost:12345" ssh-ed25519 AAAA...your-public-key... mac-bluebubbles-relay-tunnel
```

- `permitlisten="localhost:12345"` limits `-R` remote-forwarding requests to
  binding **exactly** `localhost:12345` — matching the listen-host string
  ssh(1) actually sends when the tunnel is opened as `-R 12345:localhost:1234`
  (no explicit bind address). **Gotcha, confirmed against a live OpenSSH
  9.6p1 server:** ssh sends the literal string `"localhost"` as the listen
  host when none is given on the command line — this is NOT the same string
  as `"127.0.0.1"`, and `permitlisten` does an exact match. Requesting
  `-R 127.0.0.1:12345:...` (explicit bind host) or restricting with a bare
  `permitlisten="12345"` (no host) both fail to match and get refused.
- `no-agent-forwarding,no-X11-forwarding,no-pty` individually turn off
  everything else this key could otherwise do — no shell, no agent hijack,
  no X11. **Do NOT use the `restrict` shorthand here** — it also sets
  `no-port-forwarding`, and on a live OpenSSH 9.6p1 test that blocked the
  `-R` bind outright ("Server has disabled port forwarding") even with
  `permitlisten` present; `permitlisten` narrows an *allowed* forward's
  scope, it does not override a blanket `no-port-forwarding`. Listing the
  restrictions individually (as above) is the pattern that actually works.

```bash
sudo chmod 600 /home/relay/.ssh/authorized_keys
sudo chown relay:relay /home/relay/.ssh/authorized_keys
```

**GatewayPorts must stay off** (it's Ubuntu's sshd default — don't touch
it) — this is what makes the `-R 12345:...` bind loopback-only on the VPS
instead of public. Confirm with `sshd -T | grep -i gatewayports` → should
print `gatewayports no`. If it ever prints `yes` on this box, something
edited `/etc/ssh/sshd_config` — fix it before going further, since a `yes`
here would expose port 12345 to the entire internet, defeating the tunnel
+ firewall design.

### 4. BlueBubbles Server on the Mac

1. Install [BlueBubbles Server](https://bluebubbles.app/) on the Mac that
   stays signed into the iMessage account you want Salescale to send/receive
   from.
2. In BlueBubbles Server's settings, enable the **Private API** (required
   for sending — the public API is read-only-ish and can't send messages
   the way `_bluebubbles_send` needs).
3. Set a strong **server password** — this is the value that goes into
   Salescale's "Server password" field (step 8) and is what BlueBubbles
   checks on the `?password=...` query param of every REST call.
4. Confirm the REST API is listening on `localhost:1234` (BlueBubbles'
   default — only change this, and the `-R` port arg in step 6, together if
   you have a reason to use a different port).
5. Note the Mac's iMessage handle (the phone number or Apple ID email this
   account sends/receives as) — this is Salescale's "iMessage number" field.

### 5. Caddy on the relay VPS

```bash
# Official Caddy apt repo (Debian/Ubuntu):
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

# Deploy this directory's Caddyfile (edit the CHANGE ME domain first):
sudo mkdir -p /var/log/caddy
sudo cp Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy
journalctl -u caddy -f   # watch it obtain the cert on first reload
```

Caddy will fail to obtain a cert until (a) the DNS A record from step 2 has
propagated and (b) port 80/443 are reachable from the internet on this box
(they should be, by default, on a fresh VPS with no firewall yet — step 7
tightens this down to 443-from-Salescale-only, but leave step 7 for
**after** the cert issues, since HTTP-01 needs port 80 open to everyone
briefly during issuance).

### 6. autossh on the Mac + the launchd tunnel

```bash
brew install autossh
which autossh   # confirm the path — Apple Silicon vs Intel differ; the
                # plist's ProgramArguments must match exactly
```

Edit `com.salescale.bluebubbles-tunnel.plist` (every `CHANGE ME` marker:
autossh path, your macOS username in both log paths and the key path, and
`relay@CHANGE_ME_VPS_HOST` → the real relay VPS hostname/IP), then:

```bash
mkdir -p ~/Library/Logs
cp com.salescale.bluebubbles-tunnel.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.salescale.bluebubbles-tunnel.plist
launchctl print gui/$(id -u)/com.salescale.bluebubbles-tunnel   # should show "running"
tail -f ~/Library/Logs/bluebubbles-tunnel.log ~/Library/Logs/bluebubbles-tunnel.err.log
```

First connection will prompt an SSH host-key trust decision — `-o
StrictHostKeyChecking=accept-new` in the plist accepts-and-pins it
automatically (safe for a first connection to a host you just provisioned
yourself in step 1; it still fails closed and alerts if the host key ever
*changes* later, which is what you want).

Use `launchctl bootout gui/$(id -u)/com.salescale.bluebubbles-tunnel` to
stop it, `bootstrap` again to restart after editing the plist.

**Do NOT use `autossh-bluebubbles.service` on this Mac** — it's a systemd
unit and macOS has no systemd. It's kept in this directory purely for
reference / an unusual Linux-jump-host topology; see the header comment in
that file.

### 7. Firewall the relay's public port

First, find Salescale backend's egress IP (the IP the relay will see
`X-Forwarded-For`-less, direct TCP connections from) — this is the VPS
running `deploy/docker-compose.traefik.yml` (`2.25.75.95` per the existing
runbooks, unless that's changed). Confirm it live rather than assuming:

```bash
# From the Salescale backend VPS/container itself:
curl -s https://api.ipify.org
```

Edit `SALESCALE_BACKEND_IP` in `firewall.sh` (or export it as an env var),
then on the **relay VPS**:

```bash
export SALESCALE_BACKEND_IP="<the IP from above>"
sudo -E ./firewall.sh
```

This allows 443 from that IP only, throttles SSH (22) against brute force
without IP-restricting it (the Mac's home IP is typically dynamic), and
explicitly denies the tunnel port (12345) to anything but loopback as
defense in depth. See the "Same-VPS variant" note below if the relay and
the backend end up sharing a box after all.

### 8. Verify end-to-end before touching Salescale

From the Salescale backend VPS (or anywhere with that egress IP — a curl
from your laptop will correctly get refused by the firewall, which is the
point):

```bash
curl -s "https://imessage-relay.atlasreach.io/api/v1/ping?password=<the BlueBubbles server password>"
```

A `200` with BlueBubbles' ping JSON confirms the whole chain: DNS → Caddy
TLS → loopback proxy → SSH tunnel → Mac → BlueBubbles REST API → back.

### 9. Connect the account in Salescale

In the app: **SMS → Accounts → Connect account**, select provider
**BlueBubbles**, and fill in:

| Field | Value |
|---|---|
| Relay URL | `https://imessage-relay.atlasreach.io` (the Caddy subdomain from step 2) |
| Server password | the BlueBubbles server password from step 4 |
| iMessage number | the Mac's iMessage handle from step 4 |

These map directly to `SmsAccount.relay_url` / `auth_token_encrypted`
(Fernet-encrypted server password) / `from_number` in
`backend/app/models/sms_outreach.py`; the "Test" action on that account
calls `services/sms_send._verify_bluebubbles`, which does exactly the
`GET /api/v1/ping` call from step 8 through the same path.

---

## Inbound webhooks — what this relay does and doesn't do

**Inbound (BlueBubbles → Salescale) is not part of this relay.** BlueBubbles
Server, running on the Mac, has a normal outbound internet path already —
it doesn't need a tunnel or relay to reach Salescale's public API, only
Salescale needed help reaching *it*. So the natural, simplest wiring is:
point BlueBubbles' own webhook config directly at Salescale's public API
URL, with no relay involved on that leg at all.

**The BlueBubbles inbound route (now implemented):** point BlueBubbles'
webhook config at

```
https://<salescale-api-host>/api/webhooks/imessage/bluebubbles/<account_id>
```

(`api/imessage_webhooks.py`). It accepts BlueBubbles' native JSON payload
(`type` = `new-message` / `updated-message`), records inbound replies as
CRM messages — creating a house-CRM lead when the sender's number is new —
and applies delivery/read receipts. Authenticity is the account's own
`SmsAccount.webhook_token` (shown in the Salescale UI on the account card),
supplied as either the `X-Salescale-Webhook-Secret` **header** or a
`?secret=<webhook_token>` **query param**. Because BlueBubbles' own webhook
config can only set a static URL (it can't attach a custom header itself),
a direct Mac→Salescale wiring uses the `?secret=` query form:

```
https://<salescale-api-host>/api/webhooks/imessage/bluebubbles/<account_id>?secret=<webhook_token>
```

STOP keywords in an inbound iMessage suppress the number and exit its
campaigns, exactly like the SMS path — so opt-outs are honored even on the
dev channel.

**The optional header-injection exception this Caddyfile supports:** if you'd
rather keep the shared secret out of BlueBubbles' settings and let this VPS
inject it instead — routing the inbound webhook *through* the relay so
BlueBubbles points at one stable relay hostname — uncomment the commented
block at the bottom of `Caddyfile`. It forwards
`https://imessage-relay.<domain>/salescale-webhook/*` to Salescale's public
API and injects `X-Salescale-Webhook-Secret` as a header on the way out
(fill in the account's `webhook_token` and Salescale's real API host first).
This is disabled by default because the direct `?secret=` wiring above is
simpler and needs no relay on the inbound leg.

---

## Reusing the existing VPS instead of a dedicated one

If you decide against a second VPS, Caddy and Traefik can coexist on
`2.25.75.95`, but **Traefik already owns host ports 80/443**
(`docker-compose.traefik.yml`, host network) — Caddy cannot also bind them
directly. Two supported ways to resolve this, neither of which this
workstream performs for you (both require editing the existing
`docker-compose.traefik.yml`, which is intentionally left untouched here):

1. **Let Traefik terminate TLS, Caddy stays out of the picture entirely.**
   Add a router to `docker-compose.traefik.yml` exactly like the existing
   `mail-cert-helper` pattern (see that file + `MAILSERVER.md` "TLS
   certificate" for the precedent), pointing
   `Host(\`imessage-relay.atlasreach.io\`)` at a tiny container/service that
   itself just does `reverse_proxy 127.0.0.1:12345` — at that point you
   don't need Caddy's own ACME at all, since Traefik already holds a cert
   for the hostname. This is the path most consistent with how this repo
   already handles "one more hostname on a box Traefik already owns."
2. **True SNI passthrough**, if you want Caddy to keep doing its own ACME
   independently of Traefik: add a Traefik **TCP router** with
   `tls.passthrough=true` for the relay hostname, forwarding raw TLS bytes
   to Caddy listening on an internal port; Caddy then completes its own
   ACME using the `tls-alpn-01` challenge (works even with port 80 owned by
   Traefik, since ALPN validation happens inside the passed-through TLS
   handshake, not over plaintext port 80). More correct to "Caddy does its
   own auto-TLS" but more moving parts — see Traefik's TCP router docs
   before attempting this.

Either way, also read the **firewall note** below — on a shared box, "443
from Salescale's backend IP only" may be **inert** rather than wrong,
because the backend-to-relay hop might never cross the public NIC at all.

### Same-VPS variant — firewall note

If the relay ends up on the same box as the backend, the backend's calls to
`https://imessage-relay...` either hairpin over loopback (same host-network
process) or hop over a Docker bridge (if the backend is containerized and
reaches the relay via a shared network, the same pattern
`docker-compose.traefik.yml` already uses to reach the mailserver
container — see its `networks: [default, mailserver]` comment for the
precedent). `ufw`'s `INPUT`-chain rules in `firewall.sh` govern traffic
arriving from the **public** network interface; loopback and
Docker-bridge traffic don't transit that chain the same way, so the
IP-allowlist rule doesn't add protection in that topology — the real
tenant boundary there is which Docker network the relay/backend containers
are attached to (mirroring the `mailserver` network-attachment approach),
not the ufw rule. `firewall.sh` still does no harm to run in this
configuration (nothing else should be hitting 443 on that hostname
regardless), but don't rely on it as the sole protection if you go this
route — get the Docker network attachment right instead.

---

## Security posture (summary)

- **Reverse tunnel, not an inbound port on the Mac.** The Mac dials out;
  nothing needs to be opened on its home router/NAT, and BlueBubbles' REST
  API is never bound to anything but `localhost` on the Mac itself.
- **The SSH key can do exactly one thing.** `no-agent-forwarding,no-X11-forwarding,no-pty,permitlisten=` locks
  the relay user's key to binding one loopback port over `-R` — no shell,
  no other forwards, no pty — enforced by sshd itself, not by anything in
  this app.
- **The relay's public port is allowlisted to Salescale's backend IP
  only** (`firewall.sh`) — it's a public HTTPS endpoint, but only one caller
  in the world is allowed to open a TCP connection to it.
- **Everything on the wire is HTTPS.** Caddy's automatic Let's Encrypt cert
  covers the Salescale-backend → relay hop; the relay → Mac hop rides
  inside the SSH tunnel's own encryption. BlueBubbles' `?password=` scheme
  is that app's own auth layer on top, carried over TLS end-to-end — it is
  not a substitute for the transport security above, and both are relied on
  together.
- **Loopback-only landing point.** `GatewayPorts no` (default, verified in
  step 3) plus the tunnel binding `12345` on `127.0.0.1` means the port
  Caddy proxies to is unreachable from anywhere except the relay VPS
  itself, independent of the ufw rule.
- **Inbound webhooks stay out of this path entirely** by default (see
  above) — this relay's blast radius is limited to the one send/verify
  call shape `services/sms_send.py` already makes.
