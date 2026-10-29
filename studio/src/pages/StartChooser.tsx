// The owner's first open of a new Vibe: choose what it is before anything is
// created. A webapp gets the template, preview and publish; "fra bunden" is an
// empty project that is never served on the web (control plane: kind="scratch").
// A webapp then gets a name, a design system and a palette (both can change later
// under Indstillinger), with a preview beside the choices; skipping the palette
// leaves it to the control plane's random pick.
import { useState, type ReactNode } from "react";
import { AppWindow, Cpu, Shuffle } from "lucide-react";
import { api } from "../api/client";
import { DesignPreview } from "../components/DesignPreview";
import { LoadingTiles } from "../components/Loading";
import { Swatch } from "../components/Swatch";
import type { Project, ProjectKind } from "../api/types";
import { useDesign } from "../lib/design";
import { useLocale, useT } from "../lib/i18n";

export function StartChooser({ pid, onCreated }: { pid: string; onCreated: (p: Project) => void }) {
  const t = useT();
  const locale = useLocale();
  const catalog = useDesign();
  const [step, setStep] = useState<"kind" | "design">("kind");
  const [style, setStyle] = useState("miira");
  const [palette, setPalette] = useState<string | null>(null); // null: random
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState<ProjectKind | null>(null);
  const [failed, setFailed] = useState(false);

  const start = async (kind: ProjectKind) => {
    setBusy(kind);
    setFailed(false);
    try {
      const webapp = kind === "webapp";
      const design = webapp ? { style, palette: palette ?? undefined } : undefined;
      onCreated(await api.createProject(pid, kind, (webapp && title.trim()) || undefined, design));
    } catch {
      setFailed(true);
      setBusy(null);
    }
  };

  const copy = {
    webapp: [t("start.webapp.title"), t("start.webapp.body")],
    scratch: [t("start.scratch.title"), t("start.scratch.body")],
  } as const;

  const option = (kind: ProjectKind, Icon: typeof Cpu) => (
    <button
      type="button"
      className="card start__option"
      disabled={busy !== null}
      aria-busy={busy === kind}
      onClick={() => (kind === "webapp" ? setStep("design") : start(kind))}
    >
      <span className="start__icon" aria-hidden>
        {busy === kind ? <LoadingTiles /> : <Icon size={28} />}
      </span>
      <strong>{copy[kind][0]}</strong>
      <span className="muted">{copy[kind][1]}</span>
    </button>
  );

  if (step === "kind") {
    return (
      <div className="page page--center">
        <div className="start">
          <h1>{t("start.title")}</h1>
          <div className="start__options">
            {option("webapp", AppWindow)}
            {option("scratch", Cpu)}
          </div>
          <p className="muted">{failed ? t("start.failed") : t("start.later")}</p>
        </div>
      </div>
    );
  }

  const card = (id: string | null, active: boolean, onPick: () => void, title: string, body?: string, icon?: ReactNode) => (
    <button
      key={id ?? "random"}
      type="button"
      role="radio"
      aria-checked={active}
      disabled={busy !== null}
      className={`choice-card${active ? " is-active" : ""}`}
      onClick={onPick}
    >
      {icon}
      <span>
        <strong>{title}</strong>
        {body && <span className="muted">{body}</span>}
      </span>
    </button>
  );

  const chosenStyle = catalog?.styles.find((s) => s.id === style);
  const chosenPalette = catalog?.palettes.find((p) => p.id === palette) ?? null;

  return (
    <div className="page page--center">
      <div className="start start--design">
        <h1>{t("start.design.title")}</h1>
        {!catalog ? (
          <LoadingTiles />
        ) : (
          <div className="start__design">
            <div className="start__choices">
              <h2>
                <label htmlFor="start-name">{t("start.design.name")}</label>
              </h2>
              <input
                id="start-name"
                className="field"
                maxLength={120}
                value={title}
                placeholder={t("start.design.namePlaceholder")}
                disabled={busy !== null}
                onChange={(e) => setTitle(e.target.value)}
              />
              <h2 id="start-style">{t("start.design.style")}</h2>
              <div className="start__grid" role="radiogroup" aria-labelledby="start-style">
                {catalog.styles.map((s) =>
                  card(s.id, style === s.id, () => setStyle(s.id), s.name[locale], s.description[locale]),
                )}
              </div>
              <h2 id="start-palette">{t("start.design.palette")}</h2>
              <div className="start__grid start__grid--palettes" role="radiogroup" aria-labelledby="start-palette">
                {card(null, palette === null, () => setPalette(null), t("start.design.random"), undefined, <Shuffle size={18} aria-hidden />)}
                {catalog.palettes.map((p) =>
                  card(p.id, palette === p.id, () => setPalette(p.id), p.name[locale], undefined, <Swatch colours={p.light} />),
                )}
              </div>
            </div>
            <DesignPreview
              style={style}
              styleName={chosenStyle?.name[locale] ?? style}
              palette={chosenPalette}
              paletteName={chosenPalette?.name[locale] ?? t("start.design.random")}
              title={title}
            />
          </div>
        )}
        <div className="start__actions">
          <button type="button" className="btn btn--3d" disabled={busy !== null} onClick={() => setStep("kind")}>
            {t("start.design.back")}
          </button>
          <button
            type="button"
            className="btn btn--3d btn--primary"
            disabled={busy !== null || !catalog}
            aria-busy={busy === "webapp"}
            onClick={() => start("webapp")}
          >
            {busy === "webapp" ? <LoadingTiles size="xs" /> : null}
            {t("start.design.start")}
          </button>
        </div>
        <p className="muted">{failed ? t("start.failed") : t("start.design.later")}</p>
      </div>
    </div>
  );
}
