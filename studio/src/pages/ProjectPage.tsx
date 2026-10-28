// Loads a project and renders the editor or the viewer. The owner's first open of
// a new Vibe answers `not_started`: they choose webapp or "fra bunden" first.
import { useCallback, useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { ExternalLink, Sparkles } from "lucide-react";
import { api } from "../api/client";
import { ApiError, type Project } from "../api/types";
import { useT } from "../lib/i18n";
import { useShell } from "../lib/shell";
import { Editor } from "../editor/Editor";
import { TermsModal } from "../components/TermsModal";
import { StartChooser } from "./StartChooser";
import { Loading } from "../components/Loading";

type Mode = "edit" | "view";

export function ProjectPage({ pid, mode, basePath }: { pid: string; mode: Mode; basePath: string }) {
  const t = useT();
  const navigate = useNavigate();
  const shell = useShell();
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [needTerms, setNeedTerms] = useState(false);
  const [notStarted, setNotStarted] = useState(false);

  const load = useCallback(() => {
    setError(null);
    api
      .getProject(pid)
      .then(setProject)
      .catch((e) => {
        if (e instanceof ApiError && e.code === "terms_required") setNeedTerms(true);
        else if (e instanceof ApiError && e.code === "not_started") setNotStarted(true);
        else if (e instanceof ApiError && e.status === 401 && !shell.embedded) window.location.href = api.loginUrl(window.location.pathname);
        else setError(e instanceof ApiError ? e : new ApiError(0, "network", String(e)));
      });
  }, [pid, shell.embedded]);

  useEffect(load, [load]);

  const back = () => (shell.embedded ? shell.close() : navigate("/"));

  if (needTerms) {
    return (
      <TermsModal
        onCancel={back}
        onAccepted={() => {
          setNeedTerms(false);
          load();
        }}
      />
    );
  }
  if (notStarted && !project) {
    return (
      <StartChooser
        pid={pid}
        onCreated={(p) => {
          setNotStarted(false);
          setProject(p);
        }}
      />
    );
  }
  if (error) {
    return (
      <div className="page page--center">
        <div className="card">
          <p>{error.status === 403 || error.status === 404 ? t("embed.denied") : t("common.error")}</p>
          <button className="btn btn--3d" onClick={load}>
            {t("common.retry")}
          </button>
        </div>
      </div>
    );
  }
  if (!project) {
    return (
      <div className="page page--center">
        <Loading />
      </div>
    );
  }
  if (mode === "edit" && project.myRole === "viewer") return <Navigate to={`${basePath}/view${window.location.search}`} replace />;
  if (mode === "view") return <Viewer project={project} onBack={back} />;
  return <Editor project={project} onBack={back} />;
}

function Viewer({ project, onBack }: { project: Project; onBack: () => void }) {
  const t = useT();
  const shell = useShell();
  const [src, setSrc] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!project.publishedSha || project.kind === "scratch") return;
    api
      .appSession(project.id)
      .then((r) => setSrc(r.url))
      .catch(() => setFailed(true));
  }, [project.id, project.publishedSha]);

  return (
    <div className="viewer">
      {!shell.chromeHidden && (
        <header className="topbar">
          <button className="btn btn--ghost" onClick={onBack}>
            {t("top.back")}
          </button>
          <span className="topbar__icon">
            <Sparkles size={18} />
          </span>
          <span className="topbar__name">{project.title}</span>
          <div className="topbar__spacer" />
          {project.appUrl && (
            <a className="btn btn--ghost" href={project.appUrl} target="_blank" rel="noopener">
              <ExternalLink size={16} /> {t("preview.open")}
            </a>
          )}
        </header>
      )}
      {project.kind === "scratch" ? (
        <div className="empty empty--big">{t("viewer.scratch")}</div>
      ) : !project.publishedSha ? (
        <div className="empty empty--big">{t("viewer.notPublished")}</div>
      ) : failed ? (
        <div className="empty">{t("common.error")}</div>
      ) : src ? (
        <div className="viewer__stage">
          {!loaded && <Loading overlay label={t("preview.loading")} />}
          <iframe
            className="viewer__frame"
            src={src}
            title={project.title}
            onLoad={() => setLoaded(true)}
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads"
          />
        </div>
      ) : (
        <Loading />
      )}
    </div>
  );
}
