import { ChevronDown, Play, Square } from "lucide-react";
import { useRef } from "react";
import { useCancelJob, useStartJob } from "../api/client";
import type { JobView, RunOptions } from "../api/types";
import { useToast } from "./Toast";

/**
 * Start a job for one library, or cancel the one that is active.
 * The secondary options live in a native <details>, so the popover needs
 * no state and closes on Escape / outside click by itself.
 */
export function RunMenu({ library, active }: { library: string; active: JobView | null }) {
  const start = useStartJob();
  const cancel = useCancelJob();
  const toast = useToast();
  const details = useRef<HTMLDetailsElement>(null);

  const go = async (options: RunOptions, what: string) => {
    details.current?.removeAttribute("open");
    try {
      await start.mutateAsync({ library, options });
      toast("ok", `${what} queued`, library);
    } catch (e) {
      toast("fail", `Could not start ${library}`, (e as Error).message);
    }
  };

  if (active) {
    return (
      <button
        type="button"
        className="button button--ghost button--sm"
        disabled={cancel.isPending}
        onClick={async () => {
          try {
            await cancel.mutateAsync(active.job_id);
            toast("warn", "Cancelling", library);
          } catch (e) {
            toast("fail", "Could not cancel", (e as Error).message);
          }
        }}
      >
        <Square size={12} aria-hidden="true" /> Cancel
      </button>
    );
  }

  return (
    <div className="runmenu">
      <button
        type="button"
        className="button button--sm runmenu__main"
        disabled={start.isPending}
        onClick={() => go({}, "Run")}
      >
        <Play size={12} aria-hidden="true" /> Run
      </button>
      <details ref={details} className="runmenu__more">
        <summary className="button button--sm runmenu__toggle" aria-label="More run options">
          <ChevronDown size={14} aria-hidden="true" />
        </summary>
        <div className="runmenu__pop" role="menu">
          <button type="button" role="menuitem" onClick={() => go({ dry_run: true }, "Dry run")}>
            Dry run
            <span className="faint">report only, write nothing</span>
          </button>
          <button type="button" role="menuitem" onClick={() => go({ force: true }, "Full run")}>
            Force
            <span className="faint">ignore the cache, redo every item</span>
          </button>
          <button type="button" role="menuitem" onClick={() => go({ only: ["posters", "sort"] }, "Posters + sort")}>
            Posters &amp; sort only
            <span className="faint">skip the provider lookups</span>
          </button>
        </div>
      </details>
    </div>
  );
}
