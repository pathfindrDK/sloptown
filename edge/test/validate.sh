#!/usr/bin/env bash
# Edge test: build the edge image, `caddy validate` every config (TLS_MODE acme
# and file), then run the dev config against a mock control plane and mock app
# and check routing, header stripping, forward_auth, admin IP restriction and
# CSP, and the public config with a test wildcard certificate (TLS_MODE=file).
#   edge/test/validate.sh                      (builds edge:test with a placeholder studio)
#   EDGE_IMAGE=<image> edge/test/validate.sh   (tests an already built image; used by the pipeline)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NET=sloptown-edgetest
IMG="${EDGE_IMAGE:-localhost/sloptown/edge:test}"
STUDIO_IMG="${STUDIO_IMAGE:-}"
pass=0; fail=0
ok()  { echo "  ok   $*"; pass=$((pass+1)); }
bad() { echo "  FAIL $*"; fail=$((fail+1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TLSDIR="$(mktemp -d)"
cleanup() { podman rm -f edgetest-edge edgetest-tls edgetest-cp edgetest-app >/dev/null 2>&1 || true; podman network rm -f $NET >/dev/null 2>&1 || true; }
trap 'cleanup; rm -rf "$TLSDIR"' EXIT
cleanup

if [ -n "${EDGE_IMAGE:-}" ]; then
  echo "== using $IMG"
else
if [ -z "$STUDIO_IMG" ]; then
  # Placeholder studio so the edge can be tested without building the SPA.
  STUDIO_IMG=localhost/sloptown/studio:edgetest
  printf 'FROM docker.io/library/caddy:2.11-alpine\nRUN mkdir -p /srv/studio/assets && echo "<!doctype html><title>studio</title><div id=root></div>" > /srv/studio/index.html && echo "x" > /srv/studio/assets/a.js\n' \
    | podman build -q -t "$STUDIO_IMG" -f - "$ROOT" >/dev/null
fi
echo "== build $IMG"
podman build -q -t "$IMG" --build-arg STUDIO_IMAGE="$STUDIO_IMG" -f "$ROOT/edge/Containerfile" "$ROOT" >/dev/null
fi

ENVS=(-e STUDIO_HOST=studio.sloptown.localhost -e APPS_DOMAIN=apps.sloptown.localhost
      -e STUDIO_ORIGIN=http://studio.sloptown.localhost:8080 -e "MIIRA_ORIGINS=http://miira.sloptown.localhost:8080"
      -e CP_UPSTREAM=cp:8000 -e FAKE_MIIRA_UPSTREAM=cp:8000 -e ADMIN_ALLOWED_IPS=192.0.2.1/32 -e ACME_EMAIL=ops@example.com
      -e GATE_DOMAIN=example.test -e GATE_UPSTREAM=gate:9100)

echo "== caddy validate"
for f in Caddyfile Caddyfile.dev Caddyfile.public; do
  if podman run --rm "${ENVS[@]}" "$IMG" caddy validate --adapter caddyfile --config /etc/caddy/$f >/dev/null 2>&1; then
    ok "validate $f"
  else
    bad "validate $f"; podman run --rm "${ENVS[@]}" "$IMG" caddy validate --adapter caddyfile --config /etc/caddy/$f 2>&1 | tail -5
  fi
done
if podman run --rm "${ENVS[@]}" -e GATE_MODE=off "$IMG" caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile.public >/dev/null 2>&1; then
  ok "validate Caddyfile.public with GATE_MODE=off (real Miira)"
else
  bad "validate Caddyfile.public with GATE_MODE=off"
fi

# Self-signed stand-in for the real wildcard certificate (TLS_MODE=file).
podman run --rm -v "$TLSDIR:/out:z" docker.io/alpine/openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
  -subj '/CN=*.example.test' -addext 'subjectAltName=DNS:*.example.test,DNS:example.test' \
  -keyout /out/priv.pem -out /out/fullchain.pem >/dev/null 2>&1
chmod 644 "$TLSDIR"/*.pem
TLSV=(-e TLS_MODE=file -v "$TLSDIR:/etc/caddy/tls:ro,z")
for f in Caddyfile Caddyfile.public; do
  if podman run --rm "${ENVS[@]}" "${TLSV[@]}" "$IMG" caddy validate --adapter caddyfile --config /etc/caddy/$f >/dev/null 2>&1; then
    ok "validate $f with TLS_MODE=file"
  else
    bad "validate $f with TLS_MODE=file"; podman run --rm "${ENVS[@]}" "${TLSV[@]}" "$IMG" caddy validate --adapter caddyfile --config /etc/caddy/$f 2>&1 | tail -5
  fi
done

echo "== behaviour (Caddyfile.dev against mocks)"
podman network create $NET >/dev/null
MOCK=(-v "$ROOT/edge/test/mock.py:/mock.py:ro,Z" docker.io/library/python:3.13-slim-bookworm python -u /mock.py)
podman run -d --name edgetest-cp --network $NET --network-alias cp "${MOCK[@]}" cp >/dev/null
podman run -d --name edgetest-app --network $NET --network-alias mockapp "${MOCK[@]}" app >/dev/null
podman run -d --name edgetest-edge --network $NET "${ENVS[@]}" "$IMG" caddy run --adapter caddyfile --config /etc/caddy/Caddyfile.dev >/dev/null
sleep 2

# curl from a client container on the same network, Host header selects the site.
c() { podman run --rm --network $NET docker.io/curlimages/curl:8.11.1 -s --max-time 5 "$@"; }
E=http://edgetest-edge:8080

r=$(c -i -H 'Host: studio.sloptown.localhost:8080' $E/projects/abc)
# matches the placeholder (id=root) and a real studio build (id="root")
check "studio SPA fallback serves index.html" '[[ "$r" =~ \<div\ id=\"?root ]]'
check "studio CSP frame-ancestors" '[[ "$r" == *"frame-ancestors '"'"'self'"'"' http://miira.sloptown.localhost:8080"* ]]'
r=$(c -H 'Host: studio.sloptown.localhost:8080' $E/api/me)
check "studio /api proxied to control plane" '[[ "$r" == *"\"cp\": \"/api/me\""* ]]'
r=$(c -o /dev/null -w '%{http_code}' -H 'Host: studio.sloptown.localhost:8080' $E/admin/v1/budget-settings)
check "admin API 403 from non-allowlisted IP" '[[ "$r" == 403 ]]'
r=$(c -H 'Host: studio.sloptown.localhost:8080' $E/internal/images/rolled)
check "/internal not proxied from studio host" '[[ "$r" != *"\"cp\""* ]]'

r=$(c -o /dev/null -w '%{http_code} %{redirect_url}' -H 'Host: myapp.apps.sloptown.localhost:8080' $E/)
check "app without session redirects to login" '[[ "$r" == "302 http://studio.sloptown.localhost:8080/auth/login" ]]'
r=$(c -i -H 'Host: myapp.apps.sloptown.localhost:8080' "$E/_vibe/session?code=abc&next=/")
check "/_vibe/session rewritten to cp /edge/session" '[[ "$r" == *"X-Seen-Path: /edge/session?code=abc&next=/"* ]]'
r=$(c -i -H 'Host: myapp.apps.sloptown.localhost:8080' -H 'Cookie: vibe_app=ok' \
      -H 'X-Vibe-User: spoofed' -H 'X-Vibe-Role: owner' -H 'X-Vibe-Upstream: evil:1' -H 'X-Sloptown-Internal: x' $E/hello)
check "app request reaches upstream chosen by cp" '[[ "$r" == *"\"path\": \"/hello\""* ]]'
check "spoofed X-Vibe-User replaced by forward_auth" '[[ "$r" == *"\"x-vibe-user\": \"member-1\""* && "$r" != *spoofed* ]]'
check "role comes from cp" '[[ "$r" == *"\"x-vibe-role\": \"viewer\""* ]]'
check "edge secret passed to app" '[[ "$r" == *"\"x-vibe-edge-secret\": \"edge-secret\""* ]]'
check "X-Vibe-Upstream not leaked to app" '[[ "$r" != *"x-vibe-upstream"* ]]'
check "X-Sloptown-* stripped" '[[ "$r" != *"x-sloptown-internal"* ]]'
check "app CSP frame-ancestors" '[[ "$r" == *"frame-ancestors http://studio.sloptown.localhost:8080 http://miira.sloptown.localhost:8080"* ]]'
r=$(c -H 'Host: preview-myapp.apps.sloptown.localhost:8080' -H 'Cookie: vibe_view_as=viewer' -H 'X-Vibe-View-As: owner' $E/)
check "preview uses /edge/auth/preview and view-as cookie" '[[ "$r" == *"\"x-vibe-user\": \"dev-viewer\""* ]]'
r=$(c -H 'Host: preview-myapp.apps.sloptown.localhost:8080' -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
      -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' $E/)
check "WebSocket upgrade (Vite HMR) passes forward_auth" '[[ "$r" == *"\"upgrade\": \"websocket\""* ]]'
r=$(c -H 'Host: myapp.apps.sloptown.localhost:8080' -H 'Cookie: sloptown_gate=123.abc; vibe_app=ok; theme=dark' $E/)
check "public-test gate cookie never reaches the app" '[[ "$r" != *"sloptown_gate"* && "$r" == *"vibe_app=ok"* && "$r" == *"theme=dark"* ]]'
check "X-Vibe-View-As not leaked to app" '[[ "$r" != *"x-vibe-view-as"* ]]'

echo "== wildcard certificate (Caddyfile.public, TLS_MODE=file)"
podman run -d --name edgetest-tls --network $NET "${ENVS[@]}" "${TLSV[@]}" -e GATE_MODE=off \
  -e APPS_DOMAIN=example.test -e STUDIO_HOST=studio.example.test \
  "$IMG" caddy run --adapter caddyfile --config /etc/caddy/Caddyfile.public >/dev/null
sleep 2
for h in myapp.example.test preview-myapp.example.test studio.example.test miira.example.test example.test; do
  r=$(c -k -v -o /dev/null --connect-to "$h:443:edgetest-tls:443" "https://$h/" 2>&1 || true)
  check "$h served the wildcard certificate" '[[ "$r" == *"subject: CN=*.example.test"* ]]'
done
r=$(podman logs edgetest-tls 2>&1)
check "no ACME issuance attempted" '[[ "$r" != *"obtaining certificate"* && "$r" != *"acme"*"challenge"* ]]'

echo "== $pass passed, $fail failed"
[ "$fail" -eq 0 ]
