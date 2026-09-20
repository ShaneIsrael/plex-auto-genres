import { useEffect, useRef, useState } from "react";
import { int } from "../lib/format";
import { reveal } from "../lib/reveal";

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Counts from 0 to `value` over ~600ms on first render; instant under reduced motion. */
function useCountUp(value: number | null | undefined): number | null {
  const [shown, setShown] = useState<number | null>(value == null ? null : 0);
  const animated = useRef(false);
  useEffect(() => {
    if (value == null) return;
    if (animated.current || prefersReducedMotion() || value === 0) {
      setShown(value);
      return;
    }
    animated.current = true;
    const start = performance.now();
    const dur = 600;
    let frame = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - t, 3);
      setShown(Math.round(value * eased));
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value]);
  return shown;
}

export function Stat({
  label,
  value,
  sub,
  tone,
  index = 0,
}: {
  label: string;
  value: number | null | undefined;
  sub?: string;
  tone?: "amber" | "teal" | "fail" | "ok";
  index?: number;
}) {
  const shown = useCountUp(value);
  return (
    <div {...reveal(index, `stat ${tone ? `stat--${tone}` : ""}`)}>
      <div className="label">{label}</div>
      <div className="stat__value">{shown == null ? "—" : int(shown)}</div>
      {sub && <div className="stat__sub muted mono">{sub}</div>}
    </div>
  );
}
