# Design: Bulma 1

Build the UI with Bulma 1 classes (`section`, `container`, `columns`/`column`,
`button is-primary`, `box`, `card`, `field`/`control`/`input`/`select`, `notification`,
`tag`, `table`, `title`/`subtitle`, helpers like `mb-4`, `has-text-centered`).
Prefer Bulma classes over your own CSS.

- Colours: `is-primary` / `is-link` are the palette's main colour
  (`has-text-primary`, `has-background-primary-light`). Use `has-text-grey` for
  quiet text. Don't use `is-dark`, `is-white` or `is-black` for large areas.
- Bulma has no JavaScript: toggle things (navbar burger, modals with
  `modal is-active`, dropdowns with `is-active`) with React state.
- Imports in `main.tsx`: `bulma/css/bulma.min.css`, `./palette.css`, `./design.css`,
  `./styles.css`.

## Colours

The teacher picks a colour palette in Indstillinger. `web/src/palette.css` holds it
(`--palette-primary`, `--palette-on-primary`, `--palette-bg`, `--palette-surface`,
`--palette-text`, `--palette-muted`, `--palette-border`, each also as `-rgb` and
`-h/-s/-l`), with a dark variant under `[data-theme="dark"]`. `web/src/design.css`
maps it onto this design system. Both files are the platform's: never edit them.

- Never write a raw colour (`#fff`, `rgb(...)`, `blue`). Use the design system's
  colour names above, or `var(--palette-*)` in your own CSS.
- Keep the imports at the top of `web/src/main.tsx` in this order.
- Do not install or import another CSS framework or component library.
