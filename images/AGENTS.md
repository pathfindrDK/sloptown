# images/ — the Vibe container images

Every Vibe runs in containers built from these images. Dependencies live only
in the images; project volumes hold code and data. Patching the image patches
every app.

| Image | What | Runs |
|---|---|---|
| `vibe-base` | `python:3.13-slim-bookworm` + Node 22 + uv; template deps locked into `/opt/vibe/venv` (on `PATH`) and `/opt/vibe/node_modules` (`NODE_PATH`); `miira_vibe` and `@miira/vibe` installed from `sdk/`; user `vibe` (uid 1000) | nothing |
| `vibe-dev` | base + OpenCode + git + ripgrep + vibe-agentd + `/opt/template` + `/opt/template-scratch` | supervisor: `opencode serve` :4096, Vite :5173, `uvicorn --reload` :8000, agentd :4097 |
| `vibe-runtime` | base with node/npm/uv removed | `uvicorn miira_vibe.runtime:app` :8000 (`/healthz`, `/api/*`, SPA) |

Build context is always the **repo root** (`.containerignore` there).

## Files

| Path | |
|---|---|
| `versions.env` | every pinned upstream: base images, uv, OpenCode, Caddy, Trivy. Read by the pipeline and `dev-up.sh`; bumped by Renovate |
| `vibe-base/Containerfile` | multi-stage: packs `sdk/js` (with `design/dist` for its theme), then base |
| `vibe-dev/Containerfile` | also bakes `template-design/` (catalog, palettes, styles) at `/opt/template-design`, outside `/opt/template` so it never lands in a project |
| `vibe-dev/entrypoint.sh` | seeds `/workspace` from `/opt/template` on first start (git init, commit "Start fra skabelon"); on **every** start rewrites `.git/info/exclude`, re-copies platform files read-only, commits them as "Platformopdatering" if they changed, symlinks `web/node_modules` → `/opt/vibe/node_modules`; sets `OPENCODE_SERVER_PASSWORD=$VIBE_PROJECT_TOKEN`. In a "fra bunden" project (`.vibe-kind` = `scratch`, written by agentd `/reset-scratch`) the only platform file is `AGENTS.md`, from `/opt/template-scratch`. A webapp with a design (`.vibe-design` = `<style> <palette>`, written by agentd `/design`) also gets `DESIGN.md`, `web/src/design.css` and `web/src/palette.css` from `/opt/template-design`; ids are checked against those directories, since the agent can write the marker |
| `vibe-dev/supervisor.py` | starts the four processes, restarts with backoff 1–30 s, forwards SIGTERM. Vite and the API never run (and are stopped at once) while `/workspace/.vibe-kind` says `scratch` |
| `vibe-dev/opencode.json` | OpenCode config: only provider `sloptown` (OpenAI-compatible, `OPENAI_BASE_URL`/`OPENAI_API_KEY` = egress + project token), model from `VIBE_AGENT_MODEL` (default `gpt-5.1`), `webfetch` denied |
| `vibe-dev/vibe-build.sh` | `vibe-build <dir>`: `vite build` of a published snapshot into `<dir>/web/dist` (used at publish, in a one-shot vibe-dev container) |
| `vibe-dev/platform-allowlist.txt` | sha256 of older platform-file versions that agentd's `/export` still accepts |
| `vibe-runtime/Containerfile` | |
| `vibe-agentd/` | FastAPI helper inside vibe-dev (files, git history/diff/commit/restore, export with platform-file check, SSE). Contract: `docs/internal-api.md` § vibe-agentd |
| `test/smoke.sh` | hardened end-to-end test of vibe-dev and vibe-runtime, incl. agentd `/design` + restart, and a `vibe-build` of every style in `template-design` with a palette |

## Build and test

```bash
# from the repo root
set -a; . images/versions.env; set +a
podman build -t localhost/sloptown/vibe-base:latest    -f images/vibe-base/Containerfile .
podman build -t localhost/sloptown/vibe-dev:latest     -f images/vibe-dev/Containerfile --build-arg OPENCODE_VERSION=$OPENCODE_VERSION .
podman build -t localhost/sloptown/vibe-runtime:latest -f images/vibe-runtime/Containerfile .
images/test/smoke.sh                       # 36 checks, ~4 min

# agentd unit tests (needs git in the container)
cd images/vibe-agentd && podman run --rm -v $PWD:/src:Z -w /src -e UV_PROJECT_ENVIRONMENT=/tmp/venv \
  ghcr.io/astral-sh/uv:python3.13-bookworm-slim sh -c 'apt-get -qq update && apt-get -qq install -y git >/dev/null; uv run pytest -q'
```

The nightly pipeline (`deploy/pipeline/nightly.sh`) does all of this with
`--no-cache`, Trivy-gates the result and pushes to `localhost:5000/sloptown`.

## Invariants

- **Read-only root.** Containers run `--read-only` with tmpfs `/tmp`,
  `--cap-drop ALL`, `no-new-privileges`. Anything that must write goes to
  `/workspace` (project volume), `/data` (→ `/workspace/.data` in dev, a volume
  subpath in runtime) or `/tmp`. Vite's caches are symlinks
  `/opt/vibe/node_modules/.vite{,-temp}` → `/tmp/vite{,-temp}`; the entrypoint
  and `vibe-build` create the targets.
- **No network installs.** Project containers have no route out except the
  egress proxy. Never add a step that needs npm/pip at container start.
- **Platform files** (`AGENTS.md`, `web/package.json`, `web/package-lock.json`,
  `web/vite.config.ts`, `api/requirements.lock`) come from `/opt/template` and
  are overwritten on every start. When you change one in `template/`, append the
  `sha256sum` of the **previous** version to `vibe-dev/platform-allowlist.txt`,
  or versions committed before the image update can no longer be published.
  Keep this list in sync with `PLATFORM_FILES` in `vibe-agentd/vibe_agentd/app.py`
  and `entrypoint.sh`. The design files (`DESIGN.md`, `web/src/design.css`,
  `web/src/palette.css`) are platform files too, but per style/palette from
  `/opt/template-design`; agentd's `platform_sources()` is the one list.
- **Design systems are installed for every Vibe.** Bootstrap, Tailwind (with its
  Vite plugin in the shared `vite.config.ts`), Bulma and Materialize are all in
  the template's `package.json`; `DESIGN.md` tells the agent which one to use.
  A new design system means: its package in `template/web/package.json`, a
  `template-design/styles/<id>/` and a catalog entry (see `template-design/AGENTS.md`).
- **`@miira/vibe` is not in the template's package.json.** vibe-base packs
  `sdk/js` and copies it into `/opt/vibe/node_modules/@miira/vibe`.
- **OpenCode** listens on 0.0.0.0:4096 with basic auth (`opencode` /
  `VIBE_PROJECT_TOKEN`). `OPENCODE_DISABLE_PROJECT_CONFIG=1` stops the agent
  from overriding its own config from `/workspace`; models fetch, LSP download,
  autoupdate, share and default plugins are off (no egress for them anyway).
- **`VIBE_PROJECT_ID` may be empty** (warm-pool container). Nothing in the
  images may depend on it.
- **vibe-runtime has no node, git or uv** (smoke-tested). Removing them in a
  later layer doesn't shrink the image but keeps them out of the runtime and
  out of Trivy's findings.
- Image `HEALTHCHECK` is ignored in OCI format; the control plane checks
  `/healthz` (runtime) and agentd `/health` (dev) itself.

## Known gaps

- OpenCode is pinned to 1.19.4 (released 2026-10-22) because the control
  plane targets the 1.19 API; Renovate's 14-day minimum age applies from the next bump.
- `vibe-build` skips `tsc --noEmit` (the template's `npm run build` runs it):
  a type error does not block publish. Decide if it should.
- Not yet tested under gVisor (`runsc`) or `userns=auto`; run `smoke.sh` with
  those once the host has them (see deploy/AGENTS.md).
