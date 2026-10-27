#!/usr/bin/env bash
# Run the e2e API test and the browser test against the public test deployment
# (deploy/dev-up.sh --public <domain>), through real DNS, TLS and the password gate.
#
#   tests/e2e/public.sh miira-apps.dk
#
# The stack must run with the fake model (SLOPTOWN_OPENAI_BASE_URL pointing at
# sloptown-fake-openai) for step 5 to find its marker; see docs/deploy-public-test.md.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DOMAIN="${1:?usage: tests/e2e/public.sh <domain>}"
set -a; . "$ROOT/deploy/.public.env"; set +a
export E2E_DOMAIN="$DOMAIN" E2E_GATE_PASSWORD="$GATE_PASSWORD"
podman run --rm --network host -v "$ROOT:/src:Z" -v sloptown-uv-cache:/root/.cache/uv -w /src/tests/e2e \
  -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e E2E_DOMAIN -e E2E_GATE_PASSWORD ghcr.io/astral-sh/uv:python3.13-bookworm-slim \
  uv run --no-project --with pytest --with httpx pytest -q -p no:cacheprovider ${E2E_PYTEST_ARGS:-} test_e2e.py
"$ROOT/tests/e2e/browser.sh"
