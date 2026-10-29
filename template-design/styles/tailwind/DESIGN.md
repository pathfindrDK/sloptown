# Design: Tailwind CSS 4

Build the UI with Tailwind 4 utility classes in your JSX. Write almost no CSS of
your own. There is no `tailwind.config.js`: `web/src/design.css` configures
Tailwind (it imports `tailwindcss`), and Vite's Tailwind plugin is already set up.

- Colours come from the palette: `bg-primary` / `text-primary` / `border-primary`
  (main colour) with `text-on-primary` for text on it, `bg-background` (page),
  `bg-surface` (cards, panels), `text-foreground` (text), `text-muted`,
  `border-border`. Opacity works: `bg-primary/10`.
- Never use Tailwind's own colour scales (`bg-blue-500`, `text-gray-700`,
  `bg-white`): they ignore the palette. `dark:` variants are rarely needed,
  because the palette already has a dark variant.
- Imports in `main.tsx`: `./palette.css`, `./design.css`, `./styles.css`.

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
