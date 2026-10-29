# Miira ↔ SlopTown integration contract

Status: v1, implemented on both sides. Miira's side is in miira3 (branch
`feat/sloptown-vibe`, `docs/vibe-sloptown.md` there); `fake-miira/` implements the
same contract so SlopTown also runs end to end on its own. If the two disagree, this
document wins and both sides are fixed. "Running against miira3" at the end says how
to test the real pair.

Notation: `MIIRA` is Miira's public origin (e.g. `https://miira.dk`), `STUDIO` is
SlopTown's studio origin (e.g. `https://studio.sloptown.dk`), `APPS` is the apps
domain (e.g. `miira-apps.dk`, each app on `<slug>.APPS`).

---

## 1. Identifiers

Miira never sends its internal user id to SlopTown. It sends **pairwise
identifiers**, derived with one HMAC so no mapping table is needed:

```
pairwise(sector, miira_user_id) =
    base32(HMAC-SHA256(PAIRWISE_SECRET, sector + ":" + miira_user_id))[:26].lower()
```

| Sector                | Used for                                              | Who sees it               |
|-----------------------|-------------------------------------------------------|---------------------------|
| `sloptown`            | OIDC `sub`, launch token `sub`, admin API user refs   | SlopTown control plane    |
| `vibe:<file_id>`      | member `id` for one Vibe                              | that Vibe's app (and CP)  |

The service-level id (`sloptown` sector) lets SlopTown keep budgets and audit
per person. The per-Vibe id means two apps cannot correlate the same student.
SlopTown never forwards the service-level id into an app.

`PAIRWISE_SECRET` is Miira-internal and never leaves Miira.

## 2. The `vibe` file type

- New drive file type `vibe`. File content (JSON): `{ "projectId": "<file_id>" }`.
  In v1 the project id **is** the Miira file id; SlopTown creates the project
  lazily the first time the owner opens it. The value is opaque to Miira.
- Only users with Miira role `laerer` (teacher) may create Vibe files (v1).
- The first time the owner opens a new Vibe, the studio asks what it is: an app
  (preview, publish, students use it) or "fra bunden", an empty project for code
  that is never served on the web (Arduino, scripts), downloaded as a .zip. Nothing
  changes for Miira: same file type, same sharing; viewers of a "fra bunden" project
  just see that there is nothing to open.
- Deep links SlopTown uses when it runs standalone (outside the iframe):
  - `MIIRA/drive/new?type=vibe`: create a new Vibe file in the user's drive and
    open it. The studio's "Ny Vibe" button links here, because a Vibe must be a
    Miira file before SlopTown can create the project.
  - `MIIRA/drive/file/<file_id>?share=1`: open the file with the share dialog open
    (the studio's Share button when not embedded).
- Opening a Vibe file renders an iframe:
  - owner/editor: `STUDIO/embed/<projectId>?chrome=none`
  - viewer: `STUDIO/embed/<projectId>/view?chrome=none`
  Miira may always use the first URL; SlopTown redirects viewers to `/view`.
- iframe attributes: `allow="clipboard-write; fullscreen"`,
  `sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads"`.
- Sharing uses Miira's existing share dialog (people, classes). Roles map:
  Miira "ejer" → `owner`, "kan redigere" → `editor`, "kan se" → `viewer`.

## 3. postMessage subset (embed bridge)

SlopTown speaks a subset of Miira's existing `drive:*` protocol
(`drive/src/lib/embedBridgeTemplate.ts`). All messages are
`{ type: string, ...payload }`, posted with an explicit target origin; both sides
check `event.origin` against an allowlist.

| Direction       | type                 | payload                                              | Meaning |
|-----------------|----------------------|------------------------------------------------------|---------|
| SlopTown → Miira| `drive:ready`        | `{}`                                                 | iframe loaded, send init |
| Miira → SlopTown| `drive:init`         | `{ fileId, launchToken, theme: "light"\|"dark", miiraTheme, tokens, locale: "da"\|"en" }` | start session |
| Miira → SlopTown| `drive:theme`        | `{ theme, miiraTheme, tokens }`                      | theme switched in Miira, or the school's theme changed |
| SlopTown → Miira| `drive:share`        | `{ fileId }`                                         | open Miira's share dialog |
| SlopTown → Miira| `drive:title`        | `{ fileId, title }`                                  | rename the file |
| Miira → SlopTown| `drive:title`        | `{ fileId, title }`                                  | the file was renamed in Miira's top bar; the studio renames the project (owner/editor, trimmed to 120 characters) |
| SlopTown → Miira| `drive:toolbar`      | `{ fileId, toolbar: ToolbarState \| null }`          | what Miira's top bar may offer (sent on every change; `null` when the editor closes) |
| Miira → SlopTown| `drive:icon-state`   | `{ fileId, src, custom }`                            | the Vibe's icon as Miira shows it (a `data:image/...` URL), sent once the studio runs and on every change |
| SlopTown → Miira| `drive:icon`         | `{ fileId }`                                         | open Miira's icon dialog (Indstillinger → Skift ikon) |
| Miira → SlopTown| `drive:action`       | `{ fileId, action: "publish" \| "download" \| "unpublish" }` | a press on one of those actions; the studio runs it as if its own button was pressed (`unpublish` opens the studio's "Stop udgivelsen af appen?" confirmation) |
| SlopTown → Miira| `drive:close`        | `{}`                                                 | user pressed back |
| Miira → SlopTown| `drive:access-changed`| `{ fileId }`                                        | optional fast path after the share dialog closes |

Miira answers exactly these from the studio frame (`VibeView`, not its editor
iframe path): the studio is a third-party origin and gets none of the services the
drive offers its own editors. On `drive:ready` Miira mints a fresh launch token and
answers with `drive:init`; on `drive:title` it renames the file.

With `?chrome=none` (what Miira sends) the studio draws no top bar: Miira draws the only
one, like for every Miira editor: back, file icon, editable name, then the studio's
actions, Support, Del and the user menu. Del is Miira's own share dialog. The studio's
actions are described by `drive:toolbar` and pressed with `drive:action`:

```ts
type ToolbarState = {
  canEdit: boolean;
  publish: {                  // null: scratch or viewer
    state: "publish" | "update" | "published"; // which button to draw, see below
    published: boolean;         // = state === "published"; kept for builds before `state`
    busy: boolean;              // publishing or unpublishing right now
    disabled: boolean;
    canUnpublish: boolean;      // offer "Stop udgivelse" (published, nothing running)
  } | null;
  appUrl: string | null;      // the published app, opened by Miira in a new tab
  download: boolean;          // "Hent filer" may run now
  budget: {                   // the build budget meter; null when budgets are off
    state: "ok" | "warning" | "nerfed" | "blocked";
    fraction: number | null;  // 0..1 of the limit, null = no limit
    nerfMark: number | null;  // 0..1, where the downgrade limit sits
    usedTokens: number;
    limitTokens: number | null;
  } | null;
};
```

`miiraTheme` is Miira's theme (`miira`, `neutral`, `dark`, `kontrast` or `org`) and
`tokens` the school's own `--miira-*` values when it has a theme of its own (empty
otherwise), already worked out by Miira. The studio reads its colours from Miira's
vendored tokens (`studio/src/styles/vendor/tokens.css`), so it wears every Miira theme
and lays a school's tokens over it. `theme` stays for older builds. A school's own font
is not carried: its files need the Miira session, which a studio on another site lacks.

The publish button follows `publish.state`, as the studio's own top bar does:

| `state` | Label | Hover text | |
|---|---|---|---|
| `publish` | Udgiv | Udgiv appen, så andre kan åbne den | nothing published yet |
| `update` | Opdater | Opdater udgivelse med seneste ændringer | published, with newer changes |
| `published` | Udgivet | Den udgivne app har alle de seneste ændringer | disabled |

While `busy`, the label is "Udgiver …" or "Opdaterer …". When `canUnpublish`, Miira
puts "Stop udgivelse" next to it (the studio uses a small menu on the button) and sends
`drive:action` `unpublish`; the studio asks for confirmation and calls
`POST /api/projects/{pid}/unpublish`. Indstillinger → Udgivelse in the studio offers the
same, so a Miira build without it still lets teachers unpublish.

The download itself still happens in the frame (it needs the in-memory bearer), so the
iframe keeps `allow-downloads`. Budget and publish-error dialogs stay in the studio.

## 4. OIDC provider

Standard OpenID Connect, authorization code + PKCE (S256), confidential client.

- Issuer: `MIIRA/api/vibe/oidc` (Miira's edge routes only `/api/*` to its API).
  Discovery is the issuer plus `/.well-known/openid-configuration`, as OIDC says;
  SlopTown sets `SLOPTOWN_MIIRA_ISSUER` to the issuer and rewrites every back-channel
  URL on the issuer's origin to `SLOPTOWN_MIIRA_API_BASE`. (fake-miira's issuer is its
  bare origin; both work.)
- Authorize without a Miira session: Miira sends the browser to its drive, which logs
  the user in however their school does and then resumes the same authorize request.
- Client: `client_id=sloptown`, secret shared out of band.
  Redirect URI: `STUDIO/auth/callback`.
- Scopes: `openid profile email miira`.
- ID token: RS256, keys at `jwks_uri`. Claims:

```json
{
  "iss": "MIIRA", "aud": "sloptown", "sub": "<pairwise sloptown>",
  "name": "Mette Hansen", "email": "mette@skole.dk",
  "role": "laerer" | "elev" | "admin" | "system-admin",   // miira3 sends laerer, elev, system-admin
  "org_id": "<school id>", "nonce": "...", "iat": 0, "exp": 0
}
```

- `client_credentials` grant on the same token endpoint, scope `vibe:members`,
  returns a bearer access token (JWT or opaque, ≤ 10 min) for the members API.
- Miira maps its roles as: student → `elev`, teacher and school admin → `laerer`
  (administering a school is a capability on top of teaching), platform operator →
  `system-admin`. Only `laerer` creates projects.

## 5. Launch token (iframe sign-in)

Third-party cookies are blocked in Safari, so the iframe cannot rely on an
existing STUDIO session. Miira mints a short-lived token per open and sends it
in `drive:init`.

JWT, RS256, signed with a key from the same JWKS:

```json
{
  "iss": "MIIRA", "aud": "sloptown-launch", "jti": "<uuid>",
  "iat": 0, "exp": "<iat + 120>",
  "sub": "<pairwise sloptown>", "name": "...", "email": "...",
  "role": "laerer", "org_id": "...",
  "file_id": "<file id>", "file_role": "owner" | "editor" | "viewer"
}
```

SlopTown exchanges it at `POST STUDIO/api/auth/launch` (single use, `jti`
replay-checked) for a `Partitioned; Secure; SameSite=None` session cookie and an
in-memory bearer token. `file_role` is authoritative at open time.

## 6. Members API (Miira serves, SlopTown calls)

```
GET MIIRA/api/vibe/{file_id}/members
Authorization: Bearer <client_credentials token, scope vibe:members>
```

Returns everyone with access, with class shares expanded to individual users,
whether or not they ever logged in:

```json
{
  "fileId": "...",
  "version": "<opaque, changes on every share change>",
  "members": [
    {
      "id": "<pairwise vibe:<file_id>>",
      "sub": "<pairwise sloptown>",
      "name": "Mette Hansen",
      "role": "owner" | "editor" | "viewer",
      "classes": ["7.A"],
      "avatarHue": 212
    }
  ]
}
```

No emails, no Miira user ids. `sub` is consumed by SlopTown's control plane
only (to map a logged-in user to their member row) and is stripped before
anything reaches app code. 404 if the file does not exist or is not a Vibe.

## 7. Access-changed webhook (Miira calls, SlopTown serves)

On every share change of a Vibe file (add/remove/role change, class membership
change that affects an existing class share), Miira calls:

```
POST STUDIO/hooks/miira/access-changed
Content-Type: application/json
X-Miira-Timestamp: <unix seconds>
X-Miira-Signature: sha256=<hex HMAC-SHA256(WEBHOOK_SECRET, timestamp + "." + body)>

{ "fileId": "...", "event": "access-changed" }
```

SlopTown rejects timestamps older than 5 minutes. Delivery is best effort with
retries (3 attempts, exponential backoff); SlopTown also polls with a short TTL
cache, so a lost webhook only delays an update.

## 8. Admin API (SlopTown serves, Miira's backend calls)

Miira's system-admin UI gets a "Vibe" section next to the usage-limits page. Its
backend (never the browser) calls `STUDIO/admin/v1/*` with:

```
Authorization: Bearer <JWT, RS256 from Miira's JWKS>
  { "iss": "MIIRA", "aud": "sloptown-admin", "sub": "<pairwise sloptown of acting admin>",
    "name": "...", "role": "system-admin", "iat": 0, "exp": "<iat + 60>", "jti": "<uuid>" }
```

Requests are also restricted by source IP at SlopTown's edge. User references
(`id` with `subject: "user"`) are the `sloptown`-sector pairwise ids, derived by
Miira with the same HMAC; project references are Vibe file ids.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET  | `/admin/v1/budget-settings` | | `BudgetSettings` |
| PUT  | `/admin/v1/budget-settings` | `BudgetSettings` | `BudgetSettings` |
| GET  | `/admin/v1/budgets/limited` | | `{ items: LimitedItem[] }` |
| POST | `/admin/v1/budgets/reset` | `{ subject: "user"\|"project", id, bucket: "build"\|"app" }` | `{ ok: true }` |
| POST | `/admin/v1/budgets/grant` | `{ subject, id, bucket, extraTokens }` | `{ ok: true }` |
| GET  | `/admin/v1/usage` | `?from=ISO&to=ISO` | `{ byUser[], byProject[], byModel[] }` |
| GET  | `/admin/v1/egress-settings` | | env values, read-only |

```ts
type RoleLimits = { nerfLimitTokens: number | null; hardLimitTokens: number | null };
type BucketSettings = { byRole: Record<"laerer" | "elev" | "admin" | "default", RoleLimits> };
type BudgetSettings = {
  enabled: boolean;
  nerfModel: string;                 // e.g. "gpt-6-luna"
  warningThresholds: number[];       // fractions of the hard limit, e.g. [0.8, 0.95]
  watcherEmails: string[];
  build: BucketSettings;             // per teacher per ISO week
  app: { perProject: RoleLimits };   // per published Vibe per ISO week
};
type LimitedItem = {
  subject: "user" | "project"; id: string; label: string; bucket: "build" | "app";
  state: "nerfed" | "blocked"; usedTokens: number; limitTokens: number;
  resetsAt: string;                  // next Monday 00:00 Europe/Copenhagen, ISO 8601
};
```

Defaults on first boot: `enabled: false`, all limits `null` (nothing blocks).

## 9. Content Security Policy

- Miira adds `STUDIO` and `https://*.APPS` to `frame-src` (miira3 sends no CSP today,
  so nothing blocks framing; add both when it gets one).
- SlopTown sends `frame-ancestors MIIRA` (plus STUDIO for apps embedded in the
  studio viewer and preview) on every HTML response.

## 10. What Miira builds (checklist)

All in miira3 `feat/sloptown-vibe`:

- [x] `vibe` file type, create (teachers only), open-in-iframe, share dialog reuse
- [x] creation rolled out per person first (System → Vibe → Adgang), later via the
      same tool settings as other types; opening a shared Vibe never depends on it
- [x] deep links `/drive/new?type=vibe` and `/drive/file/<id>?share=1` (§2)
- [x] `drive:*` messages from §3, including `launchToken` in `drive:init`
- [x] OIDC provider + `client_credentials` (§4), pairwise ids (§1)
- [x] Launch token (§5)
- [x] `GET /api/vibe/{file_id}/members` (§6)
- [x] access-changed webhook with retries, sent after the share commits (§7)
- [x] System admin "Vibe" section calling `/admin/v1/*` (§8)
- [ ] CSP `frame-src` (§9): nothing to add until miira3 has a CSP

## 11. Running against miira3

SlopTown side (`deploy/dev-up.sh`, local or `--public <domain>`):

```bash
SLOPTOWN_MIIRA_ORIGIN=https://staging.miira.dk \
SLOPTOWN_ADMIN_ALLOWED_IPS=<miira server IP>/32 \
  deploy/dev-up.sh --public miira-apps.dk
```

This skips fake-miira, sets the issuer to `<origin>/api/vibe/oidc`, and turns the
password gate off (`SLOPTOWN_GATE=on` keeps it). A gate cookie never reaches a studio
framed by another site (Safari and Firefox partition third-party cookies), and with a
real Miira every request is authenticated by Miira anyway. `/hooks/*` and
`/admin/v1/*` are never gated: Miira's server calls them.

Miira side (API `secrets.env`): `VIBE_STUDIO_ORIGIN=https://studio.miira-apps.dk`,
`VIBE_CLIENT_SECRET` = `CLIENT_SECRET` and `VIBE_WEBHOOK_SECRET` = `WEBHOOK_SECRET`
from `deploy/.public.env`, and a `VIBE_PAIRWISE_SECRET` of its own. Then switch Vibe
on for the testers in System → Vibe → Adgang.

Tests against it: `E2E_MIIRA_ORIGIN=<miira origin>` runs `tests/e2e/test_e2e.py`
(all 10 steps, with miira3's seeded accounts) and `tests/e2e/browser.sh`
(`browser_miira3.cjs`, `E2E_BROWSER=chromium|firefox|webkit`) against the real
Miira. Locally, add `E2E_MIIRA_ADDR=127.0.0.1:<port>` when the Miira host has no DNS.

