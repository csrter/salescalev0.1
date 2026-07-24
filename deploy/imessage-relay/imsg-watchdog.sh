#!/bin/bash
# Self-heal the iMessage relay. Two failure modes seen live:
#  1) BlueBubbles dies (reboot / crash) -> local API stops answering
#  2) tunnel goes stale on the VPS side -> local API fine, public relay dead
LOG=/tmp/imsg-watchdog.log
ts() { date "+%Y-%m-%dT%H:%M:%S"; }

local_code=$(curl -sm 8 -o /dev/null -w "%{http_code}" http://localhost:1234/api/v1/ping)
if [ "$local_code" != "401" ] && [ "$local_code" != "200" ]; then
  echo "$(ts) BlueBubbles local unhealthy (code=$local_code) -> kickstart" >> "$LOG"
  launchctl kickstart -k gui/501/com.bluebubbles.server 2>>"$LOG"
  exit 0
fi

relay_code=$(curl -sm 15 -o /dev/null -w "%{http_code}" https://imsg.atlasreach.io/api/v1/ping)
if [ "$relay_code" != "401" ] && [ "$relay_code" != "200" ]; then
  echo "$(ts) relay unhealthy (code=$relay_code) but BB local ok -> kick tunnel" >> "$LOG"
  launchctl kickstart -k system/com.salescale.imsgtunnel 2>>"$LOG"
fi
