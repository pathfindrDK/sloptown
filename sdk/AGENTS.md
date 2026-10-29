# Platform SDK — `miira_vibe` (Python) and `@miira/vibe` (JS)

The SDK is how every Vibe app gets identity, members and AI. It is platform
code: installed into `vibe-base`, never copied into projects, so the agent
can't edit it and it is patched with the image. Apps use it as documented in
`template/AGENTS.md` (the in-container reference; keep it in sync).

## Data flow

```
browser ──/api/_vibe/*──▶ app backend (miira_vibe.setup) ──Bearer VIBE_PROJECT_TOKEN──▶ egress :8200
   │                          ▲                                                        ├─ /platform/* → control plane → Miira
   │                          │ X-Vibe-* + X-Vibe-Edge-Secret                          └─ /v1/*       → OpenAI (key injected, metered)
   └──────── edge (forward_auth) ┘
```

- The browser SDK only talks to its own app backend, same origin, at
  `/api/_vibe/*` (contract: `docs/internal-api.md` "Platform SDK browser routes").
- The backend SDK trusts `X-Vibe-*` only with a matching `X-Vibe-Edge-Secret`.
  In `VIBE_MODE=dev` without them it uses a dev identity (`VIBE_DEV_ROLE`).
- Platform calls use `httpx` with `trust_env=False` so they bypass
  `HTTP(S)_PROXY` (the CONNECT proxy is for allowlisted outside hosts only).

## File map

| Path | What |
|---|---|
| `python/miira_vibe/identity.py` | `User`, `current_user`, `require_role`, header parsing, dev identity |
| `python/miira_vibe/members.py` | `list/get/fetch` (15 s cache), `sync(conn)` into SQLite `members`, `on_members_changed`, SSE watcher, `subscribe()` fan-out |
| `python/miira_vibe/ai.py` | `chat`, `stream` (OpenAI chat completions via `OPENAI_BASE_URL`), `BudgetExceeded(retry_at)` |
| `python/miira_vibe/db.py` | `connect()`, `session()` for `/data/app.sqlite` (`VIBE_DB_PATH`) |
| `python/miira_vibe/router.py` | `setup(app)`: mounts `/_vibe/{me,members,members/events,ai/chat}` and runs the members watcher in the app lifespan |
| `python/miira_vibe/runtime.py` | `miira_vibe.runtime:app` for vibe-runtime: `/healthz`, `/api` → `api/main.py`, SPA from `web/dist` (`VIBE_APP_DIR`) |
| `python/miira_vibe/_env.py` | all env lookups (read at call time) |
| `python/tests/` | pytest, respx-mocked platform |
| `js/src/client.ts` | `VibeClient` (fetch + SSE parsing), `BudgetExceededError`, types |
| `js/src/react.tsx` | `VibeProvider`, `useCurrentUser`, `useRole`, `RequireRole`, `useMembers`, `useAi`, `miiraTheme` |
| `js/src/errors.ts` | preview error bridge: posts `{type:"vibe:error", message, stack}` to the studio, and `announceReady()` posts `{type:"vibe:ready"}` after `VibeProvider`'s first render |
| `js/src/base.css` + `js/scripts/copy-theme.mjs` | builds `dist/theme.css` = `design/dist/tokens.css` (+ fonts) + base styles. Miira's tokens have no `--miira-color-*`/`--miira-space-*`/`--miira-font-family`; `base.css` defines them as aliases of Miira tokens (as `studio/src/styles/tokens.css` does). Published apps use those names: never drop one. A test checks that every `var(--miira-*)` in `theme.css` is defined |
| `js/test/` | `node --test` against the built `dist/` |

SQLite access uses the standard library `sqlite3` (no SQLAlchemy), so apps get
no extra dependency. `members.sync()` takes an optional `sqlite3.Connection`.

## Build and test (podman, from the repo root)

```bash
# Python: 11 tests
podman run --rm --network host -v $PWD:/src:Z -v sloptown-uv-cache:/root/.cache/uv -w /src/sdk/python \
  -e UV_LINK_MODE=copy -e UV_PROJECT_ENVIRONMENT=/tmp/venv ghcr.io/astral-sh/uv:python3.13-bookworm-slim \
  uv run -q pytest -q -p no:cacheprovider

# JS: typecheck, build dist/ (needs design/dist/tokens.css), 5 tests
podman run --rm -v $PWD:/src:Z -w /src/sdk/js docker.io/library/node:22-bookworm-slim sh -c 'npm ci && npm test'
```

Also run the template tests (`template/README.md`) after any SDK change: the
template is the SDK's main consumer.

## How changes reach apps

1. `images/vibe-base/Containerfile` installs `sdk/python` into `/opt/vibe/venv`
   (after `uv pip sync` of `template/api/requirements.lock`, so the SDK must
   work with exactly those versions) and packs `sdk/js` (`npm pack`) into
   `/opt/vibe/node_modules/@miira/vibe`.
2. The nightly pipeline rebuilds vibe-base → vibe-dev/vibe-runtime, scans, smoke
   tests and rolls out (`deploy/`). Editors pick it up when idle; published
   apps are rolled one at a time.
3. There is no per-project pinning: every app runs the SDK of its image.

## Versioning

- Semantic versions, same number in `python/pyproject.toml`,
  `miira_vibe/__init__.py`, `js/package.json` and `js/src/index.ts`.
- Within a major version: **backward compatible only**. Add functions and
  optional parameters; never rename, remove or change the meaning of an
  existing name, route, header or return field. Apps written by the agent
  months ago must keep working after a nightly rebuild.
- A breaking change is a new major: it ships as a new base image line
  (`vibe-base:2`), with a codemod run on publish, while the old line keeps
  getting patches.
- Wire contracts (`/api/_vibe/*`, `X-Vibe-*`, `/platform/*`, `vibe:error`, `vibe:ready`) are in
  `docs/internal-api.md`; change that file first.

## Invariants

- Never expose a Miira credential, the OpenAI key, the service-level `sub` or
  e-mail addresses to app code. Member ids are per-Vibe pairwise ids.
- Identity headers are worthless without the edge secret; keep the
  constant-time comparison and the runtime 401.
- The dev identity exists only when `VIBE_MODE=dev`; vibe-runtime sets
  `VIBE_MODE=runtime`.
- No dependencies beyond `fastapi`, `httpx`, `starlette` (Python) and the React
  peer (JS).
- Error texts shown to users are Danish.
