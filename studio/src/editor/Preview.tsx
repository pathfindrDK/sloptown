import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ExternalLink, Monitor, RotateCw, Smartphone, Tablet, Wrench } from "lucide-react";
import { api } from "../api/client";
import type { Project, Role } from "../api/types";
import { useT } from "../lib/i18n";
import { Loading, LoadingTiles } from "../components/Loading";
import { Select } from "../components/Select";
import { useSegmentedIndicator } from "../lib/useSegmentedIndicator";

type Viewport = "desktop" | "tablet" | "mobile";
const widths: Record<Viewport, string> = { desktop: "100%", tablet: "820px", mobile: "390px" };
// Apps on an editor with an older SDK never send vibe:ready; drop the overlay this long after load.
const READY_FALLBACK_MS = 4000;
// The first turn writes the whole app; it usually takes a couple of minutes.
export const FIRST_BUILD_MS = 3 * 60_000;
// Between turn.finished and the reloaded history the build is over but the Vibe
// still looks empty: keep the building cover this long instead of flashing the welcome.
const BUILD_COVER_GRACE_MS = 5000;

export type PreviewError = { message: string; stack?: string; at: number };

/** Parse a vibe:error message posted by the SDK running inside the preview. */
export function parsePreviewError(data: unknown): PreviewError | null {
  if (!data || typeof data !== "object") return null;
  const m = data as Record<string, unknown>;
  if (m.type !== "vibe:error" || typeof m.message !== "string") return null;
  return { message: m.message.slice(0, 2000), stack: typeof m.stack === "string" ? m.stack.slice(0, 4000) : undefined, at: Date.now() };
}

type Props = {
  project: Project;
  reloadSignal: number;
  /** Nothing built yet (only platform commits): invite the teacher to the chat. */
  empty: boolean;
  /** The AI is working (or a message is queued) while the Vibe is still empty. */
  building?: boolean;
  /** When the teacher sent the first message (ms), if known; else when `building` was seen. */
  buildStartedAt?: number | null;
  onFixError: (prompt: string) => void;
};

/** A pen-drawn arrow towards the chat: a wavering stroke and an uneven, open head. */
function HandDrawnArrow() {
  return (
    <svg className="preview__welcome-arrow" width="104" height="52" viewBox="0 0 120 60" fill="none" aria-hidden>
      <path
        d="M6 40c9-6 17-9 26-7 9 2 15 8 25 7 11-1 18-10 29-12 8-1 15 0 22 2"
        stroke="currentColor"
        strokeWidth="3.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M95 17c5 3 10 7 15 12-6 3-11 8-15 13"
        stroke="currentColor"
        strokeWidth="3.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** "m:ss" left of the first build's estimate, never below 0:00. */
export function formatCountdown(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Covers the preview while the first version is built: tiles and a countdown. */
function BuildingCover({ since }: { since: number }) {
  const t = useT();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const left = since + FIRST_BUILD_MS - now;
  return (
    <div className="preview__welcome preview__building" role="status" aria-live="off">
      <LoadingTiles />
      <div>
        <h2>{t("preview.building.title")}</h2>
        {left > 0 ? (
          <p>
            {t("preview.building.eta")} <strong className="preview__countdown">{formatCountdown(left)}</strong>
          </p>
        ) : (
          <p>{t("preview.building.late")}</p>
        )}
        <p className="preview__building-hint">{t("preview.building.hint")}</p>
      </div>
    </div>
  );
}

export function Preview({ project, reloadSignal, empty, building = false, buildStartedAt = null, onFixError }: Props) {
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
  const segRef = useRef<HTMLDivElement>(null);
  useSegmentedIndicator(segRef, viewport);
  // Start of the first build while the cover is up; null shows the welcome (or the app).
  const [buildSince, setBuildSince] = useState<number | null>(null);

  useEffect(() => {
    if (!empty) return setBuildSince(null);
    if (building) return setBuildSince((s) => buildStartedAt ?? s ?? Date.now());
    const id = setTimeout(() => setBuildSince(null), BUILD_COVER_GRACE_MS);
    return () => clearTimeout(id);
  }, [empty, building, buildStartedAt]);

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
        <div className="segmented" role="group" aria-label={t("preview.device")} ref={segRef}>
          <span className="segmented__indicator" aria-hidden />
          {(
            [
              ["desktop", Monitor],
              ["tablet", Tablet],
              ["mobile", Smartphone],
            ] as const
          ).map(([v, Icon]) => (
            <button
              key={v}
              type="button"
              className={viewport === v ? "active" : undefined}
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
        <div className="preview__viewas">
          <span className="muted" aria-hidden>
            {t("preview.viewAs")}
          </span>
          <Select<Role>
            label={t("preview.viewAs")}
            value={viewAs}
            onChange={setViewAs}
            options={[
              { value: "owner", label: t("role.owner") },
              { value: "editor", label: t("role.editor") },
              { value: "viewer", label: t("role.viewer") },
            ]}
          />
        </div>
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
            {empty && buildSince !== null ? (
              <BuildingCover since={buildSince} />
            ) : empty ? (
              <div className="preview__welcome">
                <div>
                  <h2>{t("preview.welcome.title")}</h2>
                  <p>{t("preview.welcome.body")}</p>
                </div>
                <HandDrawnArrow />
              </div>
            ) : (
              appLoading && <Loading overlay label={t("preview.loading")} />
            )}
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
