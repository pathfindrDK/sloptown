#!/usr/bin/env bash
# Wait until the platform containers answer their health endpoints (60 s max).
#   deploy/test/wait-healthy.sh [prefix]     container names are <prefix>-db, -cp, ...
set -euo pipefail
P="${1:-sloptown}"
probe() { # container url
  podman exec "$1" python -c "import urllib.request,sys; urllib.request.urlopen('$2', timeout=2)" >/dev/null 2>&1
}
for _ in $(seq 60); do
  ok=1
  probe "$P-cp" http://127.0.0.1:8000/health || ok=0
  probe "$P-egress" http://127.0.0.1:8200/health || ok=0
  # fake-miira is absent when a real Miira is the IdP (SLOPTOWN_MIIRA_ORIGIN).
  if podman container exists "$P-fake-miira"; then
    probe "$P-fake-miira" http://127.0.0.1:8100/healthz || ok=0
  fi
  [ "$ok" = 1 ] && { echo "healthy: $P-cp $P-egress"; exit 0; }
  sleep 1
done
for c in cp egress fake-miira; do podman container exists "$P-$c" || continue; echo "--- $P-$c logs"; podman logs --tail 20 "$P-$c" 2>&1; done
exit 1
