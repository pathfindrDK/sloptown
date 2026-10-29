# template-design/ — design systems and colour palettes for webapp Vibes

A webapp Vibe is built with one **style** (design system) and one **palette**
(colours). The teacher picks both when starting a Vibe (skipping gives the
Miira style and a random palette) and can change them under Indstillinger.
Baked into `vibe-dev` at `/opt/template-design`, outside `/opt/template`, so
none of it is copied into a project wholesale.

## Files

| Path | |
|---|---|
| `catalog.json` | **the** list of styles and palettes (ids, Danish/English names, palette colours). The control plane serves it as `GET /api/design` (image copy at `/opt/sloptown/design-catalog.json`), agentd validates against it, the studio renders it |
| `build.py` | stdlib generator: `palettes/<id>.css` from the catalog; refuses palettes whose text pairs fall below WCAG 4.5:1. `--check` fails on stale files |
| `palettes/<id>.css` | generated, committed: `--palette-{primary,on-primary,bg,surface,text,muted,border}` as hex, `-rgb` and `-h/-s/-l`; dark variant under `:root[data-theme="dark"]`. Becomes the project's `web/src/palette.css` |
| `styles/<id>/DESIGN.md` | the agent's instructions for that design system (classes, which colour names to use, what not to use). Becomes the project's `DESIGN.md` |
| `styles/<id>/design.css` | the bridge: maps `--palette-*` onto the framework's own variables (`--bs-*`, Tailwind `@theme`, `--bulma-*-h/s/l`, `--md-sys-color-*`, Miira's tokens). Becomes `web/src/design.css` |
| `styles/<id>/web/src/{main.tsx,App.tsx,styles.css}` | starter files, laid over the template only for a fresh project (agentd `/design` with `starter`) |

In a project, `DESIGN.md`, `web/src/design.css` and `web/src/palette.css` are
platform files: read-only, re-copied by the entrypoint on every start (from the
untracked marker `.vibe-design`), and checked by `/export` before publishing.

## Commands

```bash
python3 template-design/build.py           # after editing palettes in catalog.json
python3 template-design/build.py --check   # run by scripts/test-all.sh (template-design)
images/test/smoke.sh                       # builds every style with a palette in the real image
```

## Adding a style or palette

- Palette: add it to `catalog.json`, run `build.py`. Every role in light and dark.
- Style: its npm package in `template/web/package.json` (and the lock; the
  Vibe has no network, so it must be in the image), `styles/<id>/` with all
  five files, a catalog entry. Check how the framework takes colours: it must
  read CSS variables at runtime, or the palette can't reach it (why Foundation
  and Semantic UI are not here).
- `vibe-agentd`'s tests check that every catalog entry has its files; the image
  smoke test builds every style.

## Invariants

- Ids are lowercase `[a-z0-9-]` and never renamed: projects store them (control
  plane `projects.style/palette`, the marker). Remove one only after migrating
  the projects that use it.
- A bridge only maps palette variables; it never introduces a raw colour of its
  own (`color-mix` of palette colours is fine).
- Bridges select `:root, :root[data-theme]` so they win over theme blocks that
  frameworks or Miira's tokens put on `[data-theme]`.
- Framework notes, verified against the installed versions:
  - Bootstrap 5.3 compiles button colours into `.btn-*` rules, so the bridge
    sets `--bs-btn-*` there; dark mode follows our palette, not `data-bs-theme`.
  - Tailwind 4: `@theme inline` makes `bg-primary` etc. read `var(--palette-*)`;
    `dark:` is bound to `data-theme="dark"`.
  - Bulma 1 derives shades from `--bulma-<colour>-h/s/l` and also switches on
    `prefers-color-scheme`.
  - Materialize 2.4: the coloured button is `btn btn-filled` (bare `btn` has no
    colour, `btn-flat` is gone); cards mix 17% of `--md-sys-color-surface-tint`
    into their background.
