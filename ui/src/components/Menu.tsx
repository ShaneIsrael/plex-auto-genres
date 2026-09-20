import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

export interface MenuItem {
  label: string;
  hint?: string;
  onSelect: () => void;
}

/**
 * A trigger button and a menu rendered into <body> with fixed positioning,
 * so no ancestor (a card's stacking context, the rail, an overflow) can clip
 * or cover it. Right-aligned to the trigger, flips above when there is no
 * room below, follows the trigger on scroll and resize, closes on outside
 * click and Escape. Arrow keys move between items; Escape returns focus to
 * the trigger.
 */
export function Menu({
  items,
  label,
  className = "",
  disabled = false,
  children,
}: {
  items: MenuItem[];
  label: string;
  className?: string;
  disabled?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number; up: boolean } | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);

  const close = useCallback((refocus = false) => {
    setOpen(false);
    setPos(null);
    if (refocus) trigger.current?.focus();
  }, []);

  const place = useCallback(() => {
    const t = trigger.current?.getBoundingClientRect();
    const p = pop.current?.getBoundingClientRect();
    if (!t || !p) return;
    const margin = 8;
    const left = Math.max(margin, Math.min(t.right - p.width, window.innerWidth - p.width - margin));
    const below = t.bottom + 6;
    const up = below + p.height > window.innerHeight - margin && t.top - p.height - 6 > margin;
    setPos({ top: up ? t.top - p.height - 6 : below, left, up });
  }, []);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (!pop.current?.contains(target) && !trigger.current?.contains(target)) close();
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close(true);
      }
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    // Fixed positioning does not follow the trigger, so re-place on any
    // scroll or resize rather than closing (focusing the first item alone
    // can nudge the page).
    document.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    pop.current?.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, close, place]);

  const onMenuKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const buttons = [...(pop.current?.querySelectorAll<HTMLButtonElement>("button") ?? [])];
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next = e.key === "ArrowDown" ? (index + 1) % buttons.length : (index - 1 + buttons.length) % buttons.length;
    buttons[next]?.focus();
  };

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={className}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        disabled={disabled}
        onClick={() => (open ? close() : setOpen(true))}
      >
        {children}
      </button>
      {open &&
        createPortal(
          <div
            ref={pop}
            className={`menu ${pos?.up ? "menu--up" : ""} ${pos ? "" : "menu--measuring"}`}
            role="menu"
            aria-label={label}
            style={{ top: pos?.top ?? 0, left: pos?.left ?? 0 }}
            onKeyDown={onMenuKey}
          >
            {items.map((item) => (
              <button
                key={item.label}
                type="button"
                role="menuitem"
                onClick={() => {
                  close(true);
                  item.onSelect();
                }}
              >
                {item.label}
                {item.hint && <span className="faint">{item.hint}</span>}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}
