import { RotateCw } from "lucide-react";
import { useT } from "../lib/i18n";

export function LoadFailed({ onRetry }: { onRetry: () => void }) {
  const t = useT();
  return (
    <div className="empty">
      <p>{t("common.error")}</p>
      <button className="btn btn--3d btn--small" onClick={onRetry}>
        <RotateCw size={14} /> {t("common.retry")}
      </button>
    </div>
  );
}
