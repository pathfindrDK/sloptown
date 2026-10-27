#!/usr/bin/env bash
# Run tests/e2e/browser.cjs (real studio inside fake Miira) in the Playwright container, or
# browser_miira3.cjs (inside the real Miira) when E2E_MIIRA_ORIGIN is set.
# Needs the dev stack on 127.0.0.1:8080 (deploy/dev-up.sh or tests/e2e/run.sh).
# Screenshots: tests/e2e/out/browser-*.png
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$ROOT/tests/e2e/out"
if [ -n "${E2E_MIIRA_ORIGIN:-}" ]; then SPEC=browser_miira3.cjs; else SPEC=browser.cjs; fi
# Local stack: host network to reach 127.0.0.1:8080. Public domain: own network namespace,
# reaching the site via public DNS like a real visitor. (With host networking Chrome aborts
# requests with ERR_NETWORK_CHANGED whenever podman adds a project network interface.)
if [ -n "${E2E_DOMAIN:-}" ]; then NETOPT=(--network podman); else NETOPT=(--network host); fi
podman run --rm "${NETOPT[@]}" --ipc host -v "$ROOT/tests/e2e:/e2e:Z" -v sloptown-npm-cache:/root/.npm \
  -e OUT=/e2e/out -e NODE_PATH=/tmp/pw/node_modules -e E2E_DOMAIN="${E2E_DOMAIN:-}" -e E2E_GATE_PASSWORD="${E2E_GATE_PASSWORD:-}" \
  -e E2E_MIIRA_ORIGIN="${E2E_MIIRA_ORIGIN:-}" -e E2E_STUDIO_ORIGIN="${E2E_STUDIO_ORIGIN:-}" -e SPEC="$SPEC" -e E2E_BROWSER="${E2E_BROWSER:-}" \
  mcr.microsoft.com/playwright:v1.49.1-noble \
  sh -c 'mkdir -p /tmp/pw && cd /tmp/pw && npm init -y >/dev/null && npm i --silent playwright@1.49.1 && node /e2e/$SPEC'
