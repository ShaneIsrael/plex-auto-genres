import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

export interface MenuItem {
  label: string;
  hint?: string;
  onSelect: () => void;
}

interface Position {
  top: number;
  left: number;
  up: boolean;
}

/**
 * A trigger button and a menu rendered into <body> with fixed positioning,
 * so no ancestor (a card's stacking context, the rail, an overflow) can clip
 * or cover it. Right-aligned to the trigger, flipped above when there is no
 * room below, and re-placed every frame while open — the page keeps moving
 * underneath it as jobs poll and cards change height. Closes on outside
 * click and Escape; arrow keys move between items.
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
  const [pos, setPos] = useState<Position | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);

  const close = useCallback((refocus: boolean) => {
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
    const next: Position = { top: up ? t.top - p.height - 6 : below, left, up };
    // Same place: no state update, so the portal does not re-render per frame.
    setPos((prev) => (prev && prev.top === next.top && prev.left === next.left && prev.up === next.up ? prev : next));
  }, []);

  // Measure before the first paint, then keep following the trigger.
  useLayoutEffect(() => {
    if (!open) return;
    place();
    let frame = 0;
    const tick = () => {
      place();
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [open, place]);

  // Focus the first item once it is actually visible (a `visibility: hidden`
  // element cannot take focus, and the first paint is still measuring).
  useEffect(() => {
    if (!open || !pos) return;
    pop.current?.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
  }, [open, pos]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (!pop.current?.contains(target) && !trigger.current?.contains(target)) close(false);
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close(true);
      }
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

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
        onClick={() => (open ? close(false) : setOpen(true))}
        onKeyDown={(e) => {
          // Arrow keys open the menu and land on its first item, as a menu button should.
          if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
            e.preventDefault();
            setOpen(true);
          }
        }}
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
                  // No refocus: the trigger is usually about to be disabled or
                  // replaced by what the action starts.
                  close(false);
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
