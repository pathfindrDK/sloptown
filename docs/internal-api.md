# SlopTown internal contracts

How SlopTown's own components talk to each other. The Miira side is in
[miira-integration.md](miira-integration.md). Change this file first when an
interface changes.

## Components, ports and names

| Component | Runs as | Listens | Notes |
|---|---|---|---|
| `edge` (Caddy) | container `sloptown-edge` | :80/:443 (dev :8080) | TLS, routing, `forward_auth` |
| `control-plane` | container `sloptown-cp` | :8000 | FastAPI, `sloptown.app:app` |
| `egress` | container `sloptown-egress` | :8200 (ASGI), :8201 (CONNECT/forward proxy) | inference + platform API on :8200 (`sloptown.egress.app:app`); raw forward proxy for allowlisted hosts on :8201, started by the same process |
| `postgres` | container `sloptown-db` | :5432 | database `sloptown` |
| `fake-miira` | container `sloptown-fake-miira` (dev) | :8100 | `fake_miira.app:app` |
| `studio` | static files served by edge (dev: Vite :5173) | | React SPA |
| `registry` | container `sloptown-registry` | :5000 | local image registry |

Per project `<pid>` (the Miira file id, lowercased, `[a-z0-9-]`):

| Thing | Name |
|---|---|
| podman network (internal, no default route) | `vibe-net-<pid>` |
| volume (code, git, `/data/app.sqlite`, OpenCode store) | `vibe-vol-<pid>` |
| dev container (`vibe-dev`) | `vibe-dev-<pid>` |
| runtime container (`vibe-runtime`) | `vibe-run-<pid>` |
| app host | `<slug>.APPS_DOMAIN` |
| preview host | `preview-<slug>.APPS_DOMAIN` |

Pool containers are named `vibe-pool-<hex8>` with volume `vibe-vol-pool-<hex8>`
and network `vibe-net-pool-<hex8>` until claimed; claiming **renames** the
container to `vibe-dev-<pid>`, the network attachment stays. Labels cannot be
changed after creation, so the project ↔ container/volume/network mapping lives
only in the `projects` row. Because of the rename, containers are addressed by
**IP** (from `inspect` on the project network), not by DNS name; the control
plane resolves it and hands it to the edge in `X-Vibe-Upstream`.

`sloptown-edge`, `sloptown-egress` and `sloptown-cp` are connected to every
project network (`podman network connect`). They do not forward packets
between networks.

### Inside `vibe-dev` (all as uid 1000 `vibe`, workdir `/workspace`)

| Process | Port | |
|---|---|---|
| `opencode serve --hostname 0.0.0.0 --port 4096` | 4096 | agent API (opencode-ai 1.19.x). HTTP basic auth: user `opencode`, password `VIBE_PROJECT_TOKEN` (set `OPENCODE_SERVER_PASSWORD`) |
| Vite dev server (`npm run dev`, frontend at `/workspace/web`) | 5173 | proxies `/api` → 8000 |
| `uvicorn main:app --reload` (backend at `/workspace/api`) | 8000 | FastAPI |
| `vibe-agentd` | 4097 | small helper: file tree, file read, git ops, SSE file events |

Volume layout `/workspace` (volume mount), with `/data` a symlink to
`/workspace/.data` so code and data travel together but are separable:

```
/workspace/
  AGENTS.md            platform-owned (copied from image on start; agent can't change it)
  web/                 React + Vite + TS app (package.json is platform-owned)
  api/                 FastAPI app (main.py, routes)
  .data/app.sqlite     app data (gitignored)
  .opencode/           OpenCode session store (gitignored)
  .git/
```

### Inside `vibe-runtime`

Read-only root, read-only mount of a **published snapshot** (`/srv/app`,
checked out from the volume's git at the published sha into
`vibe-pub-<pid>-<sha7>`), writable mount of `/data` only. One process:
`uvicorn miira_vibe.runtime:app` on :8000, which serves the built `web/dist`
and mounts the app's `api/main.py` under `/api`.

## Environment inside project containers

| Var | Value |
|---|---|
| `VIBE_PROJECT_ID` | `<pid>` (empty in a `vibe-dev` that started in the warm pool; egress derives the project from the token) |
| `VIBE_PLATFORM_URL` | `http://sloptown-egress:8200` |
| `VIBE_PROJECT_TOKEN` | per-project bearer token (dev and runtime get different tokens) |
| `VIBE_EDGE_SECRET` | shared secret the edge sets in `X-Vibe-Edge-Secret`; SDK rejects identity headers without it |
| `VIBE_MODE` | `dev` or `runtime` |
| `OPENAI_BASE_URL` | `http://sloptown-egress:8200/v1` |
| `OPENAI_API_KEY` | same value as `VIBE_PROJECT_TOKEN` (never the real key) |
| `HTTP_PROXY`/`HTTPS_PROXY` | `http://vibe:<VIBE_PROJECT_TOKEN>@sloptown-egress:8201` (token read from `Proxy-Authorization`, Basic password or Bearer; only `EGRESS_ALLOWLIST` hosts) |
| `NO_PROXY` | `sloptown-egress,localhost,127.0.0.1` |
| `VIBE_APPS_DOMAIN` | apps domain; Vite (dev) allows `.<domain>` hosts |
| `VIBE_HMR_CLIENT_PORT` | (dev, optional) public port for Vite HMR through the edge, e.g. `443` |
| `VIBE_DB_PATH` | (optional) SQLite path, default `/data/app.sqlite` |
| `VIBE_AI_MODEL` | default model for `ai.chat` (`SLOPTOWN_APP_AI_MODEL`, default `gpt-6-luna`) |
| `VIBE_AGENT_MODEL` / `VIBE_AGENT_SMALL_MODEL` | OpenCode's models (`SLOPTOWN_AGENT_MODEL` `gpt-6.1-sol`, `SLOPTOWN_AGENT_SMALL_MODEL` `gpt-6-luna`); OpenCode talks to the egress with the Responses API (`/v1/responses`) |
| `VIBE_DEV_ROLE` | (dev, optional) role of the SDK dev identity when no edge headers are present |

## Identity headers (edge → app)

Set by edge from the `forward_auth` response; the edge **strips** any incoming
`X-Vibe-*` from clients first.

| Header | |
|---|---|
| `X-Vibe-Edge-Secret` | `VIBE_EDGE_SECRET` |
| `X-Vibe-User` | per-Vibe pairwise id (member `id`) |
| `X-Vibe-Name` | display name, percent-encoded UTF-8 |
| `X-Vibe-Role` | `owner` \| `editor` \| `viewer` |
| `X-Vibe-Classes` | comma-separated, percent-encoded |
| `X-Vibe-Upstream` | (edge-internal) `host:port` to proxy to |

In dev preview the edge also passes `X-Vibe-View-As: owner|editor|viewer` from
the `vibe_view_as` cookie; the control plane answers with a fake identity for
that role (dev only, owner/editor of the project only).

## Control plane HTTP API

All JSON. Studio auth: session cookie `st_session` (host-only, `Secure`,
`HttpOnly`, `SameSite=Lax`, or `SameSite=None; Partitioned` when created from a
launch token) **or** `Authorization: Bearer <session token>`. Errors:
`{ "error": "<code>", "message": "<human text, Danish>" , ... }`. No session
→ `401`. A project container (agentd, OpenCode) or Miira not answering →
`503 { error: "upstream_unreachable" }` with `Retry-After: 5`.

### Studio runtime config

| Method | Path | |
|---|---|---|
| GET | `/api/config` | public: `{ miiraOrigins: string[], miiraOrigin }` from `SLOPTOWN_MIIRA_ORIGINS`. The studio loads it before rendering: `miiraOrigins` is the embed bridge's allowlist, `miiraOrigin` the target of deep links. One studio build serves every environment; `VITE_MIIRA_ORIGIN(S)` are only the fallback (mock mode, Vite dev server) |

### Auth

| Method | Path | |
|---|---|---|
| GET | `/auth/login?next=` | start OIDC (PKCE) |
| GET | `/auth/callback` | OIDC redirect URI |
| POST | `/auth/logout` | |
| POST | `/api/auth/launch` | `{ launchToken }` → `{ token, user, fileId, fileRole }`, sets partitioned cookie |
| GET | `/api/me` | `{ user: { sub, name, email, role, orgId, isSystemAdmin }, termsAcceptedAt }` |
| POST | `/api/terms/accept` | `{ version }` |
| GET | `/api/terms` | `{ version, html }` |

### Projects

| Method | Path | |
|---|---|---|
| GET | `/api/projects` | projects the user owns or has been shared (cached members) |
| POST | `/api/projects` | `{ fileId, title?, kind?: "webapp"\|"scratch" }` → `Project` (teachers, terms accepted; claims from warm pool; `scratch` converts the claimed container with agentd `/reset-scratch`, and leaves the project in `status: "error"` if that fails) |
| GET | `/api/projects/{pid}` | `Project`. The owner (per launch `file_role`) of a Vibe with no project yet gets `404 { error: "not_started", canStart: true }`: the studio asks webapp or "fra bunden" and POSTs. `403 { error: "terms_required" }` comes first if that owner has not accepted the current terms (the studio shows the terms modal and retries) |
| PATCH | `/api/projects/{pid}` | `{ title }` |
| POST | `/api/projects/{pid}/publish` | `{ sha }` → `{ publishedSha, url }` |
| POST | `/api/projects/{pid}/unpublish` | |
| GET | `/api/projects/{pid}/budget` | `BudgetStatus` for the caller's build bucket and the project's app bucket |
| POST | `/api/projects/{pid}/preview-session` | `{ viewAs?: "owner"\|"editor"\|"viewer" }` → `{ url }` one-time URL that sets the preview cookie on the preview host; with `viewAs` the URL carries `&viewAs=` so `/edge/session` also sets `vibe_view_as` |
| POST | `/api/projects/{pid}/app-session` | → `{ url }` one-time URL that sets the app cookie on the app host |
| GET | `/api/projects/{pid}/download` | `?sha=` (default HEAD) → `application/zip` of the tree (editors); agentd `/export` repacked |

A `scratch` ("fra bunden") project is never served on the web: `publish`,
`preview-session` and `app-session` answer `409 { error: "not_a_webapp" }`, its
`previewUrl` and `appUrl` are `null`, and the edge endpoints (`/edge/auth/*`,
`/edge/tls-ask`) treat its hosts as unknown. Its container runs no Vite or API.

```ts
type Project = {
  id: string; title: string; kind: "webapp" | "scratch"; slug: string; ownerSub: string;
  myRole: "owner" | "editor" | "viewer";
  status: "starting" | "ready" | "stopped" | "error";
  previewUrl: string | null; appUrl: string | null; publishedSha: string | null;
  createdAt: string; updatedAt: string;
};
type BudgetState = "ok" | "warning" | "nerfed" | "blocked";
type BucketStatus = { usedTokens: number; nerfLimitTokens: number | null;
  hardLimitTokens: number | null; state: BudgetState; resetsAt: string };
type BudgetStatus = { enabled: boolean; build: BucketStatus; app: BucketStatus };
```

### Threads and chat (proxied to `opencode serve` in `vibe-dev-<pid>`)

| Method | Path | |
|---|---|---|
| GET | `/api/projects/{pid}/threads` | `Thread[]` (`?archived=1` to include archived) |
| POST | `/api/projects/{pid}/threads` | `{ title? }` → `Thread` (creates an OpenCode session) |
| PATCH | `/api/threads/{tid}` | `{ title?, archived? }` |
| GET | `/api/threads/{tid}/messages` | full transcript `Message[]` |
| POST | `/api/threads/{tid}/messages` | `{ text, images?: [{ mime, dataUrl }] }` → `{ turnId, queued: boolean }` |
| POST | `/api/threads/{tid}/stop` | abort the running turn |
| GET | `/api/projects/{pid}/events` | SSE, see below |

```ts
type Thread = { id: string; projectId: string; title: string; ownerSub: string;
  createdAt: string; archived: boolean; status: "idle" | "running" | "queued" };
type Part =
  | { type: "text"; text: string }
  | { type: "tool"; tool: string; title: string; status: "running" | "done" | "error";
      input?: unknown; output?: string }
  | { type: "image"; mime: string; url: string };
type Message = { id: string; threadId: string; role: "user" | "assistant";
  authorName?: string; parts: Part[]; createdAt: string };
```

One AI turn runs per project at a time. A message posted while a turn is running
is queued (one queued turn per project; a second one gets
`409 { error: "queue_full" }`). Turns are refused with
`429 { error: "budget_blocked", retryAt }` when the build budget is exhausted.

Project SSE stream `GET /api/projects/{pid}/events` (`text/event-stream`),
events are `event: <name>\ndata: <json>`:

| event | data |
|---|---|
| `turn.started` | `{ threadId, turnId }` |
| `message.part` | `{ threadId, messageId, index, part: Part }` (upsert by index) |
| `message.created` | `Message` |
| `turn.finished` | `{ threadId, turnId, commitSha \| null, changedFiles: string[], status: "done" \| "stopped" \| "error" }` |
| `turn.queued` | `{ threadId, turnId }` |
| `files.changed` | `{ paths: string[] }` |
| `budget` | `BudgetStatus` |
| `thread.updated` | `Thread` |
| `ping` | `{}` every 15 s |

### Files and history (via `vibe-agentd`)

| Method | Path | |
|---|---|---|
| GET | `/api/projects/{pid}/files` | `{ files: [{ path, size, locked, changed }] }` (flat, sorted) |
| GET | `/api/projects/{pid}/files/{path}` | `{ path, content, locked, language }` (`?sha=` for a version) |
| GET | `/api/projects/{pid}/history` | `{ commits: Commit[], publishedSha }` |
| GET | `/api/projects/{pid}/history/{sha}/diff` | `{ sha, files: [{ path, status, patch }] }` |
| POST | `/api/projects/{pid}/restore` | `{ sha }` → `Commit` (new commit reverting the tree to `sha`) |

```ts
type Commit = { sha: string; message: string; authorName: string; threadId: string | null;
  threadTitle: string | null; createdAt: string; files: string[] };
```

Locked (platform-owned) paths: `AGENTS.md`, `web/package.json`,
`web/package-lock.json`, `web/vite.config.ts`, `api/requirements.lock`,
`.opencode/**`, `.data/**`. Hidden: `.git/**`, `node_modules/**`, `.opencode/**`, `.data/**`.

Commit trailers: every commit made by a turn carries
`Vibe-Thread: <tid>` and `Vibe-Author: <name>` trailers.

### `vibe-agentd` (inside `vibe-dev`, :4097, called only by the control plane)

Bearer `VIBE_PROJECT_TOKEN`. `GET /files`, `GET /files/{path}?sha=`,
`GET /history` → `{ commits: Commit[] }` (`threadId` from the `Vibe-Thread` trailer),
`GET /diff/{sha}`, `POST /commit { message, trailers }` → `{ sha | null, files }`
(`sha: null` when nothing changed), `POST /restore { sha, message, trailers }` →
`Commit`, `POST /export { sha }` → `application/x-tar` of the tree at sha with
the full sha in `X-Vibe-Sha`, or `422 { paths }` when the version changes
locked (platform-owned) files; used for publish and download. `GET /kind` →
`{ kind }`, `POST /reset-scratch` → `{ kind: "scratch", ...Commit }`: turns a
freshly claimed workspace into a "fra bunden" project (scratch template from
`/opt/template-scratch`, a new history with one "Start fra bunden" commit, the
untracked marker `.vibe-kind` that the entrypoint and supervisor read); `409`
once anything but the platform has committed or the tree has changes;
idempotent. `GET /events` (SSE `files.changed`), `GET /health`.

### Edge endpoints

| Method | Path | |
|---|---|---|
| GET | `/edge/auth/app` | `forward_auth` for app hosts. 200 + `X-Vibe-*` + `X-Vibe-Upstream`, or 302 to login, or 403 |
| GET | `/edge/auth/preview` | same for preview hosts; owner/editor only; upstream is `vibe-dev-<pid>:5173` |
| GET | `/auth/app-login?project=&kind=app\|preview&next=` | studio host: needs a studio session (else → `/auth/login`), checks the role, mints a one-time code and 302s to `<host>/_vibe/session?code=&next=`. `forward_auth` redirects here when the host cookie is missing |
| GET | `/edge/session?code=&next=&viewAs=` | served on app/preview hosts via edge; sets host-only cookie `vibe_app` / `vibe_preview` (`Secure; HttpOnly; SameSite=None; Partitioned`); on preview hosts `viewAs` (bound to the code, not trusted from the query alone) sets `vibe_view_as` the same way |
| GET | `/edge/tls-ask?domain=` | Caddy on-demand TLS check: 200 if `domain` is `<slug>.APPS` or `preview-<slug>.APPS` of an existing project, else 404 |

The edge passes the original host in `X-Forwarded-Host`. It strips
`Connection`, `Upgrade` and `Sec-WebSocket-*` from the `forward_auth`
subrequest only, so WebSocket upgrades (Vite HMR in the preview) are
authorised as plain HTTP and then proxied as WebSockets. Public clients reach
`/edge/session` only as `/_vibe/session` on app/preview hosts (the edge rewrites
it); `/edge/*` and `/internal/*` are never routed from the studio host.

### Internal endpoints (host pipeline → control plane)

Require `X-Sloptown-Internal: <INTERNAL_SECRET>`. Not routed by the edge;
called with `podman exec sloptown-cp` from the host.

| Method | Path | |
|---|---|---|
| POST | `/internal/images/rolled` | `{ devImage, runtimeImage, tag }` after the nightly pipeline pushed new images. Discard and refill unclaimed pool slots whose image id differs; roll published runtime containers one at a time (health-checked via `/healthz`, previous container kept until the new one is healthy); replace editor containers on the old image once idle (no running turn, no activity for 15 min). → `{ poolRecycled, runtimesRolled, editorsPending }` |

### Project networks

Created `internal: true`, `dns_enabled: true` (the libpod API defaults it to
false; containers resolve `sloptown-egress` by name) and
`network_interface: "vb<8 hex>"` (libpod API field, ≤ 15 chars) so the host
nftables rules in `edge/nftables/sloptown.nft` can match them by `iifname "vb*"`.

- `sloptown-edge`, `-egress` and `-cp` are attached to every project network.
  Recreating one of them drops those attachments, so the control plane
  re-attaches them at startup and every 30 s.
- A project network is never force-removed (podman would remove the attached
  platform containers too): the platform containers are disconnected first.
- Pool slots are tagged `<dev image id>#spec<N>`; bumping `SLOT_SPEC` in
  `orchestrator.py` recycles unclaimed slots built with an older spec.

### Publish

`POST /api/projects/{pid}/publish { sha }`: agentd exports the tree at `sha`
(`422 locked_files_changed` if platform files differ), the tar is imported into
a new volume `vibe-pub-<pid>-<sha7>`, and a one-shot, network-less vibe-dev
container runs `vibe-build` (entrypoint overridden) to produce `web/dist`. A
failed build → `422 { error: "build_failed", log }` (tail of the build output),
and the previously published version keeps running. Then `vibe-run-<pid>` is
recreated on the new volume.

### Platform API (called by app code via the SDK, through `egress`)

`egress` authenticates `Authorization: Bearer <VIBE_PROJECT_TOKEN>`, then
forwards to the control plane with `X-Vibe-Project: <pid>` and
`X-Vibe-Token-Kind: dev|runtime` and the internal secret
`X-Sloptown-Internal: <INTERNAL_SECRET>`.

| Method | Path | |
|---|---|---|
| GET | `/platform/members` | `{ version, members: [{ id, name, role, classes, avatarHue }] }` |
| GET | `/platform/members/events` | SSE `members.changed { version }` |
| GET | `/platform/whoami` | dev helper: the dev identity |

`/v1/*` on egress is an OpenAI-compatible reverse proxy (`/v1/chat/completions`,
`/v1/responses`, `/v1/models`, `/v1/embeddings`) that injects the real key,
meters tokens into `usage_events`, applies nerf (rewrites `model`) or blocks
with `429 { error: { type: "budget_exceeded", retry_at } }`. Header
`X-Vibe-User` (optional) attributes app usage to a member. A nerfed response
carries `X-Vibe-Nerfed: <model actually used>`. Throttled requests get
`429 { error: { type: "rate_limited", reason, message } }` with `Retry-After`
(throttles from `edge/egress.env`, see `control-plane/AGENTS.md`).

Project tokens are stored as SHA-256 hex digests (`sloptown.tokens.hash_token`)
in `projects.dev_token_hash` / `projects.runtime_token_hash`; the egress caches
lookups for 30 s, so a rotated token stays valid for up to 30 s.

Bucket rule: dev token → **build** bucket, charged to the user who started the
current turn (`projects.active_turn_sub`); runtime token → **app** bucket of the
project.

### Platform SDK browser routes (app backend, `miira_vibe.setup(app)`)

The browser SDK (`@miira/vibe`) never calls the platform directly. The app's
backend mounts these at `/_vibe`; the app is served under `/api`, so the browser
calls `/api/_vibe/*` (same origin, identity from edge headers):

| Method | Path | |
|---|---|---|
| GET | `/api/_vibe/me` | `User` |
| GET | `/api/_vibe/members` | `{ version, members: [{ id, name, role, classes, avatarHue }] }` |
| GET | `/api/_vibe/members/events` | SSE `members.changed { version }`, `ping` |
| POST | `/api/_vibe/ai/chat` | `{ messages, model?, stream? }` → `{ text }` or SSE `delta {text}` / `done` / `error {message}`; 429 `{ error: "budget_exceeded", message, retryAt }` |

Not to be confused with `/_vibe/session` at the root of app/preview hosts (edge).

### Preview error messages (app → studio)

On preview hosts (`preview-*`, framed), `@miira/vibe`'s `VibeProvider` posts
`{ type: "vibe:error", message, stack? }` to `window.parent` (target origin from
`document.referrer`) on `error`, `unhandledrejection` and `console.error`, and
`{ type: "vibe:ready" }` once, after its first render. The studio covers the
preview with a loading overlay until `vibe:ready`, the first `vibe:error`, or 4 s
after the iframe's `load` (apps whose editor still runs an older SDK).

### Internal webhook relay

`POST /hooks/miira/access-changed` (see Miira contract) → invalidates cache,
publishes `members.changed` to subscribers of `/platform/members/events`.

## Database (Postgres) — main tables

`users(sub pk, name, email, role, org_id, terms_version, terms_accepted_at, last_seen_at)`
`projects(id pk, title, slug unique, owner_sub, container_name, volume_name, network_name, image_tag, status, dev_token_hash, runtime_token_hash, published_sha, published_at, active_turn_sub, created_at, updated_at)`
`pool_slots(id pk, container_name, volume_name, network_name, image_tag, state: creating|ready|claimed|discarding, created_at)`
`threads(id pk, project_id, opencode_session_id, title, owner_sub, archived, created_at)`
`turns(id pk, project_id, thread_id, author_sub, status: queued|running|done|stopped|error, commit_sha, started_at, finished_at)`
`members_cache(project_id pk, version, payload jsonb, fetched_at)`
`usage_events(id, ts, project_id, user_sub, member_id, bucket, model, requested_model, input_tokens, output_tokens, nerfed)`
`budget_settings(id=1, payload jsonb, updated_at, updated_by)`
`budget_overrides(id, subject, subject_id, bucket, week_start, reset_at, extra_tokens, created_by, created_at)`
`audit_log(id, ts, actor_sub, actor_kind: user|admin|system, action, project_id, details jsonb, ip)`
`launch_jti(jti pk, exp)`
`sessions(id pk, token_hash, sub, created_at, expires_at, partitioned)`
