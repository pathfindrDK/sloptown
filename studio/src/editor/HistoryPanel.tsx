import { useState } from "react";
import { Eye, GitCompare, History as HistoryIcon, Rocket, RotateCcw } from "lucide-react";
import { api } from "../api/client";
import type { Commit, Diff, History } from "../api/types";
import { useLocale, useT } from "../lib/i18n";
import { ConfirmModal, Modal } from "../components/Modal";
import { Loading } from "../components/Loading";
import { LoadFailed } from "../components/LoadFailed";

type Props = {
  projectId: string;
  history: History | null;
  failed: boolean;
  onRetry: () => void;
  canEdit: boolean;
  onChanged: () => void;
  onPreview: (c: Commit, parentSha: string | null) => void;
  /** Absent for a "fra bunden" project, which is never published. */
  onPublish?: (sha: string) => Promise<void>;
};

export function HistoryPanel({ projectId, history, failed, onRetry, canEdit, onChanged, onPreview, onPublish }: Props) {
  const t = useT();
  const locale = useLocale();
  const [restoring, setRestoring] = useState<Commit | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [busy, setBusy] = useState(false);

  if (!history) return failed ? <LoadFailed onRetry={onRetry} /> : <Loading label={t("common.loading")} />;
  if (!history.commits.length) return <div className="empty">{t("history.empty")}</div>;

  const fmt = new Intl.DateTimeFormat(locale === "da" ? "da-DK" : "en-GB", { dateStyle: "medium", timeStyle: "short" });

  return (
    <>
      <ol className="history">
        {history.commits.map((c, i) => {
          const published = c.sha === history.publishedSha;
          const parent = history.commits[i + 1]?.sha ?? null;
          return (
            <li key={c.sha} className={`history__item${published ? " is-published" : ""}`}>
              <div className="history__head">
                <HistoryIcon size={14} className="muted" />
                <span className="history__msg">{c.message.split("\n")[0]}</span>
                {published && (
                  <span className="role-badge role-badge--ok">
                    <Rocket size={12} /> {t("history.published")}
                  </span>
                )}
              </div>
              <div className="history__meta muted">
                {fmt.format(new Date(c.createdAt))} · {c.authorName}
                {c.threadTitle && <> · {c.threadTitle}</>} · {c.files.length} {t("history.files")}
              </div>
              <div className="history__actions">
                <button className="btn btn--ghost btn--small" onClick={() => onPreview(c, parent)}>
                  <Eye size={14} /> {t("history.preview")}
                </button>
                <button className="btn btn--ghost btn--small" onClick={async () => setDiff(await api.diff(projectId, c.sha))}>
                  <GitCompare size={14} /> {t("history.diff")}
                </button>
                {canEdit && i > 0 && (
                  <button className="btn btn--ghost btn--small" onClick={() => setRestoring(c)}>
                    <RotateCcw size={14} /> {t("history.restore")}
                  </button>
                )}
                {canEdit && !published && onPublish && (
                  <button
                    className="btn btn--ghost btn--small"
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        await onPublish(c.sha);
                        onChanged();
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    <Rocket size={14} /> {t("history.publish")}
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {restoring && (
        <ConfirmModal
          title={t("history.restoreTitle")}
          body={t("history.restoreBody")}
          confirmLabel={t("history.restore")}
          busy={busy}
          onCancel={() => setRestoring(null)}
          onConfirm={async () => {
            setBusy(true);
            try {
              await api.restore(projectId, restoring.sha);
              setRestoring(null);
              onChanged();
            } finally {
              setBusy(false);
            }
          }}
        />
      )}
      {diff && (
        <Modal title={`${t("history.diff")} · ${diff.sha.slice(0, 7)}`} onClose={() => setDiff(null)} wide>
          {diff.files.map((f) => (
            <section key={f.path} className="diff">
              <h3 className="diff__path">
                {f.path} <span className="chip chip--muted">{f.status}</span>
              </h3>
              <pre className="diff__patch">
                {f.patch.split("\n").map((line, i) => (
                  <div
                    key={i}
                    className={
                      line.startsWith("+") && !line.startsWith("+++")
                        ? "diff__add"
                        : line.startsWith("-") && !line.startsWith("---")
                          ? "diff__del"
                          : line.startsWith("@@")
                            ? "diff__hunk"
                            : undefined
                    }
                  >
                    {line || " "}
                  </div>
                ))}
              </pre>
            </section>
          ))}
        </Modal>
      )}
    </>
  );
}
