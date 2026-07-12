# Self-hosted mail server for cold outreach (Salescale)

Runbook for standing up a **self-hosted IMAP + SMTP mail server** on the
existing Salescale VPS (`2.25.75.95`, Hostinger KVM, Ubuntu, 7.8GB RAM) so the
new **cold-email outreach module** can send from and receive replies on an
**agency's own domain**. The Salescale backend container talks to it over IMAP
(read inbox) and SMTP submission (send).

Companion to `DEPLOYMENT.md` (the app-stack runbook) — read that first for the
box's layout: a **pre-existing Traefik** (host network) owns 80/443 and serves
several apps; `ufw` allows only 22/80/443; `fail2ban` guards SSH; the Salescale
app is the `deploy` compose project. **Nothing here touches that stack.** The
mail server is its own compose project (`mailserver`) and only binds mail ports
nothing else uses, plus a read-only mount of Traefik's cert store.

Compose file: `deploy/docker-compose.mailserver.yml`. Image:
[`docker-mailserver`](https://docker-mailserver.github.io/docker-mailserver/latest/)
(Postfix + Dovecot + Rspamd + OpenDKIM/OpenDMARC).

**Careful:** this box also runs Carter's other apps behind the same Traefik.
Every command below is scoped to the `mailserver` project or the
`docker-compose.mailserver.yml` file. Never run a bare `docker compose down`,
`docker system prune`, or `docker stop $(docker ps -q)` — you will take down
production and the neighbours.

---

## 0. Why self-hosting cold email is hard mode (read before you invest a day)

> **Honesty box.** Self-hosting outbound cold email on a commodity VPS is the
> hardest possible deliverability setup. Shared VPS IP ranges (Hostinger,
> DigitalOcean, OVH, Hetzner) frequently sit on blocklists because *someone
> else* on the range spammed — you inherit that reputation on day one and can't
> fully control it. Gmail/Microsoft weight IP + domain reputation heavily for
> unsolicited mail, and a fresh IP with no history starts at zero.
>
> **The pragmatic split most teams land on:**
> - Use this self-hosted box for **RECEIVING** (inbound MX for replies) and
>   **low-volume, high-intent** sends where you control the list quality.
> - If inboxing suffers at volume, **relay OUTBOUND through a transactional
>   provider** (Amazon SES, Postmark, Mailgun, SendGrid) that maintains warm,
>   monitored IP pools — while still signing as the agency's domain (SPF/DKIM
>   aligned). Salescale's email-account connect form takes **any** SMTP host,
>   so this is a **config change, not a code change**: point SMTP at the
>   provider, keep IMAP pointed at this box for replies. See
>   "Escape hatch" at the end.
>
> Decide honestly: if the agency needs 500+/day inboxing from cold lists on
> week one, start with a provider and treat this box as the reply inbox. If
> volume is modest and ramping, self-hosting is workable — this runbook gets
> you a clean 9-10/10 mail-tester score, which is the price of entry.

---

## 1. Domain strategy (decide this FIRST)

Per the deliverability guide, **cold email must never go out on a domain whose
reputation you can't afford to burn.** In Salescale's model every **Organization
sends from its OWN domain** — never anything Salescale-branded. The worked
example throughout is tenant #1, **Atlas Reach**, whose primary domain is
`atlasreach.io`.

The real decision is: does Atlas Reach send cold mail on `atlasreach.io`
directly, or on a **separate/subdomain** to insulate the primary domain's
reputation (the domain their existing clients and Google Workspace email
depend on)?

| Option | Example | Pro | Con |
|--------|---------|-----|-----|
| **Primary domain** | `carter@atlasreach.io` | Maximum trust/recognition; replies come from the "real" address; no new domain to warm from scratch | A blocklisting or spam-complaint spike damages the domain the agency runs its **whole business** on (client email, Google Workspace). High blast radius. |
| **Subdomain** (recommended) | `carter@mail.atlasreach.io` or `@go.atlasreach.io` | Inherits *some* of the parent's trust; isolates cold-send reputation so a problem doesn't sink primary-domain mail; still visibly "Atlas Reach" | Subdomain reputation is somewhat linked to the parent (not a hermetic firewall); needs its own warmup. |
| **Cousin domain** | `atlasreach-hq.com`, `getatlasreach.com`, `atlasreachmail.com` | Fully isolates reputation; if it burns, the primary is untouched — buy another and move on | A brand-new domain with zero history; longest warmup; must stand up a real website (even a redirect to `atlasreach.io`) or it looks like phishing. |

**Recommendation for this setup: a dedicated subdomain the mail server is
authoritative for — `mail.atlasreach.io`** — which is also the server's
hostname (`mail.atlasreach.io`), reverse-DNS name, and TLS cert name. From:
addresses can still be `@atlasreach.io` OR `@mail.atlasreach.io` depending on
how much isolation the agency wants; SPF/DKIM/DMARC must align to whichever
**From: domain** is chosen (see §3). If the agency wants maximum insulation,
register a cousin domain and substitute it everywhere `atlasreach.io` appears
below.

**Rules for the sending domain (from the deliverability guide):**
- Stand up a **real website** on it (even a 301 redirect to the main site) —
  bare domains look suspicious to filters.
- The domain must **visually match the company** — unrelated domains read as
  phishing.
- Keep the From: name **consistent** (`Carter at Atlas Reach`), never rotating.

### Multi-tenant note (Salescale serves many agencies)

docker-mailserver is multi-domain. This one box can host mailboxes for several
organizations' domains at once — each agency brings its **own** domain and its
**own** DNS records. To add another org's sending domain later:

```bash
docker exec -it mailserver setup email add jane@otheragency.com
docker exec -it mailserver setup config dkim domain otheragency.com
```

then publish **that domain's** SPF/DKIM/DMARC on **that agency's** DNS (§3),
and the org connects it in Salescale with its own mailbox credentials. Each
domain's From:-alignment is independent. The rest of this runbook walks
`atlasreach.io`; repeat per domain.

---

## 2. DNS records

All records below live on **whatever DNS host is authoritative for
`atlasreach.io`** — that is the *agency's* registrar/DNS provider, which **may
not be Porkbun** (Porkbun hosts `salescale.lol`, not necessarily the agency
domains). The record *values* are the same wherever they live; only the control
panel differs. Where a step is Porkbun/Hostinger-specific it's called out.

Assume the VPS public IP is `2.25.75.95`.

### 2.1 A record — the mail host

```
Type  Host / Name          Value        TTL
A     mail                 2.25.75.95   600
```

i.e. `mail.atlasreach.io → 2.25.75.95`. This is the server's hostname; the PTR
(§2.6) and TLS cert (§4) must match it exactly.

### 2.2 MX record — where replies are delivered

```
Type  Host / Name          Priority  Value                 TTL
MX    @   (atlasreach.io)  10        mail.atlasreach.io    3600
```

If you chose the subdomain-as-From strategy (From: `@mail.atlasreach.io`), add
the MX on `mail` instead of `@`. Keep it simple: one MX, priority 10, pointing
at the A record above (never at an IP — MX must point at a hostname).

**Careful:** if `atlasreach.io` already receives mail on Google Workspace /
Microsoft 365, it already has MX records. Pointing `@`'s MX at this box
**redirects ALL of the domain's inbound mail here** and breaks their existing
email. That is exactly why the **subdomain** strategy is safer — put the MX on
`mail.atlasreach.io` so only the cold-outreach subdomain's mail comes here and
the primary domain's Workspace mail is untouched.

### 2.3 SPF — authorize this server to send

One TXT record on the sending domain. **Only one SPF record per domain — a
second one silently breaks authentication.**

```
Type  Host / Name          Value                          TTL
TXT   @   (atlasreach.io)  v=spf1 mx -all                 3600
```

`v=spf1 mx -all` means "the hosts in my MX (i.e. `mail.atlasreach.io`) may send
for me; reject everything else." `-all` (hard fail) is correct for a dedicated
sending setup you fully control. If `atlasreach.io` *also* sends through Google
Workspace, you must include both and NOT publish two records — merge:
`v=spf1 include:_spf.google.com mx -all`. (Again, the subdomain strategy avoids
this: `mail.atlasreach.io` gets its own clean `v=spf1 mx -all` and the primary
SPF is left alone.)

### 2.4 DKIM — cryptographic signature

The key is **generated by the mail server** in §5, not hand-written. After
`setup config dkim` you print the public key and paste it here. It looks like:

```
Type  Host / Name                       Value                              TTL
TXT   mail._domainkey  (atlasreach.io)  v=DKIM1; h=sha256; k=rsa; p=MIIBI...  3600
```

`mail` is the **selector** docker-mailserver uses by default. The `p=` blob is
long; some panels require it as a single unbroken string, others auto-split —
paste exactly what §5 prints. Verify after propagation with
`dig +short TXT mail._domainkey.atlasreach.io`.

### 2.5 DMARC — tie it together, start in monitor mode

```
Type  Host / Name                Value                                                       TTL
TXT   _dmarc  (atlasreach.io)    v=DMARC1; p=none; rua=mailto:dmarc@atlasreach.io; fo=1     3600
```

**Start at `p=none`** (monitor, don't block) and collect the aggregate reports
that land at `dmarc@atlasreach.io` for 1-2 weeks. Once the reports confirm SPF
**and** DKIM pass and align on your real sends, tighten to
`p=quarantine` and later `p=reject`. Don't start at reject — a
misconfiguration would silently bin your own mail.

### 2.6 PTR / reverse DNS — set on Hostinger, not in the domain's DNS

This is the one record that is **NOT** in the domain's DNS zone — it's set by
whoever owns the **IP**, i.e. **Hostinger**. The PTR for `2.25.75.95` must
resolve back to `mail.atlasreach.io`. Mismatched or generic PTR (e.g.
`srv-2-25-75-95.hostinger.host`) is an instant spam signal and many receivers
reject on it outright.

- **Where:** Hostinger hPanel → VPS → your server → **Network / rDNS** (naming
  varies), set the PTR for `2.25.75.95` to `mail.atlasreach.io`. If hPanel has
  no rDNS field for KVM, **open a support ticket** asking them to set reverse
  DNS for `2.25.75.95` → `mail.atlasreach.io`.
- Verify: `dig +short -x 2.25.75.95` must return `mail.atlasreach.io.`

**Port 25 unblock (Hostinger):** many Hostinger VPS plans **block outbound
port 25 by default** to fight spam. Without it you can receive but **cannot
send to other servers**. Request an unblock via a **support ticket** ("please
unblock outbound SMTP / port 25 on VPS `2.25.75.95` for a legitimate,
authenticated mail server with SPF/DKIM/DMARC/rDNS configured"). They usually
ask you to confirm the anti-abuse setup — which this runbook gives you. Until
25 is open outbound, use the provider-relay escape hatch for sending.

### 2.7 Optional niceties

- **Autodiscover/autoconfig** and **MTA-STS** improve client setup and
  transport security but aren't required for the Salescale integration. Skip
  for v1.
- A **`mail` A record on the cousin domain** if you went that route — same
  shape, different zone.

---

## 3. From-address alignment (why all three records matter)

Deliverability turns on **alignment**: the domain in the visible `From:` header
must match the domain that SPF authorizes and the domain DKIM signs with.

- Salescale sends as `carter@atlasreach.io` (or `@mail.atlasreach.io`).
- SPF passes because the mail leaves `mail.atlasreach.io`, which is in
  `atlasreach.io`'s MX / SPF (§2.3).
- DKIM passes because this server signs with the `atlasreach.io` key (§2.4,
  §5).
- DMARC passes because both align to the From: domain (§2.5).

Get the From: domain, the SPF domain, and the DKIM `d=` domain to be the **same
registrable domain** and you pass DMARC. This is why the mailbox you create in
§6 and the DKIM key you generate in §5 must be for the **same domain** the
outreach module puts in From:.

---

## 4. TLS certificate for `mail.atlasreach.io`

The mail server needs a valid cert whose name matches its hostname, or
Salescale's IMAP/SMTP TLS connections (and other mail servers' STARTTLS) will
fail verification. The compose file is wired to read **Traefik's existing
Let's Encrypt store** (`/docker/traefik/acme.json`, mounted read-only) via
`SSL_TYPE=letsencrypt` + `SSL_DOMAIN=mail.atlasreach.io`.

**The catch:** Traefik's `acme.json` only contains certs for hostnames Traefik
actually **routes**. Today that's `app.salescale.lol` / `api.salescale.lol` —
**not** `mail.atlasreach.io`. So you must make Traefik obtain that cert first.

### Option A (recommended) — dummy Traefik router triggers HTTP-01 issuance

Traefik already owns 80/443 and can solve the HTTP-01 challenge. Give it a
trivial reason to hold a cert for `mail.atlasreach.io`:

1. Ensure the **A record** `mail.atlasreach.io → 2.25.75.95` (§2.1) has
   propagated (`dig +short mail.atlasreach.io`).
2. Add a tiny labels-only service to the **app stack's** Traefik file
   (`docker-compose.traefik.yml`) — a `whoami` or `traefik/whoami` container
   with a router on `Host(\`mail.atlasreach.io\`)` and
   `tls.certresolver=letsencrypt`. It exists only so Traefik requests and
   renews the cert; it serves nothing sensitive.

   ```yaml
   # (illustrative — add to the deploy/app Traefik stack, NOT the mailserver file)
   mail-cert-helper:
     image: traefik/whoami
     restart: unless-stopped
     labels:
       - traefik.enable=true
       - traefik.http.routers.mailcert.rule=Host(`mail.atlasreach.io`)
       - traefik.http.routers.mailcert.entrypoints=websecure
       - traefik.http.routers.mailcert.tls.certresolver=letsencrypt
       - traefik.http.services.mailcert.loadbalancer.server.port=80
   ```
3. `docker compose -f docker-compose.traefik.yml up -d` (app project). Watch
   `docker logs traefik-traefik-1 --since 5m` until the `mail.atlasreach.io`
   cert issues; confirm it's now in the store:
   `sudo grep -o 'mail.atlasreach.io' /docker/traefik/acme.json`.
4. Bring up the mail server (§5). docker-mailserver parses `acme.json`, finds
   the `mail.atlasreach.io` cert, and installs it. On renewals Traefik rewrites
   `acme.json` and DMS's change-detector reloads within a few minutes.

> **Careful:** editing `docker-compose.traefik.yml` touches the app stack — the
> one file this task otherwise leaves alone. Make that one small addition
> deliberately (or hand it to whoever owns the app deploy). It does **not**
> change the backend/frontend services, only adds a sibling helper.

### Option B (alternative) — standalone certbot with DNS-01, manual cert

If you'd rather not touch the app stack, issue the cert out-of-band and hand
DMS the files directly:

1. Since Traefik owns 80/443, you **cannot** use certbot's standalone HTTP-01.
   Use **DNS-01** against the agency's DNS provider (certbot has plugins for
   Cloudflare, Route53, etc.; for a registrar without a plugin, use
   `certbot certonly --manual --preferred-challenges dns` and add the TXT
   record it prints).
   ```bash
   sudo certbot certonly --manual --preferred-challenges dns \
     -d mail.atlasreach.io
   ```
2. Point the compose at the resulting files instead of the Traefik store —
   change the `mailserver` environment to:
   ```yaml
   SSL_TYPE: manual
   SSL_CERT_PATH: /etc/letsencrypt/live/mail.atlasreach.io/fullchain.pem
   SSL_KEY_PATH:  /etc/letsencrypt/live/mail.atlasreach.io/privkey.pem
   ```
   and mount `/etc/letsencrypt:/etc/letsencrypt:ro` in place of the single
   `acme.json` line.
3. **You now own renewals** — add a `certbot renew` cron/systemd-timer and
   `docker restart mailserver` (or `setup helper` reload) after renewal.
   Option A is preferred precisely because Traefik handles this automatically.

---

## 5. Initial bring-up + DKIM

Create the state directories, start the container, add the DKIM key.

```bash
# On the VPS. Create the /docker/mailserver tree (matches the box convention).
sudo mkdir -p /docker/mailserver/{mail-data,mail-state,mail-logs,config}

cd ~/salescale/deploy

# Start ONLY the mail server (its own project — never touches "deploy").
docker compose -f docker-compose.mailserver.yml up -d
docker compose -f docker-compose.mailserver.yml logs -f mailserver   # watch it boot
```

Generate the DKIM keypair for the sending domain (do this AFTER at least one
mailbox exists, or pass the domain explicitly):

```bash
# Generates a 2048-bit key with selector "mail" for atlasreach.io.
docker exec -it mailserver setup config dkim domain atlasreach.io

# The public key to paste into DNS (§2.4) is printed here:
sudo cat /docker/mailserver/config/opendkim/keys/atlasreach.io/mail.txt
```

That file contains the exact `mail._domainkey ... v=DKIM1; ... p=...` TXT
record. Paste the `p=` contents into the DNS record from §2.4. **The private
half sits in `/docker/mailserver/config/opendkim/keys/` — losing it means
re-keying DKIM and re-publishing DNS, so it's the #1 thing to back up (§9).**

Restart to load signing:

```bash
docker compose -f docker-compose.mailserver.yml restart mailserver
```

---

## 6. Create the outreach mailbox(es)

```bash
# Primary Atlas Reach outreach mailbox. You'll be prompted for a password
# (or append it as a 2nd arg). Use a long random password — store it in the
# agency's password manager; Salescale will hold it too (encrypted).
docker exec -it mailserver setup email add carter@atlasreach.io

# Recommended supporting addresses so bounces/abuse/DMARC land somewhere real:
docker exec -it mailserver setup email add postmaster@atlasreach.io
docker exec -it mailserver setup email add dmarc@atlasreach.io

# List / verify:
docker exec -it mailserver setup email list
```

Optional: alias `abuse@` and `postmaster@` to the mailbox you actually read:

```bash
docker exec -it mailserver setup alias add abuse@atlasreach.io carter@atlasreach.io
```

For a second org later, repeat with their domain (see §1 multi-tenant note).

---

## 7. ufw — open the mail ports

Only these, in addition to the existing 22/80/443 (`DEPLOYMENT.md`):

```bash
sudo ufw allow 25/tcp     # SMTP: inbound MX (replies) + outbound relay
sudo ufw allow 587/tcp    # Submission (STARTTLS) — Salescale sends here
sudo ufw allow 993/tcp    # IMAPS — Salescale reads the inbox here
sudo ufw allow 465/tcp    # Submission (implicit TLS) — only if you kept 465
sudo ufw status numbered
```

Leave 143/110/995 closed — this setup exposes IMAPS (993) only. Recall the
Docker-vs-ufw gotcha from `DEPLOYMENT.md`: docker-mailserver **does** publish
these ports to the host, so they're internet-reachable by design — that's
correct here (a mail server must be), and the ufw rules above document intent.

**Also check Hostinger's hPanel VPS firewall** (separate from ufw) allows
25/587/993/465 inbound, and that outbound 25 is unblocked (§2.6).

---

## 8. Verification

Work top-down: transport → auth → reputation.

**a) Ports reachable + TLS cert correct:**
```bash
# From your Mac. Should show the cert CN = mail.atlasreach.io, valid chain.
openssl s_client -connect mail.atlasreach.io:993 -servername mail.atlasreach.io </dev/null 2>/dev/null | openssl x509 -noout -subject -dates
openssl s_client -starttls smtp -connect mail.atlasreach.io:587 -servername mail.atlasreach.io </dev/null 2>/dev/null | openssl x509 -noout -subject
```

**b) Authenticated test send** with [`swaks`](https://github.com/jetmore/swaks)
(`brew install swaks`):
```bash
swaks --server mail.atlasreach.io --port 587 -tls \
  --auth LOGIN --auth-user carter@atlasreach.io \
  --to <your-personal-gmail>@gmail.com \
  --from carter@atlasreach.io \
  --header "Subject: Atlas Reach mail test"
```
It should authenticate, hand off, and arrive in Gmail. Open the Gmail message →
"Show original" → confirm **SPF: PASS, DKIM: PASS, DMARC: PASS**.

**c) mail-tester.com** — the headline check. Get an address from
[mail-tester.com](https://www.mail-tester.com), send to it from the mailbox
(swaks or the Salescale UI once connected), then load the score. **Target ≥
9/10.** Below 7 means something above is broken — it itemizes SPF/DKIM/DMARC,
PTR, blocklists, and content.

**d) Reverse DNS:** `dig +short -x 2.25.75.95` → must be `mail.atlasreach.io.`
(§2.6).

**e) Blocklists:** [MXToolbox Blacklist Check](https://mxtoolbox.com/blacklists.aspx)
on both `2.25.75.95` and `atlasreach.io`. Also run MXToolbox's SPF/DKIM/DMARC
lookups. A fresh VPS IP may already be on a list or two — see §9 delisting.

---

## 9. Warmup & volume policy

A brand-new domain/IP has zero sending reputation. Ramp slowly and let positive
engagement build trust. Salescale's **email-verification gate**
(`email_verification.sendable()` — see `CLAUDE.md` Phase 12) already keeps
invalid/risky addresses out of every audience, which is the single biggest
lever on bounce rate; lean on it.

| Week | Emails/day (per domain) | Focus |
|------|--------------------------|-------|
| 1 | 5–10 | Real conversations only — send to colleagues/known contacts, get genuine **replies**. Not cold yet. |
| 2 | 20–30 | Small, highly-targeted cold batches. Verified addresses only. Watch bounces. |
| 3 | 40–60 | Expand slightly. Keep open rate > 30%, bounce < 3%. |
| 4 | 80–100 | Approaching normal volume. Watch spam-complaint rate (< 0.1%). |
| 5+ | up to 150–200 (cap) | Full volume. **Stay under 100–200/day/domain** — beyond that, add another sending domain/mailbox rather than pushing one harder. |

**Hard rules (deliverability guide):**
- **Bounce rate < 5%** (target < 2%). Above 5% is dangerous — pause and reclean
  the list. Salescale's verifier is your first line here.
- **Spread sends across the day** (not a single burst) — burst sending from a
  cold IP looks like spam. Configure the outreach module to drip.
- **Spam complaints < 0.1%.** One-click unsubscribe / honest reply-to-opt-out
  and honoring it immediately keeps this down.
- **Plain-text, one link max, consistent From: name, physical address +
  unsubscribe** in every mail (CAN-SPAM / GDPR — see the deliverability &
  legal guidance). The mail server doesn't enforce this; the outreach copy
  must.
- Consider a **warmup tool** (Lemwarm, Mailreach, Warmup Inbox) for weeks 1–4
  to build reputation faster on the new domain.

**Warmup is failing if:** open rate < 20%, bounce > 3%, or mail lands in
Gmail's Promotions/Spam. Slow down a step when you see these.

---

## 10. Ongoing operations

**Logs:**
```bash
docker compose -f docker-compose.mailserver.yml logs mailserver --tail 200
# On-disk (persisted): /docker/mailserver/mail-logs/  (mail.log, etc.)
sudo tail -f /docker/mailserver/mail-logs/mail.log
```

**fail2ban interplay:** docker-mailserver runs its **own** fail2ban inside the
container (guards Postfix/Dovecot auth-fail — the host's fail2ban can't see
those logs). The host fail2ban keeps guarding SSH. They don't conflict.
Inspect the mail one:
```bash
docker exec -it mailserver setup fail2ban        # status
docker exec -it mailserver setup fail2ban ban <ip>
docker exec -it mailserver setup fail2ban unban <ip>
```

**Rspamd / spam tuning:** kept light so cold-email **replies aren't lost** —
greylisting off, spam **filed to Junk, never rejected or deleted**. If real
replies still get Junked, lower Rspamd's aggressiveness via an override in
`/docker/mailserver/config/rspamd/override.d/` and restart. If you get
spammed, tighten there. Never move to outright SMTP-time rejection on the
inbound path for this use case — a mis-scored prospect reply vanishing is worse
than a Junk-folder false positive.

**Backups — the important part.** Back up **`/docker/mailserver/`** on a
schedule off the box. Priorities:
1. **`config/opendkim/keys/`** — the DKIM **private keys**. Lose these and you
   must re-key + re-publish DNS and eat a reputation hit. Also copy the printed
   public record into the agency's password manager.
2. **`config/`** — accounts, passwords (`postfix-accounts.cf`), aliases,
   overrides.
3. **`mail-data/`** — the actual mailboxes (received replies).
```bash
# Simple nightly tarball to off-box storage (adapt destination):
sudo tar czf /root/mailserver-$(date +%F).tgz -C /docker mailserver
# then scp/rclone it off the VPS. Keep DKIM keys ALSO in the password manager.
```

**When the IP/domain gets listed:** stop or throttle sending immediately,
find the cause (bad list slice, complaint spike, warmup pushed too fast), then
use the specific blocklist's delisting flow — common ones:
[Spamhaus](https://www.spamhaus.org/lookup/) (SBL/CSS/PBL removal),
[Barracuda](https://www.barracudacentral.org/rbl/removal-request),
[Spamcop](https://www.spamcop.net/bl.shtml), UCEProtect. Also register
[Google Postmaster Tools](https://postmaster.google.com) (Gmail reputation) and
[Microsoft SNDS/JMRP](https://sendersupport.olc.protection.outlook.com/snds/)
for Outlook. If a listing sticks, fall back to the escape hatch.

**Updates:** bump the pinned image tag in the compose file deliberately after
reading that release's UPGRADING notes; then
`docker compose -f docker-compose.mailserver.yml up -d`. Never floating-`latest`
a mail server.

---

## 11. Connecting Salescale to this mail server

The Salescale backend runs in the **`deploy` compose project**; this mail
server is the **`mailserver` project** — **different Docker networks**. Two ways
for the app container to reach it; pick one.

### Recommended: reach it by its public hostname

Enter the mail server's **public FQDN** in Salescale's email-account connect
form. The container's DNS resolves `mail.atlasreach.io → 2.25.75.95` and the
connection hairpins back to the box. **This is the clean choice because the TLS
cert is issued for `mail.atlasreach.io`** (§4) — connecting by that exact name
means certificate validation passes with no exceptions.

**Values to enter in the Salescale connect form:**

| Field | IMAP (read inbox) | SMTP (send) |
|-------|-------------------|-------------|
| Host | `mail.atlasreach.io` | `mail.atlasreach.io` |
| Port | `993` | `587` |
| Security | **SSL/TLS** (implicit) | **STARTTLS** |
| Username | `carter@atlasreach.io` | `carter@atlasreach.io` |
| Password | the mailbox password from §6 | same |

(If you standardized on implicit-TLS submission, use SMTP port `465` +
SSL/TLS instead of 587/STARTTLS.)

**Hairpin-NAT caveat:** a few VPS networks don't route a container's connection
to the box's own public IP back to itself. If Salescale can't reach
`mail.atlasreach.io` but external clients can, add a host-mapping to the **app**
stack so the name resolves to the box's internal/Docker-gateway address while
**keeping the same hostname** (so the cert still matches):
```yaml
# in docker-compose.traefik.yml, under the backend service:
extra_hosts:
  - "mail.atlasreach.io:172.17.0.1"   # docker0 gateway, or the VPS LAN IP
```
The cert name still validates because Salescale still connects **by name**.

### Alternative: shared Docker network

Attach both projects to an external Docker network and use the container name:
```bash
docker network create mailnet
# add `networks: [mailnet]` to the mailserver service + a top-level
# `networks: { mailnet: { external: true } }` in BOTH compose files, and the
# backend service in the deploy stack.
```
Then host = `mailserver` (the container name). **Downside:** the TLS cert is
for `mail.atlasreach.io`, not `mailserver`, so connecting by container name
**fails cert validation** unless Salescale is told to skip verification — which
you should not do. Net: the **public-hostname** approach is cleaner. Use the
shared network only if hairpin NAT is impossible and you can set a TLS SNI /
verify-name of `mail.atlasreach.io` while dialing the container.

---

## 12. Escape hatch — relay outbound through a provider later

If self-hosted outbound inboxing disappoints at volume (see §0), keep this box
as the **reply inbox** and switch **sending** to a transactional provider —
**no code change**, because Salescale's connect form takes any SMTP host:

1. Sign up for Amazon SES / Postmark / Mailgun; verify `atlasreach.io`
   (add **their** SPF include + **their** DKIM CNAME/TXT to the agency DNS,
   alongside or instead of §2.3/§2.4 — keep From: aligned to `atlasreach.io`).
2. In Salescale's connect form, change **only the SMTP** side to the provider's
   host/port/credentials (e.g. `email-smtp.us-east-1.amazonaws.com:587`
   STARTTLS). Leave **IMAP** pointed at `mail.atlasreach.io:993` so replies
   still land in this self-hosted inbox.
3. Warm the provider path per §9 anyway — a provider's shared pool is warmer
   than a fresh VPS IP, but your *domain* still needs reputation.

This hybrid — **provider for send, self-host for receive** — is the most robust
option and the one to reach for the moment blocklists or Postmaster data show
the VPS IP dragging deliverability down.

---

## Quick reference

```bash
# Bring up / status / logs  (always -f the mailserver file; never bare compose)
cd ~/salescale/deploy
docker compose -f docker-compose.mailserver.yml up -d
docker compose -f docker-compose.mailserver.yml ps
docker compose -f docker-compose.mailserver.yml logs mailserver --tail 100

# Management CLI
docker exec -it mailserver setup email add user@atlasreach.io
docker exec -it mailserver setup email list
docker exec -it mailserver setup config dkim domain atlasreach.io
docker exec -it mailserver setup fail2ban

# DKIM public record to paste into DNS
sudo cat /docker/mailserver/config/opendkim/keys/atlasreach.io/mail.txt
```

Ports: **25** (MX in / relay out) · **587** (submission, Salescale sends) ·
**993** (IMAPS, Salescale reads) · **465** (submission implicit-TLS, optional).
Salescale connect: IMAP `mail.atlasreach.io:993` SSL · SMTP
`mail.atlasreach.io:587` STARTTLS · user `carter@atlasreach.io`.
