// "Indstillinger" in the side pane: the Vibe's icon (kept by Miira), who can open the
// published app, and its description. More settings land here later.
import { useEffect, useState } from "react";
import type { IconState } from "../lib/embedBridge";
import { Globe, ImagePlus, Lock } from "lucide-react";
import { api } from "../api/client";
import type { Project } from "../api/types";
import { useT } from "../lib/i18n";
import { useShell } from "../lib/shell";

type Props = { project: Project; onChange: (project: Project) => void };

export function SettingsPanel({ project, onChange }: Props) {
  const t = useT();
  const shell = useShell();
  const owner = project.myRole === "owner";
  const [description, setDescription] = useState(project.description);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => setDescription(project.description), [project.description]);
  const [icon, setIcon] = useState<IconState | null>(null);
  useEffect(() => shell.onIcon((fileId, next) => fileId === project.id && setIcon(next)), [shell, project.id]);

  const save = async (patch: { description?: string; public?: boolean }) => {
    setSaving(true);
    setFailed(false);
    try {
      onChange(await api.updateProject(project.id, patch));
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
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

      {project.kind !== "scratch" && (
        <section className="settings__section">
          <h3 id="settings-access">{t("settings.access")}</h3>
          <div className="settings__choices" role="radiogroup" aria-labelledby="settings-access">
            {([false, true] as const).map((pub) => (
              <button
                key={String(pub)}
                role="radio"
                aria-checked={project.public === pub}
                disabled={!owner || saving}
                className={`settings__choice${project.public === pub ? " is-active" : ""}`}
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

      {failed && <p className="settings__error">{t("common.error")}</p>}
    </div>
  );
}
