import type { Level, RunStatus } from "../api/types";

export type Tone = "ok" | "warn" | "fail" | "muted" | "running" | "teal";

export function toneForRun(status: RunStatus): Tone {
  switch (status) {
    case "ok":
      return "ok";
    case "partial":
      return "warn";
    case "failed":
      return "fail";
    case "running":
      return "running";
    default:
      return "muted";
  }
}

export function toneForLevel(level: Level): Tone {
  return level === "ok" ? "ok" : level === "warn" ? "warn" : "fail";
}

/** A coloured dot that is never the only carrier of meaning: `label` is required. */
export function StatusDot({ tone, label, className = "" }: { tone: Tone; label: string; className?: string }) {
  return (
    <span className={`status status--${tone} ${className}`}>
      <span className="status__dot" aria-hidden="true" />
      <span className="status__label">{label}</span>
    </span>
  );
}
