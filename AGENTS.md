# SlopTown — notes for humans and coding agents

SlopTown hosts AI-built teacher apps ("Vibes") for Miira. Miira is the identity
provider and the source of access rights; SlopTown never stores its own access
lists, it only caches Miira's.

Read first:
- `docs/miira-integration.md` — the contract with Miira (what Miira builds).
- `docs/internal-api.md` — contracts between SlopTown's own components. Change it
  before changing an interface.

## Layout

| Dir | What |
|---|---|
| `control-plane/` | FastAPI control plane (`sloptown`) and the egress/inference proxy (`sloptown.egress`) |
| `studio/` | React 18 + Vite + TS studio: project list, editor, viewer, embed bridge |
| `images/` | `vibe-base`, `vibe-dev`, `vibe-runtime` Containerfiles, `vibe-agentd` |
| `template/` | boilerplate Vibe app, baked into `vibe-dev` at `/opt/template`, with the agent's `AGENTS.md` |
| `template-design/` | design systems (Miira, Bootstrap, Tailwind, Bulma, Material) and colour palettes for webapps: catalog, per-style files, generated palettes; baked at `/opt/template-design` |
| `template-scratch/` | the "fra bunden" project (README + the agent's `AGENTS.md`), baked at `/opt/template-scratch`; never published (its optional `index.html` UI shows only on the editor-only preview host) |
| `sdk/python/` `sdk/js/` | platform SDK (`miira_vibe`, `@miira/vibe`), installed into `vibe-base` |
| `edge/` | Caddyfile, `egress.env`, nftables rules |
| `fake-miira/` | dev-only Miira: OIDC, members API, webhook, launch token |
| `deploy/` | host bootstrap, quadlet units, nightly image pipeline |
| `design/` | vendored Miira design assets and `sync-from-miira.mjs` |

Each directory has its own `AGENTS.md` (module map, exact commands, invariants);
read it before changing that component. `tests/e2e/` holds the whole-stack tests.

## Build and test

The host has no Node or Python toolchain: everything runs in containers via podman.

```bash
scripts/test-all.sh           # all unit suites (~3 min); needs only podman
scripts/test-all.sh --full    # + edge, image smoke, platform smoke, real-Podman
                              #   integration, end-to-end API test, browser test (~15 min)
ONLY="control-plane studio" scripts/test-all.sh
deploy/dev-up.sh              # run the whole stack on 127.0.0.1:8080 (fake Miira included)
```

Definition of done for a change: the suites of every component you touched pass,
and `scripts/test-all.sh --full` passes when the change crosses components
(contracts, images, edge, orchestrator). Update the component's `AGENTS.md` and
`docs/internal-api.md` in the same change.

## Rules

- Never write into the Miira repo. Miira is read only (design guide, protocol).
- UI copy is Danish first, English second. Use `--miira-*` tokens only.
- Apps never hold Miira credentials or the OpenAI key. Everything goes through
  the egress proxy with a per-project token.
- Audit-log every state change that touches a project or a budget.
- Host changes (nftables, gVisor, packages) live in `deploy/` scripts; they are
  never applied ad hoc.
