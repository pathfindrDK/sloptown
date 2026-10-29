# Design: Miira

The app looks like Miira. Style it with Miira's design tokens in plain CSS
(`web/src/styles.css` and other `.css` files you create).

- Colours: `var(--miira-color-bg)`, `--miira-color-surface`, `--miira-color-text`,
  `--miira-color-text-muted`, `--miira-color-border`, `--miira-color-accent`
  (buttons, highlights) with `--miira-color-accent-contrast` for text on it,
  `--miira-color-success`, `--miira-color-warning`, `--miira-color-danger`.
- Spacing `--miira-space-1` … `--miira-space-6`, corners `--miira-radius-sm|md|lg`,
  shadows `--miira-shadow-sm|md`, font `--miira-font-family`.
- Imports in `main.tsx`: `@miira/vibe/theme.css`, `./palette.css`, `./design.css`,
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
