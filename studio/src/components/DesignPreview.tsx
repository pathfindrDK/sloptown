// A small mock of a Vibe in a design system and palette, so the teacher can see a
// choice before committing to it (start screen, and the "skift designsystem" dialog).
// It is drawn with the studio's own CSS (.design-preview[data-style=...] in app.css),
// not the real framework: each style gets its characteristic shapes (corners, button
// form, shadows, inputs), the palette gives the colours. The colours are catalog
// data set as --pv-* properties, like the Swatch dots.
import { useState, type CSSProperties } from "react";
import { Moon, Sun } from "lucide-react";
import type { DesignPalette, PaletteColours } from "../api/types";
import { useT } from "../lib/i18n";

// "Overrask mig": the control plane picks the palette, so the mock stays grey.
const NEUTRAL: Record<"light" | "dark", PaletteColours> = {
  light: { primary: "#4b5563", onPrimary: "#ffffff", bg: "#f4f4f5", surface: "#ffffff", text: "#18181b", muted: "#5f5f68", border: "#dcdce0" },
  dark: { primary: "#a1a1aa", onPrimary: "#18181b", bg: "#18181b", surface: "#24242a", text: "#f4f4f5", muted: "#a1a1aa", border: "#3a3a42" },
};

type Props = {
  style: string;
  styleName: string;
  /** null: the palette is picked at random when the Vibe is created. */
  palette: DesignPalette | null;
  paletteName: string;
  title: string;
};

export function DesignPreview({ style, styleName, palette, paletteName, title }: Props) {
  const t = useT();
  const [mode, setMode] = useState<"light" | "dark">("light");
  const c = (palette ?? NEUTRAL)[mode];
  const vars = {
    "--pv-primary": c.primary,
    "--pv-on-primary": c.onPrimary,
    "--pv-bg": c.bg,
    "--pv-surface": c.surface,
    "--pv-text": c.text,
    "--pv-muted": c.muted,
    "--pv-border": c.border,
  } as CSSProperties;
  const name = title.trim() || t("preview.untitled");

  return (
    <figure className="design-preview-wrap">
      <div
        className="design-preview"
        data-style={style}
        style={vars}
        role="img"
        aria-label={t("preview.label").replace("{name}", name).replace("{style}", styleName).replace("{palette}", paletteName)}
      >
        <div className="design-preview__bar">
          <span className="design-preview__title">{name}</span>
        </div>
        <div className="design-preview__body">
          <div className="design-preview__card">
            <strong>{t("preview.heading")}</strong>
            <span className="design-preview__muted">{t("preview.text")}</span>
            <span className="design-preview__input">{t("preview.input")}</span>
            <span className="design-preview__actions">
              <span className="design-preview__btn design-preview__btn--primary">{t("preview.primary")}</span>
              <span className="design-preview__btn">{t("preview.secondary")}</span>
            </span>
          </div>
          <span className="design-preview__chips">
            <span className="design-preview__chip">{t("preview.chip1")}</span>
            <span className="design-preview__chip">{t("preview.chip2")}</span>
          </span>
        </div>
      </div>
      <figcaption className="design-preview__caption">
        <span className="muted">{palette ? `${styleName} · ${paletteName}` : t("preview.random")}</span>
        <button
          type="button"
          className="icon-btn"
          aria-pressed={mode === "dark"}
          aria-label={t("preview.dark")}
          title={t("preview.dark")}
          onClick={() => setMode(mode === "light" ? "dark" : "light")}
        >
          {mode === "light" ? <Moon size={16} /> : <Sun size={16} />}
        </button>
      </figcaption>
    </figure>
  );
}
