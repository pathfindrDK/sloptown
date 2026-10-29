# Design: Material (Materialize 2)

Build the UI with Materialize 2 classes, which follow Google's Material Design
(`container`, `row`/`col s12 m6`, `card`/`card-content`/`card-action`, `collection`,
`chip`, `input-field`, `z-depth-1`). Buttons in Materialize 2: `btn btn-filled` (main
action), `btn tonal`, `btn outlined`, `btn text` (quiet), `btn-floating`. There is no
`btn-flat` any more, and a bare `btn` has no colour.
Prefer Materialize classes over your own CSS.

- Colours come from the palette through Material's colour roles: `btn-filled` and
  `btn-floating` use the main colour automatically. In your own CSS use
  `var(--md-sys-color-primary)`, `--md-sys-color-on-primary`, `--md-sys-color-surface`,
  `--md-sys-color-on-surface`, `--md-sys-color-outline`. Don't use Materialize's
  colour classes (`red`, `teal lighten-2`, `blue-text`): they ignore the palette.
- Components that need JavaScript (modals, dropdowns, selects, tabs): build them
  with React state and the CSS classes. Don't call `M.AutoInit()`.
- Icons: use `lucide-react` (Material Icons fonts can't be loaded).
- Imports in `main.tsx`: `@materializecss/materialize/dist/css/materialize.min.css`,
  `./palette.css`, `./design.css`, `./styles.css`.

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
