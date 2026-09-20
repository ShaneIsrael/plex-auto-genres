import { Link } from "react-router-dom";
import type { RunView } from "../api/types";
import { dateTime } from "../lib/format";
import { toneForRun } from "./StatusDot";

const SLOTS = 40;

/**
 * The console's tape counter: the last forty runs as a strip of blocks,
 * newest on the right. Colour is doubled by the tooltip and the legend, so
 * nothing here relies on colour alone.
 */
export function RunTape({ runs }: { runs: RunView[] }) {
  const recent = runs.slice(0, SLOTS).reverse();
  const padding = Math.max(0, SLOTS - recent.length);
  return (
    <div className="tape" role="list" aria-label={`Last ${recent.length} runs, oldest to newest`}>
      {Array.from({ length: padding }).map((_, i) => (
        <span key={`pad-${i}`} className="tape__cell tape__cell--empty" aria-hidden="true" />
      ))}
      {recent.map((run) => {
        const tone = toneForRun(run.status);
        const written = run.report?.written ?? 0;
        const failed = run.report?.failed ?? 0;
        const title = `${run.library} · ${run.action} · ${run.status} · ${written} written, ${failed} failed · ${dateTime(run.started_at)}`;
        return (
          <Link
            key={run.run_id}
            role="listitem"
            className={`tape__cell tape__cell--${tone}`}
            to={`/runs/${run.run_id}`}
            title={title}
            aria-label={title}
          />
        );
      })}
    </div>
  );
}

export function TapeLegend() {
  return (
    <div className="tape-legend mono faint">
      <span><i className="tape-legend__swatch tape__cell--ok" /> ok</span>
      <span><i className="tape-legend__swatch tape__cell--warn" /> partial</span>
      <span><i className="tape-legend__swatch tape__cell--fail" /> failed</span>
      <span><i className="tape-legend__swatch tape__cell--muted" /> undone / interrupted</span>
      <span><i className="tape-legend__swatch tape__cell--running" /> running</span>
    </div>
  );
}
