# edge/ — Caddy front door and host firewall rules

Owner of everything between the internet and SlopTown's containers. `egress.env`
in this directory belongs to the egress proxy (see `control-plane/AGENTS.md`).

## Files

| Path | |
|---|---|
| `common.caddy` | snippets `(studio)` and `(apps)`, imported by both Caddyfiles. All routing lives here |
| `Caddyfile` | production: `{$STUDIO_HOST}` and `*.{$APPS_DOMAIN}` with on-demand TLS (`ask` → cp `/edge/tls-ask`) |
| `Caddyfile.dev` | plain http on :8080 for `studio.sloptown.localhost`, `miira.sloptown.localhost` (→ fake-miira), `*.apps.sloptown.localhost` |
| `Caddyfile.public` | public *test* on a real domain (`dev-up.sh --public`): Let's Encrypt, fake Miira on `miira.<domain>`, every host behind `gate.py`. Studio and miira hosts are on-demand too, because Caddy treats them as covered by the `*.<domain>` site (cp `/edge/tls-ask` approves them) |
| `gate-on.caddy` / `gate-off.caddy` | the `gate` snippet `Caddyfile.public` imports per `GATE_MODE` (default `on`). On: `forward_auth` to `gate.py` for everything but `/hooks/*` and `/admin/v1/*` (Miira's server calls, which carry their own HMAC/JWT auth). Off: nothing, for a real Miira as IdP (`dev-up.sh` with `SLOPTOWN_MIIRA_ORIGIN`), where a gate cookie would never reach a studio framed by Miira's site |
| `gate.py` | stdlib password gate for the public test: one login sets a `Domain=<domain>` cookie checked by `forward_auth` on every host (basic auth can't work across subdomains and iframes). `common.caddy` strips its cookie before requests reach apps |
| `Containerfile` | xcaddy build with `caddy-ratelimit`, the studio SPA from `STUDIO_IMAGE`, the Caddyfiles |
| `nftables/sloptown.nft` | table `inet sloptown_vibe`: project bridges (`vb*`) may reach only aardvark-dns on the host |
| `test/validate.sh` | builds the image, `caddy validate`s all three configs, runs the dev config against mocks (21 checks) |
| `test/test_gate.py` | pytest for `gate.py` (tokens, open-redirect guard, login and check flow) |
| `test/mock.py` | mock control plane (`forward_auth` answers) and echo app |

Env used by the Caddyfiles: `STUDIO_HOST`, `APPS_DOMAIN`, `STUDIO_ORIGIN`,
`MIIRA_ORIGINS` (space separated), `CP_UPSTREAM` (`sloptown-cp:8000`),
`ADMIN_ALLOWED_IPS` (space separated CIDRs), `ACME_EMAIL`, `FAKE_MIIRA_UPSTREAM` (dev).

## Routing

Studio host:
- `/api/* /auth/* /hooks/* /admin/v1/*` → control plane (`flush_interval -1` for SSE).
  `/admin/v1/*` answers 403 unless the client IP is in `ADMIN_ALLOWED_IPS`.
- Everything else: SPA from `/srv/studio` with `index.html` fallback; `/assets/*` cached immutable.
- `/edge/*` and `/internal/*` are never proxied from here.

App and preview hosts (`<slug>.APPS`, `preview-<slug>.APPS`):
1. strip every incoming `X-Vibe-*` and `X-Sloptown-*` header;
2. preview only: set `X-Vibe-View-As` from the `vibe_view_as` cookie;
3. `/_vibe/session` → cp `/edge/session?<query>` (sets the host-only cookie);
4. `forward_auth` to cp `/edge/auth/preview` or `/edge/auth/app`, copying
   `X-Vibe-Edge-Secret X-Vibe-User X-Vibe-Name X-Vibe-Role X-Vibe-Classes X-Vibe-Upstream`.
   A non-2xx answer (302 to `/auth/app-login`, 403) goes straight to the client;
5. `reverse_proxy {http.request.header.X-Vibe-Upstream}`, removing
   `X-Vibe-Upstream`/`X-Vibe-View-As`; websockets (Vite HMR) pass through.

Both: `Content-Security-Policy: frame-ancestors …` set after the upstream
(`>` prefix), so apps cannot loosen it; rate limit per client IP
(600/min studio, 1200/min apps).

## Build and test

```bash
edge/test/validate.sh                               # builds edge:test with a placeholder studio
EDGE_IMAGE=localhost:5000/sloptown/edge:latest edge/test/validate.sh   # test a built image
nft -c -f edge/nftables/sloptown.nft                # syntax check only, applies nothing
```

## Invariants

- The edge is the **only** place identity headers come from. Never add a route
  on an app host that skips step 1 or 4.
- Matchers inside `route` are evaluated when each directive runs, so a `rewrite`
  changes what later matchers see. Rewrite inside `reverse_proxy { rewrite … }`
  instead (that is why `/_vibe/session` is written that way).
- Rate limiting needs the `caddy-ratelimit` plugin, hence the xcaddy build.
  `order rate_limit before basic_auth` must stay in both global blocks.
- On-demand TLS issues one certificate per app host. Fine for tens of apps; for
  more, switch to a DNS-01 wildcard (`*.APPS`) with the DNS provider's
  caddy-dns plugin in the `Containerfile`, and drop `on_demand`.
- nftables: measured on this host (podman 5.7 / netavark 1.16, rootful), an
  `--internal` network still reaches every host service on its gateway IP
  (sshd :5700, smtp :25 were open). The input chain closes that. Rootless
  project networks live in the rootless netns and have no such path. The rules
  match interface names `vb*`, so the control plane must create networks with
  `network_interface: "vb<8 hex>"`. Never load them with `nftables.service`:
  the stock `/etc/nftables.conf` flushes the whole ruleset, which wipes
  Docker's and netavark's rules on this shared host. `deploy/systemd/sloptown-nft.service`
  loads only this table.
- Traffic inside one project bridge (container ↔ egress, cp/edge → container)
  is layer 2 and not filtered. Services reachable there must authenticate.
