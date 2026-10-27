# tests/e2e — whole-stack tests

Two tests against the real dev stack (`deploy/dev-up.sh` on 127.0.0.1:8080),
with a scripted fake OpenAI so no real key or tokens are needed.

| File | What |
|---|---|
| `run.sh` | starts `sloptown-fake-openai`, runs `deploy/dev-up.sh` (builds all images unless `--no-build`), then `test_e2e.py`; prints platform and vibe-dev logs on failure |
| `test_e2e.py` | 11 ordered steps over HTTP through the edge: Miira file + launch token → studio session/terms/project from the warm pool → files/history → preview → a real OpenCode turn (commit, SSE, metering) → publish (vibe-build) → app as owner → student via OIDC before/after a class share (webhook, members) → restore → admin API usage and hard limit → a "fra bunden" (scratch) project is never served |
| `fake_openai.py` | stdlib OpenAI chat-completions stand-in; writes `web/src/App.tsx` with the marker from the prompt using OpenCode's `write` tool or, for GPT models (which get `apply_patch`), `bash` |
| `browser.sh` / `browser.cjs` | Playwright: the real studio inside fake Miira's iframe (drive:init handshake, terms, editor, preview, code tab); fails on page errors or 5xx; screenshots in `out/` |
| `browser_miira3.cjs` | Playwright against the **real** Miira (miira3), run by `browser.sh` when `E2E_MIIRA_ORIGIN` is set: Opret ny offers Vibe to a lærer, `/drive/new` deep link, studio + preview inside Miira's iframe, Del opens Miira's share dialog, signed-out standalone studio → Miira login → authorize resumed, an elev opens a class-shared Vibe as viewer, System → Vibe (rollout + budgets). `E2E_BROWSER=chromium\|firefox\|webkit` |
| `conftest.py` | exposes per-test reports so later steps skip after a failure |
| `public.sh` | both tests against the public test deployment (`E2E_DOMAIN`, gate login, real DNS/TLS) |

```bash
tests/e2e/run.sh                 # full: build images, start stack, run the 11 steps (~6 min)
tests/e2e/run.sh --no-build      # reuse localhost/sloptown/*:dev images (~1 min)
E2E_PYTEST_ARGS="-x -k publish" tests/e2e/run.sh --no-build
tests/e2e/browser.sh             # needs the stack up
tests/e2e/public.sh miira-apps.dk   # against deploy/dev-up.sh --public (fake model needed for step 5)
```

Against the real Miira (miira3 with `VIBE_*` set, its seed loaded, and SlopTown started with
`SLOPTOWN_MIIRA_ORIGIN`; see `docs/miira-integration.md` §11): set `E2E_MIIRA_ORIGIN` (and
`E2E_MIIRA_ADDR=host:port` when the Miira host has no DNS) for both `test_e2e.py` (same 10
steps with miira3's seeded accounts: Lea Lærer, Erik Elev in klasse 1. A, the platform admin)
and `browser.sh`. Lea must be on the Vibe rollout (the miira3 seed puts her there).

Rebuild the image you changed before `--no-build` runs, e.g.
`podman build -t localhost/sloptown/control-plane:dev -f deploy/containers/control-plane.Containerfile .`
(edge embeds the studio: rebuild studio, then edge with `--build-arg STUDIO_IMAGE=localhost/sloptown/studio:dev`).

Gotchas:
- The test client sends every request to the edge with the real `Host` header
  (`EdgeTransport`), so no DNS for `*.sloptown.localhost` is needed, and drops the
  `Secure` cookie attribute on `*.localhost` like browsers do.
- Public mode: the browser runs in its own network namespace. With host networking
  Chrome aborts requests (`ERR_NETWORK_CHANGED`) whenever podman adds a project
  network interface on the host.
- Each run creates a new Vibe and leaves it; the stack stays up afterwards
  (`deploy/dev-up.sh --down`). Their vibe-dev containers run until the control plane's
  idle stop (30 min) — many back-to-back runs on a 16 GB host exhaust memory and make
  the browser tests flaky; `podman stop` the test Vibes' containers between batches.
- browser_miira3: WebKit drops `Secure` cookies over plain http even on `*.localhost`,
  so the standalone-login step is skipped there locally (it runs on https).
- Run browser_miira3 against a built drive (Caddy + `try_files`, like production), not
  Vite's dev server: its hundreds of modules over HTTP/1.1 stall a second tab.
- Failures here so far were all real integration bugs (network DNS, platform
  reattachment, token caching, build entrypoint, embed origins, HMR through
  forward_auth). Fix the platform, not the test, unless the test is wrong.
