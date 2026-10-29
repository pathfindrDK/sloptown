// "Indstillinger" in the side pane: the Vibe's icon (kept by Miira), its design
// system and colours, who can open the published app, and its description.
import { useEffect, useState } from "react";
import type { IconState } from "../lib/embedBridge";
import { Globe, ImagePlus, Lock } from "lucide-react";
import { api } from "../api/client";
import { ApiError, type Project } from "../api/types";
import { DesignPreview } from "../components/DesignPreview";
import { ConfirmModal } from "../components/Modal";
import { Select } from "../components/Select";
import { Swatch } from "../components/Swatch";
import { useDesign } from "../lib/design";
import { useLocale, useT } from "../lib/i18n";
import { useShell } from "../lib/shell";

type Props = {
  project: Project;
  onChange: (project: Project) => void;
  /** Put a prompt in the chat composer for the teacher to send (Editor.askAiToFix). */
  onAskAi: (prompt: string) => void;
};

type Patch = { description?: string; public?: boolean; style?: string; palette?: string };

export function SettingsPanel({ project, onChange, onAskAi }: Props) {
  const t = useT();
  const locale = useLocale();
  const shell = useShell();
  const catalog = useDesign();
  const owner = project.myRole === "owner";
  const [description, setDescription] = useState(project.description);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [pendingStyle, setPendingStyle] = useState<string | null>(null);
  useEffect(() => setDescription(project.description), [project.description]);
  const [icon, setIcon] = useState<IconState | null>(null);
  useEffect(() => shell.onIcon((fileId, next) => fileId === project.id && setIcon(next)), [shell, project.id]);

  const save = async (patch: Patch): Promise<boolean> => {
    setSaving(true);
    setFailed(null);
    try {
      onChange(await api.updateProject(project.id, patch));
      return true;
    } catch (e) {
      setFailed(e instanceof ApiError && e.code === "turn_running" ? t("settings.turnRunning") : t("common.error"));
      return false;
    } finally {
      setSaving(false);
    }
  };

  // Vibes from before palettes existed have none: their app looks like Miira's.
  const currentPalette = catalog?.palettes.find((p) => p.id === (project.palette ?? "miira")) ?? null;
  const styleName = (id: string) => catalog?.styles.find((s) => s.id === id)?.name[locale] ?? id;

  // A new design system needs the AI to rebuild the UI: confirm, switch, then
  // hand the teacher a prompt to send (a normal turn on their budget).
  const changeStyle = async (style: string) => {
    setPendingStyle(null);
    if (await save({ style })) onAskAi(t("settings.stylePrompt").replace("{style}", styleName(style)));
  };

  return (
    <div className="settings">
      {shell.embedded && (
        <section className="settings__section">
          <h3>{t("settings.icon")}</h3>
          <div className="settings__icon">
            <div className="settings__icon-frame">
              {icon ? <img src={icon.src} width={64} height={64} alt={t("settings.iconAlt")} /> : <span className="muted">…</span>}
            </div>
            <div className="settings__icon-copy">
              <p className="muted">{t(icon?.custom ? "settings.iconCustom" : "settings.iconBody")}</p>
              <button className="btn btn--3d" onClick={() => shell.requestIcon(project.id)}>
                <ImagePlus size={16} /> {t("settings.iconButton")}
              </button>
            </div>
          </div>
        </section>
      )}

      {project.kind !== "scratch" && catalog && (
        <section className="settings__section">
          <h3>{t("settings.design")}</h3>
          <p className="muted">{t("settings.designBody")}</p>
          <div className="settings__design">
            <div className="settings__field">
              <span className="settings__label">{t("settings.style")}</span>
              <Select
                label={t("settings.style")}
                value={project.style ?? "miira"}
                options={catalog.styles.map((s) => ({ value: s.id, label: s.name[locale] }))}
                onChange={(style) => style !== (project.style ?? "miira") && setPendingStyle(style)}
              />
            </div>
            <div className="settings__field">
              <span className="settings__label">{t("settings.palette")}</span>
              <Select
                label={t("settings.palette")}
                value={project.palette ?? ""}
                options={[
                  ...(project.palette ? [] : [{ value: "", label: t("settings.paletteNone") }]),
                  ...catalog.palettes.map((p) => ({ value: p.id, label: p.name[locale], icon: <Swatch colours={p.light} /> })),
                ]}
                onChange={(palette) => palette && palette !== project.palette && save({ palette })}
              />
            </div>
          </div>
        </section>
      )}

      {pendingStyle && (
        <ConfirmModal
          title={t("settings.styleConfirmTitle")}
          body={t("settings.styleConfirmBody").replace("{style}", styleName(pendingStyle))}
          confirmLabel={t("settings.styleConfirm")}
          busy={saving}
          onConfirm={() => changeStyle(pendingStyle)}
          onCancel={() => setPendingStyle(null)}
        >
          {catalog && (
            <DesignPreview
              style={pendingStyle}
              styleName={styleName(pendingStyle)}
              palette={currentPalette}
              paletteName={currentPalette?.name[locale] ?? t("settings.paletteNone")}
              title={project.title}
            />
          )}
        </ConfirmModal>
      )}

      {project.kind !== "scratch" && (
        <section className="settings__section">
          <h3 id="settings-access">{t("settings.access")}</h3>
          <div className="choice-cards" role="radiogroup" aria-labelledby="settings-access">
            {([false, true] as const).map((pub) => (
              <button
                key={String(pub)}
                role="radio"
                aria-checked={project.public === pub}
                disabled={!owner || saving}
                className={`choice-card${project.public === pub ? " is-active" : ""}`}
                onClick={() => project.public !== pub && save({ public: pub })}
              >
                {pub ? <Globe size={18} aria-hidden /> : <Lock size={18} aria-hidden />}
                <span>
                  <strong>{t(pub ? "settings.public" : "settings.private")}</strong>
                  <span className="muted">{t(pub ? "settings.publicBody" : "settings.privateBody")}</span>
                </span>
              </button>
            ))}
          </div>
          {!owner && <p className="muted">{t("settings.ownerOnly")}</p>}
        </section>
      )}

      <section className="settings__section">
        <h3>
          <label htmlFor="settings-description">{t("settings.description")}</label>
        </h3>
        <textarea
          id="settings-description"
          className="field"
          rows={4}
          maxLength={500}
          value={description}
          placeholder={t("settings.descriptionPlaceholder")}
          onChange={(e) => setDescription(e.target.value)}
          onBlur={() => description.trim() !== project.description && save({ description })}
        />
      </section>

      {failed && <p className="settings__error">{failed}</p>}
    </div>
  );
}
