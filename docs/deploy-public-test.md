# Public test deployment (miira-apps.dk)

A way to try SlopTown from any browser on a real domain, with real TLS, before
Miira ships its side. It runs the dev stack (fake Miira included) behind a
password gate. It is **not** the production setup in `deploy/AGENTS.md`.

## DNS

At the DNS provider (simply.com for miira-apps.dk), both records point at the server:

```
miira-apps.dk     A   138.201.117.115
*.miira-apps.dk   A   138.201.117.115
```

The wildcard covers `studio.`, `miira.` (fake Miira), `<slug>.` (published apps)
and `preview-<slug>.` (editor previews).

## Start, stop, update

```bash
deploy/dev-up.sh --public miira-apps.dk              # build all images and start
deploy/dev-up.sh --public miira-apps.dk --no-build   # restart with the current images
deploy/dev-up.sh --down                              # stop (data volumes are kept)
```

- Secrets (session, internal, edge, OIDC client, webhook, pairwise) and the gate
  password are generated once into `deploy/.public.env` (mode 600, gitignored) and
  reused on every start. Deleting that file invalidates all sessions and project
  tokens.
- Volumes: `sloptown-public-db` (Postgres), `sloptown-public-fake-miira` (fake Miira's
  files, shares and signing key), `sloptown-public-caddy` (certificates; keep it, Let's
  Encrypt allows 50 new certificates per week for the domain).
- TLS: when `/etc/sloptown/tls` holds `fullchain.pem` (certificate, then chain) and
  `priv.pem`, `dev-up.sh` mounts it into the edge and every host uses that wildcard
  certificate (`TLS_MODE=file`); nothing is requested from Let's Encrypt. Without it
  (or with `SLOPTOWN_TLS_DIR=`), Caddy gets one Let's Encrypt certificate per host.
  Renew by replacing both files and running `dev-up.sh --public <domain> --no-build`.
- Inference: by default the egress has no OpenAI key, so the agent can't answer. Either
  pass a real key (`SLOPTOWN_OPENAI_API_KEY=sk-... deploy/dev-up.sh --public ...`), or run
  the scripted fake model the tests use:

  ```bash
  podman run -d --name sloptown-fake-openai --network sloptown --label sloptown=1 \
    -v "$PWD/tests/e2e/fake_openai.py:/fake_openai.py:ro,Z" \
    ghcr.io/astral-sh/uv:python3.13-bookworm-slim python /fake_openai.py 8300
  SLOPTOWN_OPENAI_BASE_URL=http://sloptown-fake-openai:8300/v1 SLOPTOWN_OPENAI_API_KEY=sk-fake \
    deploy/dev-up.sh --public miira-apps.dk --no-build
  ```

## Using it

1. Open https://miira.miira-apps.dk. The first request on any host redirects to
   `https://miira-apps.dk/_gate`; enter `GATE_PASSWORD` from `deploy/.public.env`.
   The cookie covers every subdomain for 30 days.
2. Fake Miira lists seeded users: log in as **Mette Hansen** or **Jonas Holm** (teachers),
   **Sanne Sysadmin** (system admin: `/admin/vibe`), or one of the 7.A students.
3. As a teacher: "Ny Vibe" creates a Vibe file and opens the studio in an iframe, like
   Miira's drive will. Share it with class 7.A, publish it, and open the app as a student.

## How it differs from production

| | Public test | Production |
|---|---|---|
| Miira | fake-miira on `miira.<domain>` | the real Miira (OIDC, members API, webhooks) |
| Studio host | `studio.miira-apps.dk` (same site as the apps) | a separate registered domain |
| Access | password gate on every host | Miira login only |
| TLS | wildcard from `/etc/sloptown/tls` if present, else on-demand per host (HTTP-01) | `TLS_MODE=file` wildcard for the apps domain; studio on its own domain via ACME |
| Stack | `dev-up.sh` containers, rootful, `SLOPTOWN_DEV_MODE=true` | quadlets, rootless, gVisor (`bootstrap.sh`) |

Because the studio shares the apps' site here, the control plane refuses state-changing
cookie requests whose `Origin` isn't the studio (`app.same_origin_writes`). That guard
stays on in production too.

## Tests against the deployment

```bash
tests/e2e/public.sh miira-apps.dk    # the 10-step API test + the browser test, via real DNS/TLS
```

Step 5 needs the fake model (see above); with a real key the agent writes something else.

## With the real Miira (miira3 staging)

Point the public stack at a deployed miira3 instead of fake-miira:

```bash
SLOPTOWN_MIIRA_ORIGIN=https://staging.miira.dk \
SLOPTOWN_ADMIN_ALLOWED_IPS=<staging server IP>/32 \
SLOPTOWN_OPENAI_API_KEY=sk-... \
  deploy/dev-up.sh --public miira-apps.dk --no-build
```

- fake-miira is not started and the password gate is off: Miira authenticates every
  studio and app request, and a gate cookie would never reach a studio framed by
  `staging.miira.dk` (`SLOPTOWN_GATE=on` keeps it anyway). `/hooks/*` and
  `/admin/v1/*` are never gated.
- In miira3's `secrets.env` on staging: `VIBE_STUDIO_ORIGIN=https://studio.miira-apps.dk`,
  `VIBE_CLIENT_SECRET` and `VIBE_WEBHOOK_SECRET` copied from `CLIENT_SECRET` and
  `WEBHOOK_SECRET` in `deploy/.public.env`, plus a `VIBE_PAIRWISE_SECRET` of its own.
  On production (`hej.miira.dk`) also `VIBE_SIGNING_KEY`, an RSA private key (PEM inline
  with `\n`, or a path): the API refuses to boot without it there; elsewhere it generates
  one into `system_settings`. Then System → Vibe → Adgang to switch Vibe on for the testers.
- Back to fake-miira: run `dev-up.sh --public miira-apps.dk --no-build` without
  `SLOPTOWN_MIIRA_ORIGIN`.
- To make the choice stick across restarts, put `SLOPTOWN_MIIRA_ORIGIN` and
  `SLOPTOWN_ADMIN_ALLOWED_IPS` in `deploy/.public.env` (it is sourced after the
  environment, so it wins). Since 2026-10-29 it points at production, `https://hej.miira.dk`.

## Production switches

`SLOPTOWN_DEV_MODE=false` (no "view as" in previews) and `SLOPTOWN_RESTART_POLICY=on-failure`
in `deploy/.public.env` run this stack in production mode; both have been set there since
2026-10-31. On this host, start it through systemd (`systemd-run -r ...`), not from a
`sudo -i` shell: see `/root/SLOPTOWN-RESTART.md`.
