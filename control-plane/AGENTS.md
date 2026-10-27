# control-plane — notes for coding agents

The control plane (`sloptown.app:app`, :8000) is SlopTown's brain: it signs teachers in
through Miira, owns projects and their containers, proxies chat to OpenCode, serves
files/history, gates app and preview hosts for the edge, and relays Miira's member
list to apps. The egress/inference proxy (`sloptown.egress`) lives in the same package
and database; its notes are in the second half of this file.

Contracts (change them first, then the code):
- `../docs/internal-api.md` — every endpoint, event, header, env var and table here.
- `../docs/miira-integration.md` — OIDC, launch token, members API, webhook, admin JWT.

## Control plane

### Module map

| Module | Owns |
|---|---|
| `app.py` | App factory, routers, error handlers, lifespan (create tables, recover turns, pool + editor rollover loops), `/api/projects/{pid}/events`, `/health` |
| `config.py` | `Settings` from `SLOPTOWN_*` env vars |
| `db.py` / `models.py` | Async engine/sessions; all tables (shared with egress/budgets) |
| `errors.py` | `ApiError` and handlers; every error body is `{error, message, ...}` |
| `auth.py` | OIDC login (code + PKCE), `/api/auth/launch` (launch token → partitioned session, jti replay check), sessions, `current_principal`, `/api/me` |
| `miira.py` | Miira client: discovery (issuer vs back-channel base), JWKS + RS256 verify, code exchange, `client_credentials` service token |
| `terms.py` | Terms text and acceptance |
| `access.py` | Role resolution: owner → launch `file_role` → cached members; `require_project` |
| `members.py` | Members API fetch + `members_cache` (TTL, stale fallback), `for_app` strips `sub` |
| `projects.py` | Project CRUD, create = warm-pool claim (+ agentd `/reset-scratch` for `kind="scratch"`), publish/unpublish, budget, one-time host-session URLs, `/download` (.zip). The owner's first open answers `not_started`; a scratch project is refused every web path (`require_webapp`) and unknown to `edge.py` |
| `pool.py` | Warm pool: `claim` (`FOR UPDATE SKIP LOCKED`), `refill_once`/`refill_loop` (advisory lock), stale-image discard |
| `orchestrator.py` | Podman libpod REST (hardened specs, networks, volumes, rename, publish via volume import, runtime roll, editor replace); `FakeOrchestrator` for tests |
| `containers.py` | httpx clients for vibe-agentd (:4097, bearer) and opencode serve (:4096, basic) in `vibe-dev` |
| `threads.py` | Threads (= OpenCode sessions) and `TurnManager`: one turn per project + one queued, event mapping, commit after turn |
| `files.py` | Files, history, diff, restore via agentd; `commit_turn`; agentd `files.changed` relay |
| `events.py` | In-process pub/sub (`project:<pid>`, `members:<pid>`) and SSE helpers |
| `edge.py` | Caddy `forward_auth` (`/edge/auth/app`, `/edge/auth/preview`), `/auth/app-login`, `/edge/session`, `/edge/tls-ask`, dev view-as |
| `platform.py` | `/platform/*` for app code via egress (members, members SSE, dev whoami) |
| `hooks.py` | `/hooks/miira/access-changed` (HMAC + timestamp), cache refresh, notifications |
| `internal.py` | `/internal/images/rolled` and the idle-editor image rollover loop |
| `activity.py` | `touch(pid)` (debounced `last_active_at`), idle-stop loop for `vibe-dev` containers |
| `notify.py` | Budget notices to watchers: levels once per window, SMTP after commit (egress calls it) |
| `audit.py` | `audit(...)` helper; add the row in the same transaction as the change |
| `tokens.py` | Random tokens, SHA-256 hashing, derived container tokens (dev/runtime) |

### Run tests

Needs Postgres on 127.0.0.1:55432 (container `sloptown-test-db`, user/pass/db `sloptown`):

```bash
podman run -d --name sloptown-test-db -e POSTGRES_USER=sloptown -e POSTGRES_PASSWORD=sloptown \
  -e POSTGRES_DB=sloptown -p 127.0.0.1:55432:5432 docker.io/library/postgres:17   # once

cd control-plane
podman run --rm --network host -v $PWD:/src:Z -v sloptown-uv-cache:/root/.cache/uv -w /src \
  -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_LINK_MODE=copy \
  ghcr.io/astral-sh/uv:python3.13-bookworm-slim uv run pytest -q tests/
```

Control-plane tests: `test_auth`, `test_projects` (incl. pool), `test_edge`,
`test_members_hooks`, `test_turns`, `test_files`, `test_internal`. Fixtures are in
`tests/a_support.py` (`env` fixture: ASGI client, `FakeMiira` on respx, `FakeOrchestrator`,
captured hub events). They reuse B's database fixtures (`sloptown_test_b`, truncated per
test), so both suites run in one session. Mock container services at
`a_support.dev_base(pid, port)` (`http://fake-vibe-dev-<pid>:<port>`).

Real-Podman integration tests (opt-in, needs the socket and `localhost/sloptown/vibe-dev:dev`):
see the command at the top of `tests/test_podman_integration.py` (`SLOPTOWN_PODMAN_IT=1`).
The end-to-end test of the whole stack is `../tests/e2e/run.sh`.

### Run locally

```bash
export SLOPTOWN_DATABASE_URL=postgresql+asyncpg://sloptown:sloptown@127.0.0.1:55432/sloptown
export SLOPTOWN_MIIRA_API_BASE=http://127.0.0.1:8100        # fake-miira
export SLOPTOWN_ORCHESTRATOR_ENABLED=false                   # no containers; or point PODMAN_URL at a socket
uv run uvicorn sloptown.app:app --port 8000                  # ONE worker
```

With real containers: `SLOPTOWN_PODMAN_URL=unix:///run/podman/podman.sock`,
`SLOPTOWN_DEV_IMAGE`/`RUNTIME_IMAGE`, `SLOPTOWN_OCI_RUNTIME=runsc` for gVisor. The control
plane must run as container `sloptown-cp` on the same Podman so it can join project networks.

### Invariants and gotchas

- **One turn per project.** All threads share one working tree. `TurnManager` holds a
  per-project lock; submit, finish→start-next and restore all take it. At most one queued
  turn (`409 queue_full`); budget is checked at submit *and* when a queued turn starts.
- **Idle editors are stopped, not removed.** `activity.idle_stop_loop` stops a `vibe-dev`
  after 30 min without activity, a running turn or an open project SSE stream; any later
  access (`containers.dev_host`, preview `forward_auth`) starts it again. Call
  `activity.touch(pid)` from new code paths that mean "someone is working here".
- **Editors restart `on-failure`, never `always`.** `podman-restart.service` starts every
  `always` container at boot, stopped ones included: one reboot woke ~90 idle editors at
  once and the host ran out of memory (load 370). Editors and pool slots are woken on
  access instead; only published `vibe-run-*` runtimes and platform containers are `always`.
- **Long-lived streams must not hold a DB connection.** A request's `Db` session lives
  until the response ends, which for SSE is hours. Every vibe container keeps
  `/platform/members/events` open and every editor `/api/projects/{pid}/events`, so
  call `await db.close()` after the access check and before returning the stream
  (`test_event_streams_do_not_hold_db_connections`). Otherwise the pool (5 + 10)
  runs dry and every request fails with 500.
- **Single process.** Turn state and SSE fan-out are in memory: run one uvicorn worker.
  On startup, turns left `running`/`queued` are marked `error`.
- **Pool claim locking.** `claim` uses `SELECT … FOR UPDATE SKIP LOCKED` and deletes the slot
  row in the caller's transaction; refill runs under a session advisory lock on a dedicated
  connection. An empty pool creates a container on demand (slow path).
- **Never forward `sub` to apps.** Members carry the service-level `sub` for our own mapping;
  `members.for_app`/`to_app` strip it. Apps get the per-Vibe `id` in `X-Vibe-User` (or a
  local HMAC fallback id), never `sub`.
- **Audit-log every state change** (`audit()` in the same transaction): login, launch,
  terms, project create/rename/publish/unpublish/restore, thread create/update, turn
  start/finish/stop, host sessions, member changes, image rollouts.
- **Tokens are derived, not stored.** Dev token = HMAC(internal_secret, seed); only hashes
  live in `projects.*_token_hash` (egress looks them up). Rotating `internal_secret`
  invalidates every container token.
- **Never force-remove a project network.** `podman network rm --force` also removes every
  attached container, i.e. sloptown-edge/-egress/-cp. `orchestrator._remove_network`
  disconnects whatever is attached (the reconnect loop may race it), then removes
  without force, retrying up to 3 times.
- **Project networks need `dns_enabled: true`** (the libpod API defaults it to false, the CLI
  to true); containers reach `sloptown-egress` by name. When the slot spec changes, bump
  `orchestrator.SLOT_SPEC` so unclaimed pool slots are rebuilt.
- **Platform containers lose project networks when recreated.** `app.reconnect_loop` re-attaches
  edge/egress/cp to every `vibe-net-*` network every 30 s (and at startup).
- **Containers are addressed by IP**, resolved per request (`orchestrator.address`), because
  claimed containers are renamed.
- **userns=auto needs host config**: a `containers` range in `/etc/subuid` and `/etc/subgid`
  (else container create fails with "not enough unused IDs"). `SLOPTOWN_USERNS_AUTO=false`
  turns it off for a dev box only.
- Columns added after a database exists go in `db.ADDED_COLUMNS` (idempotent DDL run by
  `create_all`), because `create_all` never alters a table. E.g. `projects.kind`.
- The runtime mounts `<volume>/.data` with a volume `SubPath`; publish creates the dir first
  via exec in `vibe-dev` (Podman refuses a missing subpath).
- A member missing from the cache triggers one forced recheck (at most every 10 s) before
  access is denied, so a fresh share works even if the webhook is late.
- `files.py` filters hidden paths (`.git/`, `node_modules/`, `.opencode/`, `.data/`) even
  if agentd returns them.
- OpenCode API targeted: opencode-ai **1.19.4** (`/session`, `/session/{id}/prompt_async`,
  `/session/{id}/message`, `/session/{id}/abort`, `/event`). Events used: `message.updated`,
  `message.part.updated`, `session.idle`, `session.status`, `session.error`.


## Egress, budgets, admin API

### Module map

| Module | What |
|---|---|
| `sloptown/budgets.py` | Weekly budget engine: window, settings (seeded off), `budget_status`, `decide`, `check_build_allowed`, `record_usage`, `reset`, `grant`, `limited_items` |
| `sloptown/admin.py` | `router` for `/admin/v1/*` (Miira system-admin backend only). JWT auth dependency `admin_claims` |
| `sloptown/egress/app.py` | Egress ASGI app on :8200: `/v1/*` inference proxy, `/platform/*` relay, `/health`. Starts the CONNECT proxy in its lifespan |
| `sloptown/egress/connect.py` | Raw asyncio CONNECT + absolute-form HTTP forward proxy on `EGRESS_PROXY_PORT` (8201), allowlist only |
| `sloptown/egress/auth.py` | Project token -> `Caller(project_id, kind)`; kind `dev` = build bucket, `runtime` = app bucket |
| `sloptown/egress/limits.py` | In-memory token bucket (RPS), sliding window (inference RPM), concurrency counter |
| `sloptown/egress/config.py` | `EgressConfig.load()` from `edge/egress.env` + env overrides; `host_allowed` |
| `../edge/egress.env` | Throttling defaults (commented) |

Run the egress: `python -m sloptown.egress` (or `uvicorn sloptown.egress.app:app --port 8200`,
**one worker**: throttles and the CONNECT server live in-process).

### Tests

Postgres must be running on 127.0.0.1:55432 (container `sloptown-test-db`, user/pass/db
`sloptown`). The tests create and use their own database `sloptown_test_b`.

```bash
cd control-plane
podman run --rm --network host -v $PWD:/src:Z -v sloptown-uv-cache:/root/.cache/uv -w /src \
  -e UV_PROJECT_ENVIRONMENT=/tmp/venv ghcr.io/astral-sh/uv:python3.13-bookworm-slim \
  uv run pytest -q tests/test_budgets.py tests/test_egress.py tests/test_admin.py
```

Fixtures are in `tests/b_support.py` (imported explicitly, not via conftest). OpenAI,
the control plane and Miira's JWKS are mocked with respx; the CONNECT test uses a
local echo server.

### Budget semantics

- Two buckets: **build** (per teacher, OpenCode usage from the dev container) and **app**
  (per published Vibe, usage by viewers through the runtime container). The token kind
  decides the bucket, never the request.
- Build usage is charged to `projects.active_turn_sub` (the author of the running turn),
  falling back to the project owner (e.g. the preview app calling AI between turns).
  App usage is charged to the project; `X-Vibe-User` is stored as `member_id` for reporting.
- Window: ISO week, Monday 00:00 **Europe/Copenhagen** (DST-safe). `resetsAt` = next Monday.
- `reset` inserts a `budget_overrides` row with `reset_at=now`, which moves the window
  floor forward for the current week. `grant` inserts `extra_tokens`, added to both the nerf
  and the hard limit for the current week. **The ledger (`usage_events`) is never updated
  or deleted.** Overrides only apply to the week they were made in.
- Limits: build limits per Miira role (`laerer`, `elev`, `admin`, `default`; `system-admin`
  maps to `admin`, unknown roles to `default`); app limits are one `perProject` pair.
  `null` = no limit.
- States: `blocked` (used >= hard), `nerfed` (used >= nerf: model swapped for `nerfModel`),
  `warning` (used >= lowest `warningThresholds` x hard), else `ok`. With `enabled: false`
  everything is `ok` and nothing is nerfed or blocked (the default on first boot).
- Blocked inference -> `429 {error:{type:"budget_exceeded", retry_at, bucket}}`. The control
  plane should call `check_build_allowed` before starting a turn so the teacher sees the
  modal instead of a failed agent run.
- Usage is metered after the call (from the response's `usage`), so the call that crosses a
  limit completes; the next one is nerfed/blocked.

### Throttling (`edge/egress.env`)

`EGRESS_ALLOWLIST` (forward-proxy hosts, `*.x.dk` and `:port` allowed; `/v1` and `/platform`
are always allowed), `EGRESS_RPS_PER_PROJECT` + `EGRESS_BURST_PER_PROJECT` (token bucket over
all egress), `EGRESS_MAX_CONCURRENT_PER_PROJECT` (open requests/tunnels; `/platform/*events`
streams exempt), `INFERENCE_RPM_PER_PROJECT` + `INFERENCE_RPM_GLOBAL` (sliding 60 s window),
`EGRESS_MAX_BODY_BYTES` (413 above), `EGRESS_TIMEOUT_SECONDS` (connect and idle-read timeout,
not total), `EGRESS_PROXY_PORT` (0 disables the CONNECT server). The file is found at
`SLOPTOWN_EGRESS_ENV_FILE` or `<repo>/edge/egress.env`; real environment variables override it.
`GET /admin/v1/egress-settings` returns the loaded values (read-only for now).

### Admin API auth

Bearer JWT from Miira: RS256 via Miira's JWKS (`sloptown.miira.miira.verify`), `aud=sloptown-admin`,
`role=system-admin`, `jti` required and single-use (stored in `launch_jti` as `admin:<jti>`).
`settings.admin_allowed_ips` (CIDRs) is checked against the first `X-Forwarded-For` hop. Every
write calls `audit(...)` with `actor_kind="admin"` and the admin's `sub`. Reset/grant only
accept `user+build` or `project+app`.

### Gotchas

- Run the egress with **one** worker. More workers multiply every limit and fight over port 8201.
- The token lookup cache is 30 s: a revoked token keeps working for up to 30 s.
- Streaming chat completions always get `stream_options.include_usage=true`, so clients see one
  extra final chunk with empty `choices` (OpenAI SDKs handle it). Without usage in the
  response nothing is recorded (logged as a warning).
- Upstream is asked for `Accept-Encoding: identity` so SSE can be scanned for usage.
- `/v1/*` only allows `chat/completions`, `responses`, `embeddings`, `models`; anything else is 404.
- The forward proxy does not resolve or block private IPs itself; it relies on the explicit
  allowlist (and nftables). Never put wildcards for internal domains in `EGRESS_ALLOWLIST`.
- Notifications (`notify.py`): after each usage event the egress records newly reached
  levels (each warning threshold, `nerfed`, `blocked`) in `budget_notifications`, once per
  subject, bucket and window floor (week start or last reset, so a reset re-arms them),
  and mails `watcherEmails` after commit via `SLOPTOWN_SMTP_*` (unset host = log only).
  A failing check never loses the usage row (separate transaction).
- In a container, set `SLOPTOWN_EGRESS_ENV_FILE` (the default path assumes the repo layout).
