import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { ChevronDown } from "lucide-react";

export type SelectOption<T extends string> = { value: T; label: string };

const GAP = 4;

/** Miira's dropdown (design guide §4, "Dropdowns … are never browser-native"): a
 *  .field-styled trigger opening a .menu listbox, never a native <select>, whose option
 *  popup is browser chrome no token can reach. Portalled with a fixed position so the
 *  preview toolbar cannot clip it; flips above the trigger when there is no room below.
 *  Keyboard as a native select: arrows/Home/End move, Enter/Space pick, Escape/Tab close,
 *  and focus returns to the trigger. */
export function Select<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: SelectOption<T>[];
  onChange: (value: T) => void;
  label: string;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [style, setStyle] = useState<CSSProperties>({ visibility: "hidden" });
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const current = options.find((o) => o.value === value) ?? options[0];

  const show = (index: number) => {
    setActive(index);
    setStyle({ visibility: "hidden" });
    setOpen(true);
  };
  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  const pick = (index: number) => {
    onChange(options[index].value);
    close();
  };

  useLayoutEffect(() => {
    if (!open || !trigger.current || !list.current) return;
    const r = trigger.current.getBoundingClientRect();
    const h = list.current.offsetHeight;
    const fitsBelow = r.bottom + GAP + h <= window.innerHeight;
    setStyle({
      left: r.left,
      minWidth: r.width,
      ...(fitsBelow ? { top: r.bottom + GAP } : { bottom: window.innerHeight - r.top + GAP }),
    });
    list.current.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const inside = (t: EventTarget | null) =>
      t instanceof Node && !!(trigger.current?.contains(t) || list.current?.contains(t));
    const onDown = (e: MouseEvent) => !inside(e.target) && setOpen(false);
    const onMove = (e: Event) => !inside(e.target) && setOpen(false);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("scroll", onMove, true);
    window.addEventListener("resize", onMove);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("scroll", onMove, true);
      window.removeEventListener("resize", onMove);
    };
  }, [open]);

  const selectedIndex = Math.max(0, options.indexOf(current));

  const onTriggerKey = (e: KeyboardEvent) => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
      e.preventDefault();
      show(selectedIndex);
    }
  };

  const onListKey = (e: KeyboardEvent) => {
    const last = options.length - 1;
    const moves: Record<string, number> = {
      ArrowDown: Math.min(last, active + 1),
      ArrowUp: Math.max(0, active - 1),
      Home: 0,
      End: last,
    };
    if (e.key in moves) {
      e.preventDefault();
      setActive(moves[e.key]);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      pick(active);
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="select-field"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${id}-list` : undefined}
        aria-label={`${label}: ${current?.label ?? ""}`}
        onClick={() => (open ? setOpen(false) : show(selectedIndex))}
        onKeyDown={onTriggerKey}
      >
        <span>{current?.label}</span>
        <ChevronDown size={14} aria-hidden />
      </button>
      {open &&
        createPortal(
          <div
            ref={list}
            id={`${id}-list`}
            className="menu select-field__menu"
            role="listbox"
            aria-label={label}
            tabIndex={-1}
            aria-activedescendant={`${id}-${active}`}
            style={style}
            onKeyDown={onListKey}
          >
            {options.map((o, i) => (
              <div
                key={o.value}
                id={`${id}-${i}`}
                role="option"
                aria-selected={o.value === value}
                className={`menu__item${i === active ? " is-active" : ""}`}
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(i)}
              >
                {o.label}
              </div>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}
