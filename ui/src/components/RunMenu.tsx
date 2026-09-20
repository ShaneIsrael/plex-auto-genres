import { ChevronDown, Play, Square } from "lucide-react";
import { useCancelJob, useStartJob } from "../api/client";
import type { JobView, RunOptions } from "../api/types";
import { Menu } from "./Menu";
import { useToast } from "./Toast";

/** Start a job for one library, or cancel the one that is active. */
export function RunMenu({ library, active }: { library: string; active: JobView | null }) {
  const start = useStartJob();
  const cancel = useCancelJob();
  const toast = useToast();

  const go = async (options: RunOptions, what: string) => {
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
      <Menu
        label={`More run options for ${library}`}
        className="button button--sm runmenu__toggle"
        disabled={start.isPending}
        items={[
          { label: "Dry run", hint: "report only, write nothing", onSelect: () => void go({ dry_run: true }, "Dry run") },
          { label: "Force", hint: "ignore the cache, redo every item", onSelect: () => void go({ force: true }, "Full run") },
          { label: "Posters & sort only", hint: "skip the provider lookups", onSelect: () => void go({ only: ["posters", "sort"] }, "Posters + sort") },
        ]}
      >
        <ChevronDown size={14} aria-hidden="true" />
      </Menu>
    </div>
  );
}
