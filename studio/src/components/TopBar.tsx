// Mirrors the docs-editor TopBar pattern: back, file icon, editable title, actions.
import { useEffect, useState } from "react";
import { ArrowLeft, Download, ExternalLink, Rocket, Share2, Sparkles } from "lucide-react";
import type { BudgetStatus, Project } from "../api/types";
import { useT } from "../lib/i18n";
import { BudgetMeter } from "./Budget";

type Props = {
  project: Project;
  budget: BudgetStatus | null;
  headSha: string | null;
  canEdit: boolean;
  publishing: boolean;
  /** Embedded with chrome=none: Miira draws back button and file name, we show only actions. */
  compact?: boolean;
  onBack: () => void;
  onRename: (title: string) => void;
  onShare: () => void;
  onPublish: () => void;
  onDownload: () => void;
};

export function TopBar({ project, budget, headSha, canEdit, publishing, compact, onBack, onRename, onShare, onPublish, onDownload }: Props) {
  const t = useT();
  const [title, setTitle] = useState(project.title);
  useEffect(() => setTitle(project.title), [project.title]);

  const commit = () => {
    const next = title.trim();
    if (next && next !== project.title) onRename(next);
    else setTitle(project.title);
  };

  const upToDate = !!headSha && headSha === project.publishedSha;

  return (
    <header className={`topbar${compact ? " topbar--compact" : ""}`}>
      {!compact && (
        <>
      <button className="icon-btn" onClick={onBack} aria-label={t("top.back")} title={t("top.back")}>
        <ArrowLeft size={18} />
      </button>
      <span className="topbar__icon" aria-hidden>
        <Sparkles size={18} />
      </span>
      <input
        className="topbar__title"
        value={title}
        placeholder={t("top.titlePlaceholder")}
        aria-label={t("top.titlePlaceholder")}
        disabled={!canEdit}
        onChange={(e) => setTitle(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") {
            setTitle(project.title);
            (e.target as HTMLInputElement).blur();
          }
        }}
      />
        </>
      )}
      <div className="topbar__spacer" />
      {budget && <BudgetMeter bucket={budget.build} enabled={budget.enabled} />}
      {project.appUrl && project.publishedSha && (
        <a className="btn btn--ghost" href={project.appUrl} target="_blank" rel="noopener">
          <ExternalLink size={16} /> {t("top.openApp")}
        </a>
      )}
      {canEdit && (
        <button className="btn btn--ghost" onClick={onDownload} disabled={!headSha}>
          <Download size={16} /> {t("top.download")}
        </button>
      )}
      <button className="btn btn--3d" onClick={onShare}>
        <Share2 size={16} /> {t("top.share")}
      </button>
      {canEdit && project.kind !== "scratch" && (
        <button
          className="btn btn--3d btn--primary"
          onClick={onPublish}
          disabled={!headSha || upToDate || publishing}
          title={t("top.publishVersion")}
        >
          <Rocket size={16} /> {upToDate ? t("top.published") : t("top.publish")}
        </button>
      )}
    </header>
  );
}
