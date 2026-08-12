#!/bin/bash
# Self-heal the iMessage relay. Two failure modes seen live:
#  1) BlueBubbles dies (reboot / crash) -> local API stops answering
#  2) tunnel goes stale on the VPS side -> local API fine, public relay dead
#
# Install (see FRESH-MAC-SETUP.md step 11):
#   sudo cp imsg-watchdog.sh /usr/local/bin/ && sudo chmod +x /usr/local/bin/imsg-watchdog.sh
#   cp com.salescale.imsgwatchdog.plist ~/Library/LaunchAgents/   (or /Library/LaunchDaemons)
#
# PER-MAC CONFIG. Each sender Mac has its own relay hostname and tunnel port,
# so do NOT leave these at the defaults on a second machine — a watchdog
# pointed at another Mac's relay will "heal" this one every 60 seconds
# forever. Either edit the defaults below or drop overrides in
# /usr/local/etc/imsg-watchdog.conf (plain KEY=value, sourced as shell):
#
#   RELAY_URL=https://imsg2.atlasreach.io
#   TUNNEL_LABEL=com.salescale.bluebubbles-tunnel
#   TUNNEL_DOMAIN=gui
#
# TUNNEL_DOMAIN is "gui" when the tunnel is a ~/Library/LaunchAgents job (the
# com.salescale.bluebubbles-tunnel.plist in this directory) and "system" when
# it is a /Library/LaunchDaemons job (the EC2 Mac's com.salescale.imsgtunnel).
# A system-domain kickstart needs root, so with TUNNEL_DOMAIN=system this
# script must itself run as a LaunchDaemon.

RELAY_URL="${RELAY_URL:-https://imsg.atlasreach.io}"
TUNNEL_LABEL="${TUNNEL_LABEL:-com.salescale.imsgtunnel}"
TUNNEL_DOMAIN="${TUNNEL_DOMAIN:-system}"
BB_LABEL="${BB_LABEL:-com.bluebubbles.server}"
LOCAL_URL="${LOCAL_URL:-http://localhost:1234}"
LOG="${LOG:-/tmp/imsg-watchdog.log}"

[ -r /usr/local/etc/imsg-watchdog.conf ] && . /usr/local/etc/imsg-watchdog.conf

ts() { date "+%Y-%m-%dT%H:%M:%S"; }

# BlueBubbles is a GUI app, so its LaunchAgent lives in the console user's
# domain — not necessarily uid 501 on a Mac whose sender account was created
# after other users.
uid=$(stat -f %u /dev/console 2>/dev/null || id -u)

# 401 (password required) and 200 both prove the API is alive and answering.
local_code=$(curl -sm 8 -o /dev/null -w "%{http_code}" "$LOCAL_URL/api/v1/ping")
if [ "$local_code" != "401" ] && [ "$local_code" != "200" ]; then
  echo "$(ts) BlueBubbles local unhealthy (code=$local_code) -> kickstart" >> "$LOG"
  launchctl kickstart -k "gui/$uid/$BB_LABEL" 2>>"$LOG"
  # Nothing downstream can be healthy while the app is restarting, so stop
  # here rather than also blaming the tunnel for it.
  exit 0
fi

relay_code=$(curl -sm 15 -o /dev/null -w "%{http_code}" "$RELAY_URL/api/v1/ping")
if [ "$relay_code" != "401" ] && [ "$relay_code" != "200" ]; then
  echo "$(ts) relay unhealthy (code=$relay_code) but BB local ok -> kick tunnel" >> "$LOG"
  if [ "$TUNNEL_DOMAIN" = "system" ]; then
    launchctl kickstart -k "system/$TUNNEL_LABEL" 2>>"$LOG"
  else
    launchctl kickstart -k "gui/$uid/$TUNNEL_LABEL" 2>>"$LOG"
  fi
  # Known limit: if the VPS still holds the OLD forwarded socket on the
  # tunnel port, autossh reconnects and dies on "remote port forwarding
  # failed" in a loop, and no amount of kickstarting fixes it from this
  # side. That one needs the stale `sshd:` session killed on the VPS —
  # README "stale tunnel socket".
fi
