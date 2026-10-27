# SlopTown

Platform for AI-built teacher apps ("Vibes") inside Miira. Teachers describe an
app in the studio, an OpenCode agent builds it in an isolated container, and the
published app is shared through Miira's normal share dialog.

- Architecture and component contracts: [docs/internal-api.md](docs/internal-api.md)
- What Miira implements: [docs/miira-integration.md](docs/miira-integration.md)
- Repo map and ground rules: [AGENTS.md](AGENTS.md)

## Run locally

```bash
deploy/dev-up.sh          # podman: db, fake-miira, control plane, egress, edge on :8080
```

Then open http://miira.sloptown.localhost:8080 (fake Miira drive), sign in as the
seeded teacher and create a Vibe.

## Public test on a real domain

`deploy/dev-up.sh --public miira-apps.dk` runs the same stack on
https://studio.miira-apps.dk behind a password gate; see
[docs/deploy-public-test.md](docs/deploy-public-test.md).

## Production host

`deploy/bootstrap.sh` prepares the host (rootless Podman, gVisor, nftables,
unattended-upgrades, local registry). Data lives on the LUKS volume at
`/mnt/encrypted-at-rest`; see `/root/UNLOCK.md` on the host for the unlock
procedure after a reboot.
