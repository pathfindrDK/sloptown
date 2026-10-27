#!/usr/bin/env bash
# Run every SlopTown test suite. Everything runs in containers; the host only needs podman.
#
#   scripts/test-all.sh            unit suites (~3 min): control plane, egress, fake-miira,
#                                  SDKs, template, studio, vibe-agentd, design sync
#   scripts/test-all.sh --full     also the container-level suites (~15 min): edge config,
#                                  image smoke test, platform smoke, real-Podman integration,
#                                  the end-to-end API test of the whole stack, and a browser
#                                  test of the studio embedded in fake Miira
#   ONLY="studio sdk-js" scripts/test-all.sh   run just the named suites
#
# Unit suites need the test Postgres on 127.0.0.1:55432 (started here if missing).
# --full builds images and starts the dev stack on 127.0.0.1:8080 (left running).
# Prints a summary table; exit code is non-zero if any suite failed.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
FULL=0
[ "${1:-}" = "--full" ] && FULL=1

UV=ghcr.io/astral-sh/uv:python3.13-bookworm-slim
NODE=docker.io/library/node:22-bookworm-slim
PY=(podman run --rm --network host -v "$ROOT:/src:Z" -v sloptown-uv-cache:/root/.cache/uv -e UV_PROJECT_ENVIRONMENT=/tmp/venv)
JS=(podman run --rm -v "$ROOT:/src:Z" -v sloptown-npm-cache:/root/.npm)
LOGDIR="${TMPDIR:-/tmp}/sloptown-tests"
mkdir -p "$LOGDIR"
declare -a RESULTS=()
FAILED=0

suite() { # name command...
  local name="$1"; shift
  if [ -n "${ONLY:-}" ] && [[ " $ONLY " != *" $name "* ]]; then return; fi
  local log="$LOGDIR/$name.log" start=$SECONDS
  printf '%-22s ' "$name"
  if "$@" >"$log" 2>&1; then
    RESULTS+=("ok    $name ($((SECONDS - start))s)"); echo "ok"
  else
    RESULTS+=("FAIL  $name ($((SECONDS - start))s)  log: $log"); echo "FAIL (log: $log)"
    tail -15 "$log" | sed 's/^/    /'
    FAILED=1
  fi
}

if ! podman container exists sloptown-test-db; then
  podman run -d --name sloptown-test-db -e POSTGRES_USER=sloptown -e POSTGRES_PASSWORD=sloptown \
    -e POSTGRES_DB=sloptown -p 127.0.0.1:55432:5432 docker.io/library/postgres:17 >/dev/null
  sleep 3
else
  podman start sloptown-test-db >/dev/null
fi

suite control-plane "${PY[@]}" -w /src/control-plane $UV uv run -q pytest -q -p no:cacheprovider
suite fake-miira    "${PY[@]}" -w /src/fake-miira $UV uv run -q pytest -q -p no:cacheprovider
suite sdk-python    "${PY[@]}" -w /src/sdk/python $UV uv run -q pytest -q -p no:cacheprovider
suite sdk-js        "${JS[@]}" -w /src/sdk/js $NODE sh -c 'npm ci --silent && npm test'
suite template-web  "${JS[@]}" -w /src/template/web $NODE sh -c 'npm ci --silent && npm run sdk:link && npm run build'
suite template-api  "${PY[@]}" -w /src/template $UV uv run -q --no-project --with-requirements api/requirements.lock \
                      --with ../sdk/python --with pytest --with respx pytest -q -p no:cacheprovider tests
suite studio        "${JS[@]}" -w /src/studio $NODE sh -c 'npm ci --silent && npm run typecheck && npm test && npm run build'
suite vibe-agentd   "${PY[@]}" -w /src/images/vibe-agentd $UV sh -c \
                      'apt-get -qq update && apt-get -qq install -y git >/dev/null && uv run -q pytest -q -p no:cacheprovider'
suite design-sync   design/test/test-sync.sh
suite edge-gate     "${PY[@]}" -w /src/edge $UV uv run -q --no-project --with pytest pytest -q -p no:cacheprovider test/test_gate.py

if [ "$FULL" = 1 ]; then
  # e2e first: dev-up.sh builds every localhost/sloptown/*:dev image the others use.
  suite e2e           tests/e2e/run.sh
  suite browser       tests/e2e/browser.sh
  suite edge          edge/test/validate.sh
  suite images-smoke  env VIBE_DEV_IMAGE=localhost/sloptown/vibe-dev:dev \
                        VIBE_RUNTIME_IMAGE=localhost/sloptown/vibe-runtime:dev images/test/smoke.sh
  suite platform      deploy/test/platform-smoke.sh
  # rootful socket path; for rootless use $XDG_RUNTIME_DIR/podman/podman.sock
  suite podman-it     "${PY[@]}" -v /run/podman/podman.sock:/run/podman/podman.sock --security-opt label=disable \
                        -w /src/control-plane -e SLOPTOWN_PODMAN_IT=1 $UV uv run -q pytest -q -p no:cacheprovider \
                        tests/test_podman_integration.py
fi

echo
echo "== summary"
printf '%s\n' "${RESULTS[@]}"
exit $FAILED
