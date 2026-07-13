#!/usr/bin/env bash
# firewall.sh — lock the relay VPS's public HTTPS port down to ONLY
# Salescale's backend egress IP, and make sure the loopback tunnel port is
# never externally reachable.
#
# Run this ON THE RELAY VPS as root/sudo, after Caddy + the autossh tunnel
# are both up and reachable (see README steps 5-7). Re-running is safe —
# it removes any prior rule this script added before re-adding it, so it
# won't pile up duplicate rules on a second run.
#
# CHANGE ME before running:
set -euo pipefail

SALESCALE_BACKEND_IP="${SALESCALE_BACKEND_IP:-203.0.113.10}"   # CHANGE ME — Salescale backend's egress IP (see README "Finding the backend's egress IP")
TUNNEL_PORT="${TUNNEL_PORT:-12345}"                            # must match Caddyfile + autossh -R port
RELAY_HTTPS_PORT=443
SSH_PORT=22

if [ "${SALESCALE_BACKEND_IP}" = "203.0.113.10" ]; then
	echo "Refusing to run with the placeholder IP — edit SALESCALE_BACKEND_IP first." >&2
	exit 1
fi

if ! command -v ufw >/dev/null 2>&1; then
	echo "ufw not found. This script targets ufw (Ubuntu default)." >&2
	echo "See the commented iptables fallback at the bottom of this file." >&2
	exit 1
fi

echo "== Resetting any prior rule this script added for port ${RELAY_HTTPS_PORT} =="
# Idempotent-ish: ufw has no "replace rule", so delete-then-add. `|| true`
# because the delete fails harmlessly the first time there's nothing to
# delete yet.
sudo ufw delete allow from "${SALESCALE_BACKEND_IP}" to any port "${RELAY_HTTPS_PORT}" proto tcp 2>/dev/null || true
sudo ufw delete allow "${RELAY_HTTPS_PORT}/tcp" 2>/dev/null || true   # in case an earlier "allow 443 from anywhere" rule exists — this relay must NEVER be open to the world

echo "== Allowing HTTPS on ${RELAY_HTTPS_PORT} ONLY from Salescale's backend (${SALESCALE_BACKEND_IP}) =="
sudo ufw allow from "${SALESCALE_BACKEND_IP}" to any port "${RELAY_HTTPS_PORT}" proto tcp comment 'salescale-backend-only: imessage relay'

echo "== SSH stays reachable (needed for both admin access and the reverse tunnel itself) =="
echo "   Restricting WHO can do anything useful over it is handled by authorized_keys"
echo "   (README step 3 — restrict,permitlisten=...), not by ufw source-IP limits,"
echo "   since the Mac's home IP is typically dynamic/unknown in advance."
sudo ufw limit "${SSH_PORT}/tcp"   # `limit` throttles repeat connection attempts (basic brute-force mitigation)

echo "== Explicitly denying the tunnel port from anything but loopback =="
# Caddy reaches the tunnel endpoint via 127.0.0.1:${TUNNEL_PORT}, which never
# transits ufw's INPUT-from-network path. This deny rule is defense in
# depth only, in case autossh's -R ever gets misconfigured with
# GatewayPorts and binds 0.0.0.0 instead of loopback (see README
# "GatewayPorts must stay off" for why that would be bad).
sudo ufw deny "${TUNNEL_PORT}/tcp" comment 'imessage relay: tunnel port must stay loopback-only'

echo "== Default posture: deny incoming, allow outgoing (only if not already set) =="
sudo ufw default deny incoming
sudo ufw default allow outgoing

echo "== Enabling =="
sudo ufw --force enable
sudo ufw status verbose

cat <<'EOF'

NOTE — if this relay runs on the SAME VPS as the Salescale backend
(deploy/docker-compose.traefik.yml), read README.md "Same-VPS variant"
before running this script: the backend-to-relay call may never cross the
public NIC at all (loopback, or a Docker bridge hop), in which case the
IP-restriction rule above is inert rather than wrong — the real boundary in
that topology is the Docker network attachment, not ufw. This script still
does no harm to run (it only tightens things further) but confirm you know
which topology you're in before assuming the IP allowlist is the only thing
protecting this endpoint.
EOF

# =============================================================================
# iptables fallback (commented — use only if ufw is unavailable/disabled on
# this distro; do not run both ufw and raw iptables rules at once, they
# conflict). Same intent as the ufw rules above, expressed directly:
# =============================================================================
#
# BACKEND_IP="203.0.113.10"     # CHANGE ME
# TUNNEL_PORT=12345
#
# iptables -N SALESCALE_RELAY 2>/dev/null || iptables -F SALESCALE_RELAY
# iptables -A SALESCALE_RELAY -p tcp --dport 443 -s "$BACKEND_IP" -j ACCEPT
# iptables -A SALESCALE_RELAY -p tcp --dport 443 -j DROP
# iptables -A SALESCALE_RELAY -p tcp --dport "$TUNNEL_PORT" -j DROP
# iptables -C INPUT -j SALESCALE_RELAY 2>/dev/null || iptables -A INPUT -j SALESCALE_RELAY
#
# # Persist across reboots (Debian/Ubuntu):
# #   sudo apt-get install iptables-persistent
# #   sudo netfilter-persistent save
#
# # ip6tables: mirror the same three rules if this host has a public IPv6
# # address — an IPv4-only allowlist leaves the same port open over IPv6.
