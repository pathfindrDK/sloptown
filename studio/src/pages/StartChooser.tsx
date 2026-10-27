// The owner's first open of a new Vibe: choose what it is before anything is
// created. A webapp gets the template, preview and publish; "fra bunden" is an
// empty project that is never served on the web (control plane: kind="scratch").
import { useState } from "react";
import { AppWindow, Cpu } from "lucide-react";
import { api } from "../api/client";
import type { Project, ProjectKind } from "../api/types";
import { useT } from "../lib/i18n";

export function StartChooser({ pid, onCreated }: { pid: string; onCreated: (p: Project) => void }) {
  const t = useT();
  const [busy, setBusy] = useState<ProjectKind | null>(null);
  const [failed, setFailed] = useState(false);

  const start = async (kind: ProjectKind) => {
    setBusy(kind);
    setFailed(false);
    try {
      onCreated(await api.createProject(pid, kind));
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
      onClick={() => start(kind)}
    >
      <span className="start__icon" aria-hidden>
        {busy === kind ? <span className="spinner" /> : <Icon size={28} />}
      </span>
      <strong>{copy[kind][0]}</strong>
      <span className="muted">{copy[kind][1]}</span>
    </button>
  );

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
