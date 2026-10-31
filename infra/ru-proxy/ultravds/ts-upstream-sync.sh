#!/usr/bin/env bash
# UltraVDS: regenerate the ru.interoves.com upstream list from the tailnet.
#
# Every online peer tagged $TAG becomes an upstream server, so a replaced or
# added AWS gateway is picked up within one timer tick and no IP is pinned.
# Uses only the local `tailscale status`, no API token.
#
# If no tagged peer is online the previous file is kept: an empty upstream
# block would make Nginx refuse to reload, and the old entry may still work.
set -euo pipefail

TAG="${TAG:-tag:interoves-ru-gw}"
PORT="${PORT:-80}"
TARGET="${TARGET:-/etc/nginx/interoves-upstreams.conf}"
# Optional: keep today's EC2 node as a last-resort backup during migration,
# e.g. FALLBACK=100.93.29.79. Remove once the gateway has proven itself.
FALLBACK="${FALLBACK:-}"

peers="$(tailscale status --json | jq -r --arg tag "$TAG" '
    [.Peer[]? | select(.Online == true) | select((.Tags // []) | index($tag))
     | (.TailscaleIPs[]? | select(test("^100\\.")))] | sort | unique | .[]')"

# Idle tailnet peers need a fresh WireGuard handshake over DERP, which can
# outlast proxy_connect_timeout. Ping every upstream each run so a failover
# lands on a warm path.
for ip in $peers $FALLBACK; do
    tailscale ping -c 1 --timeout 5s "$ip" >/dev/null 2>&1 || true
done

if [[ -z "$peers" ]]; then
    logger -t ts-upstream-sync "no online peers tagged ${TAG}; keeping ${TARGET}"
    exit 0
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
for ip in $peers; do
    # A vanished tailnet peer times out instead of refusing; keep it marked
    # down for 30 s so only one request per window pays the connect timeout.
    echo "server ${ip}:${PORT} max_fails=1 fail_timeout=30s;" >>"$tmp"
done
if [[ -n "$FALLBACK" ]] && ! grep -q "^server ${FALLBACK}:" "$tmp"; then
    # Never mark the last resort down: "no live upstreams" is a 502 for all.
    echo "server ${FALLBACK}:${PORT} backup max_fails=0;" >>"$tmp"
fi

if [[ -f "$TARGET" ]] && cmp -s "$tmp" "$TARGET"; then
    exit 0
fi

cp "$TARGET" "${TARGET}.prev" 2>/dev/null || true
install -m 0644 "$tmp" "$TARGET"
if nginx -t -q; then
    systemctl reload nginx
    logger -t ts-upstream-sync "upstreams updated: $(tr '\n' ' ' <"$TARGET")"
else
    logger -t ts-upstream-sync "nginx -t failed; restoring previous upstreams"
    [[ -f "${TARGET}.prev" ]] && install -m 0644 "${TARGET}.prev" "$TARGET"
    exit 1
fi
