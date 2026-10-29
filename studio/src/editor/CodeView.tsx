import { useEffect, useRef, useState } from "react";
import { Code2, Eye, GitCompare, Lock } from "lucide-react";
import { EditorState, RangeSetBuilder, type Extension } from "@codemirror/state";
import { Decoration, EditorView, lineNumbers, highlightActiveLine } from "@codemirror/view";
import { defaultHighlightStyle, syntaxHighlighting, foldGutter } from "@codemirror/language";
import { javascript } from "@codemirror/lang-javascript";
import { python } from "@codemirror/lang-python";
import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { unifiedMergeView } from "@codemirror/merge";
import { api } from "../api/client";
import { changedLines } from "../lib/lineDiff";
import { useT } from "../lib/i18n";
import { previewKind } from "../lib/filePreview";
import { useSegmentedIndicator } from "../lib/useSegmentedIndicator";
import { FilePreview } from "./FilePreview";

function languageFor(path: string): Extension[] {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  switch (ext) {
    case "ts":
      return [javascript({ typescript: true })];
    case "tsx":
      return [javascript({ typescript: true, jsx: true })];
    case "js":
    case "mjs":
    case "cjs":
      return [javascript()];
    case "jsx":
      return [javascript({ jsx: true })];
    case "py":
      return [python()];
    case "css":
      return [css()];
    case "html":
      return [html()];
    case "json":
      return [json()];
    case "md":
      return [markdown()];
    default:
      return [];
  }
}

const changedLine = Decoration.line({ class: "cm-vibe-changed" });

function changedDecorations(lines: Set<number>): Extension {
  return EditorView.decorations.of((view) => {
    const b = new RangeSetBuilder<Decoration>();
    for (let n = 1; n <= view.state.doc.lines; n++) {
      if (lines.has(n)) b.add(view.state.doc.line(n).from, view.state.doc.line(n).from, changedLine);
    }
    return b.finish();
  });
}

const theme = EditorView.theme({
  "&": { height: "100%", fontSize: "var(--miira-font-size-sm)", backgroundColor: "var(--miira-color-surface)" },
  ".cm-scroller": { fontFamily: "var(--miira-font-mono)" },
  ".cm-gutters": {
    backgroundColor: "var(--miira-color-surface-2)",
    color: "var(--miira-color-text-subtle)",
    borderRight: "1px solid var(--miira-color-border)",
  },
  ".cm-content": { color: "var(--miira-color-text)" },
  ".cm-activeLine": { backgroundColor: "var(--miira-color-surface-2)" },
  ".cm-vibe-changed": { backgroundColor: "var(--miira-color-highlight)" },
  ".cm-changedLine": { backgroundColor: "var(--miira-color-diff-add)" },
  ".cm-deletedChunk": { backgroundColor: "var(--miira-color-diff-remove)" },
});

type Props = {
  projectId: string;
  path: string;
  /** View the file at this version instead of the working tree. */
  sha?: string;
  /** Version to compare against for highlight and diff (usually the parent commit). */
  compareSha?: string | null;
  /** Highlight lines changed relative to compareSha (the AI touched this file in its last turn). */
  highlight: boolean;
  reloadSignal: number;
  /** HTML files open on the preview host (a "fra bunden" project). */
  previewHost?: boolean;
};

type Mode = "code" | "preview";

export function CodeView({ projectId, path, sha, compareSha, highlight, reloadSignal, previewHost = false }: Props) {
  const t = useT();
  const host = useRef<HTMLDivElement>(null);
  const kind = previewKind(path);
  // Rendered first, except when the AI just changed the file: then its changed lines.
  const [mode, setMode] = useState<Mode>(kind && !highlight ? "preview" : "code");
  const segRef = useRef<HTMLDivElement>(null);
  useSegmentedIndicator(segRef, mode);
  const [content, setContent] = useState<string | null>(null);
  const [binary, setBinary] = useState(false);
  const [previous, setPrevious] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);
  const [diff, setDiff] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    let live = true;
    setError(false);
    api
      .file(projectId, path, sha)
      .then((f) => {
        if (!live) return;
        setContent(f.content);
        setBinary(f.content === null);
        setLocked(f.locked);
      })
      .catch(() => live && setError(true));
    if (compareSha) {
      api
        .file(projectId, path, compareSha)
        .then((f) => live && setPrevious(f.content))
        .catch(() => live && setPrevious("")); // file is new in this version
    } else {
      setPrevious(null);
    }
    return () => {
      live = false;
    };
  }, [projectId, path, sha, compareSha, reloadSignal]);

  // A binary file (an image, a sound) has no code to show.
  const showPreview = !!kind && (mode === "preview" || binary);

  useEffect(() => {
    if (showPreview || !host.current || content === null) return;
    const exts: Extension[] = [
      lineNumbers(),
      foldGutter(),
      highlightActiveLine(),
      syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
      EditorState.readOnly.of(true),
      EditorView.editable.of(false),
      theme,
      ...languageFor(path),
    ];
    if (diff && previous !== null) {
      exts.push(unifiedMergeView({ original: previous, mergeControls: false, gutter: true }));
    } else if (highlight && previous !== null) {
      exts.push(changedDecorations(changedLines(previous, content)));
    }
    const view = new EditorView({ state: EditorState.create({ doc: content, extensions: exts }), parent: host.current });
    return () => view.destroy();
  }, [content, previous, diff, highlight, path, showPreview]);

  return (
    <div className="codeview">
      <div className="codeview__toolbar">
        <span className="codeview__path">{path}</span>
        {sha && (
          <span className="chip">
            {t("code.atVersion")} {sha.slice(0, 7)}
          </span>
        )}
        {locked ? (
          <span className="chip chip--muted" title={t("code.locked")}>
            <Lock size={12} /> {t("code.locked")}
          </span>
        ) : (
          <span className="chip chip--muted">{t("code.readonly")}</span>
        )}
        <div className="topbar__spacer" />
        {!showPreview && (
          <button
            className={`btn btn--ghost btn--small${diff ? " is-active" : ""}`}
            onClick={() => setDiff((d) => !d)}
            disabled={previous === null}
            aria-pressed={diff}
          >
            <GitCompare size={14} /> {t("code.diff")}
          </button>
        )}
        {kind && !binary && (
          <div className="segmented" role="group" aria-label={t("code.mode")} ref={segRef}>
            <span className="segmented__indicator" aria-hidden />
            {(
              [
                ["code", Code2, t("code.modeCode")],
                ["preview", Eye, t("code.modePreview")],
              ] as const
            ).map(([m, Icon, label]) => (
              <button key={m} className={mode === m ? "active" : undefined} aria-pressed={mode === m} onClick={() => setMode(m)}>
                <Icon size={14} /> {label}
              </button>
            ))}
          </div>
        )}
      </div>
      {error ? (
        <div className="empty">{t("common.error")}</div>
      ) : showPreview && kind ? (
        <div className="codeview__preview">
          <FilePreview
            projectId={projectId}
            path={path}
            sha={sha}
            kind={kind}
            content={content}
            previewHost={previewHost}
            reloadSignal={reloadSignal}
          />
        </div>
      ) : (
        <div className="codeview__editor" ref={host} />
      )}
    </div>
  );
}
