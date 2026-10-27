#!/usr/bin/env bash
# Platform smoke test: boot db + control plane + egress + fake-miira from built
# images on a throwaway network (no podman socket, orchestrator off) and check
# they come up and answer. Also dry-runs the quadlet units through podman's
# quadlet generator.
#   CP_IMAGE=... FAKE_MIIRA_IMAGE=... deploy/test/platform-smoke.sh
# Defaults build both images from the working tree.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
P=stsmoke
NET=$P-net
pass=0; fail=0
check() { if eval "$2"; then echo "  ok   $1"; pass=$((pass+1)); else echo "  FAIL $1"; fail=$((fail+1)); fi; }
cleanup() { podman rm -f -t 0 $P-db $P-cp $P-egress $P-fake-miira >/dev/null 2>&1 || true; podman network rm -f $NET >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

echo "== quadlet dry-run"
QDIR="$(mktemp -d)"
cp "$ROOT"/deploy/quadlet/* "$QDIR/"
if out=$(QUADLET_UNIT_DIRS="$QDIR" /usr/libexec/podman/quadlet -dryrun -user 2>&1); then
  n=$(grep -c '^---.*---$' <<<"$out" || true)
  check "quadlet generates units ($n)" '[ "$n" -ge 9 ]'
else
  echo "$out" | tail -5; check "quadlet generates units" false
fi
rm -rf "$QDIR"

if [ -z "${CP_IMAGE:-}" ]; then
  CP_IMAGE=localhost/sloptown/control-plane:smoke
  podman build -q -t $CP_IMAGE -f "$ROOT/deploy/containers/control-plane.Containerfile" "$ROOT" >/dev/null
fi
if [ -z "${FAKE_MIIRA_IMAGE:-}" ]; then
  FAKE_MIIRA_IMAGE=localhost/sloptown/fake-miira:smoke
  podman build -q -t $FAKE_MIIRA_IMAGE -f "$ROOT/deploy/containers/fake-miira.Containerfile" "$ROOT" >/dev/null
fi

echo "== boot"
podman network create $NET >/dev/null
podman run -d --name $P-db --network $NET -e POSTGRES_USER=sloptown -e POSTGRES_PASSWORD=sloptown -e POSTGRES_DB=sloptown \
  docker.io/library/postgres:17 >/dev/null
for _ in $(seq 30); do podman exec $P-db pg_isready -q -U sloptown 2>/dev/null && break; sleep 1; done
ENV=(-e SLOPTOWN_DATABASE_URL=postgresql+asyncpg://sloptown:sloptown@$P-db:5432/sloptown
     -e SLOPTOWN_MIIRA_API_BASE=http://$P-fake-miira:8100 -e SLOPTOWN_CONTROL_PLANE_URL=http://$P-cp:8000
     -e SLOPTOWN_ORCHESTRATOR_ENABLED=false -e SLOPTOWN_BACKGROUND_TASKS=false
     -e SLOPTOWN_EGRESS_ENV_FILE=/etc/sloptown/egress.env)
podman run -d --name $P-fake-miira --network $NET -e FAKE_MIIRA_STUDIO_API_BASE=http://$P-cp:8000 "$FAKE_MIIRA_IMAGE" >/dev/null
podman run -d --name $P-cp --network $NET --read-only --tmpfs /tmp "${ENV[@]}" "$CP_IMAGE" >/dev/null
podman run -d --name $P-egress --network $NET --read-only --tmpfs /tmp "${ENV[@]}" \
  -v "$ROOT/edge/egress.env:/etc/sloptown/egress.env:ro,Z" "$CP_IMAGE" python -m sloptown.egress >/dev/null
check "all healthy" '"$ROOT/deploy/test/wait-healthy.sh" $P >/dev/null'
check "quadlet HealthCmd helper passes for cp and egress" 'podman exec $P-cp sloptown-healthcheck http://127.0.0.1:8000/health && podman exec $P-egress sloptown-healthcheck http://127.0.0.1:8200/health'

get() { # container url -> "status body"
  podman exec "$1" python -c "
import urllib.request, urllib.error
try:
    r = urllib.request.urlopen('$2', timeout=5); print(r.status, r.read().decode()[:300])
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode()[:300])"
}
r=$(get $P-cp http://127.0.0.1:8000/api/me)
check "cp /api/me without session is 401" '[[ "$r" == 401* ]]'
r=$(get $P-cp http://$P-fake-miira:8100/.well-known/openid-configuration)
check "cp reaches fake-miira discovery" '[[ "$r" == 200*issuer* ]]'
r=$(get $P-egress http://127.0.0.1:8200/v1/models)
check "egress /v1 rejects missing project token" '[[ "$r" == 401* ]]'
r=$(get $P-cp http://127.0.0.1:8000/admin/v1/budget-settings)
check "admin API rejects unsigned call" '[[ "$r" == 401* || "$r" == 403* ]]'
podman exec $P-egress python -c "import socket; socket.create_connection(('127.0.0.1', 8201), 2)" 2>/dev/null
check "egress CONNECT proxy listening on :8201" '[ $? = 0 ]'

echo "== $pass passed, $fail failed"
[ "$fail" = 0 ] || { for c in cp egress; do echo "--- $P-$c"; podman logs --tail 30 $P-$c 2>&1; done; exit 1; }
