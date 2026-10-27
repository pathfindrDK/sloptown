#!/usr/bin/env bash
# Smoke test for vibe-dev and vibe-runtime, run with the production hardening
# (read-only root, tmpfs /tmp, all capabilities dropped, no-new-privileges).
#
#   images/test/smoke.sh
#   VIBE_DEV_IMAGE=... VIBE_RUNTIME_IMAGE=... images/test/smoke.sh   (pipeline)
#
# dev:     seeds /workspace, git history, locked platform files, agentd /health
#          and /files, Vite serves the app (incl. @miira/vibe), API on :8000,
#          opencode serve answers.
# runtime: vibe-build of the template (in vibe-dev), then vibe-runtime serves
#          /healthz, index.html and the API under /api.
set -euo pipefail
DEV="${VIBE_DEV_IMAGE:-localhost/sloptown/vibe-dev:latest}"
RUN="${VIBE_RUNTIME_IMAGE:-localhost/sloptown/vibe-runtime:latest}"
P=vibesmoke
pass=0; fail=0
check() { if eval "$2"; then echo "  ok   $1"; pass=$((pass+1)); else echo "  FAIL $1"; fail=$((fail+1)); fi; }
HARD=(--read-only --tmpfs /tmp:rw,exec,mode=1777 --cap-drop ALL --security-opt no-new-privileges --pids-limit 512 --memory 2g)
cleanup() {
  podman rm -f -t 0 $P-dev $P-run >/dev/null 2>&1 || true
  podman volume rm -f $P-ws $P-pub $P-data >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

# GET inside a container with its own python: prints "<status> <body prefix>".
# AUTH overrides the Authorization header (default: bearer project token).
get() {
  podman exec "$1" python -c "
import sys, urllib.request, urllib.error
req = urllib.request.Request('$2', headers={'Authorization': '${AUTH:-Bearer smoke-token}', 'Accept': 'text/html,application/json'})
try:
    r = urllib.request.urlopen(req, timeout=5); print(r.status, r.read().decode(errors='replace')[:${3:-400}])
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode(errors='replace')[:200])
except Exception as e:
    print('ERR', e)"
}
wait_for() { # container url seconds
  for _ in $(seq "$3"); do [[ "$(get "$1" "$2" 20)" == 200* ]] && return 0; sleep 1; done; return 1
}

echo "== vibe-dev ($DEV)"
podman volume create $P-ws >/dev/null
podman run -d --name $P-dev "${HARD[@]}" -v $P-ws:/workspace:U \
  -e VIBE_PROJECT_TOKEN=smoke-token -e VIBE_PROJECT_ID=smoke -e VIBE_PLATFORM_URL=http://127.0.0.1:9 \
  -e OPENAI_BASE_URL=http://127.0.0.1:9/v1 -e OPENAI_API_KEY=smoke-token "$DEV" >/dev/null
check "agentd /health" 'wait_for $P-dev http://127.0.0.1:4097/health 60'
check "vite serves index.html" 'wait_for $P-dev http://127.0.0.1:5173/ 90'
r=$(get $P-dev http://127.0.0.1:5173/src/main.tsx 2000)
check "vite transforms main.tsx with @miira/vibe" '[[ "$r" == 200* && "$r" == *"@miira/vibe"* || "$r" == 200*"/node_modules/.vite/deps/@miira_vibe"* ]]'
dep=$(grep -oE '/node_modules/\.vite/deps/@miira_vibe[^"'"'"']*' <<<"$r" | head -1 || true)
[ -n "$dep" ] && check "vite serves prebundled @miira/vibe" '[[ "$(get $P-dev "http://127.0.0.1:5173$dep" 20)" == 200* ]]'
r=$(get $P-dev http://127.0.0.1:5173/node_modules/@miira/vibe/dist/theme.css 50)
check "vite serves @miira/vibe/theme.css" '[[ "$r" == 200* ]]'
check "api :8000 up" 'wait_for $P-dev http://127.0.0.1:8000/docs 60'
OC_AUTH="Basic $(printf opencode:smoke-token | base64)"
check "opencode serve :4096 up (basic auth)" 'AUTH="$OC_AUTH" wait_for $P-dev http://127.0.0.1:4096/config 60'
check "opencode rejects missing auth" '[[ "$(AUTH=none get $P-dev http://127.0.0.1:4096/config 20)" == 401* ]]'
check "opencode uses the SlopTown provider" '[[ "$(AUTH="$OC_AUTH" get $P-dev http://127.0.0.1:4096/config 4000)" == *"sloptown/gpt-6.1-sol"* ]]'
r=$(get $P-dev http://127.0.0.1:4097/files 4000)
check "agentd /files lists template, AGENTS.md locked" '[[ "$r" == 200* && "$r" == *"\"path\":\"AGENTS.md\",\"size\":"*"\"locked\":true"* ]]'
check "agentd /files hides node_modules and .git" '[[ "$r" != *node_modules* && "$r" != *".git/"* ]]'
r=$(podman exec $P-dev git -C /workspace log --format=%s)
check "workspace seeded with initial commit" '[[ "$r" == "Start fra skabelon" ]]'
check "platform files read-only" '! podman exec $P-dev sh -c "echo x >> /workspace/web/package.json" 2>/dev/null'
check "root filesystem read-only" '! podman exec $P-dev touch /opt/vibe/x 2>/dev/null'
check "/data is the project volume" 'podman exec $P-dev sh -c "echo 1 > /data/probe && test -f /workspace/.data/probe"'
check "git status clean after start" '[ -z "$(podman exec $P-dev git -C /workspace status --porcelain)" ]'
podman restart -t 5 $P-dev >/dev/null
check "opencode stays up (no crash loop)" '[ "$(podman logs $P-dev 2>&1 | grep -c "opencode: exited")" = 0 ]'
check "restart keeps history (no reseed)" 'wait_for $P-dev http://127.0.0.1:4097/health 60 && [ "$(podman exec $P-dev git -C /workspace rev-list --count HEAD)" = 1 ]'

echo "== vibe-build + vibe-runtime ($RUN)"
# Publish-like snapshot: git archive of HEAD into a fresh volume, built by vibe-dev.
podman volume create $P-pub >/dev/null
podman exec $P-dev git -C /workspace archive --format=tar HEAD | podman volume import $P-pub -
r=$(podman run --rm "${HARD[@]}" -v $P-pub:/srv/app:U --entrypoint vibe-build "$DEV" /srv/app 2>&1 || true); r=$(tail -1 <<<"$r")
check "vibe-build produces web/dist" '[[ "$r" == "vibe-build: ok"* ]]'
podman volume create $P-data >/dev/null
podman run -d --name $P-run "${HARD[@]}" -v $P-pub:/srv/app:ro -v $P-data:/data:U \
  -e VIBE_PROJECT_TOKEN=smoke-token -e VIBE_PROJECT_ID=smoke -e VIBE_PLATFORM_URL=http://127.0.0.1:9 "$RUN" >/dev/null
check "runtime /healthz" 'wait_for $P-run http://127.0.0.1:8000/healthz 30'
r=$(get $P-run http://127.0.0.1:8000/healthz)
check "runtime has api and web" '[[ "$r" == *"\"api\":true"*"\"web\":true"* ]]'
r=$(get $P-run http://127.0.0.1:8000/some/client/route)
check "runtime serves SPA index" '[[ "$r" == 200*"<div id=\"root\">"* ]]'
r=$(get $P-run http://127.0.0.1:8000/api/openapi.json)
check "runtime mounts the API under /api" '[[ "$r" == 200* ]]'
check "runtime has no node, git or uv" '! podman exec $P-run sh -c "command -v node || command -v git || command -v uv"'
check "runtime can write /data only" 'podman exec $P-run sh -c "touch /data/x" && ! podman exec $P-run touch /srv/app/x 2>/dev/null'

echo "== $pass passed, $fail failed"
if [ "$fail" != 0 ]; then
  echo "--- $P-dev logs"; podman logs --tail 40 $P-dev 2>&1
  echo "--- $P-run logs"; podman logs --tail 20 $P-run 2>&1 || true
  exit 1
fi
