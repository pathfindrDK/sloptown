# deploy/ — host setup, units, image pipeline, runbook

SlopTown runs on one host (today the Ubuntu 26.04 box, reached over SSH :5700
on the company VPN), as **rootless podman** under the system user `sloptown`.
Its home, and therefore all container storage, is on the LUKS vault
`/mnt/encrypted-at-rest` (see `/root/UNLOCK.md`).

## Files

| Path | |
|---|---|
| `bootstrap.sh` | idempotent host setup (Debian 12+/Ubuntu 24.04+). `--dry-run` prints every change; `--dev` also installs fake-miira. Steps are listed in its header |
| `harden-host.sh` | isolation for a host that runs the stack rootful through `dev-up.sh`: SlopTown's nftables table and unit, a `containers` subuid range (userns=auto, plus search permission on podman's temp dir), gVisor and `sloptown-podman-runsc.socket`. Writes `/etc/sloptown/isolation-verified` only when a container created over that socket runs on runsc with root mapped to a host id > 0 and deletes cleanly; only then does `dev-up.sh` give the control plane that socket and set userns=auto. `--dry-run` prints every change |
| `systemd/sloptown-podman-runsc.{socket,service}` | a second podman API (`/run/sloptown/podman-runsc.sock`) whose runtime is runsc with `overlay2=root:memory`; the control plane creates Vibes through it |
| `sloptown.env.example` | every setting for `/etc/sloptown/sloptown.env` (secrets `CHANGE_ME`) |
| `quadlet/` | podman quadlet units for the `sloptown` user: network `sloptown`, `sloptown-{db,registry,cp,egress,edge,fake-miira}` (+ volumes). All `AutoUpdate=registry`, the long-running ones `Notify=healthy` so `podman auto-update` can roll back |
| `containers/` | Containerfiles: `control-plane` (also the egress, `python -m sloptown.egress`), `fake-miira`, `studio` (data-only image with `/srv/studio`); `healthcheck.py` → `/usr/local/bin/sloptown-healthcheck <url>` in the control-plane image |
| `systemd/` | `sloptown-nft.service` (system; loads `edge/nftables/sloptown.nft`), `sloptown-nightly.{service,timer}` (user; 02:30 Europe/Copenhagen) |
| `pipeline/nightly.sh` | build → Trivy gate → smoke tests → push → `podman auto-update` → tell the control plane |
| `dev-up.sh` | whole stack locally on 127.0.0.1:8080 (dev, uses the caller's podman socket); `--public <domain>` runs it on a real domain behind the password gate (see `../docs/deploy-public-test.md`; secrets in `deploy/.public.env`, gitignored). `SLOPTOWN_MIIRA_ORIGIN=<origin>` uses a real Miira (miira3) instead of fake-miira, in either mode; see `../docs/miira-integration.md` §11 |
| `test/platform-smoke.sh` | quadlet dry-run + boots db/cp/egress/fake-miira from images and probes them |
| `test/wait-healthy.sh` | waits for cp/egress/fake-miira health |

## Tests

```bash
bash -n deploy/bootstrap.sh && deploy/bootstrap.sh --dry-run   # dry run still needs root and the vault mounted
deploy/test/platform-smoke.sh                                  # builds cp + fake-miira images, 8 checks
PIPELINE_GIT_PULL=0 deploy/pipeline/nightly.sh --no-push       # everything, no push, no rollout
```

## Runbook

### First install

1. Unlock the vault (`/root/UNLOCK.md`).
2. `sudo deploy/bootstrap.sh` from a checkout on the host (add `--dev` for
   fake-miira). It clones the repo to `/mnt/encrypted-at-rest/sloptown-home/sloptown`;
   everything below runs from that clone.
3. Fill `/etc/sloptown/sloptown.env` (every `CHANGE_ME`; `openssl rand -hex 32`
   for secrets) and review `/etc/sloptown/egress.env`.
4. Read the bootstrap's gVisor line. If rootless `runsc` failed, set
   `SLOPTOWN_OCI_RUNTIME=` (empty = crun) until it's fixed. That is a weaker
   sandbox; record the decision.
5. As `sloptown` (`sudo -iu sloptown`; set `XDG_RUNTIME_DIR=/run/user/$(id -u)` if needed):
   ```bash
   systemctl --user start sloptown-registry
   ~/sloptown/deploy/pipeline/nightly.sh --first-install     # build, gate, push; no rollout
   systemctl --user start sloptown-db sloptown-cp sloptown-egress sloptown-edge
   ```
6. DNS: `STUDIO_HOST` and `*.APPS_DOMAIN` → this host. Check
   `https://STUDIO_HOST/` and `podman logs sloptown-cp`.

### After a reboot

The vault is locked after every boot and `user@<uid>.service` for `sloptown`
is skipped until it is mounted (drop-in from bootstrap). Follow
`/root/UNLOCK.md`, then:

```bash
systemctl start sloptown-nft.service user@$(id -u sloptown).service
```

Quadlet units start with the user manager. Nothing reboots on its own:
unattended-upgrades runs with `Automatic-Reboot "false"`. Plan kernel-update
reboots so someone is there to unlock.

### Nightly pipeline

`sloptown-nightly.timer` runs `pipeline/nightly.sh` as `sloptown` every night,
even without code changes, so distro and base-image patches land daily:

1. `git pull --ff-only` (skip with `PIPELINE_GIT_PULL=0`), pull the upstream bases from `images/versions.env`;
2. `podman build --no-cache` of vibe-base/dev/runtime, control-plane, fake-miira, studio, edge, tagged `localhost:5000/sloptown/<name>:<YYYYMMDD-HHMM>`;
3. Trivy: fail on any **fixable CRITICAL** (`.trivyignore` at the repo root for accepted ones, with reason and expiry);
4. `images/test/smoke.sh`, `edge/test/validate.sh`, `deploy/test/platform-smoke.sh`;
5. tag `:latest`, push both tags;
6. `podman auto-update --rollback=true`: restarts quadlet units whose `:latest` changed and rolls a unit back if it fails its health check;
7. `POST /internal/images/rolled` on the control plane (via `podman exec sloptown-cp`): recycle unclaimed pool slots, roll published runtimes one at a time, replace idle editors;
8. keep the newest 7 dated tags locally.

A failure at any step stops the run before the push, and the unit shows as
failed:

```bash
systemctl --user status sloptown-nightly.service
journalctl --user -u sloptown-nightly.service -n 200
systemctl --user start sloptown-nightly.service     # re-run now
```

### Rollback

- **Automatic:** step 6 restores the previous image of a unit that does not
  become healthy.
- **Manual, a platform unit:** point `:latest` back at a known-good dated tag and restart:
  ```bash
  podman images localhost:5000/sloptown/control-plane          # find the tag
  podman tag localhost:5000/sloptown/control-plane:20261025-0230 localhost:5000/sloptown/control-plane:latest
  podman push --tls-verify=false localhost:5000/sloptown/control-plane:latest
  systemctl --user restart sloptown-cp sloptown-egress
  ```
- **Manual, Vibe images:** re-tag `vibe-dev`/`vibe-runtime` the same way, then
  recycle (next section) so pool and runtimes pick them up.
- Pause the pipeline while investigating: `systemctl --user stop sloptown-nightly.timer`.

### Recycle the warm pool / roll runtimes

```bash
podman exec sloptown-cp python - <<'PY'
import json, os, urllib.request
req = urllib.request.Request("http://127.0.0.1:8000/internal/images/rolled", method="POST",
    data=json.dumps({"devImage": os.environ["SLOPTOWN_DEV_IMAGE"], "runtimeImage": os.environ["SLOPTOWN_RUNTIME_IMAGE"], "tag": "manual"}).encode(),
    headers={"Content-Type": "application/json", "X-Sloptown-Internal": os.environ["SLOPTOWN_INTERNAL_SECRET"]})
print(urllib.request.urlopen(req, timeout=600).read().decode())
PY
```

To see the pool: `podman ps -a --filter label=sloptown.kind=dev`. Pool
containers are named `vibe-pool-*` until claimed.

### Firewall

`sloptown-nft.service` loads only the table `inet sloptown_vibe`. Inspect with
`nft list table inet sloptown_vibe`. Change the rules in
`edge/nftables/sloptown.nft` and re-run bootstrap, which copies the file to
`/etc/sloptown/` and checks it with `nft -c` first. See edge/AGENTS.md for why
the stock `nftables.service` must not be used here.

## Invariants

- **A Vibe must reach no host service.** On a rootful host, project bridges are host
  interfaces and an internal network still reaches the gateway IP (sshd, smtp, ...). Load
  `edge/nftables/sloptown.nft` (`harden-host.sh` or bootstrap) before running real Vibes, and
  check from inside one that only aardvark-dns answers on the gateway.
- **userns=auto needs podman's temp dir to be traversable** by the mapped ids: podman makes an
  intermediate rootfs mount in `image_copy_tmp_dir`. On this host another project's
  `/etc/containers/containers.conf.d/10-pathfindr.conf` points that at
  `/mnt/encrypted-at-rest/containers/tmp` (0700); `harden-host.sh` adds `o+x` (search only)
  to it and its parents, agreed with the host's owner, and leaves that config alone.
- **Over the libpod API the runtime is the service's.** A per-container `oci_runtime` in the
  create spec is accepted and ignored (podman 5.7), so gVisor comes from the socket the
  control plane uses, not from `SLOPTOWN_OCI_RUNTIME` (which only marks the container tag).
- **runsc needs `overlay2=root:memory` here.** With the default `root:self` gVisor keeps the
  rootfs overlay in a filestore file inside podman's intermediate mount, which is gone by
  `runsc delete`: every container delete then fails with HTTP 500 though the record goes.
- **Never force editor rollovers on a host full of idle editors.** Before
  `orchestrator.replace_dev` kept stopped editors stopped, a forced rollover started ~100
  idle test editors one after another and the host ran out of memory (2026-10-28).

- The host is shared (Docker, Falco, logitor run here). Bootstrap touches only
  its own files: `/etc/sloptown`, `/etc/sysctl.d/60-sloptown.conf`,
  `/etc/apt/apt.conf.d/52sloptown-unattended`, its apt repos, the `sloptown`
  user, one drop-in for its `user@` unit, a marked section in `/root/UNLOCK.md`
  and `subuid`/`subgid` entries.
- `net.ipv4.ip_unprivileged_port_start=80` lets rootless Caddy bind 80/443.
  It applies host-wide: any local user can then bind ports ≥ 80.
- `sloptown-cp` gets the `sloptown` user's podman socket. Whoever controls that
  container controls every Vibe container. Keep it minimal and patched. The
  socket is owned by the host user, so the unit maps it to the image's uid 1000
  with `UserNS=keep-id:uid=1000,gid=1000`. Without that mapping the control
  plane gets `Permission denied` and the warm pool stays empty. `dev-up.sh`
  does the same rootless, and as root (rootful dev) runs the cp as uid 0.
- The registry listens on 127.0.0.1:5000 only, over plain HTTP. It is marked
  insecure in the user's `registries.conf.d`.
- `userns=auto`: the `sloptown` user gets 16M subordinate ids (about 250
  containers at 65536 each). Bootstrap also adds `containers:2147483647:2147483648`
  for rootful runs. This dev host has neither yet, so `dev-up.sh` defaults to
  `SLOPTOWN_USERNS_AUTO=false`.
- Never run `bootstrap.sh` on a host without reading `--dry-run` first.
- Quadlet `HealthCmd=` values must not contain nested quotes: the quadlet
  generator drops one, the health check then always fails, and with
  `Notify=healthy` the unit never starts. Use `sloptown-healthcheck <url>`.

## Not verified on this host

- `bootstrap.sh` has never been run. It was checked only with `bash -n`; this
  host was not changed.
- Rootless gVisor (`runsc --ignore-cgroups`) with netavark networks. Rootless
  runsc has known limits; the bootstrap reports whether it works.
- `podman auto-update` rollback and the user-level quadlet units at runtime
  (`platform-smoke.sh` only dry-runs the quadlet generator).
- On-demand TLS issuance (needs real DNS).
