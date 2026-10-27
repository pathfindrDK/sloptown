import type { BucketStatus, BudgetState } from "../api/types";

export type Meter = {
  /** 0..1 of the hard limit (or nerf limit when there is no hard limit); null = unlimited */
  fraction: number | null;
  state: BudgetState;
  /** fraction where the nerf limit sits on the meter, if both limits exist */
  nerfMark: number | null;
  showModal: boolean;
};

export function meterFor(bucket: BucketStatus | null | undefined, enabled = true): Meter {
  if (!bucket || !enabled) return { fraction: null, state: "ok", nerfMark: null, showModal: false };
  const limit = bucket.hardLimitTokens ?? bucket.nerfLimitTokens;
  const fraction = limit && limit > 0 ? Math.min(1, bucket.usedTokens / limit) : null;
  const nerfMark =
    bucket.hardLimitTokens && bucket.nerfLimitTokens ? Math.min(1, bucket.nerfLimitTokens / bucket.hardLimitTokens) : null;
  return {
    fraction,
    state: bucket.state,
    nerfMark,
    showModal: bucket.state === "nerfed" || bucket.state === "blocked",
  };
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")} mio.`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

/** "mandag kl. 00:00" style reset label, always shown in Copenhagen time. */
export function formatReset(iso: string, locale: "da" | "en"): string {
  const d = new Date(iso);
  return new Intl.DateTimeFormat(locale === "da" ? "da-DK" : "en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Copenhagen",
  }).format(d);
}
