import { useEffect, useState } from "react";
import { ScrollText } from "lucide-react";
import { api } from "../api/client";
import type { Terms } from "../api/types";
import { useT } from "../lib/i18n";
import { Modal } from "./Modal";

/** Teachers accept the terms of use before their first Vibe. */
export function TermsModal({ onAccepted, onCancel }: { onAccepted: () => void; onCancel: () => void }) {
  const t = useT();
  const [terms, setTerms] = useState<Terms | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.terms().then(setTerms);
  }, []);
  return (
    <Modal
      title={t("terms.title")}
      icon={<ScrollText size={20} />}
      onClose={onCancel}
      wide
      actions={
        <>
          <button className="btn btn--3d" onClick={onCancel}>
            {t("common.cancel")}
          </button>
          <button
            className="btn btn--3d btn--primary"
            disabled={!terms || busy}
            onClick={async () => {
              if (!terms) return;
              setBusy(true);
              try {
                await api.acceptTerms(terms.version);
                onAccepted();
              } finally {
                setBusy(false);
              }
            }}
          >
            {t("terms.accept")}
          </button>
        </>
      }
    >
      <p>{t("terms.intro")}</p>
      {/* Terms HTML is authored by SlopTown (control plane), not by users. */}
      {terms ? <div className="terms" dangerouslySetInnerHTML={{ __html: terms.html }} /> : <p className="muted">{t("common.loading")}</p>}
    </Modal>
  );
}
