import type { PaletteColours } from "../api/types";

/** A palette as three dots (main colour, surface, text). The colours are the
 *  palette's own data from the catalog, the one place the studio shows raw colours. */
export function Swatch({ colours }: { colours: PaletteColours }) {
  return (
    <span className="swatch" aria-hidden>
      {[colours.primary, colours.surface, colours.text].map((c, i) => (
        <span key={i} style={{ background: c }} />
      ))}
    </span>
  );
}
