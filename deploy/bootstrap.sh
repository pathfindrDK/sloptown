#!/usr/bin/env bash
# SlopTown host bootstrap. Idempotent; safe to re-run after changes.
# Supports Debian 12+ and Ubuntu 24.04+ (this box is Ubuntu 26.04).
#
#   sudo deploy/bootstrap.sh [--dev] [--repo <git url or path>] [--dry-run]
#
# What it does (each step skips itself when already done):
#   1. packages: podman, passt, uidmap, nftables, unattended-upgrades, git, jq, curl
#   2. gVisor (runsc) and Trivy from their apt repositories
#   3. user `sloptown` with home on the LUKS volume, big subuid/subgid range, linger
#   4. user@<uid> waits for the LUKS mount (like docker/podman units on this host)
#   5. sysctl so rootless Caddy can bind :80/:443
#   6. /etc/sloptown/{sloptown.env,egress.env,sloptown.nft,tls/} and the nft loader unit
#   7. rootless podman config (runsc runtime, insecure localhost:5000 registry)
#   8. repo checkout for the sloptown user, quadlet units, nightly pipeline timer
#   9. unattended-upgrades WITHOUT automatic reboot (the LUKS volume needs a human)
#
# It never starts the stack: fill in /etc/sloptown/sloptown.env, then follow
# the runbook in deploy/AGENTS.md ("First install").
set -euo pipefail

VAULT=/mnt/encrypted-at-rest
SVC_USER=sloptown
SVC_HOME=$VAULT/sloptown-home
ETC=/etc/sloptown
DEV=0
DRY=0
REPO_SRC="$(cd "$(dirname "$0")/.." && pwd)"

while [ $# -gt 0 ]; do
  case "$1" in
    --dev) DEV=1 ;;
    --repo) REPO_SRC="$2"; shift ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '\n== %s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then echo "+ $*"; else "$@"; fi; }
as_user() { run runuser -u "$SVC_USER" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$SVC_USER")" HOME="$SVC_HOME" "$@"; }

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
mountpoint -q "$VAULT" || { echo "$VAULT is not mounted; unlock it first (see /root/UNLOCK.md)" >&2; exit 1; }
. /etc/os-release
case "$ID" in debian|ubuntu) ;; *) echo "unsupported distro $ID" >&2; exit 1 ;; esac
ARCH="$(dpkg --print-architecture)"

say "1. packages"
export DEBIAN_FRONTEND=noninteractive
PKGS=(podman passt uidmap nftables unattended-upgrades apt-listchanges git jq curl ca-certificates gnupg)
if [ "$ID" = debian ] && [ "${VERSION_CODENAME}" = bookworm ]; then
  # Podman in bookworm is 4.3; quadlet Notify=healthy and friends need 5.x.
  if [ ! -f /etc/apt/sources.list.d/backports.list ]; then
    run sh -c "echo 'deb http://deb.debian.org/debian bookworm-backports main' > /etc/apt/sources.list.d/backports.list"
  fi
  run apt-get update -q
  run apt-get install -y -q -t bookworm-backports podman passt
fi
missing=()
for p in "${PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
if [ ${#missing[@]} -gt 0 ]; then
  run apt-get update -q
  run apt-get install -y -q "${missing[@]}"
fi

say "2. gVisor (runsc) and Trivy"
add_repo() { # name keyurl line
  local key=/usr/share/keyrings/$1.gpg list=/etc/apt/sources.list.d/$1.list
  if [ ! -f "$key" ]; then run sh -c "curl -fsSL '$2' | gpg --dearmor -o '$key'"; fi
  if [ ! -f "$list" ]; then run sh -c "echo '$3' > '$list'"; fi
}
add_repo gvisor https://gvisor.dev/archive.key \
  "deb [arch=$ARCH signed-by=/usr/share/keyrings/gvisor.gpg] https://storage.googleapis.com/gvisor/releases release main"
add_repo trivy https://aquasecurity.github.io/trivy-repo/deb/public.key \
  "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb generic main"
if ! command -v runsc >/dev/null || ! command -v trivy >/dev/null; then
  run apt-get update -q
  run apt-get install -y -q runsc trivy
fi

say "3. user $SVC_USER (home on the LUKS volume)"
if ! id "$SVC_USER" >/dev/null 2>&1; then
  run useradd --system --create-home --home-dir "$SVC_HOME" --shell /bin/bash "$SVC_USER"
fi
# userns=auto hands every container its own 65536-id slice: reserve 16M ids
# (room for ~250 containers) above every existing range.
if ! grep -q "^$SVC_USER:" /etc/subuid; then
  start=$(awk -F: 'BEGIN{m=100000} {e=$2+$3; if(e>m)m=e} END{print m}' /etc/subuid /etc/subgid)
  start=$(( (start / 65536 + 1) * 65536 ))
  run usermod --add-subuids "$start-$((start + 16777215))" --add-subgids "$start-$((start + 16777215))" "$SVC_USER"
fi
# Rootful podman (dev runs as root, SLOPTOWN_USERNS_AUTO=true) takes userns=auto
# ranges from the `containers` entry instead.
for f in /etc/subuid /etc/subgid; do
  grep -q '^containers:' "$f" || run sh -c "echo 'containers:2147483647:2147483648' >> $f"
done
run loginctl enable-linger "$SVC_USER"
SVC_UID=$(id -u "$SVC_USER" 2>/dev/null || echo 0)

say "4. user manager waits for the vault"
DROPIN=/etc/systemd/system/user@$SVC_UID.service.d/sloptown-vault.conf
if [ ! -f "$DROPIN" ]; then
  run mkdir -p "$(dirname "$DROPIN")"
  run sh -c "printf '[Unit]\nConditionPathIsMountPoint=$VAULT\n' > '$DROPIN'"
  run systemctl daemon-reload
fi
MARK="<!-- sloptown -->"
if [ -f /root/UNLOCK.md ] && ! grep -q "$MARK" /root/UNLOCK.md; then
  if [ "$DRY" = 1 ]; then echo "+ append SlopTown section to /root/UNLOCK.md"; else
    cat >> /root/UNLOCK.md <<EOF

$MARK
## SlopTown

The sloptown user's systemd manager (and with it every SlopTown container) is
skipped at boot until the vault is mounted. After mounting:

    systemctl start sloptown-nft.service user@$SVC_UID.service

Runbook: $SVC_HOME/sloptown/deploy/AGENTS.md
EOF
  fi
fi

say "5. sysctl: rootless bind of :80/:443"
SYSCTL=/etc/sysctl.d/60-sloptown.conf
if [ ! -f "$SYSCTL" ]; then
  run sh -c "echo 'net.ipv4.ip_unprivileged_port_start=80' > $SYSCTL"
  run sysctl -q -p "$SYSCTL"
fi

say "6. /etc/sloptown and nftables"
run install -d -m 0750 -o root -g "$SVC_USER" "$ETC"
[ -f "$ETC/sloptown.env" ] || run install -m 0640 -o root -g "$SVC_USER" "$REPO_SRC/deploy/sloptown.env.example" "$ETC/sloptown.env"
[ -f "$ETC/egress.env" ] || run install -m 0640 -o root -g "$SVC_USER" "$REPO_SRC/edge/egress.env" "$ETC/egress.env"
# Wildcard certificate for TLS_MODE=file; the edge unit mounts it (empty is fine).
# Rootless edge: the service user must own the key.
[ -d "$ETC/tls" ] || run install -d -m 0700 -o "$SVC_USER" -g "$SVC_USER" "$ETC/tls"
run install -m 0644 "$REPO_SRC/edge/nftables/sloptown.nft" "$ETC/sloptown.nft"
run nft -c -f "$ETC/sloptown.nft"
# Own unit, not nftables.service: the stock /etc/nftables.conf flushes the whole
# ruleset, which would wipe Docker's and netavark's rules on this shared host.
run install -m 0644 "$REPO_SRC/deploy/systemd/sloptown-nft.service" /etc/systemd/system/sloptown-nft.service
run systemctl daemon-reload
run systemctl enable --now sloptown-nft.service

say "7. rootless podman config"
CFG=$SVC_HOME/.config/containers
run install -d -o "$SVC_USER" -g "$SVC_USER" "$CFG" "$CFG/systemd" "$CFG/registries.conf.d"
run sh -c "cat > '$CFG/containers.conf' <<'EOF'
[engine]
runtime = \"crun\"

[engine.runtimes]
# Rootless gVisor: no cgroup control from inside the user namespace.
runsc = [\"/usr/bin/runsc\", \"--ignore-cgroups\"]
EOF"
run sh -c "cat > '$CFG/registries.conf.d/sloptown.conf' <<'EOF'
[[registry]]
location = \"localhost:5000\"
insecure = true
EOF"
run chown -R "$SVC_USER:$SVC_USER" "$CFG"

say "8. repo, quadlets, timers"
SRC=$SVC_HOME/sloptown
if [ ! -d "$SRC/.git" ]; then
  run git config --system --add safe.directory "$REPO_SRC"
  as_user git clone -q "$REPO_SRC" "$SRC"
fi
for f in "$SRC"/deploy/quadlet/*; do
  case "$f" in *fake-miira*) [ "$DEV" = 1 ] || continue ;; esac
  run install -m 0644 -o "$SVC_USER" -g "$SVC_USER" "$f" "$CFG/systemd/"
done
UNITS=$SVC_HOME/.config/systemd/user
run install -d -o "$SVC_USER" -g "$SVC_USER" "$UNITS"
run install -m 0644 -o "$SVC_USER" -g "$SVC_USER" "$SRC/deploy/systemd/sloptown-nightly.service" "$SRC/deploy/systemd/sloptown-nightly.timer" "$UNITS/"
as_user systemctl --user daemon-reload
as_user systemctl --user enable --now podman.socket sloptown-nightly.timer
# The nightly pipeline runs `podman auto-update` itself, after its gates pass.
as_user systemctl --user disable --now podman-auto-update.timer || true

say "gVisor check"
if as_user podman --runtime runsc run --rm docker.io/library/busybox:1.37 true >/dev/null 2>&1; then
  echo "runsc works rootless: keep SLOPTOWN_OCI_RUNTIME=runsc"
else
  echo "WARNING: rootless runsc failed. Set SLOPTOWN_OCI_RUNTIME= (crun) until fixed; see deploy/AGENTS.md."
fi

say "9. unattended-upgrades (no automatic reboot)"
run sh -c "cat > /etc/apt/apt.conf.d/52sloptown-unattended <<'EOF'
// SlopTown: patch daily, never reboot on our own. A reboot locks the LUKS
// volume and needs a human (see /root/UNLOCK.md).
APT::Periodic::Update-Package-Lists \"1\";
APT::Periodic::Unattended-Upgrade \"1\";
Unattended-Upgrade::Automatic-Reboot \"false\";
Unattended-Upgrade::Remove-Unused-Dependencies \"true\";
EOF"
run systemctl enable --now unattended-upgrades.service

say "done"
cat <<EOF
Next (deploy/AGENTS.md, "First install"):
  1. edit $ETC/sloptown.env (every CHANGE_ME) and $ETC/egress.env
  2. as $SVC_USER: systemctl --user start sloptown-registry
  3. as $SVC_USER: $SRC/deploy/pipeline/nightly.sh --first-install
  4. as $SVC_USER: systemctl --user start sloptown-db sloptown-cp sloptown-egress sloptown-edge
EOF
