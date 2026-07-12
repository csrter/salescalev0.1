# Self-hosted mail server for cold outreach (Salescale)

Runbook for standing up a **self-hosted IMAP mail server** on the existing
Salescale VPS (`2.25.75.95`, Hostinger KVM, Ubuntu, 7.8GB RAM) so the new
**cold-email outreach module** can receive replies on an **agency's own
domain**. The chosen architecture (§0) is a hybrid: **Elastic Email sends**,
this box **only receives** — the Salescale backend container talks to it over
IMAP (read inbox) only; SMTP submission goes to Elastic Email, not here.
docker-mailserver still runs Postfix + Dovecot under the hood (Postfix
handles inbound MX delivery into the mailboxes Dovecot serves over IMAP), so
the compose file and image are unchanged from an all-in-one setup — only the
outbound-sending pieces of the configuration are skipped.

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

## 0. What this box does now (read before you touch anything)

> **Honesty box.** After weighing self-hosted outbound deliverability against
> a managed provider, the agency has **decided** on a hybrid architecture:
> **Elastic Email (a transactional/bulk ESP) handles SENDING**, and **this
> self-hosted box handles RECEIVING ONLY** — it exists purely as the IMAP
> inbox replies land in. This is the chosen, primary setup this runbook
> documents — not a fallback to consider later.
>
> Why: self-hosting outbound cold email on a commodity VPS is the hardest
> possible deliverability setup. Shared VPS IP ranges (Hostinger, DigitalOcean,
> OVH, Hetzner) frequently sit on blocklists because *someone else* on the
> range spammed — you inherit that reputation on day one and can't fully
> control it. Gmail/Microsoft weight IP + domain reputation heavily for
> unsolicited mail, and a fresh IP with no history starts at zero. Elastic
> Email maintains warm, monitored sending infrastructure so the agency isn't
> fighting IP reputation from scratch on outbound, while this box still keeps
> the reply inbox self-hosted (full control, no vendor lock-in on receiving —
> replies land exactly where Salescale's IMAP sync already expects them).
>
> **What this means concretely, and why several sections below got shorter:**
> - Salescale's email-account connect form points **SMTP at Elastic Email**
>   and **IMAP at this box** (`mail.atlasreach.io:993`) — see §11.
> - This box's Postfix **never sends outbound mail as the agency's domain**.
>   Everything that used to exist here to make THIS SERVER a trusted
>   *sender* — its own SPF inclusion, its own DKIM keys, outbound port-25
>   unblock, IP-level warmup — is **no longer required**, and the affected
>   sections (§2.3, §2.4, §2.6, §5, §7, §9) have been trimmed accordingly.
> - If the agency ever wants to drop Elastic Email and send directly from
>   this box instead, that reverses back to the old all-in-one design — see
>   §12, which now documents that as the secondary/alternative path.

---

## 1. Domain strategy (decide this FIRST)

Per the deliverability guide, **cold email must never go out on a domain whose
reputation you can't afford to burn.** In Salescale's model every **Organization
sends from its OWN domain** — never anything Salescale-branded. The worked
example throughout is tenant #1, **Atlas Reach**, whose primary domain is
`atlasreach.io`.

**With the Elastic-Email-sends / self-host-receives split, this decision
splits into two separate questions:**

1. **What From: domain does Elastic Email send as?** This is whatever domain
   the agency verifies in Elastic Email's dashboard — `atlasreach.io` or a
   subdomain of it. Elastic Email's own SPF/DKIM records (§2.3, §2.4) get
   published for *that* domain.
2. **Where do replies to that From: address route?** That's this box —
   `mail.atlasreach.io` — regardless of which domain Elastic Email sends as,
   as long as the MX for the From:-domain (or a Reply-To override) points
   here (§2.2). The subdomain recommendation below is now primarily about
   this **receiving hostname/inbox address**, not the SMTP-envelope sending
   identity — the two no longer have to be the same domain, though keeping
   them aligned (e.g. From: `carter@mail.atlasreach.io`, replies also to
   `mail.atlasreach.io`) is simplest and still recommended.

The remaining question below — primary domain vs. subdomain vs. cousin
domain — still matters for picking that From:/receiving domain and its
reputation blast radius:

| Option | Example | Pro | Con |
|--------|---------|-----|-----|
| **Primary domain** | `carter@atlasreach.io` | Maximum trust/recognition; replies come from the "real" address; no new domain to warm from scratch | A blocklisting or spam-complaint spike damages the domain the agency runs its **whole business** on (client email, Google Workspace). High blast radius. |
| **Subdomain** (recommended) | `carter@mail.atlasreach.io` or `@go.atlasreach.io` | Inherits *some* of the parent's trust; isolates cold-send reputation so a problem doesn't sink primary-domain mail; still visibly "Atlas Reach" | Subdomain reputation is somewhat linked to the parent (not a hermetic firewall); needs its own warmup. |
| **Cousin domain** | `atlasreach-hq.com`, `getatlasreach.com`, `atlasreachmail.com` | Fully isolates reputation; if it burns, the primary is untouched — buy another and move on | A brand-new domain with zero history; longest warmup; must stand up a real website (even a redirect to `atlasreach.io`) or it looks like phishing. |

**Recommendation for this setup: a dedicated subdomain this box is
authoritative for RECEIVING — `mail.atlasreach.io`** — which is also the
server's hostname, reverse-DNS name, and TLS cert name. From: addresses sent
via Elastic Email can be `@atlasreach.io` OR `@mail.atlasreach.io` depending
on what's verified in Elastic Email's dashboard and how much isolation the
agency wants; Elastic Email's SPF/DKIM must align to whichever **From: domain**
is chosen there (see §3), and the MX for that same domain (or its Reply-To)
must point at this box so replies actually arrive. If the agency wants maximum
insulation, register a cousin domain and substitute it everywhere
`atlasreach.io` appears below.

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
```

then verify **that domain** in Elastic Email (their SPF include + DKIM go on
**that agency's** DNS, same as §2.3/§2.4) and publish DMARC (§2.5) on that
agency's DNS; the org connects it in Salescale with its own mailbox
credentials for the IMAP leg and its own Elastic Email credentials for the
SMTP leg. Each domain's From:-alignment is independent. The
`setup config dkim domain otheragency.com` step only applies if that org
specifically activates the direct-send escape hatch (§12) instead of using
Elastic Email. The rest of this runbook walks `atlasreach.io`; repeat per
domain.

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

### 2.2 MX record — reply routing (still needed)

This box no longer sends, but it still needs to be where **replies to the
From: address route**. The MX record needed is entirely about **reply
routing**, not "where this server sends from":

```
Type  Host / Name          Priority  Value                 TTL
MX    @   (atlasreach.io)  10        mail.atlasreach.io    3600
```

The MX goes on **whichever domain/subdomain the Elastic Email From: address
actually uses**. If From: is `carter@atlasreach.io`, the MX for `@`
(`atlasreach.io` itself) needs to point here. If From: is
`carter@mail.atlasreach.io`, put the MX on `mail` instead of `@`. Keep it
simple either way: one MX, priority 10, pointing at the A record above (never
at an IP — MX must point at a hostname).

**Careful:** if `atlasreach.io` already receives mail on Google Workspace /
Microsoft 365, it already has MX records. Pointing `@`'s MX at this box
**redirects ALL of the domain's inbound mail here** and breaks their existing
email. That is exactly why the **subdomain** strategy is safer — put the MX on
`mail.atlasreach.io` so only the cold-outreach subdomain's replies come here
and the primary domain's Workspace mail is untouched. This caveat doesn't
change with the Elastic Email split — it's about which domain's inbound mail
you're redirecting, independent of who sends the outbound leg.

### 2.3 SPF — authorize Elastic Email to send (this box is NOT in it)

This box **does not need to be in SPF at all** — it never sends as this
domain anymore, only Postfix's inbound/reply-handling role touches mail here.
SPF instead needs to authorize **Elastic Email** as the sender for whichever
domain the From: address uses.

Elastic Email's own domain-verification page in their dashboard gives you the
exact `include:` mechanism to add — it's commonly documented as something
shaped like:

```
Type  Host / Name          Value                                  TTL
TXT   @   (atlasreach.io)  v=spf1 include:elasticemail.com ~all   3600
```

**Do not copy that value verbatim** — get the exact current `include:` string
from Elastic Email's own domain-verification/dashboard page for this domain
and use that, since providers occasionally change the mechanism. **Only one
SPF record per domain — a second one silently breaks authentication.** If
`atlasreach.io` *also* sends through Google Workspace, you must merge **all**
sources into that ONE record rather than publishing two, e.g.:
`v=spf1 include:elasticemail.com include:_spf.google.com ~all` (again, using
Elastic Email's exact include value, not the illustrative one above). The
subdomain strategy still helps here: verifying/sending as
`mail.atlasreach.io` in Elastic Email lets that subdomain get its own clean
SPF record while the primary domain's existing SPF (Workspace, etc.) is left
alone.

### 2.4 DKIM — Elastic Email's responsibility now, not this box's

DKIM signing for the From:/sending domain is now **Elastic Email's job**, not
this server's. In Elastic Email's dashboard, verify the sending domain and
they'll give you their own CNAME or TXT record(s) to add to this same DNS
zone (exact record names/values come from their dashboard — don't hand-copy
from an old setup or guess).

This self-hosted box's **own** DKIM (docker-mailserver's built-in
OpenDKIM/`setup config dkim`) is **not needed at all** for the primary
send-via-Elastic-Email / receive-here flow, since this box never sends as the
domain. Skip the DKIM keygen step in §5 for the normal setup.

If the agency ever wants a **direct-send fallback** from this box instead of
Elastic Email, DKIM could be configured here later — that path (keygen,
DNS record shape, selector) is preserved in §12 (now the secondary/alternative
path), not here.

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

DMARC is domain-level and **provider-agnostic** — this record's requirements
don't change with the Elastic Email split, and no rework is needed here. If
the agency already worked through an AWS SES setup wizard earlier, this may
already be done; just double-check there is only **ONE** `_dmarc` TXT record
for the domain (a second one silently breaks DMARC the same way a duplicate
SPF record does).

### 2.6 PTR / reverse DNS — now a nice-to-have, not a requirement

This is the one record that is **NOT** in the domain's DNS zone — it's set by
whoever owns the **IP**, i.e. **Hostinger**. PTR matters far more for
**outbound sending** reputation than for a receive-only box, so with Elastic
Email doing the sending, this is now a **nice-to-have** rather than a hard
requirement. It's still cheap to set correctly, so do it if convenient — the
PTR for `2.25.75.95` resolving to `mail.atlasreach.io` (instead of a generic
host name) is a small hygiene win with no downside.

- **Where:** Hostinger hPanel → VPS → your server → **Network / rDNS** (naming
  varies), set the PTR for `2.25.75.95` to `mail.atlasreach.io`. If hPanel has
  no rDNS field for KVM, **open a support ticket** asking them to set reverse
  DNS for `2.25.75.95` → `mail.atlasreach.io`.
- Verify: `dig +short -x 2.25.75.95` must return `mail.atlasreach.io.`

**Port 25 outbound: no longer needed.** The old requirement to file a
Hostinger support ticket unblocking **outbound** port 25 no longer applies —
this box never originates SMTP connections to other mail servers now that
Elastic Email sends. Inbound port 25 (for receiving MX traffic) still needs
to be reachable — that's a `ufw`/firewall matter (§7), not a Hostinger
outbound-block matter. Only revisit the outbound-25 ticket if the agency
activates the direct-send escape hatch in §12.

### 2.7 Optional niceties

- **Autodiscover/autoconfig** and **MTA-STS** improve client setup and
  transport security but aren't required for the Salescale integration. Skip
  for v1.
- A **`mail` A record on the cousin domain** if you went that route — same
  shape, different zone.

---

## 3. From-address alignment (why all three records matter)

Deliverability turns on **alignment**: the domain in the visible `From:` header
must match the domain that SPF authorizes and the domain DKIM signs with. With
the Elastic Email split, all of that alignment now happens on **Elastic
Email's side**, added to the **same DNS zone** — this self-hosted box's only
remaining DNS involvement is the A/MX pair used for receiving (§2.1, §2.2).

- Salescale sends as `carter@atlasreach.io` (or `@mail.atlasreach.io`) —
  Elastic Email is the actual outbound mail transfer agent.
- SPF passes because Elastic Email's `include:` mechanism is published on the
  From: domain (§2.3) — not because of anything this box does.
- DKIM passes because **Elastic Email** signs with the key/CNAME they provide
  for that domain (§2.4) — this server's own OpenDKIM is not part of the
  picture.
- DMARC passes because both align to the From: domain (§2.5), same as before —
  DMARC's requirements don't change with who sends.

Get the From: domain, the Elastic-Email-authorized SPF domain, and the
Elastic-Email DKIM `d=` domain to be the **same registrable domain** and you
pass DMARC. This is why the domain you verify in Elastic Email must be the
**same domain** the outreach module puts in From: — this box's only job is
making sure the MX for that domain (§2.2) routes replies to the mailbox
created in §6.

---

## 4. TLS certificate for `mail.atlasreach.io`

The mail server needs a valid cert whose name matches its hostname, or
Salescale's IMAP TLS connection (and other mail servers' opportunistic
STARTTLS when delivering inbound mail on port 25) will fail verification. The
compose file is wired to read **Traefik's existing
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

## 5. Initial bring-up

Create the state directories and start the container. **DKIM keygen is
skipped in the primary flow** — this box doesn't sign or send outbound mail
as the domain, so docker-mailserver's own DKIM isn't needed here (see §2.4).

```bash
# On the VPS. Create the /docker/mailserver tree (matches the box convention).
sudo mkdir -p /docker/mailserver/{mail-data,mail-state,mail-logs,config}

cd ~/salescale/deploy

# Start ONLY the mail server (its own project — never touches "deploy").
docker compose -f docker-compose.mailserver.yml up -d
docker compose -f docker-compose.mailserver.yml logs -f mailserver   # watch it boot
```

That's it for bring-up — proceed to §6 to create the mailbox that receives
replies.

> DKIM keypair generation (`docker exec -it mailserver setup config dkim
> domain atlasreach.io`) is only relevant to the **direct-send escape hatch**
> in §12, for if the agency ever wants this box to sign and send outbound
> mail itself instead of Elastic Email. Skip it for the normal setup.

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

With Elastic Email sending, this box only needs **two** ports open, in
addition to the existing 22/80/443 (`DEPLOYMENT.md`):

```bash
sudo ufw allow 25/tcp     # SMTP: inbound MX only — replies route in here
sudo ufw allow 993/tcp    # IMAPS — Salescale reads the inbox here
sudo ufw status numbered
```

**587/465 (submission) are NOT needed** in the primary setup — Salescale
sends via Elastic Email's SMTP, not this box's, so nothing ever needs to
connect to this server's submission ports. They're only relevant if the
agency activates the **direct-send escape hatch** in §12; leave them closed
until/unless that happens.

Leave 143/110/995 closed — this setup exposes IMAPS (993) only. Recall the
Docker-vs-ufw gotcha from `DEPLOYMENT.md`: docker-mailserver **does** publish
these ports to the host, so they're internet-reachable by design — that's
correct here (a mail server must be), and the ufw rules above document intent.

**Also check Hostinger's hPanel VPS firewall** (separate from ufw) allows
25/993 inbound. The outbound-port-25 check from the old setup is gone — this
box doesn't originate outbound SMTP connections anymore (§2.6).

---

## 8. Verification

Work top-down: transport → auth → reputation.

**a) IMAPS port reachable + TLS cert correct** (this box only serves IMAP now,
so this is the relevant port to check here):
```bash
# From your Mac. Should show the cert CN = mail.atlasreach.io, valid chain.
openssl s_client -connect mail.atlasreach.io:993 -servername mail.atlasreach.io </dev/null 2>/dev/null | openssl x509 -noout -subject -dates
```

**b) Reply-receiving test** — send a plain email (from any personal address)
**to** the outreach mailbox's address (e.g. `carter@atlasreach.io`), then
confirm it lands in the mailbox created in §6 (via IMAP client or
`docker exec -it mailserver setup email list` + checking `mail-data/`). This
proves MX + inbound routing work end-to-end.

The old authenticated-send test via `swaks` against this box's port 587 no
longer applies to the primary flow — 587 isn't open here (§7). To verify the
**sending** leg, send a test message through **Elastic Email** (their
dashboard/API, or the Salescale UI once connected) and confirm it arrives
with SPF/DKIM/DMARC passing — see (c). The swaks-against-this-box test is
still useful if the agency activates the direct-send escape hatch (§12).

**c) mail-tester.com** — the headline check for the **sending** leg. Get an
address from [mail-tester.com](https://www.mail-tester.com) and send to it
via **Elastic Email** (their send API, or the Salescale UI once connected),
then load the score. **Target ≥ 9/10.** Below 7 means something in Elastic
Email's domain verification (SPF/DKIM/DMARC) is broken — check their
dashboard's domain-verification status first.

**d) Reverse DNS (nice-to-have, not required):** `dig +short -x 2.25.75.95` →
ideally `mail.atlasreach.io.` (§2.6). Less critical now that this box only
receives, but still cheap to get right.

**e) Blocklists:** [MXToolbox Blacklist Check](https://mxtoolbox.com/blacklists.aspx)
on `atlasreach.io` and on Elastic Email's sending IPs/domain-auth status
(their dashboard surfaces this). Checking `2.25.75.95` itself matters less
now — a receive-only IP being blocklisted doesn't block outbound sends since
Elastic Email's IPs, not this box's, carry that reputation.

---

## 9. Warmup & volume policy

**IP-level warmup is now largely Elastic Email's problem, not this VPS's** —
outbound mail leaves from their warm, monitored IP pool, not a fresh VPS
address, so the old week-by-week IP-ramp schedule tied to this box no longer
applies here. (That schedule is preserved in §12 for the direct-send escape
hatch, where it would matter again.)

What still matters regardless of provider: **domain-level** reputation (as
opposed to IP reputation) is still earned gradually, and ramping send volume
up over the first few weeks is still wise practice even on a provider with a
warm IP pool — a brand-new sending domain with a sudden volume spike still
reads as suspicious to receiving mailbox providers. Follow Elastic Email's own
ramp-up guidance for the account/domain if they provide one; where they
don't, a conservative multi-week ramp (low volume → gradually increasing) is
still sound practice.

**Hard rules (deliverability guide — provider-agnostic, still fully apply):**
- **Bounce rate < 5%** (target < 2%). Above 5% is dangerous — pause and
  reclean the list. Salescale's **email-verification gate**
  (`email_verification.sendable()` — see `CLAUDE.md` Phase 12) already keeps
  invalid/risky addresses out of every audience, which is the single biggest
  lever on bounce rate; lean on it regardless of who's sending.
- **Spread sends across the day** (not a single burst) — burst sending still
  looks like spam even from a warm provider IP. Configure the outreach module
  to drip.
- **Spam complaints < 0.1%.** One-click unsubscribe / honest reply-to-opt-out
  and honoring it immediately keeps this down.
- **Plain-text, one link max, consistent From: name, physical address +
  unsubscribe** in every mail (CAN-SPAM / GDPR — see the deliverability &
  legal guidance). Neither this mail server nor Elastic Email enforces this
  automatically; the outreach copy must.

**Warmup is failing if:** open rate < 20%, bounce > 3%, or mail lands in
Gmail's Promotions/Spam. Slow down a step when you see these, regardless of
which side (Elastic Email's ramp or the agency's own pacing) needs the
adjustment.

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
schedule off the box. Priorities (reordered from the old all-in-one setup —
this box no longer holds DKIM keys by default, since it doesn't sign or
send):
1. **`mail-data/`** — the actual mailboxes (received replies). This is the
   entire reason the box exists now; losing this loses the reply history.
2. **`config/`** — accounts, passwords (`postfix-accounts.cf`), aliases,
   overrides.
3. **`config/opendkim/keys/`** — only present/relevant if the direct-send
   escape hatch (§12) has been activated on this box. If so, treat these DKIM
   **private keys** as top priority again: losing them means re-keying +
   re-publishing DNS and eating a reputation hit.
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

Salescale's connect form now points **two different providers** at the two
different legs — Elastic Email for SMTP (sending), this box for IMAP
(receiving replies). This directly matches the real product UI: the connect
dialog has a **"Same login as SMTP"** checkbox (checked by default, for the
common case of one mailbox doing both legs) — for this hybrid setup,
**uncheck it**, since Elastic Email and this mailbox are different providers
with different credentials.

**Values to enter in the Salescale connect form:**

| Field | SMTP (send — Elastic Email) | IMAP (read inbox — this box) |
|-------|------------------------------|-------------------------------|
| Host | `smtp.elasticemail.com` (verify current value in the Elastic Email dashboard) | `mail.atlasreach.io` |
| Port | `2525` or `587` (both commonly documented for Elastic Email — confirm the current recommended port in their dashboard) | `993` |
| Security | **STARTTLS** | **SSL/TLS** (implicit) |
| Username | the Elastic Email account email (get from their dashboard) | `carter@atlasreach.io` |
| Password | the Elastic Email **API key** (not the account password — get from their dashboard) | the mailbox password from §6 |

**Do not hardcode the Elastic Email host/port beyond what's noted above as
"commonly documented"** — confirm the exact current values (hostname, port,
and whether they recommend STARTTLS on that port) on Elastic Email's own SMTP
integration/dashboard page before entering them, since providers do change
these over time.

The mail server this box runs (§4–§10) still needs its TLS cert issued for
`mail.atlasreach.io` and reachable on 993 for the IMAP leg to work — that part
of the setup is unchanged from the all-in-one design, just narrower in scope
(IMAP only, no SMTP submission ports needed — see §7).

The Salescale backend runs in the **`deploy` compose project**; this mail
server is the **`mailserver` project** — **different Docker networks**. Two ways
for the app container to reach the IMAP side; pick one.

### Recommended: reach it by its public hostname

Enter the mail server's **public FQDN** in the IMAP fields of Salescale's
email-account connect form. The container's DNS resolves
`mail.atlasreach.io → 2.25.75.95` and the connection hairpins back to the
box. **This is the clean choice because the TLS cert is issued for
`mail.atlasreach.io`** (§4) — connecting by that exact name means certificate
validation passes with no exceptions.

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

## 12. Alternative: direct-send from this box instead of a provider

This section used to describe switching **away** from self-hosted sending
**to** a provider. That pivot has already happened (§0) — Elastic Email sends,
this box only receives. This section now documents the **opposite** fallback:
how to reactivate direct sending **from this box** if the agency ever wants
to drop Elastic Email, for example if provider costs, deliverability, or
control become a problem. The technical pieces are the same ones the old
all-in-one setup used; they're preserved here rather than deleted, just
relabeled as the secondary path.

**To reactivate direct-send from this box:**

1. **Generate DKIM on this server** (skipped in §5 for the primary flow):
   ```bash
   # Generates a 2048-bit key with selector "mail" for atlasreach.io.
   docker exec -it mailserver setup config dkim domain atlasreach.io

   # The public key to paste into DNS is printed here:
   sudo cat /docker/mailserver/config/opendkim/keys/atlasreach.io/mail.txt
   ```
   That file contains the exact `mail._domainkey ... v=DKIM1; ... p=...` TXT
   record — paste the `p=` contents into DNS (shape shown in the old §2.4
   pattern: `TXT mail._domainkey.atlasreach.io`). Restart to load signing:
   `docker compose -f docker-compose.mailserver.yml restart mailserver`.
   The private half sits in `/docker/mailserver/config/opendkim/keys/` —
   back it up (§10) once this is active.

2. **Publish SPF for this box directly** instead of (or alongside) Elastic
   Email's include: `v=spf1 mx -all` on the sending domain — see the
   reasoning in the old §2.3 pattern (only one SPF record per domain; merge
   sources rather than publishing two).

3. **Unblock outbound port 25.** Many Hostinger VPS plans block outbound port
   25 by default. Request an unblock via a **support ticket** ("please
   unblock outbound SMTP / port 25 on VPS `2.25.75.95` for a legitimate,
   authenticated mail server with SPF/DKIM/DMARC/rDNS configured"). Until 25
   is open outbound, this box cannot deliver to other mail servers.

4. **Open the submission ports** in `ufw` (§7): `587/tcp` (STARTTLS) and,
   if used, `465/tcp` (implicit TLS).

5. **Set PTR/reverse DNS properly** (§2.6) — this matters much more once the
   box sends outbound; get Hostinger to point the PTR for `2.25.75.95` at
   `mail.atlasreach.io`.

6. **Point Salescale's SMTP field back at this box** (`mail.atlasreach.io`,
   port 587 STARTTLS or 465 implicit-TLS, mailbox credentials from §6) — no
   code change needed, same as switching providers is a config-only change.

7. **Re-run the IP/domain warmup schedule** — a fresh VPS IP sending directly
   has zero reputation, unlike Elastic Email's warm pool:

   | Week | Emails/day (per domain) | Focus |
   |------|--------------------------|-------|
   | 1 | 5–10 | Real conversations only — send to colleagues/known contacts, get genuine **replies**. Not cold yet. |
   | 2 | 20–30 | Small, highly-targeted cold batches. Verified addresses only. Watch bounces. |
   | 3 | 40–60 | Expand slightly. Keep open rate > 30%, bounce < 3%. |
   | 4 | 80–100 | Approaching normal volume. Watch spam-complaint rate (< 0.1%). |
   | 5+ | up to 150–200 (cap) | Full volume. **Stay under 100–200/day/domain** — beyond that, add another sending domain/mailbox rather than pushing one harder. |

   Consider a warmup tool (Lemwarm, Mailreach, Warmup Inbox) for weeks 1-4.
   The hard rules in §9 (bounce < 5%, spam complaints < 0.1%, plain-text +
   unsubscribe) apply here exactly as they do under Elastic Email.

If direct-send disappoints again after activating this, reverse steps 2-7 and
fall back to Elastic Email (§0) — this really is a config change either
direction, not a code change, since Salescale's connect form takes any SMTP
host.

---

## Quick reference

Primary architecture: **Elastic Email sends, this box only receives.**

```bash
# Bring up / status / logs  (always -f the mailserver file; never bare compose)
cd ~/salescale/deploy
docker compose -f docker-compose.mailserver.yml up -d
docker compose -f docker-compose.mailserver.yml ps
docker compose -f docker-compose.mailserver.yml logs mailserver --tail 100

# Management CLI
docker exec -it mailserver setup email add user@atlasreach.io
docker exec -it mailserver setup email list
docker exec -it mailserver setup fail2ban

# DKIM config/keygen is NOT part of the primary flow (§2.4, §5) — only
# needed for the direct-send escape hatch (§12):
# docker exec -it mailserver setup config dkim domain atlasreach.io
# sudo cat /docker/mailserver/config/opendkim/keys/atlasreach.io/mail.txt
```

Ports: **25** (MX in — replies route here) · **993** (IMAPS, Salescale reads
replies here). 587/465 are **not** opened in the primary setup — Salescale
sends via Elastic Email, not this box (only needed if the §12 escape hatch is
activated).

Salescale connect:
- **SMTP (send):** Elastic Email — host `smtp.elasticemail.com`, port `2525`
  or `587` (verify current values in the Elastic Email dashboard), STARTTLS,
  username = Elastic Email account email, password = Elastic Email API key.
- **IMAP (receive):** `mail.atlasreach.io:993` SSL/TLS, user
  `carter@atlasreach.io`, password = the mailbox password from §6.
- Uncheck **"Same login as SMTP"** in the connect form — these are two
  different providers with two different credential sets.
