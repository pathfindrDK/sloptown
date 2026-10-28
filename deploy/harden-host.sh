#!/usr/bin/env bash
# Isolation for a host that runs SlopTown with rootful podman through dev-up.sh
# (the public test stack). bootstrap.sh sets up the production layout (rootless,
# user `sloptown`); this applies the same isolation to the rootful one. Idempotent.
#
#   sudo deploy/harden-host.sh [--dry-run]
#
#   1. nftables: project bridges reach no host service but aardvark-dns, and
#      nothing leaves or enters them through the host (edge/nftables/sloptown.nft,
#      loaded by its own unit like bootstrap does)
#   2. a `containers` subuid/subgid range, so every Vibe gets its own user
#      namespace (SLOPTOWN_USERNS_AUTO): root or uid 1000 in a Vibe is nobody on
#      the host; podman's temp dir gets search permission for those ids
#   3. gVisor (runsc) and a podman API socket that creates containers with it
#      (sloptown-podman-runsc.socket): Vibes run on gVisor's kernel, not the
#      host's. Over the API podman ignores a per-container runtime, so the
#      control plane gets this socket instead of the stock one
#
# dev-up.sh turns 2 and 3 on once the check at the end has passed (marker file). Recreate the Vibe
# containers afterwards (deploy/AGENTS.md, "Recycle the warm pool"), or they keep
# the old settings until replaced.
set -euo pipefail

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1
REPO=$(cd "$(dirname "$0")/.." && pwd)
ETC=/etc/sloptown
run() { if [ "$DRY" = 1 ]; then echo "+ $*"; else "$@"; fi; }
say() { echo "== $*"; }
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

say "1. nftables"
run install -d -m 0755 "$ETC"
run install -m 0644 "$REPO/edge/nftables/sloptown.nft" "$ETC/sloptown.nft"
nft -c -f "$REPO/edge/nftables/sloptown.nft"
# Own unit, not nftables.service: the stock /etc/nftables.conf flushes the whole
# ruleset, which would drop netavark's and Docker's tables on this shared host.
run install -m 0644 "$REPO/deploy/systemd/sloptown-nft.service" /etc/systemd/system/sloptown-nft.service
run systemctl daemon-reload
run systemctl enable sloptown-nft.service
run systemctl restart sloptown-nft.service

say "2. subuid/subgid for userns=auto"
for f in /etc/subuid /etc/subgid; do
  grep -q '^containers:' "$f" || run sh -c "echo 'containers:2147483647:2147483648' >> $f"
done

# userns=auto has podman mount each container's rootfs in image_copy_tmp_dir, which the
# mapped (unprivileged) root must be able to pass through. Search permission only (o+x):
# nobody can list these folders. On this host that dir belongs to another project's
# config, which is why it is set here and not changed.
TMPD=$(podman info --format '{{.Store.ImageCopyTmpDir}}' 2>/dev/null || true)
if [ -n "$TMPD" ] && [ -d "$TMPD" ]; then
  d=$TMPD
  while [ "$d" != "/" ]; do
    [ "$(stat -c %A "$d" | cut -c10)" = "x" ] || run chmod o+x "$d"
    d=$(dirname "$d")
  done
fi

say "3. gVisor"
if ! command -v runsc >/dev/null; then
  ARCH=$(dpkg --print-architecture)
  run sh -c "curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor --yes -o /usr/share/keyrings/gvisor.gpg"
  run sh -c "echo 'deb [arch=$ARCH signed-by=/usr/share/keyrings/gvisor.gpg] https://storage.googleapis.com/gvisor/releases release main' > /etc/apt/sources.list.d/gvisor.list"
  run apt-get update -q
  run apt-get install -y -q runsc
fi
run install -d -m 0755 /etc/containers/containers.conf.d
run sh -c "cat > /etc/containers/containers.conf.d/60-sloptown-runsc.conf <<'CONF'
# Written by SlopTown deploy/harden-host.sh. The platform containers keep crun;
# the control plane asks for runsc per Vibe container (SLOPTOWN_OCI_RUNTIME).
[engine.runtimes]
runsc = [\"/usr/bin/runsc\"]
CONF"

run install -m 0644 "$REPO/deploy/systemd/sloptown-podman-runsc.socket" "$REPO/deploy/systemd/sloptown-podman-runsc.service" /etc/systemd/system/
run systemctl daemon-reload
run systemctl enable --now sloptown-podman-runsc.socket

say "check"
if [ "$DRY" = 0 ]; then
  nft list table inet sloptown_vibe >/dev/null && echo "nftables: inet sloptown_vibe loaded"
  grep '^containers:' /etc/subuid
  # The way the control plane does it: over the API socket, with userns=auto in the spec.
  SOCK=/run/sloptown/podman-runsc.sock
  api() { curl -s --unix-socket "$SOCK" "$@"; }
  api -X DELETE "http://d/v5.0.0/libpod/containers/sloptown-isolation-check?force=true" >/dev/null || true
  api -X POST -H "Content-Type: application/json" \
    -d '{"name":"sloptown-isolation-check","image":"docker.io/library/busybox:1.37","command":["sleep","30"],"userns":{"nsmode":"auto"},"idmappings":{"AutoUserNs":true}}' \
    http://d/v5.0.0/libpod/containers/create >/dev/null
  api -X POST http://d/v5.0.0/libpod/containers/sloptown-isolation-check/start >/dev/null
  got=$(podman inspect sloptown-isolation-check --format '{{.OCIRuntime}} {{.State.Status}} {{json .HostConfig.IDMappings}}' 2>/dev/null || true)
  deleted=$(api -o /dev/null -w '%{http_code}' -X DELETE "http://d/v5.0.0/libpod/containers/sloptown-isolation-check?force=true" || true)
  echo "isolation check: $got, delete $deleted"
  # runsc, running, root mapped to a non-zero host id, and a clean delete (the control
  # plane removes containers all the time).
  if [ "${got%% *}" = runsc ] && echo "$got" | grep -q ' running .*"UidMap":\["0:[1-9]' && [ "$deleted" = 200 ]; then
    echo "gVisor + userns=auto over the API: ok"
    touch "$ETC/isolation-verified"
  else
    rm -f "$ETC/isolation-verified"
    echo "WARNING: gVisor with userns=auto failed; dev-up.sh keeps Vibes on crun without user namespaces" >&2
    exit 1
  fi
fi
