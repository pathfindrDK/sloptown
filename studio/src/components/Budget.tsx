import { Gauge, Hourglass } from "lucide-react";
import type { BucketStatus } from "../api/types";
import { formatReset, formatTokens, meterFor } from "../lib/budget";
import { useLocale, useT } from "../lib/i18n";
import { Modal } from "./Modal";

export function BudgetMeter({ bucket, enabled }: { bucket: BucketStatus | null; enabled: boolean }) {
  const t = useT();
  const m = meterFor(bucket, enabled);
  if (!bucket || !enabled) return null;
  const pct = m.fraction === null ? null : Math.round(m.fraction * 100);
  const limit = bucket.hardLimitTokens ?? bucket.nerfLimitTokens;
  const title = limit
    ? `${formatTokens(bucket.usedTokens)} / ${formatTokens(limit)} tokens`
    : `${formatTokens(bucket.usedTokens)} tokens · ${t("budget.unlimited")}`;
  return (
    <div className={`budget-meter budget-meter--${m.state}`} title={title}>
      <Gauge size={16} aria-hidden />
      <div className="budget-meter__bar" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? 0}>
        <div className="budget-meter__fill" style={{ width: `${pct ?? 0}%` }} />
        {m.nerfMark !== null && <div className="budget-meter__mark" style={{ left: `${m.nerfMark * 100}%` }} />}
      </div>
      <span className="budget-meter__label">
        {pct === null ? `${formatTokens(bucket.usedTokens)} tokens` : `${pct}%`} <span className="muted">{t("budget.used")}</span>
      </span>
    </div>
  );
}

export function BudgetModal({ bucket, onClose }: { bucket: BucketStatus; onClose: () => void }) {
  const t = useT();
  const locale = useLocale();
  const blocked = bucket.state === "blocked";
  return (
    <Modal
      title={t(blocked ? "budget.blockedTitle" : "budget.nerfedTitle")}
      icon={blocked ? <Hourglass size={20} /> : <Gauge size={20} />}
      onClose={onClose}
      actions={
        <button className="btn btn--3d btn--primary" onClick={onClose}>
          {t("budget.ok")}
        </button>
      }
    >
      <p>{t(blocked ? "budget.blockedBody" : "budget.nerfedBody")}</p>
      <p className="budget-modal__reset">
        <strong>{t("budget.resets")}</strong>
        <br />
        <span className="muted">
          {t("budget.resetsAt")}: {formatReset(bucket.resetsAt, locale)}
        </span>
      </p>
    </Modal>
  );
}
