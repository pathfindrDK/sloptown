import { TriangleAlert, Wrench } from "lucide-react";
import { ApiError } from "../api/types";
import { useT } from "../lib/i18n";
import { Modal } from "./Modal";

/** Shown when publishing fails. A failed build offers to hand the build log to the AI. */
export function PublishErrorModal({ error, onFix, onClose }: { error: ApiError; onFix: (prompt: string) => void; onClose: () => void }) {
  const t = useT();
  const log = error.code === "build_failed" && typeof error.body.log === "string" ? error.body.log.trim() : "";
  const paths = error.code === "locked_files_changed" && Array.isArray(error.body.paths) ? (error.body.paths as string[]) : [];
  return (
    <Modal
      title={t("publish.failed")}
      icon={<TriangleAlert size={18} />}
      onClose={onClose}
      wide={!!log}
      actions={
        <>
          <button className="btn btn--3d" onClick={onClose}>
            {t("common.close")}
          </button>
          {log && (
            <button
              className="btn btn--3d btn--primary"
              onClick={() => {
                onFix(`${t("publish.fixPrompt")}\n\n\`\`\`\n${log}\n\`\`\``);
                onClose();
              }}
            >
              <Wrench size={16} /> {t("preview.fix")}
            </button>
          )}
        </>
      }
    >
      <p>{error.message}</p>
      {paths.length > 0 && (
        <ul>
          {paths.map((p) => (
            <li key={p}>
              <code>{p}</code>
            </li>
          ))}
        </ul>
      )}
      {log && <pre className="publish-error__log">{log}</pre>}
    </Modal>
  );
}
