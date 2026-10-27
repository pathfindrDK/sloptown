#!/usr/bin/env bash
# End-to-end test: bring up the dev stack with a scripted fake OpenAI and drive it through the edge.
#
#   tests/e2e/run.sh [--no-build]        extra pytest args via E2E_PYTEST_ARGS, e.g. "-x -k publish"
#
# --no-build  reuse the localhost/sloptown/*:dev images (rebuild after code changes!)
# The stack stays up afterwards (deploy/dev-up.sh --down stops it). Each run
# creates a new Vibe; old test projects stay until removed by hand.
# Needs: podman (rootful or rootless with its socket running), port 127.0.0.1:8080 free.
# Touches no firewall or system config. Logs of the platform containers are
# printed when a step fails.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
UV=ghcr.io/astral-sh/uv:python3.13-bookworm-slim
BUILD_ARGS=()
for a in "$@"; do
  case "$a" in
    --no-build) BUILD_ARGS+=(--no-build) ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done

podman network exists sloptown || podman network create --label sloptown=1 sloptown >/dev/null
podman rm -f sloptown-fake-openai >/dev/null 2>&1 || true
podman run -d --name sloptown-fake-openai --network sloptown --label sloptown=1 \
  -v "$ROOT/tests/e2e/fake_openai.py:/fake_openai.py:ro,Z" $UV python /fake_openai.py 8300 >/dev/null

# Own database volume, so the interactive dev stack's data never affects the test.
SLOPTOWN_DEV_DB_VOLUME=sloptown-e2e-db \
SLOPTOWN_OPENAI_BASE_URL=http://sloptown-fake-openai:8300/v1 SLOPTOWN_OPENAI_API_KEY=sk-e2e-fake \
  deploy/dev-up.sh "${BUILD_ARGS[@]}"

set +e
podman run --rm --network host -v "$ROOT:/src:Z" -v sloptown-uv-cache:/root/.cache/uv -w /src/tests/e2e \
  -e UV_PROJECT_ENVIRONMENT=/tmp/venv $UV \
  uv run --no-project --with pytest --with httpx pytest -q -p no:cacheprovider ${E2E_PYTEST_ARGS:-} test_e2e.py
rc=$?
set -e

if [ "$rc" != 0 ]; then
  for c in cp egress edge fake-miira fake-openai; do echo "--- sloptown-$c (last 40 lines)"; podman logs --tail 40 "sloptown-$c" 2>&1; done
  for c in $(podman ps -a --format '{{.Names}}' --filter label=sloptown.kind=dev | grep '^vibe-dev-'); do
    echo "--- $c (last 40 lines)"; podman logs --tail 40 "$c" 2>&1
  done
fi
echo "e2e exit code: $rc"
exit "$rc"
