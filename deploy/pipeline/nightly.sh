#!/usr/bin/env bash
# SlopTown image pipeline. Runs nightly as the sloptown user (sloptown-nightly.timer),
# even when nothing changed, so base-image and distro patches land every day.
#
#   deploy/pipeline/nightly.sh [--first-install] [--no-push] [--only vibe|platform]
#
#   --first-install  build, gate and push, but no rollout (nothing runs yet)
#   --no-push        build, gate and smoke test only (dev / CI)
#   --only vibe      only vibe-base, vibe-dev, vibe-runtime
#   --only platform  only control-plane, fake-miira, studio, edge
#
# Stages: pull upstream bases -> build (--no-cache) -> Trivy gate (CRITICAL,
# fixable) -> smoke tests -> push :<tag> and :latest -> podman auto-update
# (with rollback) -> tell the control plane (pool, runtimes, idle editors).
# Any failure stops the run before anything is pushed; the unit then shows as
# failed (check-server.sh / journal).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
REG="${SLOPTOWN_REGISTRY:-localhost:5000/sloptown}"
TAG="${TAG:-$(date +%Y%m%d-%H%M)}"
FIRST=0; PUSH=1; ONLY=all
while [ $# -gt 0 ]; do
  case "$1" in
    --first-install) FIRST=1 ;;
    --no-push) PUSH=0 ;;
    --only) ONLY="$2"; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
set -a; . images/versions.env; set +a
step() { printf '\n== [%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

if [ "${PIPELINE_GIT_PULL:-1}" = 1 ] && git remote get-url origin >/dev/null 2>&1; then
  step "git pull"
  git pull --ff-only -q
fi
COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

step "pull upstream base images"
for img in "$PYTHON_IMAGE" "$NODE_IMAGE" "$UV_IMAGE" "$CADDY_BUILDER" "$CADDY_IMAGE"; do
  podman pull -q "$img" >/dev/null
done

ARGS=(--no-cache --pull=never --label "org.opencontainers.image.revision=$COMMIT" --label "sloptown.tag=$TAG")
for v in PYTHON_IMAGE NODE_IMAGE UV_IMAGE OPENCODE_VERSION CADDY_BUILDER CADDY_IMAGE CADDY_VERSION RATELIMIT_VERSION; do
  ARGS+=(--build-arg "$v=${!v}")
done
BUILT=()
build() { # name containerfile [extra args]
  local name=$1 file=$2; shift 2
  step "build $name:$TAG"
  podman build -q "${ARGS[@]}" "$@" -t "$REG/$name:$TAG" -f "$file" . >/dev/null
  BUILT+=("$name")
}

if [ "$ONLY" != platform ]; then
  build vibe-base images/vibe-base/Containerfile
  build vibe-dev images/vibe-dev/Containerfile --build-arg "BASE_IMAGE=$REG/vibe-base:$TAG"
  build vibe-runtime images/vibe-runtime/Containerfile --build-arg "BASE_IMAGE=$REG/vibe-base:$TAG"
fi
if [ "$ONLY" != vibe ]; then
  build control-plane deploy/containers/control-plane.Containerfile
  build fake-miira deploy/containers/fake-miira.Containerfile
  build studio deploy/containers/studio.Containerfile
  build edge edge/Containerfile --build-arg "STUDIO_IMAGE=$REG/studio:$TAG"
fi

step "trivy gate (CRITICAL, fixable)"
SCAN_DIR="$(mktemp -d)"; trap 'rm -rf "${SCAN_DIR:?}"' EXIT
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/trivy"; mkdir -p "$CACHE"
trivy_scan() { # tarball
  if command -v trivy >/dev/null; then
    trivy image -q --cache-dir "$CACHE" --input "$1" --scanners vuln --severity CRITICAL --ignore-unfixed \
      --ignorefile "$ROOT/.trivyignore" --exit-code 1
  else
    podman run --rm -v "$1:/scan.tar:ro,Z" -v "$CACHE:/cache:Z" -v "$ROOT/.trivyignore:/trivyignore:ro,Z" "$TRIVY_IMAGE" \
      image -q --cache-dir /cache --input /scan.tar --scanners vuln --severity CRITICAL --ignore-unfixed \
      --ignorefile /trivyignore --exit-code 1
  fi
}
for name in "${BUILT[@]}"; do
  [ "$name" = studio ] && continue  # static files only; scanned via the edge image
  podman save -q -o "$SCAN_DIR/$name.tar" "$REG/$name:$TAG"
  echo "-- $name"
  trivy_scan "$SCAN_DIR/$name.tar"
  rm -f "${SCAN_DIR:?}/${name:?}.tar"
done

step "smoke tests"
if [ "$ONLY" != platform ]; then
  VIBE_DEV_IMAGE="$REG/vibe-dev:$TAG" VIBE_RUNTIME_IMAGE="$REG/vibe-runtime:$TAG" images/test/smoke.sh
fi
if [ "$ONLY" != vibe ]; then
  EDGE_IMAGE="$REG/edge:$TAG" edge/test/validate.sh
  CP_IMAGE="$REG/control-plane:$TAG" FAKE_MIIRA_IMAGE="$REG/fake-miira:$TAG" deploy/test/platform-smoke.sh
fi

for name in "${BUILT[@]}"; do podman tag "$REG/$name:$TAG" "$REG/$name:latest"; done
if [ "$PUSH" = 0 ]; then
  step "done (no push): ${BUILT[*]} @ $TAG"
  exit 0
fi

step "push $TAG and latest"
for name in "${BUILT[@]}"; do
  podman push -q --tls-verify=false "$REG/$name:$TAG"
  podman push -q --tls-verify=false "$REG/$name:latest"
done

if [ "$FIRST" = 1 ]; then
  step "first install: pushed, no rollout"
  exit 0
fi

step "podman auto-update (rolls back units that fail their health check)"
podman auto-update --rollback=true

step "tell the control plane"
if podman container exists sloptown-cp; then
  podman exec -e "TAG=$TAG" -e "DEV=$REG/vibe-dev:latest" -e "RUN=$REG/vibe-runtime:latest" sloptown-cp python - <<'PY'
import json, os, urllib.request
body = json.dumps({"devImage": os.environ["DEV"], "runtimeImage": os.environ["RUN"], "tag": os.environ["TAG"]}).encode()
req = urllib.request.Request("http://127.0.0.1:8000/internal/images/rolled", data=body, method="POST",
    headers={"Content-Type": "application/json", "X-Sloptown-Internal": os.environ["SLOPTOWN_INTERNAL_SECRET"]})
print(urllib.request.urlopen(req, timeout=600).read().decode())
PY
else
  echo "sloptown-cp not running; skipped"
fi

step "prune: keep the newest 7 dated tags per image"
for name in "${BUILT[@]}"; do
  podman images --format '{{.Tag}}' "$REG/$name" | grep -E '^[0-9]{8}-[0-9]{4}$' | sort -r | tail -n +8 \
    | while read -r old; do podman rmi -i "$REG/$name:$old" >/dev/null || true; done
done
podman image prune -f >/dev/null
step "done: ${BUILT[*]} @ $TAG ($COMMIT)"
