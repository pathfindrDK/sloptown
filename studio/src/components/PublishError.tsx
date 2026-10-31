import { TriangleAlert, Wrench } from "lucide-react";
import { ApiError, type PublishFinding } from "../api/types";
import { useT } from "../lib/i18n";
import { Modal } from "./Modal";

/** Shown when publishing fails. A failed build, or a pre-publish check the AI could not
 *  fix by itself, offers to hand what was found to the AI. */
export function PublishErrorModal({ error, onFix, onClose }: { error: ApiError; onFix: (prompt: string) => void; onClose: () => void }) {
  const t = useT();
  const log = error.code === "build_failed" && typeof error.body.log === "string" ? error.body.log.trim() : "";
  const paths = error.code === "locked_files_changed" && Array.isArray(error.body.paths) ? (error.body.paths as string[]) : [];
  const findings = error.code === "check_failed" && Array.isArray(error.body.findings) ? (error.body.findings as PublishFinding[]) : [];
  const fixPrompt = log
    ? `${t("publish.fixPrompt")}\n\n\`\`\`\n${log}\n\`\`\``
    : findings.length
      ? `${t("publish.checkFixPrompt")}\n\n${findings.map((f) => `- ${f.title}: \`${f.path}:${f.line}\` (${f.rule})`).join("\n")}`
      : "";
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
          {fixPrompt && (
            <button
              className="btn btn--3d btn--primary"
              onClick={() => {
                onFix(fixPrompt);
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
      {findings.length > 0 && (
        <ul>
          {findings.map((f) => (
            <li key={`${f.path}:${f.line}:${f.rule}`}>{f.title}</li>
          ))}
        </ul>
      )}
      {log && <pre className="publish-error__log">{log}</pre>}
    </Modal>
  );
}
