import type { JobProgress, JobStatus } from "../api/types";
import { int } from "../lib/format";

/** A job's current action as a bar plus the counters that matter. */
export function LiveProgress({
  progress,
  status,
  compact = false,
}: {
  progress: JobProgress | null;
  status: JobStatus | null;
  compact?: boolean;
}) {
  const running = status === "running";
  const queued = status === "queued";
  const pending = progress?.pending ?? 0;
  const done = progress?.done ?? 0;
  const pct = pending > 0 ? Math.min(100, Math.round((done / pending) * 100)) : running ? 0 : 100;
  const label = queued
    ? "queued"
    : running
      ? pending
        ? `${int(done)} / ${int(pending)}`
        : progress?.action
          ? `${progress.action}…`
          : "starting…"
      : status ?? "";

  return (
    <div className={`live ${compact ? "live--compact" : ""} live--${status ?? "idle"}`}>
      <div className="live__row">
        <span className="live__label mono">
          {progress?.action && <span className="live__action">{progress.action}</span>}
          <span>{label}</span>
        </span>
        {!compact && progress && pending > 0 && (
          <span className="live__counts mono">
            <span className="tone-amber">{int(progress.written)} written</span>
            <span className="muted"> · {int(progress.unchanged)} unchanged</span>
            {progress.failed > 0 && <span className="tone-fail"> · {int(progress.failed)} failed</span>}
          </span>
        )}
      </div>
      <div
        className="live__bar"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label={progress?.action ? `${progress.action} progress` : "progress"}
      >
        <span className="live__fill" style={{ width: `${pct}%` }} />
      </div>
      {!compact && running && progress?.title && (
        <div className="live__title mono faint" aria-live="off">
          {progress.title}
        </div>
      )}
    </div>
  );
}
