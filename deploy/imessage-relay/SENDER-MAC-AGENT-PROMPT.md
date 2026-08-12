# Prompt: hand this to a Claude Code session running ON the sender Mac

Copy `deploy/imessage-relay/` to that Mac first (AirDrop or scp) — the prompt
treats those files as the source of truth. Then paste everything below the
line into a fresh Claude Code session on that machine.

Fill in the two `<>` placeholders before pasting. Leave the rest alone: the
ground rules exist because each one maps to a failure this project already
paid for.

---

You are running on a Mac that is being set up as a dedicated BlueBubbles
iMessage sender host for Salescale. When this is done, the Mac must send
iMessage and SMS unattended, 24/7, reachable from the Salescale backend over
a reverse SSH tunnel — surviving reboots, power cuts, and network drops with
nobody at the keyboard.

**Read `FRESH-MAC-SETUP.md` and `README.md` in the `imessage-relay` directory
I've put on this Mac before doing anything.** They are the authority; they
encode failures this project already hit in production, and they beat your
priors about how to set up a Mac. If those files aren't here, stop and ask me
for them.

## Parameters

| | |
|---|---|
| Relay hostname | `<imsg2.atlasreach.io>` |
| Tunnel port | `<12346>` — 12345 and 8443 belong to other relays, never reuse them |
| VPS | `2.25.75.95` |
| VPS tunnel username | **ask me** — do not guess and do not probe |
| BlueBubbles local API | `http://localhost:1234` |
| macOS admin user here | ask me if it isn't obvious from `whoami` |

## Ground rules

1. **Never type a password or credential.** Apple ID sign-in, the BlueBubbles
   server password, disabling SIP in Recovery, turning FileVault off,
   enabling auto-login, Text Message Forwarding on the iPhone — all of these
   are mine. For each, give me the exact click path or keystrokes and wait.
   Don't guess at values I haven't given you.
2. **sudo:** if a command needs it and you can't run it non-interactively,
   put it in a bash block for me to run, then verify the result yourself.
3. **Never probe SSH usernames against the VPS.** Trying `root`/`ubuntu`/etc.
   trips fail2ban, bans this Mac's IP, and stalls the tunnel you're building.
   Use only the username I give you.
4. **Verify every phase before moving to the next**, and show me the actual
   command output. "That should work" is not a verification. If a check
   fails, stop and tell me rather than working around it.
5. **Don't send test messages to anyone but numbers I explicitly give you.**
   Real prospects are on the other side of this system.
6. Work one phase at a time and check in between phases.

## Phases

**0 — Survey.** Report where this Mac actually stands before changing
anything: `sw_vers`, `whoami`, `csrutil status`, `fdesetup status`,
`pmset -g`, whether BlueBubbles is installed and answering on :1234, whether
Homebrew and autossh exist, `networksetup -listnetworkserviceorder`, and
whether `~/Library/LaunchAgents` already has any `com.salescale.*` or
`com.bluebubbles.*` jobs. Then tell me which of the checklist steps are
already done and which remain.

**1 — Uptime hardening (the part that matters most).** BlueBubbles drives
Messages.app, so it only runs inside a logged-in GUI session — everything
here protects that session:

- Sleep and power: never sleep, run with the lid closed on AC, reboot after a
  power cut. Verify with `pmset -g`, don't assume the set took.
- **FileVault off** and **auto-login on.** These are mine to do; give me the
  click paths. Explain to me why, so I don't "helpfully" re-enable FileVault
  later. Verify with `fdesetup status` and by checking the auto-login setting
  afterward — this exact gap has stranded the relay twice.
- Automatic macOS updates off (an update re-enables SIP and breaks the
  Private API helper, which is a silent kill).
- Remote login + Screen Sharing on, so I can get in without a monitor. Never
  expose 5900 publicly — it gets tunnelled.
- Ethernet ahead of Wi-Fi in the service order if there's an adapter.

**2 — SIP + BlueBubbles.** SIP must be disabled *before* BlueBubbles is
installed, from Recovery — that's mine, walk me through it. Then confirm
`csrutil status` says disabled. After I install BlueBubbles and set its
server password, check `/api/v1/server/info` and do not proceed unless
**both** `private_api` and `helper_connected` are `true`. If either is false
we're on the AppleScript path, which is the configuration that reported 434
successful sends that had all silently failed — treat it as a hard stop, not
a warning.

**3 — Tunnel.** Generate an ed25519 key for the tunnel, show me the public
half so I can install it on the VPS, install autossh via Homebrew, and set up
`com.salescale.bluebubbles-tunnel.plist` in `~/Library/LaunchAgents`
(RunAtLoad + KeepAlive), editing the autossh path, key path, username, host,
and port. Two confirmed gotchas the README covers: the forward must be a bare
port (`-R <PORT>:localhost:1234`) matching `permitlisten="localhost:<PORT>"`,
and the VPS side must never use the `restrict` shorthand.

Once I've told you DNS and the VPS route are live, verify from here:
`curl -s -o /dev/null -w '%{http_code}' https://<HOST>/api/v1/ping` → **401**.
That single 401 proves DNS, TLS, tunnel, and BlueBubbles are all alive at
once. Anything else, diagnose the layer rather than retrying.

**4 — Self-healing.** Install `com.bluebubbles.server.plist` (KeepAlive) and
`imsg-watchdog.sh` + `com.salescale.imsgwatchdog.plist`. **The watchdog's
defaults point at a different Mac's relay** — set `RELAY_URL`,
`TUNNEL_LABEL=com.salescale.bluebubbles-tunnel` and `TUNNEL_DOMAIN=gui` for
this machine, either in the script's config block or
`/usr/local/etc/imsg-watchdog.conf`. Then prove it works instead of assuming:
`killall BlueBubbles`, wait, and show me `/tmp/imsg-watchdog.log` plus the
app back up.

**5 — Verification.** Run the whole checklist at the end of
`FRESH-MAC-SETUP.md` and give me a pass/fail table. Two of those items are
the ones that actually catch silent failure:

- After a real send, query the device rather than trusting the API:
  `sqlite3 ~/Library/Messages/chat.db "select service,error,is_sent from message where is_from_me=1 order by date desc limit 5;"`
  — want `error=0, is_sent=1`. The API said fine while 434 sends were dead.
- Test **both legs separately**: to an iPhone (blue, iMessage) and to a known
  non-iMessage number (green, SMS). The SMS leg depends on Text Message
  Forwarding from the paired iPhone and is the one that silently doesn't
  exist. Roughly 90% of a cold list is green-bubble, so a working blue leg
  alone means the Mac is mostly useless.
- Finally, reboot it. Auto-login, BlueBubbles, tunnel, and the 401 must all
  come back with nobody touching anything. If that doesn't hold, none of the
  rest counts.

## When you're done

Give me: the pass/fail table, anything you changed that isn't in the
checklist and why, and an explicit list of what's still on me (Apple ID,
DNS, VPS-side key and route, connecting the account in Salescale, pasting
the inbound webhook URL into BlueBubbles). Be blunt about anything you
couldn't verify — I would rather know it's untested than find out from a
campaign.
