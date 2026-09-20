import { useEffect, useState } from "react";
import { useCronPreview } from "../../api/client";
import type { ScheduleSettings, SchedulerStatus, ValidationIssue } from "../../api/types";
import { Field, describedBy } from "../../components/form/Field";
import { Segmented } from "../../components/form/Segmented";
import { Toggle } from "../../components/form/Toggle";
import { dateTime, relTime } from "../../lib/format";
import { SCHEDULE_PRESETS, issueAt, type Help } from "./editor";

/**
 * When the automatic pass runs: a preset or a cron expression, checked live
 * against the server, and a switch to pause it without losing the expression.
 */
export function ScheduleEditor({
  id,
  value,
  effective,
  onChange,
  errors,
  help,
}: {
  id: string;
  value: ScheduleSettings;
  /** What the running server is actually set to, for the "server default" case. */
  effective: SchedulerStatus | null | undefined;
  onChange: (next: ScheduleSettings) => void;
  errors: ValidationIssue[];
  help: Help;
}) {
  const cron = value.cron ?? "";
  const trimmed = cron.trim();
  // Whitespace is not an expression: store it as "unset" so the document does
  // not differ from a saved null by a space nobody can see.
  const set = (next: Partial<ScheduleSettings>) => {
    const merged = { ...value, ...next };
    onChange({ ...merged, cron: (merged.cron ?? "").trim() ? merged.cron : null });
  };
  const [debounced, setDebounced] = useState(trimmed);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(trimmed), 300);
    return () => window.clearTimeout(handle);
  }, [trimmed]);
  const preview = useCronPreview(debounced);
  const fieldError = issueAt(errors, ["schedule", "cron"]);

  // "custom" is a sticky choice: it must not snap back to a preset just
  // because the expression the user started from happens to match one.
  const [custom, setCustom] = useState(false);
  const matching = SCHEDULE_PRESETS.find((p) => p.cron === trimmed)?.key;
  const presetKey = custom || (trimmed && !matching) ? "custom" : matching ?? (trimmed ? "custom" : "server");
  const choosePreset = (key: string) => {
    setCustom(key === "custom");
    if (key === "custom") {
      set({ cron: trimmed || "0 2 * * *" });
      return;
    }
    set({ cron: SCHEDULE_PRESETS.find((p) => p.key === key)?.cron ?? null });
  };

  let status: React.ReactNode;
  if (!trimmed) {
    status =
      effective?.source === "env" ? (
        <>
          Using the server's own schedule, <code className="mono">{effective.cron}</code>
          {effective.enabled && effective.next_fire_at ? <> — next {relTime(effective.next_fire_at)}.</> : "."}
        </>
      ) : (
        <>No automatic pass: pick a preset or type an expression.</>
      );
  } else if (fieldError) {
    status = null;
  } else if (preview.data?.ok) {
    status = (
      <>
        Next: {preview.data.next_fire_at.map((ts, i) => <strong key={ts}>{i ? " · " : ""}{dateTime(ts)}</strong>)}
      </>
    );
  } else if (preview.data && !preview.data.ok) {
    status = <span className="tone-fail">{preview.data.error}</span>;
  } else if (preview.isError) {
    // retry is off: say so rather than claiming to still be checking.
    status = <span className="tone-fail">Could not check this expression ({(preview.error as Error).message}).</span>;
  } else {
    status = <span className="faint">Checking…</span>;
  }

  return (
    <div className="schedule">
      <Toggle
        id={`${id}-enabled`}
        checked={value.enabled}
        onChange={(v) => set({ enabled: v })}
        label={value.enabled ? "Automatic pass on" : "Automatic pass paused"}
        help={help("ScheduleSettings", "enabled") ?? "Off pauses the schedule; the expression is kept."}
      />

      <div className="schedule__row">
        <Field id={`${id}-preset`} label="When" help="Every enabled library, in config order, through the job queue.">
          <Segmented
            name={`${id}-preset`}
            ariaLabel="Schedule preset"
            value={presetKey}
            onChange={choosePreset}
            options={[
              { value: "server", label: "server default", hint: "Whatever --cron / CRON_SCHEDULE says" },
              ...SCHEDULE_PRESETS.map((p) => ({ value: p.key, label: p.label, hint: p.cron })),
              { value: "custom", label: "custom", hint: "Type a cron expression" },
            ]}
          />
        </Field>

        <Field id={`${id}-cron`} label="Cron expression" help={help("ScheduleSettings", "cron")} error={fieldError}>
          <input
            id={`${id}-cron`}
            className="input mono"
            value={cron}
            placeholder="minute hour day month weekday"
            spellCheck={false}
            aria-describedby={describedBy(`${id}-cron`, true, fieldError)}
            onChange={(e) => set({ cron: e.target.value })}
          />
        </Field>
      </div>

      {status && (
        <p className={`schedule__preview mono ${value.enabled ? "" : "schedule__preview--paused"}`} aria-live="polite">
          {status}
          {!value.enabled && trimmed && <span className="chip chip--warn">paused</span>}
        </p>
      )}
    </div>
  );
}
