import { Suspense, lazy, useCallback, useEffect, useReducer, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Eye, FileCode, PanelRightClose, PanelRightOpen, X } from "lucide-react";
import { api } from "../api/client";
import { ApiError, type BucketStatus, type Commit, type Project } from "../api/types";
import { initialProjectState, projectReducer } from "../lib/projectState";
import { useT } from "../lib/i18n";
import { useShell } from "../lib/shell";
import { TopBar } from "../components/TopBar";
import { BudgetModal } from "../components/Budget";
import { PublishErrorModal } from "../components/PublishError";
import { Preview } from "./Preview";
import { ChatPanel } from "./ChatPanel";
import { FilesPanel } from "./FilesPanel";
import { HistoryPanel } from "./HistoryPanel";
import { useLoad } from "../lib/useLoad";
import { Loading } from "../components/Loading";

// CodeMirror is most of the bundle; load it when the first file tab opens.
const CodeView = lazy(() => import("./CodeView").then((m) => ({ default: m.CodeView })));

type Tab = { kind: "preview" } | { kind: "file"; path: string; sha?: string; compareSha?: string | null; highlight?: boolean };
const tabKey = (t: Tab) => (t.kind === "preview" ? "preview" : `${t.path}@${t.sha ?? "work"}`);

type SideTab = "chat" | "files" | "history";

const SIDEBAR_KEY = "sloptown.sidebarWidth";
function loadWidth(): number {
  try {
    const n = Number(localStorage.getItem(SIDEBAR_KEY));
    return n >= 300 && n <= 900 ? n : 420;
  } catch {
    return 420;
  }
}

export function Editor({ project: initial, onBack }: { project: Project; onBack: () => void }) {
  const t = useT();
  const shell = useShell();
  const [project, setProject] = useState(initial);
  const [state, dispatch] = useReducer(projectReducer, initialProjectState);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  // A "fra bunden" project has no preview; it opens on its README instead.
  const webapp = initial.kind !== "scratch";
  const firstTab: Tab = webapp ? { kind: "preview" } : { kind: "file", path: "README.md" };
  const [tabs, setTabs] = useState<Tab[]>([firstTab]);
  const [activeTab, setActiveTab] = useState(tabKey(firstTab));
  const [sideTab, setSideTab] = useState<SideTab>("chat");
  const [sideOpen, setSideOpen] = useState(true);
  const [sideWidth, setSideWidth] = useState(loadWidth);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [budgetModal, setBudgetModal] = useState<BucketStatus | null>(null);
  const [publishError, setPublishError] = useState<ApiError | null>(null);
  const [publishing, setPublishing] = useState(false);
  const shownBudgetState = useRef<string>("ok");

  const canEdit = project.myRole === "owner" || project.myRole === "editor";

  // Initial loads + live stream.
  useEffect(() => {
    const ac = new AbortController();
    api.listThreads(project.id, true).then((threads) => {
      dispatch({ event: "local.threads", data: threads });
      // Open on a fresh conversation, unless a turn is already under way.
      const busy = threads.find((th) => th.status !== "idle");
      if (busy) setActiveThreadId((cur) => cur ?? busy.id);
    });
    api.budget(project.id).then((b) => dispatch({ event: "local.budget", data: b }));
    api.events(project.id, dispatch, ac.signal);
    return () => ac.abort();
  }, [project.id]);

  // Poll project status while the container is starting.
  useEffect(() => {
    if (project.status === "ready") return;
    const id = setInterval(() => api.getProject(project.id).then(setProject).catch(() => {}), 2000);
    return () => clearInterval(id);
  }, [project.id, project.status]);

  const historyLoad = useLoad(() => api.history(project.id), [project.id, project.status, state.historyVersion, historyRefresh]);
  const history = historyLoad.data;

  // Friendly modal the first time the build budget becomes nerfed or blocked.
  useEffect(() => {
    const b = state.budget;
    if (!b?.enabled) return;
    const s = b.build.state;
    if ((s === "nerfed" || s === "blocked") && shownBudgetState.current !== s) setBudgetModal(b.build);
    shownBudgetState.current = s;
  }, [state.budget]);

  const headSha = history?.commits[0]?.sha ?? null;
  const prevSha = history?.commits[1]?.sha ?? null;

  const openTab = useCallback((tab: Tab) => {
    const key = tabKey(tab);
    setTabs((ts) => (ts.some((x) => tabKey(x) === key) ? ts : [...ts, tab]));
    setActiveTab(key);
  }, []);

  const closeTab = (key: string) => {
    const rest = tabs.filter((x) => tabKey(x) !== key);
    setTabs(rest);
    if (activeTab === key) setActiveTab(rest[0] ? tabKey(rest[0]) : "");
  };

  const publish = async (sha: string) => {
    setPublishing(true);
    try {
      const r = await api.publish(project.id, sha);
      setProject((p) => ({ ...p, publishedSha: r.publishedSha, appUrl: r.url }));
      setHistoryRefresh((n) => n + 1);
    } catch (e) {
      setPublishError(e instanceof ApiError ? e : new ApiError(0, "network", String(e)));
    } finally {
      setPublishing(false);
    }
  };

  const askAiToFix = (prompt: string) => {
    setDraft(prompt);
    setSideOpen(true);
    setSideTab("chat");
  };

  const rename = async (title: string) => {
    setProject(await api.renameProject(project.id, title));
    shell.titleChanged(project.id, title);
  };

  const onPreviewCommit = (c: Commit, parent: string | null) => {
    const path = c.files[0];
    if (path) openTab({ kind: "file", path, sha: c.sha, compareSha: parent, highlight: true });
  };

  const startResize = (e: ReactPointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = sideWidth;
    let latest = startW;
    const move = (ev: PointerEvent) => {
      latest = Math.min(900, Math.max(300, startW + (startX - ev.clientX)));
      setSideWidth(latest);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.classList.remove("is-resizing");
      try {
        localStorage.setItem(SIDEBAR_KEY, String(latest));
      } catch {
        /* storage unavailable */
      }
    };
    document.body.classList.add("is-resizing");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const active: Tab | undefined = tabs.find((x) => tabKey(x) === activeTab) ?? tabs[0];
  const blocked = !!state.budget?.enabled && state.budget.build.state === "blocked";

  return (
    <div className={`editor${shell.chromeHidden ? " editor--embedded" : ""}`}>
      <TopBar
        project={project}
        budget={state.budget}
        headSha={headSha}
        canEdit={canEdit}
        publishing={publishing}
        compact={shell.chromeHidden}
        onBack={onBack}
        onRename={rename}
        onShare={() => shell.share(project.id)}
        onPublish={() => headSha && publish(headSha)}
        onDownload={() => api.download(project.id, `${project.title || "vibe"}.zip`)}
      />
      <div className="editor__body">
        <main className="editor__main">
          <div className="tabs" role="tablist">
            {tabs.map((tab) => {
              const key = tabKey(tab);
              return (
                <div key={key} className={`tabs__tab${key === activeTab ? " is-active" : ""}`} role="tab" aria-selected={key === activeTab}>
                  <button className="tabs__label" onClick={() => setActiveTab(key)}>
                    {tab.kind === "preview" ? <Eye size={14} /> : <FileCode size={14} />}
                    {tab.kind === "preview" ? t("preview.tab") : tab.path.split("/").pop()}
                    {tab.kind === "file" && tab.sha && <span className="muted">@{tab.sha.slice(0, 7)}</span>}
                  </button>
                  {tab.kind === "file" && (
                    <button className="tabs__close" onClick={() => closeTab(key)} aria-label={t("common.close")}>
                      <X size={12} />
                    </button>
                  )}
                </div>
              );
            })}
            <div className="topbar__spacer" />
            <button
              className="icon-btn"
              onClick={() => setSideOpen((o) => !o)}
              title={sideOpen ? t("side.collapse") : t("side.expand")}
              aria-label={sideOpen ? t("side.collapse") : t("side.expand")}
            >
              {sideOpen ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
            </button>
          </div>
          <div className="editor__view">
            {/* Preview stays mounted so switching tabs doesn't reload the app. */}
            {webapp && (
              <div className="editor__pane" hidden={active?.kind !== "preview"}>
                <Preview
                  project={project}
                  reloadSignal={state.historyVersion}
                  onFixError={askAiToFix}
                />
              </div>
            )}
            {!active && <div className="empty">{t("editor.noTabs")}</div>}
            {active?.kind === "file" && (
              <div className="editor__pane">
                <Suspense fallback={<Loading />}>
                <CodeView
                  key={tabKey(active)}
                  projectId={project.id}
                  path={active.path}
                  sha={active.sha}
                  compareSha={active.sha ? active.compareSha : prevSha}
                  highlight={active.sha ? !!active.highlight : state.changedFiles.includes(active.path)}
                  reloadSignal={active.sha ? 0 : state.filesVersion}
                />
                </Suspense>
              </div>
            )}
          </div>
        </main>
        {sideOpen && (
          <aside className="sidepane" style={{ width: sideWidth }}>
            <div className="sidepane__resizer" onPointerDown={startResize} role="separator" aria-orientation="vertical" />
            <div className="sidepane__tabs" role="tablist">
              {(["chat", "files", "history"] as const).map((k) => (
                <button key={k} role="tab" aria-selected={sideTab === k} className={`sidepane__tab${sideTab === k ? " is-active" : ""}`} onClick={() => setSideTab(k)}>
                  {t(`side.${k}`)}
                </button>
              ))}
            </div>
            <div className="sidepane__content">
              {sideTab === "chat" &&
                (canEdit ? (
                  <ChatPanel
                    projectId={project.id}
                    state={state}
                    dispatch={dispatch}
                    activeThreadId={activeThreadId}
                    setActiveThreadId={setActiveThreadId}
                    draft={draft}
                    clearDraft={() => setDraft(null)}
                    blocked={blocked}
                    onBlocked={() => state.budget && setBudgetModal(state.budget.build)}
                  />
                ) : (
                  <div className="empty">{t("embed.denied")}</div>
                ))}
              {sideTab === "files" && (
                <FilesPanel
                  projectId={project.id}
                  version={state.filesVersion}
                  ready={project.status === "ready"}
                  changedFiles={state.changedFiles}
                  activePath={active?.kind === "file" ? active.path : null}
                  onOpen={(path) => openTab({ kind: "file", path })}
                />
              )}
              {sideTab === "history" && (
                <HistoryPanel
                  projectId={project.id}
                  history={history ? { ...history, publishedSha: project.publishedSha ?? history.publishedSha } : null}
                  failed={historyLoad.failed}
                  onRetry={historyLoad.retry}
                  canEdit={canEdit}
                  onChanged={() => setHistoryRefresh((n) => n + 1)}
                  onPreview={onPreviewCommit}
                  onPublish={webapp ? publish : undefined}
                />
              )}
            </div>
          </aside>
        )}
      </div>
      {budgetModal && <BudgetModal bucket={budgetModal} onClose={() => setBudgetModal(null)} />}
      {publishError && <PublishErrorModal error={publishError} onFix={askAiToFix} onClose={() => setPublishError(null)} />}
    </div>
  );
}
