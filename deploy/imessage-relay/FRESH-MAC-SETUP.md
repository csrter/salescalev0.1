# Fresh Mac → BlueBubbles sender host, in order

Follow top to bottom on a just-erased Mac. Deep rationale for each choice is
in `README.md` ("Mac mini build" + "Using a MacBook instead"); this file is
the do-it checklist.

This doc assumes hostname **`imsg2.atlasreach.io`** and tunnel port
**`12346`** — change both consistently if you pick different ones. Existing
relays use 12345 (MacBook) and 8443 (EC2), so don't reuse those.

Legend: **[MAC]** on the Mac · **[VPS]** on 2.25.75.95 · **[DNS]** at Porkbun
· **[APP]** in Salescale

---

## 1. [MAC] Erase and install

System Settings → General → **Transfer or Reset → Erase All Content and
Settings**. (Or Recovery → Disk Utility → erase → reinstall macOS.)

In Setup Assistant:

- Connect **ethernet** (adapter in now — skip Wi-Fi setup entirely if you can).
- **Do NOT sign in with your personal Apple ID.** Skip it here; a dedicated
  one goes in at step 5.
- Skip Screen Time, Siri, Analytics.
- **Decline FileVault.** Critical: FileVault stops a reboot at the
  disk-unlock screen *before* auto-login runs, which strands the relay. If
  the installer already enabled it, turn it off: System Settings → Privacy &
  Security → FileVault → Turn Off. Verify: `fdesetup status`.
- Create one admin user — this doc assumes **`sender`**. Use a password you
  will not lose; auto-login, SIP, and Screen Sharing all need it.

## 2. [MAC] Network — ethernet primary

System Settings → Network → **⋯ → Set Service Order** → drag **Ethernet**
above Wi-Fi. Leave Wi-Fi on as failover (harmless) or off.

Give it a **DHCP reservation** on your router so the LAN address is stable.

```bash
networksetup -listnetworkserviceorder      # confirm Ethernet is (1)
route -n get default | grep interface      # should be the ethernet iface
```

## 3. [MAC] Disable SIP — do this BEFORE installing BlueBubbles

This is what makes Private API possible, which is the whole reliability story.

1. Shut down. Hold the **power button** until "Loading startup options".
2. **Options → Continue → Utilities → Terminal**.
3. `csrutil disable` → authenticate as `sender` → `reboot`.

```bash
csrutil status      # must print: System Integrity Protection status: disabled.
```

If it says enabled, stop here — nothing below will give you reliable sending.

## 4. [MAC] Uptime + access hardening

```bash
# never sleep, run with the lid closed, come back after a power cut
sudo pmset -a disablesleep 1
sudo pmset -a sleep 0 displaysleep 10 autorestart 1
pmset -g | grep -Ei "sleep|autorestart"

# remote access (Screen Sharing stays tunnelled — never expose 5900)
sudo systemsetup -setremotelogin on
sudo launchctl enable system/com.apple.screensharing
sudo launchctl load -w /System/Library/LaunchDaemons/com.apple.screensharing.plist

# macOS updates re-enable SIP and break the Private API helper — no surprises
sudo softwareupdate --schedule off
```

Also in System Settings:

- **Users & Groups → Automatically log in as → `sender`.** BlueBubbles drives
  Messages.app, so it only runs inside a logged-in GUI session. Without this
  a reboot parks at the login window and the relay stays down.
- **Lock Screen** → "Require password after screen saver begins" → **Never**.
- **General → Software Update → Automatic Updates** → turn everything off.

## 5. [MAC + IPHONE] Dedicated Apple ID, and sending FROM your carrier number

The goal: outbound shows your outreach line (a Tello number here), in blue to
iMessage users and green over SMS to everyone else. That takes an iPhone
holding the SIM, not just the Mac.

**The constraint that dictates everything:** a phone number can be registered
to exactly ONE Apple ID for iMessage at a time. So the number must live on
the same dedicated Apple ID the sender Mac uses. Use a line bought for
outreach — moving a personal number here removes it from your personal
iMessage, and signing the Mac into a personal Apple ID also collides with any
other BlueBubbles Mac on that ID (both receive every inbound and both fire
their webhook, so every reply is ingested twice).

**5a. Create the Apple ID** at appleid.apple.com — for this purpose only.
Automated bulk messaging is against Apple's iMessage ToS, and the realistic
consequence is the ID getting flagged; keep the blast radius replaceable.

**5b. Register the number, on the iPhone holding the SIM.** Only *Messages*
needs the dedicated ID — iCloud, photos and the rest can stay personal:

- Settings → Messages → **Send & Receive → Apple ID → Sign Out**, then sign
  in with the dedicated ID.
- Settings → Messages → **iMessage ON**. Activation sends a silent SMS to
  Apple and can take several minutes. On an MVNO like Tello it sometimes
  needs a nudge: confirm the line can send a normal text, then toggle
  airplane mode on/off. Do not continue while it says "Waiting for
  activation".
- Send & Receive → confirm the **phone number is listed and ticked** under
  "You can be reached by".

**5c. Point the Mac at that number.** Messages on the Mac, signed into the
**same** dedicated Apple ID:

- Messages → Settings → iMessage → tick the phone number under "You can be
  reached at".
- **Start new conversations from → the phone number.** This is the setting
  that makes outbound show the Tello number instead of the Apple ID email.
  Miss it and every first touch arrives from an unknown address.

**5d. Text Message Forwarding — this is what reaches non-iMessage numbers.**
On the iPhone: Settings → Messages → **Text Message Forwarding** → enable the
sender Mac, and type the code it displays.

Do not treat this as optional. Sampling a live Salescale audience, only 2 of
26 prospects were iMessage-registered; the other 24 were plain cell numbers
reachable only as green-bubble SMS. Without forwarding you reach roughly 8%
of a cold list.

The toggle only appears when both devices are signed into the same Apple ID
for Messages and iMessage is active. The iPhone must stay **powered on and
online** for SMS to keep flowing — it is the actual radio. Leave it on a
charger, turn off Low Power Mode, and turn off automatic iOS updates so a
3am restart doesn't strand the SMS leg. A data-only eSIM cannot do any of
this; the line needs voice+SMS.

## 6. [MAC] BlueBubbles + Private API

1. Install BlueBubbles Server (https://bluebubbles.app/).
2. Enable **Private API** and install its helper when prompted (follow the
   in-app instructions — the extra AMFI/library-validation steps are
   macOS-version specific).
3. Set a strong **server password** — keep it; it goes into Salescale.
4. Enable BlueBubbles' own **auto-start**.
5. Verify locally, and do not proceed until both are true:

```bash
curl -s "http://localhost:1234/api/v1/server/info?password=YOURPW" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print("private_api:", d["private_api"], "| helper_connected:", d["helper_connected"])'
```

`private_api: True` and `helper_connected: True`. If either is False you are
on the AppleScript path — the exact configuration that silently failed 200
sends on EC2. Fix it here.

## 7. [MAC] Tunnel key

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_bluebubbles_relay \
  -C "air-bluebubbles-relay-tunnel" -N ""
cat ~/.ssh/id_ed25519_bluebubbles_relay.pub
```

Send me that **public** key (it's not secret) and I'll install it on the VPS
with the right restrictions, or do it yourself per README step 3 — the line is:

```
no-agent-forwarding,no-X11-forwarding,no-pty,permitlisten="localhost:12346" ssh-ed25519 AAAA... air-bluebubbles-relay-tunnel
```

Do **not** use the `restrict` shorthand — it implies `no-port-forwarding`,
which blocks the `-R` bind even with `permitlisten` present (confirmed
against live OpenSSH 9.6).

## 8. [MAC] autossh + launchd

```bash
# Homebrew (installs Xcode CLT if missing), then autossh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install autossh
which autossh    # note the path for the plist
```

Copy `com.salescale.bluebubbles-tunnel.plist` from this directory to
`~/Library/LaunchAgents/`, editing: the autossh path, the relay user/host,
the key path, and the forward as **`-R 12346:localhost:1234`** (bare port —
must match `permitlisten="localhost:12346"`).

```bash
launchctl load -w ~/Library/LaunchAgents/com.salescale.bluebubbles-tunnel.plist
launchctl list | grep bluebubbles-tunnel
```

## 9. [DNS] A record

Porkbun → `imsg2.atlasreach.io` → **A** → `2.25.75.95`.

**`atlasreach.io` has a wildcard `*` record pointing at WordPress.com**, so
every subdomain already resolves — to the wrong place. Verified: a
deliberately nonexistent name returns the same `192.0.79.166 / 192.0.79.152`
as `imsg2` does today. The existing `imsg.atlasreach.io` only works because
it has an explicit record overriding the wildcard.

The trap is that DNS *looks* configured: the name resolves and even answers
HTTP, from WordPress. Don't check with `dig +short` alone — check the value:

```bash
dig +short imsg2.atlasreach.io A     # must be 2.25.75.95, NOT 192.0.79.x
```

An explicit A record beats the wildcard. Wait for it to actually flip before
step 10, or Traefik will request a certificate for a name still pointed
elsewhere.

## 10. [VPS] Traefik route

A file-provider route in `/docker/traefik/dynamic/` pointing
`imsg2.atlasreach.io` → `http://127.0.0.1:12346`, same shape as the existing
`imsg-relay.yml`. Auto-TLS via the existing letsencrypt resolver. **I can do
this step for you** once DNS resolves.

## 11. [MAC] Watchdog + keepalive

Install from this directory:

- `com.bluebubbles.server.plist` → `~/Library/LaunchAgents/` (KeepAlive; belt
  and braces alongside the app's own auto-start), then
  `launchctl load -w ~/Library/LaunchAgents/com.bluebubbles.server.plist`
- `imsg-watchdog.sh` → `/usr/local/bin/` (`chmod +x`) +
  `com.salescale.imsgwatchdog.plist` → `~/Library/LaunchAgents/`. Covers both
  "local API stopped answering" and "local API fine but the public relay is
  dead (stale tunnel socket)".

  **Point it at THIS Mac before loading it.** Its defaults are the EC2 box's
  hostname and system-domain tunnel label; left alone on a second Mac it
  health-checks someone else's relay and kickstarts this one every 60s
  forever. Either edit the config block at the top of the script or create
  `/usr/local/etc/imsg-watchdog.conf`:

  ```
  RELAY_URL=https://imsg2.atlasreach.io
  TUNNEL_LABEL=com.salescale.bluebubbles-tunnel
  TUNNEL_DOMAIN=gui
  ```

  `TUNNEL_DOMAIN=gui` because step 8 installs the tunnel as a
  `~/Library/LaunchAgents` job. Verify with a deliberate break —
  `killall BlueBubbles`, then watch `/tmp/imsg-watchdog.log`.

## 12. [APP] Connect in Salescale

SMS → Accounts → Connect a number → provider **BlueBubbles**:

| Field | Value |
|---|---|
| Relay URL | `https://imsg2.atlasreach.io` |
| Server password | from step 6 |
| iMessage number | the **carrier number** from step 5 (E.164, e.g. `+1602...`) |
| Min / max seconds between sends | leave **20 / 45** |
| Send as SMS only | **OFF** (that flag is only for the dead-iMessage EC2 box) |

Connect this as a **new** account rather than editing the existing
BlueBubbles one: that row points at the old relay with `force_sms` on, and
`(organization, from_number)` is uniquely indexed, so a new number is a new
row. Retire the old account afterward so lead notifications stop burning a
failed attempt on a dead relay before failing over.

Then copy the account's **inbound webhook URL** from its card and paste it
into BlueBubbles' webhook settings on the Mac. Inbound is required — without
it STOP replies never reach us.

## 13. Verification — all must pass before you trust it

- [ ] `csrutil status` → disabled
- [ ] `server/info` → `private_api: true`, `helper_connected: true`
- [ ] `curl https://imsg2.atlasreach.io/api/v1/ping` → **401** "Missing server
      password" (proves DNS + TLS + tunnel + BlueBubbles are all alive)
- [ ] A real send from Salescale **arrives on a test phone**, and the
      recipient sees it **from the Tello number** (not the Apple ID email —
      that means step 5c was missed)
- [ ] **Both legs, separately.** Send to an iPhone (expect blue) AND to a
      known non-iMessage number such as an Android handset (expect green).
      The SMS leg is the one that silently doesn't exist if Text Message
      Forwarding didn't take.
- [ ] The device agrees — not just the API:
      ```bash
      sqlite3 ~/Library/Messages/chat.db \
        "select service,error,is_sent from message where is_from_me=1 order by date desc limit 5;"
      ```
      want `error=0` and `is_sent=1`, with `service` showing **iMessage** for
      the blue one and **SMS** for the green one. This query is what exposed
      the EC2 failure; the API said fine while 434 sends were dead.
- [ ] Power the **iPhone** off, send to a non-iMessage number, and confirm it
      fails rather than silently vanishing — that is the failure mode to
      recognise later, and it tells you the SMS leg genuinely depends on that
      phone staying up.
- [ ] Inbound: text the Mac's handle → appears in Salescale's Messages tab
- [ ] `killall BlueBubbles` → watchdog relaunches within ~60s
- [ ] **Reboot** → auto-logs in, BlueBubbles up, tunnel up, ping still 401

## 14. Ramp

New Apple ID = start slow. Keep the 20–45s spacing, send tens/day for the
first week, and watch `channel_health` on the account card. Bulk volume from
a cold Apple ID is what gets it flagged.
