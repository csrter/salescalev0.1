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

## 5. [MAC] Dedicated Apple ID + iMessage

Sign **Messages** into an Apple ID created for this purpose — never a personal
one, and **not the same Apple ID as any other BlueBubbles Mac** (two Macs on
one Apple ID both receive every inbound and both fire their webhook, so every
reply gets ingested twice).

Messages → Settings → iMessage → confirm it activates and shows the handle
you expect. Note that handle — it's Salescale's "iMessage number".

Optional, only if you also want green-bubble SMS: pair an iPhone with a
**voice+SMS** line via Settings → Messages → Text Message Forwarding. A
data-only eSIM cannot do this.

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

## 10. [VPS] Traefik route

A file-provider route in `/docker/traefik/dynamic/` pointing
`imsg2.atlasreach.io` → `http://127.0.0.1:12346`, same shape as the existing
`imsg-relay.yml`. Auto-TLS via the existing letsencrypt resolver. **I can do
this step for you** once DNS resolves.

## 11. [MAC] Watchdog + keepalive

Install from this directory:

- `com.bluebubbles.server.plist` → `~/Library/LaunchAgents/` (KeepAlive; belt
  and braces alongside the app's own auto-start)
- `imsg-watchdog.sh` + `com.salescale.imsgwatchdog.plist` → covers both
  "local API stopped answering" and "local API fine but the public relay is
  dead (stale tunnel socket)"

## 12. [APP] Connect in Salescale

SMS → Accounts → Connect a number → provider **BlueBubbles**:

| Field | Value |
|---|---|
| Relay URL | `https://imsg2.atlasreach.io` |
| Server password | from step 6 |
| iMessage number | the handle from step 5 |
| Min / max seconds between sends | leave **20 / 45** |
| Send as SMS only | **OFF** (that flag is only for the dead-iMessage EC2 box) |

Then copy the account's **inbound webhook URL** from its card and paste it
into BlueBubbles' webhook settings on the Mac. Inbound is required — without
it STOP replies never reach us.

## 13. Verification — all must pass before you trust it

- [ ] `csrutil status` → disabled
- [ ] `server/info` → `private_api: true`, `helper_connected: true`
- [ ] `curl https://imsg2.atlasreach.io/api/v1/ping` → **401** "Missing server
      password" (proves DNS + TLS + tunnel + BlueBubbles are all alive)
- [ ] A real send from Salescale **arrives on a test phone**
- [ ] The device agrees — not just the API:
      ```bash
      sqlite3 ~/Library/Messages/chat.db \
        "select service,error,is_sent from message where is_from_me=1 order by date desc limit 5;"
      ```
      want `error=0` and `is_sent=1`. This query is what exposed the EC2
      failure; the API said fine while 434 sends were dead.
- [ ] Inbound: text the Mac's handle → appears in Salescale's Messages tab
- [ ] `killall BlueBubbles` → watchdog relaunches within ~60s
- [ ] **Reboot** → auto-logs in, BlueBubbles up, tunnel up, ping still 401

## 14. Ramp

New Apple ID = start slow. Keep the 20–45s spacing, send tens/day for the
first week, and watch `channel_health` on the account card. Bulk volume from
a cold Apple ID is what gets it flagged.
