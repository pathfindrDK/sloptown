# Design: Bootstrap 5

Build the UI with Bootstrap 5.3 classes (`container`, `row`/`col-*`, `btn btn-primary`,
`btn btn-outline-primary`, `card`, `form-control`, `form-select`, `form-check`,
`list-group`, `alert`, `badge`, `table`, spacing utilities like `mb-3`, `gap-2`).
Prefer Bootstrap classes over your own CSS.

- Colours: `primary` is the palette's main colour (`btn-primary`, `text-primary`,
  `bg-primary`, `text-bg-primary`, `border-primary`). Use `body-secondary` /
  `text-body-secondary` for quiet text. Don't use `bg-white`/`bg-dark`/`text-dark`:
  they ignore the palette and break dark mode; use `bg-body` and `text-body`.
- Interactive widgets (modals, dropdowns, collapse, tooltips) need Bootstrap's
  JavaScript: build them as React state instead (e.g. show a modal with a
  `useState` flag and the `modal d-block` classes). Don't import `bootstrap/dist/js`.
- Imports in `main.tsx`: `bootstrap/dist/css/bootstrap.min.css`, `./palette.css`,
  `./design.css`, `./styles.css`.

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
