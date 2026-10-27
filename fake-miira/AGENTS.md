# fake-miira (dev only)

A stand-in for Miira that implements exactly the Miira side of
`docs/miira-integration.md`, so SlopTown runs end to end before Miira ships.
**If fake-miira and the contract disagree, the contract wins**: fix
fake-miira (and tell the Miira team if the contract changed). Never deploy it
to production.

## What it implements

| Contract § | Endpoint / page |
|---|---|
| §1 pairwise ids | `tokens.pairwise()`: `base32(HMAC-SHA256(secret, sector:user))[:26].lower()`, sectors `sloptown` and `vibe:<file_id>` |
| §2 vibe file type | `/` drive page (list, "Ny Vibe" for teachers), `/files/{id}` opens `STUDIO/embed/<id>?chrome=none` in an iframe |
| §3 postMessage | bridge script in `pages.file_page`: answers `drive:ready` with `drive:init {fileId, launchToken, theme, locale}`; handles `drive:share`, `drive:title`, `drive:close`; sends `drive:theme`, `drive:access-changed` |
| §4 OIDC | `/.well-known/openid-configuration`, `/oidc/authorize` (click a seeded user), `/oidc/token` (code + PKCE S256, `client_credentials` scope `vibe:members`), `/oidc/jwks`, `/oidc/userinfo` |
| §5 launch token | `POST /api/files/{id}/launch` (RS256, `aud=sloptown-launch`, 120 s, `file_role`) |
| §6 members | `GET /api/vibe/{file_id}/members` (service bearer token) |
| §7 webhook | share dialog → `PUT /api/files/{id}/shares` → signed `POST STUDIO/hooks/miira/access-changed`, 3 attempts with backoff |
| §8 admin caller | `/admin/vibe` page (system-admin) → `POST /admin/vibe/call` signs an admin JWT (`aud=sloptown-admin`, 60 s) and calls `STUDIO/admin/v1/*` server-side; the page lists every user's SlopTown `sub` for reset/grant |

Seed data (`store.py`): org `org-skole`; teachers Mette Hansen (`u-mette`) and
Jonas Holm (`u-jonas`); system admin Sanne (`u-admin`); class 7.A (`c-7a`)
with students Ali, Freja, Noah, Ida. Miira-internal `u-*` ids never leave
fake-miira.

## File map

| Path | What |
|---|---|
| `fake_miira/config.py` | `FAKE_MIIRA_*` settings |
| `fake_miira/tokens.py` | RSA key, JWKS, `sign`/`verify`, pairwise ids, webhook signature |
| `fake_miira/store.py` | users, classes, Vibe files, shares, role resolution, member expansion |
| `fake_miira/app.py` | all routes |
| `fake_miira/pages.py` | HTML for login, drive, file (iframe + bridge + share dialog), admin |
| `tests/test_fake_miira.py` | contract tests (OIDC/PKCE, members, launch, webhook signature + retries, admin JWT) |
| `Containerfile` | image for `sloptown-fake-miira` |

## Configuration

| Env | Default | |
|---|---|---|
| `FAKE_MIIRA_ISSUER` | `http://miira.sloptown.localhost:8080` | public origin = OIDC issuer |
| `FAKE_MIIRA_STUDIO_ORIGIN` | `http://studio.sloptown.localhost:8080` | iframe src, allowed redirect URI prefix |
| `FAKE_MIIRA_STUDIO_API_BASE` | = studio origin | how the backend reaches SlopTown (webhook, admin API), e.g. `http://sloptown-cp:8000` |
| `FAKE_MIIRA_CLIENT_ID` / `_CLIENT_SECRET` | `sloptown` / `dev-client-secret` | must match `SLOPTOWN_OIDC_CLIENT_*` |
| `FAKE_MIIRA_WEBHOOK_SECRET` | `dev-webhook-secret` | must match `SLOPTOWN_MIIRA_WEBHOOK_SECRET` |
| `FAKE_MIIRA_PAIRWISE_SECRET` | `dev-pairwise-secret` | |
| `FAKE_MIIRA_DATA_FILE` | empty (memory) | JSON file for files and shares |
| `FAKE_MIIRA_KEY_FILE` | empty (new key per start) | PEM; set it so the control plane's JWKS cache survives restarts |

## Build, test, run (podman, from the repo root)

```bash
# tests (9)
podman run --rm --network host -v $PWD:/src:Z -v sloptown-uv-cache:/root/.cache/uv -w /src/fake-miira \
  -e UV_LINK_MODE=copy -e UV_PROJECT_ENVIRONMENT=/tmp/venv ghcr.io/astral-sh/uv:python3.13-bookworm-slim \
  uv run -q pytest -q -p no:cacheprovider

# image + run
podman build -t localhost/sloptown/fake-miira -f fake-miira/Containerfile fake-miira
podman run -d --name sloptown-fake-miira -p 127.0.0.1:8100:8100 -v sloptown-fake-miira:/var/lib/fake-miira \
  -e FAKE_MIIRA_STUDIO_API_BASE=http://sloptown-cp:8000 localhost/sloptown/fake-miira
```

## Invariants

- Same wire formats as the contract: claim names, audiences, TTLs (launch 120 s,
  admin 60 s), header names, signature format, member fields (no e-mail, no
  Miira user id).
- Pairwise ids are deterministic for a given secret; never expose `u-*` ids.
- Only teachers create Vibes; only owners change shares; only system admins
  reach `/admin/vibe`.
- The fake-miira session cookie (`fm_user`) is an unsigned user id: fine for
  dev, the reason this service must never be exposed publicly.
