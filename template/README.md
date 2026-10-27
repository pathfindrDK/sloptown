# Vibe app template (maintainers)

The boilerplate every new Vibe starts from. `images/vibe-dev` copies this
directory to `/opt/template`; its entrypoint seeds `/workspace` from it on the
first start of a project and re-copies the platform-owned files on every start.

`AGENTS.md` here is **not** for you: it is the in-container instruction file
OpenCode reads while building a teacher's app (rules + SDK reference). Keep it
in sync with the SDK's public API (`sdk/AGENTS.md`).

## File map

| Path | Owner | Notes |
|---|---|---|
| `AGENTS.md` | platform (locked) | agent rules + SDK reference |
| `web/package.json`, `web/package-lock.json` | platform (locked) | frontend deps; `npm ci`'d into vibe-base at `/opt/vibe/node_modules`. `@miira/vibe` is **not** listed: vibe-base copies the SDK into `node_modules/@miira/vibe` |
| `web/vite.config.ts` | platform (locked) | port 5173, `/api` → `127.0.0.1:8000` with the `/api` prefix stripped (same as vibe-runtime), `allowedHosts` from `VIBE_APPS_DOMAIN`, HMR port from `VIBE_HMR_CLIENT_PORT` |
| `web/tsconfig.json`, `web/index.html`, `web/src/**` | app (agent edits) | example UI: greeting, guestbook, AI box, member list |
| `api/requirements.in` → `api/requirements.lock` | platform (locked) | hashed, compiled for Python 3.13 / linux x86_64; `uv pip sync`'d into vibe-base |
| `api/main.py` | app (agent edits) | example routes `/hej`, `/klassen`, `/hilsner`, `/spoerg` |
| `tests/` | platform | maintainer tests for the template backend (shipped in the image but unused there) |

Locked = listed in `PLATFORM_FILES` in `images/vibe-dev/entrypoint.sh` and the
locked-path list in `docs/internal-api.md`. Adding a locked file means updating
both.

## Build and test (podman, from the repo root)

```bash
# JS SDK must be built first (template links it for local builds)
podman run --rm -v $PWD:/src:Z -w /src/sdk/js docker.io/library/node:22-bookworm-slim sh -c 'npm ci && npm run build'

# Frontend: install from the lock, link the SDK, typecheck + build
podman run --rm -v $PWD:/src:Z -w /src/template/web docker.io/library/node:22-bookworm-slim \
  sh -c 'npm ci && npm run sdk:link && npm run build'

# Backend tests (FastAPI TestClient, SDK dev identity, AI mocked)
podman run --rm --network host -v $PWD:/src:Z -v sloptown-uv-cache:/root/.cache/uv -w /src/template \
  -e UV_LINK_MODE=copy ghcr.io/astral-sh/uv:python3.13-bookworm-slim \
  uv run -q --no-project --with-requirements api/requirements.lock --with ../sdk/python \
    --with pytest --with respx pytest -q -p no:cacheprovider tests
```

Updating dependencies (also done by Renovate):

```bash
# frontend
podman run --rm -v $PWD:/src:Z -w /src/template/web docker.io/library/node:22-bookworm-slim npm update
# backend: only versions at least 14 days old
podman run --rm --network host -v $PWD:/src:Z -w /src/template/api ghcr.io/astral-sh/uv:python3.13-bookworm-slim \
  uv pip compile -q --generate-hashes --python-version 3.13 --python-platform x86_64-unknown-linux-gnu \
  --exclude-newer "$(date -d '14 days ago' +%F)" requirements.in -o requirements.lock
```

Security fixes may skip the 14-day wait, one package at a time: add
`--exclude-newer-package <pkg>=<today> --upgrade-package <pkg>` to the compile
command (the command line is recorded at the top of the lock, so the exception
is visible). Example: certifi 2026.10.23 for GHSA-6q48-6c7w-9gw2 (Trivy gate,
2026-10-26). Drop the exception the next time the lock is regenerated normally.

## Invariants

- Every dependency the agent may use is in the image; projects never install
  packages. Changing a lock changes vibe-base, so it goes through the nightly
  pipeline (scan + smoke test), never into a running project directly.
- The app backend is served under `/api` in both dev (Vite proxy rewrite) and
  runtime (`miira_vibe.runtime` mount), and sees paths without `/api`.
- The template uses only the SDK for identity, members and AI, and only
  `--miira-*` tokens for styling; it is the example the agent copies from.
- UI copy is Danish.

## OpenCode permissions

The provider and permissions live in `images/vibe-dev/opencode.json` (global,
`OPENCODE_CONFIG`), not in the template, because a project-level
`opencode.json` would be editable by the agent. Recommended rules (OpenCode
`permission` syntax, last matching rule wins), as defence in depth on top of
the read-only locked files and the egress allowlist:

```json
"permission": {
  "edit": { "*": "allow", "AGENTS.md": "deny", "web/package.json": "deny", "web/package-lock.json": "deny",
            "web/vite.config.ts": "deny", "api/requirements.lock": "deny", ".git/**": "deny" },
  "bash": { "*": "allow", "npm install*": "deny", "npm i *": "deny", "npx *": "deny", "pip *": "deny",
            "uv *": "deny", "git *": "deny", "curl *": "deny", "wget *": "deny" },
  "webfetch": "deny",
  "websearch": "deny",
  "external_directory": "deny"
}
```

`ask` must not be used: there is no human to answer prompts in `opencode serve`.
