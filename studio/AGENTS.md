# studio — notes for coding agents

The teacher-facing web app: project list, the Vibe editor (preview, code,
chat, files, history), the viewer for published apps, and the iframe embed
used by Miira's drive. React 18 + Vite + TypeScript, no state library.

Contracts it implements (change those docs first, then this code):
- `../docs/internal-api.md` — every control-plane endpoint and SSE event.
- `../docs/miira-integration.md` §3 — the `drive:*` postMessage protocol.

## Commands

No Node on the host; run everything in a container from the repo root:

```bash
N="podman run --rm -v /mnt/encrypted-at-rest/sloptown:/src:Z -w /src/studio docker.io/library/node:22-bookworm-slim"
$N npm install
$N npm run typecheck        # tsc --noEmit
$N npm test                 # vitest (jsdom)
$N npm run build            # -> dist/  (served by the edge in prod)
$N npm run build:mock       # -> dist-mock/ with the in-browser fake backend
```

Dev server (proxies `/api`, `/auth`, `/edge` to `SLOPTOWN_CP_URL`, default
`http://localhost:8000`):

```bash
podman run --rm -it --network host -v /mnt/encrypted-at-rest/sloptown:/src:Z -w /src/studio \
  docker.io/library/node:22-bookworm-slim npm run dev        # or dev:mock
```

Screenshots: build mock, `vite preview --outDir dist-mock`, drive it with
`mcr.microsoft.com/playwright:v1.49.1-noble` (set `locale: "da-DK"`).
`docs/screenshot-*.png` were made that way.

## Map

| Path | What |
|---|---|
| `src/main.tsx` | boot; installs the mock transport when `VITE_MOCK=1` |
| `src/App.tsx` | routes and the standalone/embedded shells |
| `src/api/types.ts` | types mirroring internal-api.md |
| `src/api/client.ts` | typed `api.*` for every endpoint; `Transport` seam; in-memory bearer |
| `src/api/sse.ts` | SSE parser (fetch-based so the bearer header can be sent) |
| `src/api/mock.ts` | fake control plane: streaming turns, queue, files, history, publish, restore, budgets |
| `src/lib/config.ts` | runtime config from `GET /api/config` (Miira origins), loaded in `main.tsx` before render; `VITE_MIIRA_ORIGIN(S)` are only the fallback |
| `src/lib/projectState.ts` | reducer for the editor's live state, fed by SSE |
| `src/lib/embedBridge.ts` | `drive:*` bridge with origin allowlist |
| `src/lib/shell.tsx` | `Shell`: share / close / titleChanged, standalone vs embedded |
| `src/lib/budget.ts` | meter maths and Copenhagen reset formatting |
| `src/lib/design.ts` | `useDesign()`: the design catalog (`GET /api/design`), fetched once |
| `src/lib/i18n.tsx` | Danish + English dictionaries, `useT()` (stable per locale, so `t` is safe in effect dependencies; a new function per render once looped `ProjectList`'s load into the edge's rate limit) |
| `src/lib/lineDiff.ts`, `fileTree.ts` | changed-line highlight, flat list → tree |
| `src/pages/` | `ProjectList`, `ProjectPage` (+ `Viewer`), `StartChooser` (the owner's first open: "En app" or "Fra bunden", on `not_started`; "En app" then asks for the app's name (the project title), a design system and a palette, with a `DesignPreview` beside them; "Overrask mig" leaves the palette to the control plane's random pick), `EmbedGate` |
| `src/editor/` | `Editor` layout, `Preview` (welcome cover while `onlyPlatformCommits`: every commit is `SlopTown`'s, i.e. template seed, design starter, platform updates; once a turn runs or a message is queued it becomes the building cover: tiles and a countdown from `FIRST_BUILD_MS` (3 min) since `firstMessageAt`, then "nearly there"; it stays up 5 s after the turn so the welcome doesn't flash before the history reloads), `CodeView` (lazy, CodeMirror 6), `ChatPanel` (thread list under a "Tråde" label with "Vis arkiverede" and "Ny tråd", then the conversation), `FilesPanel`, `HistoryPanel`, `SettingsPanel` (icon, design system and palette, who can open the app, description). A new palette is saved at once; a new design system is confirmed (the dialog shows a `DesignPreview` with the current palette), saved, and then a prompt is put in the chat composer for the teacher to send (`askAiToFix`), since the AI must rebuild the UI on their budget |
| `src/components/` | `TopBar`, `Budget` (meter + modal), `Modal`/`ConfirmModal`, `Menu`, `Select` (Miira dropdown; options may carry an `icon`), `Swatch` (a palette as dots), `DesignPreview` (a mock Vibe with its name in a style and palette, light/dark toggle; CSS-drawn, not the real framework), `TermsModal`, `PublishError` (build log → "Ret fejlen" in chat) |
| `src/styles/tokens.css` | **placeholder** Miira tokens; overwritten by `design/sync-from-miira.mjs` |
| `src/styles/app.css` | all component CSS |
| `public/mock-preview.html` | stand-in Vibe app used by mock mode |

## Project kinds

`Project.kind` is `webapp` or `scratch` ("fra bunden"). A scratch project has no
preview tab (the editor opens on `README.md`), no Udgiv (top bar and history), and
the viewer says there is nothing to open. "Hent filer" (`api.download`, a fetched
.zip so an embedded session's bearer is sent) is there for both kinds.

## Routes

| Route | |
|---|---|
| `/` | project list; "Ny Vibe" (teachers) → terms modal on first use → Miira `/drive/new?type=vibe` (mock: creates directly) |
| `/p/:pid` | editor; viewers are redirected to `/view` |
| `/p/:pid/view` | published app via `POST app-session` one-time URL |
| `/embed/:pid`, `/embed/:pid/view` | same, inside Miira's iframe (`?chrome=none` hides back/title) |

## State and live updates

- `Editor` owns one `useReducer(projectReducer)` and one SSE subscription to
  `/api/projects/{pid}/events`; every event goes straight to the reducer.
- `message.part` is an upsert by `(messageId, index)`; the message is created on
  its first part. Don't append parts.
- `turn.finished` bumps `historyVersion` and `filesVersion`; views refetch on
  those counters rather than patching lists by hand. The preview reloads on
  `historyVersion`.
- `changedFiles` = files touched by the running/last turn; used for the tree
  markers and the CodeMirror line highlight (vs. the previous commit).
- The editor opens the chat on a fresh conversation (`activeThreadId = null`),
  or on the running/queued thread if a turn is under way. The thread is created
  on the first send, titled from that message; "Ny tråd" only clears the view,
  so no empty threads are left behind.
- One AI turn per project: the reducer tracks `runningThreadId` and one
  `queuedThreadId`; the API answers `409 queue_full` / `429 budget_blocked`.

## Embed rules

- The embed allowlist comes from the control plane at boot (`lib/config.ts`), never
  from the build: the same image runs against every Miira environment. Without it
  the bridge ignores every message and the iframe waits forever ("Venter på Miira").

- Never trust a message whose `event.origin` isn't in `VITE_MIIRA_ORIGINS` or
  whose `event.source` isn't `window.parent`. After `drive:init` the bridge only
  accepts and sends to that one origin.
- The launch token is exchanged once at `POST /api/auth/launch`; the bearer is
  kept in memory only (`setBearer`), never in storage.
- Components never call `postMessage` directly; they use `useShell()`.
- The preview shows a loading overlay until `{type:"vibe:ready"}` (or an error,
  or 4 s after `load`), so a compiling Vite dev server never shows a white page.
- Preview errors arrive as `{type:"vibe:error", message, stack}` from the
  preview origin only (posted by `@miira/vibe`'s `VibeProvider`).

## Design rules

- Only `--miira-*` variables for colours, type, spacing, radius, shadow. No hex
  values outside `tokens.css`. One exception: `Swatch` and `DesignPreview` paint a
  palette's own colours from the catalog (data, not styling; the preview's grey
  "Overrask mig" palette too), and `.design-preview[data-style]` uses each design
  system's own corners, fonts and shadows, since it imitates them.
- Patterns: `.btn .btn--3d [.btn--primary|.btn--danger]`, `.modal`, `.menu`,
  `.chip`, `.icon-btn`, `.field` (text inputs and textareas), `.select-field`.
  Icons from `lucide-react`.
- The design guide's `components.css` (vendored) is **not** loaded, only its
  tokens. A guide component the studio uses is re-created in `app.css` from the
  guide's rules (`.field`, `.select-field`); copy them rather than inventing a
  look. Selectable cards are `.choice-card` in a `role="radiogroup"` (Indstillinger,
  the start screen's design step). Selected ones (`.choice-card.is-active`) use the accent edge and
  the guide's checked-row tint, and still lift on hover.
- Theme via `data-theme` on `<html>` (`applyTheme`); embedded follows
  `drive:init` / `drive:theme`, standalone follows the OS.
- Copy: add every string to both `da` and `en` in `i18n.tsx`; Danish is the
  source. The `en` table is typed against the `da` keys, so a missing key fails
  the typecheck.
- No `alert`/`confirm`/`prompt`: use `ConfirmModal`.

## Not done / known gaps

- Code view is read-only (v1). No hand editing.
- "Se version" opens the commit's first changed file at that sha; it does not
  run an old version of the app.
- `tokens.css` and fonts are placeholders until the design sync runs.
- Against the real stack, `../tests/e2e/browser.sh` covers the embed handshake, terms,
  editor, preview and code view; chat streaming and publish from the UI are covered
  at the API level by `../tests/e2e/test_e2e.py` and in mock mode here.
