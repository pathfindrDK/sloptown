import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { useT } from "../lib/i18n";

type Props = {
  title: string;
  icon?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
  onClose: () => void;
  wide?: boolean;
};

/** Miira .modal pattern. Escape and backdrop click close; focus moves into the dialog. */
export function Modal({ title, icon, children, actions, onClose, wide }: Props) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    ref.current?.querySelector<HTMLElement>("button.btn--primary, button, [tabindex]")?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal${wide ? " modal--wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <header className="modal__header">
          {icon && <span className="modal__icon">{icon}</span>}
          <h2 className="modal__title">{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}>
            <X size={18} />
          </button>
        </header>
        <div className="modal__body">{children}</div>
        {actions && <footer className="modal__actions">{actions}</footer>}
      </div>
    </div>
  );
}

type ConfirmProps = {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
};

export function ConfirmModal({ title, body, confirmLabel, onConfirm, onCancel, busy }: ConfirmProps) {
  const t = useT();
  return (
    <Modal
      title={title}
      onClose={onCancel}
      actions={
        <>
          <button className="btn btn--3d" onClick={onCancel}>
            {t("common.cancel")}
          </button>
          <button className="btn btn--3d btn--primary" onClick={onConfirm} disabled={busy}>
            {confirmLabel}
          </button>
        </>
      }
    >
      <p>{body}</p>
    </Modal>
  );
}
