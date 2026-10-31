#!/usr/bin/env bash
# Bring up the whole SlopTown stack with podman: locally (dev) or on a real domain (public test).
#
#   deploy/dev-up.sh [--no-build] [--down]
#   deploy/dev-up.sh --public <domain> [--no-build]
#
# Builds all images as localhost/sloptown/<name>:dev (unless --no-build), then
# runs on network `sloptown`: sloptown-db, sloptown-fake-miira, sloptown-cp,
# sloptown-egress and sloptown-edge.
#
# Local (default): Caddyfile.dev, plain http on 127.0.0.1:8080 for *.sloptown.localhost.
#   Open http://studio.sloptown.localhost:8080 (tunnel 8080 over SSH when remote).
# Public test (--public miira-apps.dk): Caddyfile.public on :80/:443 with Let's Encrypt,
#   studio.<domain>, miira.<domain> (fake Miira), <slug>.<domain> and preview-<slug>.<domain>,
#   everything behind the password gate (edge/gate.py, container sloptown-gate).
#   Needs DNS: <domain> and *.<domain> -> this host. Secrets and the gate password are
#   generated once into deploy/.public.env (gitignored, mode 600). See docs/deploy-public-test.md.
#   TLS: if $SLOPTOWN_TLS_DIR (default /etc/sloptown/tls) holds fullchain.pem and priv.pem,
#   that wildcard certificate (*.<domain> and <domain>) is mounted into the edge and used for
#   every host (TLS_MODE=file); otherwise Caddy gets one Let's Encrypt certificate per host.
#   SLOPTOWN_TLS_DIR= (empty) forces Let's Encrypt.
#
# Real Miira instead of fake-miira (either mode): set SLOPTOWN_MIIRA_ORIGIN to Miira's
#   public origin (e.g. https://staging.miira.dk). Its OIDC issuer is <origin>/api/vibe/oidc.
#   SLOPTOWN_MIIRA_API_BASE overrides how the control plane reaches it (default: the
#   origin). fake-miira is not started, the public gate is off (SLOPTOWN_GATE=on keeps
#   it) and SLOPTOWN_ADMIN_ALLOWED_IPS adds Miira's server address for /admin/v1.
#   The values Miira needs (VIBE_*) are printed at the end. See docs/miira-integration.md.
#
# Uses the podman socket of whoever runs it; touches no firewall or system config.
# SLOPTOWN_DEV_DB_VOLUME picks the Postgres volume (tests/e2e uses its own).
# Set SLOPTOWN_OPENAI_API_KEY in the environment to get real inference
# (SLOPTOWN_OPENAI_BASE_URL points it elsewhere; tests/e2e/run.sh uses a fake).
#
# Production switches (put them in deploy/.public.env to make them stick):
#   SLOPTOWN_DEV_MODE=false          turns off dev-only features ("view as" in previews). Default true.
#   SLOPTOWN_RESTART_POLICY=on-failure  podman restarts a platform container that crashes.
#                                    Default: none. Survives nothing across a reboot (the vault is locked).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
T=localhost/sloptown
NET=sloptown
BUILD=1
PUBLIC=""
PLATFORM=(sloptown-edge sloptown-gate sloptown-egress sloptown-cp sloptown-fake-miira sloptown-db)
while [ $# -gt 0 ]; do
  case "$1" in
    --no-build) BUILD=0 ;;
    --public) PUBLIC="${2:?--public needs a domain}"; shift ;;
    --down)
      podman rm -f "${PLATFORM[@]}" >/dev/null 2>&1 || true
      echo "stopped (volumes and vibe-* project containers kept; remove with: podman rm -f \$(podman ps -aq --filter label=sloptown=1))"
      exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$(id -u)" = 0 ]; then SOCK=/run/podman/podman.sock; else SOCK="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/podman/podman.sock"; fi
# The control plane creates the Vibes; with deploy/harden-host.sh verified it does that
# through the gVisor service, so every Vibe runs on runsc.
CP_SOCK=$SOCK
[ "$(id -u)" = 0 ] && [ -f /etc/sloptown/isolation-verified ] && [ -S /run/sloptown/podman-runsc.sock ] && CP_SOCK=/run/sloptown/podman-runsc.sock
[ -S "$SOCK" ] || { echo "podman socket $SOCK missing: systemctl ${SUDO_USER:+--user }start podman.socket" >&2; exit 1; }

if [ -n "$PUBLIC" ]; then
  # Real secrets, generated once and reused (sessions and project tokens depend on them).
  SECRETS="$ROOT/deploy/.public.env"
  if [ ! -f "$SECRETS" ]; then
    umask 077
    {
      for k in SESSION_SECRET INTERNAL_SECRET EDGE_SECRET CLIENT_SECRET WEBHOOK_SECRET PAIRWISE_SECRET; do
        echo "$k=$(openssl rand -hex 32)"
      done
      echo "GATE_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"
    } > "$SECRETS"
    echo "-- generated secrets and gate password in $SECRETS"
  fi
  set -a; . "$SECRETS"; set +a
  SCHEME=https PORT="" CADDYFILE=Caddyfile.public
  STUDIO_HOST="studio.$PUBLIC" MIIRA_HOST="miira.$PUBLIC" APPS="$PUBLIC"
  EDGE_PORTS=(-p 80:80 -p 443:443)
  DB_VOLUME="${SLOPTOWN_DEV_DB_VOLUME:-sloptown-public-db}"
else
  SESSION_SECRET=dev-session-secret INTERNAL_SECRET=dev-internal-secret EDGE_SECRET=dev-edge-secret
  CLIENT_SECRET="${SLOPTOWN_OIDC_CLIENT_SECRET:-dev-client-secret}"
  WEBHOOK_SECRET="${SLOPTOWN_MIIRA_WEBHOOK_SECRET:-dev-webhook-secret}" PAIRWISE_SECRET=dev-pairwise-secret
  SCHEME=http PORT=":8080" CADDYFILE=Caddyfile.dev
  STUDIO_HOST=studio.sloptown.localhost MIIRA_HOST=miira.sloptown.localhost APPS=apps.sloptown.localhost
  EDGE_PORTS=(-p 127.0.0.1:8080:8080)
  DB_VOLUME="${SLOPTOWN_DEV_DB_VOLUME:-sloptown-dev-db}"
fi
STUDIO_ORIGIN="$SCHEME://$STUDIO_HOST$PORT"
MIIRA_ORIGIN="$SCHEME://$MIIRA_HOST$PORT"
MIIRA_ISSUER="$MIIRA_ORIGIN"
MIIRA_API_BASE=http://sloptown-fake-miira:8100
GATE_MODE=on
ADMIN_IPS="10.0.0.0/8 127.0.0.1/32"
DEV_MODE="${SLOPTOWN_DEV_MODE:-true}"
RESTART=()
[ -n "${SLOPTOWN_RESTART_POLICY:-}" ] && RESTART=(--restart "$SLOPTOWN_RESTART_POLICY")
EXTERNAL_MIIRA="${SLOPTOWN_MIIRA_ORIGIN:-}"
if [ -n "$EXTERNAL_MIIRA" ]; then
  MIIRA_ORIGIN="${EXTERNAL_MIIRA%/}"
  MIIRA_ISSUER="$MIIRA_ORIGIN/api/vibe/oidc"
  MIIRA_API_BASE="${SLOPTOWN_MIIRA_API_BASE:-$MIIRA_ORIGIN}"
  GATE_MODE="${SLOPTOWN_GATE:-off}"
  ADMIN_IPS="$ADMIN_IPS ${SLOPTOWN_ADMIN_ALLOWED_IPS:-}"
fi

if [ "$BUILD" = 1 ]; then
  set -a; . images/versions.env; set +a
  b() { echo "-- build $1"; podman build -q -t "$T/$1:dev" "${@:3}" -f "$2" . >/dev/null; }
  b vibe-base images/vibe-base/Containerfile
  b vibe-dev images/vibe-dev/Containerfile --build-arg BASE_IMAGE=$T/vibe-base:dev --build-arg OPENCODE_VERSION=$OPENCODE_VERSION
  b vibe-runtime images/vibe-runtime/Containerfile --build-arg BASE_IMAGE=$T/vibe-base:dev
  b control-plane deploy/containers/control-plane.Containerfile
  b fake-miira deploy/containers/fake-miira.Containerfile
  b studio deploy/containers/studio.Containerfile
  b edge edge/Containerfile --build-arg STUDIO_IMAGE=$T/studio:dev
fi

podman network exists $NET || podman network create --label sloptown=1 $NET >/dev/null
podman rm -f "${PLATFORM[@]}" >/dev/null 2>&1 || true

CP_ENV=(
  -e SLOPTOWN_DEV_MODE="$DEV_MODE"
  -e SLOPTOWN_DATABASE_URL=postgresql+asyncpg://sloptown:sloptown@sloptown-db:5432/sloptown
  -e SLOPTOWN_STUDIO_ORIGIN="$STUDIO_ORIGIN"
  -e SLOPTOWN_APPS_DOMAIN="$APPS"
  -e SLOPTOWN_APPS_SCHEME=$SCHEME -e SLOPTOWN_APPS_PORT="$PORT"
  -e "SLOPTOWN_MIIRA_ORIGINS=[\"$MIIRA_ORIGIN\"]"
  -e SLOPTOWN_MIIRA_ISSUER="$MIIRA_ISSUER"
  -e SLOPTOWN_MIIRA_API_BASE="$MIIRA_API_BASE"
  -e SLOPTOWN_OIDC_CLIENT_SECRET="$CLIENT_SECRET"
  -e SLOPTOWN_MIIRA_WEBHOOK_SECRET="$WEBHOOK_SECRET"
  -e SLOPTOWN_SESSION_SECRET="$SESSION_SECRET"
  -e SLOPTOWN_INTERNAL_SECRET="$INTERNAL_SECRET"
  -e SLOPTOWN_EDGE_SECRET="$EDGE_SECRET"
  -e SLOPTOWN_PODMAN_URL=unix:///run/podman/podman.sock
  -e SLOPTOWN_DEV_IMAGE=$T/vibe-dev:dev -e SLOPTOWN_RUNTIME_IMAGE=$T/vibe-runtime:dev
  -e SLOPTOWN_POOL_SIZE="${SLOPTOWN_POOL_SIZE:-2}"
  # Isolation from deploy/harden-host.sh, on once that script has seen a container start
  # with it (its marker file): half-applied, it would stop every new Vibe from starting.
  -e SLOPTOWN_USERNS_AUTO="${SLOPTOWN_USERNS_AUTO:-$([ -f /etc/sloptown/isolation-verified ] && echo true || echo false)}"
  # Only a label in the container tag: the runtime comes from the socket below.
  -e SLOPTOWN_OCI_RUNTIME="${SLOPTOWN_OCI_RUNTIME-$([ -f /etc/sloptown/isolation-verified ] && echo runsc || true)}"
  -e SLOPTOWN_CONTROL_PLANE_URL=http://sloptown-cp:8000
  -e SLOPTOWN_OPENAI_API_KEY="${SLOPTOWN_OPENAI_API_KEY:-}"
  -e SLOPTOWN_OPENAI_BASE_URL="${SLOPTOWN_OPENAI_BASE_URL:-https://api.openai.com/v1}"
  -e SLOPTOWN_EGRESS_ENV_FILE=/etc/sloptown/egress.env
)

echo "-- sloptown-db"
podman run -d --name sloptown-db "${RESTART[@]}" --network $NET --label sloptown=1 \
  -e POSTGRES_USER=sloptown -e POSTGRES_PASSWORD=sloptown -e POSTGRES_DB=sloptown \
  -v "$DB_VOLUME:/var/lib/postgresql/data" docker.io/library/postgres:17 >/dev/null
for _ in $(seq 30); do podman exec sloptown-db pg_isready -q -U sloptown 2>/dev/null && break; sleep 1; done

if [ -z "$EXTERNAL_MIIRA" ]; then
echo "-- sloptown-fake-miira"
FM_STATE=()
# Public test: keep files, shares and the signing key across restarts.
[ -n "$PUBLIC" ] && FM_STATE=(-v sloptown-public-fake-miira:/var/lib/fake-miira
  -e FAKE_MIIRA_DATA_FILE=/var/lib/fake-miira/store.json -e FAKE_MIIRA_KEY_FILE=/var/lib/fake-miira/key.pem)
podman run -d --name sloptown-fake-miira "${RESTART[@]}" --network $NET --label sloptown=1 "${FM_STATE[@]}" \
  -e FAKE_MIIRA_ISSUER="$MIIRA_ORIGIN" \
  -e FAKE_MIIRA_STUDIO_ORIGIN="$STUDIO_ORIGIN" \
  -e FAKE_MIIRA_STUDIO_API_BASE=http://sloptown-cp:8000 \
  -e FAKE_MIIRA_CLIENT_SECRET="$CLIENT_SECRET" \
  -e FAKE_MIIRA_WEBHOOK_SECRET="$WEBHOOK_SECRET" \
  -e FAKE_MIIRA_PAIRWISE_SECRET="$PAIRWISE_SECRET" \
  $T/fake-miira:dev >/dev/null
fi

echo "-- sloptown-cp"
# The socket is owned by the caller. Rootful (root): run the cp as uid 0 so it
# can open it (dev only). Rootless: keep-id maps the caller to the image's uid 1000.
if [ "$(id -u)" = 0 ]; then CP_USER=(--user 0:0); else CP_USER=(--userns keep-id:uid=1000,gid=1000); fi
podman run -d --name sloptown-cp "${RESTART[@]}" --network $NET --label sloptown=1 "${CP_ENV[@]}" "${CP_USER[@]}" \
  --security-opt label=disable -v "$CP_SOCK:/run/podman/podman.sock" \
  $T/control-plane:dev >/dev/null

echo "-- sloptown-egress"
podman run -d --name sloptown-egress "${RESTART[@]}" --network $NET --label sloptown=1 "${CP_ENV[@]}" \
  -v "$ROOT/edge/egress.env:/etc/sloptown/egress.env:ro,Z" \
  $T/control-plane:dev python -m sloptown.egress >/dev/null

EDGE_ENV=()
if [ -n "$PUBLIC" ] && [ "$GATE_MODE" = on ]; then
  echo "-- sloptown-gate"
  podman run -d --name sloptown-gate "${RESTART[@]}" --network $NET --label sloptown=1 \
    -e GATE_PASSWORD="$GATE_PASSWORD" -e GATE_DOMAIN="$PUBLIC" -e GATE_SECRET="$SESSION_SECRET" \
    -v "$ROOT/edge/gate.py:/gate.py:ro,Z" ghcr.io/astral-sh/uv:python3.13-bookworm-slim python /gate.py >/dev/null
  # Certificates survive restarts (Let's Encrypt rate limits: 50 new certs/week per domain).
  EDGE_ENV=(-e GATE_DOMAIN="$PUBLIC" -e GATE_UPSTREAM=sloptown-gate:9100 -v sloptown-public-caddy:/data)
elif [ -n "$PUBLIC" ]; then
  EDGE_ENV=(-e GATE_DOMAIN="$PUBLIC" -e GATE_MODE=off -v sloptown-public-caddy:/data)
fi
TLS_DIR="${SLOPTOWN_TLS_DIR-/etc/sloptown/tls}"
if [ -n "$PUBLIC" ] && [ -n "$TLS_DIR" ] && [ -r "$TLS_DIR/fullchain.pem" ] && [ -r "$TLS_DIR/priv.pem" ]; then
  echo "-- edge uses the wildcard certificate in $TLS_DIR"
  EDGE_ENV+=(-e TLS_MODE=file -v "$TLS_DIR:/etc/caddy/tls:ro,z")
fi

echo "-- sloptown-edge (${EDGE_PORTS[*]})"
podman run -d --name sloptown-edge "${RESTART[@]}" --network $NET --label sloptown=1 "${EDGE_PORTS[@]}" "${EDGE_ENV[@]}" \
  -e STUDIO_HOST="$STUDIO_HOST" -e APPS_DOMAIN="$APPS" \
  -e STUDIO_ORIGIN="$STUDIO_ORIGIN" -e MIIRA_ORIGINS="$MIIRA_ORIGIN" \
  -e CP_UPSTREAM=sloptown-cp:8000 -e FAKE_MIIRA_UPSTREAM=sloptown-fake-miira:8100 \
  -e ADMIN_ALLOWED_IPS="$ADMIN_IPS" \
  $T/edge:dev caddy run --adapter caddyfile --config /etc/caddy/$CADDYFILE >/dev/null

echo "-- waiting for health"
deploy/test/wait-healthy.sh
if [ -n "$EXTERNAL_MIIRA" ]; then
  if [ -n "$PUBLIC" ]; then FROM="CLIENT_SECRET / WEBHOOK_SECRET in $SECRETS"; else FROM="$CLIENT_SECRET / $WEBHOOK_SECRET"; fi
  cat <<EOF

Using the real Miira at $MIIRA_ORIGIN (issuer $MIIRA_ISSUER, reached via $MIIRA_API_BASE).
Miira's API needs, in its secrets.env:
  VIBE_STUDIO_ORIGIN=$STUDIO_ORIGIN
  VIBE_CLIENT_SECRET / VIBE_WEBHOOK_SECRET = $FROM
  VIBE_PAIRWISE_SECRET = any long random value, set once on the Miira side
  VIBE_SIGNING_KEY     = RSA private key PEM (required in production only)
EOF
fi
if [ -n "$PUBLIC" ]; then
  cat <<EOF

SlopTown public stack is up (password gate: $GATE_MODE, dev mode: $DEV_MODE, restart: ${SLOPTOWN_RESTART_POLICY:-no}):
  studio      https://$STUDIO_HOST
  Miira       $MIIRA_ORIGIN
  apps        https://<slug>.$APPS
  gate        password in $SECRETS (GATE_PASSWORD)
Logs: podman logs -f sloptown-cp     Stop: deploy/dev-up.sh --down
EOF
else
  cat <<EOF

SlopTown dev stack is up:
  studio      http://$STUDIO_HOST:8080
  Miira       $MIIRA_ORIGIN
  apps        http://<slug>.$APPS:8080
Logs: podman logs -f sloptown-cp     Stop: deploy/dev-up.sh --down
EOF
fi
