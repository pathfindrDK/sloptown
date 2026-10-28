import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ExternalLink, Monitor, RotateCw, Smartphone, Tablet, Wrench } from "lucide-react";
import { api } from "../api/client";
import type { Project, Role } from "../api/types";
import { useT } from "../lib/i18n";
import { Loading } from "../components/Loading";

type Viewport = "desktop" | "tablet" | "mobile";
const widths: Record<Viewport, string> = { desktop: "100%", tablet: "820px", mobile: "390px" };
// Apps on an editor with an older SDK never send vibe:ready; drop the overlay this long after load.
const READY_FALLBACK_MS = 4000;

export type PreviewError = { message: string; stack?: string; at: number };

/** Parse a vibe:error message posted by the SDK running inside the preview. */
export function parsePreviewError(data: unknown): PreviewError | null {
  if (!data || typeof data !== "object") return null;
  const m = data as Record<string, unknown>;
  if (m.type !== "vibe:error" || typeof m.message !== "string") return null;
  return { message: m.message.slice(0, 2000), stack: typeof m.stack === "string" ? m.stack.slice(0, 4000) : undefined, at: Date.now() };
}

type Props = { project: Project; reloadSignal: number; onFixError: (prompt: string) => void };

export function Preview({ project, reloadSignal, onFixError }: Props) {
  const t = useT();
  const [viewport, setViewport] = useState<Viewport>("desktop");
  const [viewAs, setViewAs] = useState<Role>("owner");
  const [src, setSrc] = useState<string | null>(null);
  const [frameKey, setFrameKey] = useState(0);
  const [errors, setErrors] = useState<PreviewError[]>([]);
  const [failed, setFailed] = useState(false);
  // The iframe is up before Vite has compiled and rendered the app: cover it until
  // the SDK says vibe:ready (or the first error, or the fallback after load).
  const [appLoading, setAppLoading] = useState(true);
  const fallback = useRef<ReturnType<typeof setTimeout>>();

  // One-time session URL sets the preview cookie (and view-as) on the preview host.
  useEffect(() => {
    let live = true;
    setFailed(false);
    api
      .previewSession(project.id, viewAs)
      .then((r) => live && setSrc(r.url))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [project.id, viewAs]);

  const previewOrigin = useMemo(() => {
    try {
      return project.previewUrl ? new URL(project.previewUrl, window.location.href).origin : null;
    } catch {
      return null;
    }
  }, [project.previewUrl]);

  useEffect(() => {
    const onMessage = (ev: MessageEvent) => {
      if (ev.origin !== previewOrigin) return;
      if ((ev.data as { type?: unknown } | null)?.type === "vibe:ready") return setAppLoading(false);
      const err = parsePreviewError(ev.data);
      if (err) setAppLoading(false);
      if (err) setErrors((es) => (es.some((e) => e.message === err.message) ? es : [...es.slice(-9), err]));
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [previewOrigin]);

  // A new frame (session URL, reload, viewport role switch) starts covered again.
  useEffect(() => {
    setAppLoading(true);
    return () => clearTimeout(fallback.current);
  }, [src, frameKey]);

  const onFrameLoad = () => {
    clearTimeout(fallback.current);
    fallback.current = setTimeout(() => setAppLoading(false), READY_FALLBACK_MS);
  };

  // The session URL is single use; once the cookie is set, reloads go to the plain preview URL.
  const reload = useCallback(() => {
    setErrors([]);
    setSrc(project.previewUrl);
    setFrameKey((k) => k + 1);
  }, [project.previewUrl]);

  // Reload after each finished turn so the teacher sees the result.
  useEffect(() => {
    if (reloadSignal) reload();
  }, [reloadSignal, reload]);

  const fix = () => {
    const details = errors.map((e) => [e.message, e.stack].filter(Boolean).join("\n")).join("\n\n");
    onFixError(`${t("preview.fixPrompt")}\n\n\`\`\`\n${details}\n\`\`\``);
  };

  return (
    <div className="preview">
      <div className="preview__toolbar">
        <div className="segmented" role="group">
          {(
            [
              ["desktop", Monitor],
              ["tablet", Tablet],
              ["mobile", Smartphone],
            ] as const
          ).map(([v, Icon]) => (
            <button
              key={v}
              className={`segmented__btn${viewport === v ? " is-active" : ""}`}
              onClick={() => setViewport(v)}
              aria-pressed={viewport === v}
              title={t(`preview.${v}`)}
            >
              <Icon size={16} />
            </button>
          ))}
        </div>
        <button className="icon-btn" onClick={reload} title={t("preview.reload")} aria-label={t("preview.reload")}>
          <RotateCw size={16} />
        </button>
        <a className="icon-btn" href={project.previewUrl ?? undefined} target="_blank" rel="noopener" title={t("preview.open")} aria-label={t("preview.open")}>
          <ExternalLink size={16} />
        </a>
        <label className="preview__viewas">
          <span className="muted">{t("preview.viewAs")}</span>
          <select className="select" value={viewAs} onChange={(e) => setViewAs(e.target.value as Role)}>
            <option value="owner">{t("role.owner")}</option>
            <option value="editor">{t("role.editor")}</option>
            <option value="viewer">{t("role.viewer")}</option>
          </select>
        </label>
        <div className="topbar__spacer" />
        {errors.length > 0 && (
          <div className="preview__errors">
            <AlertTriangle size={16} />
            <span title={errors.map((e) => e.message).join("\n")}>
              {errors.length} {t("preview.errors")}
            </span>
            <button className="btn btn--3d btn--small btn--danger" onClick={fix}>
              <Wrench size={14} /> {t("preview.fix")}
            </button>
          </div>
        )}
      </div>
      <div className="preview__stage">
        {failed ? (
          <div className="empty">{t("common.error")}</div>
        ) : project.status !== "ready" || !src ? (
          <Loading label={t("preview.starting")} />
        ) : (
          <div className="preview__frame-wrap" style={{ width: widths[viewport] }}>
            {appLoading && <Loading overlay label={t("preview.loading")} />}
            <iframe
              key={`${frameKey}-${src}`}
              className="preview__frame"
              style={{ width: "100%" }}
              src={src}
              title={t("preview.tab")}
              onLoad={onFrameLoad}
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads"
            />
          </div>
        )}
      </div>
    </div>
  );
}
