import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";

export type MenuItem = { label: string; icon?: ReactNode; onSelect: () => void; danger?: boolean };

const GAP = 4;

/** Miira .menu pattern: a trigger button that opens a small popover list.
 *  The list is portalled to <body> with a fixed position, because its triggers sit in
 *  scrolling boxes (the thread list) that would clip it; it opens upwards when there is
 *  no room below, and closes on scroll or resize rather than drifting from its trigger. */
export function Menu({ trigger, items, label }: { trigger: ReactNode; items: MenuItem[]; label: string }) {
  const [open, setOpen] = useState(false);
  const [style, setStyle] = useState<CSSProperties>({ visibility: "hidden" });
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (!open || !button.current || !list.current) return;
    const r = button.current.getBoundingClientRect();
    const h = list.current.offsetHeight;
    const right = window.innerWidth - r.right;
    const fitsBelow = r.bottom + GAP + h <= window.innerHeight;
    setStyle(fitsBelow ? { top: r.bottom + GAP, right } : { bottom: window.innerHeight - r.top + GAP, right });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    // A window scroll or resize has the window as target, which is not a Node.
    const inside = (t: EventTarget | null) =>
      t instanceof Node && !!(button.current?.contains(t) || list.current?.contains(t));
    const onDown = (e: MouseEvent) => !inside(e.target) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onMove = (e: Event) => !inside(e.target) && setOpen(false);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onMove, true);
    window.addEventListener("resize", onMove);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onMove, true);
      window.removeEventListener("resize", onMove);
    };
  }, [open]);

  return (
    <div className="menu-anchor">
      <button
        ref={button}
        className="icon-btn"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setStyle({ visibility: "hidden" });
          setOpen((o) => !o);
        }}
      >
        {trigger}
      </button>
      {open &&
        createPortal(
          <div className="menu" role="menu" ref={list} style={style}>
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
          </div>,
          document.body,
        )}
    </div>
  );
}
