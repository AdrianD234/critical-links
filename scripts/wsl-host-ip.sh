#!/usr/bin/env bash
# Print the address WSL can use to reach a service listening on the Windows host.
#
# WSL2 forwards Windows -> WSL on localhost, but NOT the reverse: from inside
# WSL, `localhost` is WSL itself. Under mirrored networking localhost does work
# both ways, so that is tried first.
set -euo pipefail

PORT="${1:-8787}"

try() {
  if curl -sf -m 3 "http://$1:${PORT}/health" > /dev/null 2>&1; then
    echo "$1"
    return 0
  fi
  return 1
}

# Mirrored networking: localhost reaches the host directly.
try 127.0.0.1 && exit 0

# NAT networking: the default gateway is the Windows host.
GW="$(ip route show default | awk '{print $3}' | head -1)"
[ -n "$GW" ] && try "$GW" && exit 0

# Fallback: the resolver address is usually the host under NAT.
NS="$(awk '/^nameserver/ {print $2; exit}' /etc/resolv.conf 2>/dev/null || true)"
[ -n "$NS" ] && try "$NS" && exit 0

echo "could not reach a service on port ${PORT} from WSL" >&2
echo "  tried: 127.0.0.1, gateway ${GW:-none}, resolver ${NS:-none}" >&2
exit 1
