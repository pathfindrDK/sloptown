import { useEffect, useRef, useState, type ReactNode } from "react";

export type MenuItem = { label: string; icon?: ReactNode; onSelect: () => void; danger?: boolean };

/** Miira .menu pattern: a trigger button that opens a small popover list. */
export function Menu({ trigger, items, label }: { trigger: ReactNode; items: MenuItem[]; label: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div className="menu-anchor" ref={ref}>
      <button
        className="icon-btn"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        {trigger}
      </button>
      {open && (
        <div className="menu" role="menu">
          {items.map((it) => (
            <button
              key={it.label}
              role="menuitem"
              className={`menu__item${it.danger ? " menu__item--danger" : ""}`}
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                it.onSelect();
              }}
            >
              {it.icon}
              <span>{it.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
