import { ArrowLeft, RotateCcw, Square } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useCancelJob, useJobEvents, useRun, useUndoRun } from "../api/client";
import { useConfirm } from "../components/ConfirmDialog";
import { ErrorBlock } from "../components/Empty";
import { LiveProgress } from "../components/LiveProgress";
import { PageHeader, Panel } from "../components/Panel";
import { PageSkeleton } from "../components/Skeleton";
import { RUN_STATUS_LABEL, StatusDot, toneForRun } from "../components/StatusDot";
import { useToast } from "../components/Toast";
import { dateTime, duration, int } from "../lib/format";

export default function RunDetail() {
  const { runId = "" } = useParams();
  const run = useRun(runId);
  const r = run.data;
  // Only follow the stream while the run is actually in flight; finished
  // runs render from their stored report.
  const live = useJobEvents(r && r.status === "running" ? r.job_id : null);
  const cancel = useCancelJob();
  const undo = useUndoRun();
  const confirm = useConfirm();
  const toast = useToast();

  if (run.isPending) return <PageSkeleton />;
  if (run.isError || !r) {
    return (
      <div className="page">
        <Link to="/runs" className="backlink mono"><ArrowLeft size={14} aria-hidden="true" /> runs</Link>
        <ErrorBlock error={run.error} onRetry={() => run.refetch()} />
      </div>
    );
  }

  const report = r.report;
  const running = r.status === "running";
  const canUndo =
    !running &&
    !r.dry_run &&
    !r.undone_at &&
    report !== null &&
    (report.written > 0 || report.plex_requests > 0) &&
    ["ok", "partial", "failed", "cancelled"].includes(r.status);

  const onCancel = async () => {
    if (!r.job_id) return;
    try {
      await cancel.mutateAsync(r.job_id);
      toast("warn", "Cancelling", `${r.library} stops after the items already in flight.`);
    } catch (e) {
      toast("fail", "Could not cancel", (e as Error).message);
    }
  };

  const onUndo = async () => {
    const ok = await confirm({
      title: `Undo this run on ${r.library}?`,
      body: (
        <>
          Every item this run touched goes back to the tags it had before —{" "}
          <strong>{int(report?.written ?? 0)}</strong> item{(report?.written ?? 0) === 1 ? "" : "s"}. Changes made since by other runs are overwritten too.
        </>
      ),
      confirmLabel: "Undo run",
      danger: true,
    });
    if (!ok) return;
    try {
      const result = await undo.mutateAsync(r.run_id);
      toast("ok", "Run undone", `${int(result.restored)} restored, ${int(result.skipped)} skipped.`);
    } catch (e) {
      toast("fail", "Undo failed", (e as Error).message);
    }
  };

  const cells: [string, string, string?][] = report
    ? [
        ["Written", int(report.written), "amber"],
        ["Already correct", int(report.unchanged)],
        ["Cached", int(report.skipped)],
        ["Failed", int(report.failed), report.failed ? "fail" : undefined],
        ["Plex writes", int(report.plex_requests), "amber"],
        ["Provider calls", int(report.provider_requests), "teal"],
        ["Duration", duration(report.duration_s)],
      ]
    : [];

  return (
    <div className="page">
      <Link to="/runs" className="backlink mono reveal"><ArrowLeft size={14} aria-hidden="true" /> runs</Link>
      <PageHeader
        eyebrow={`02 · Run ${r.run_id}`}
        title={<>{r.library} <span className="muted">/ {r.action}</span></>}
        lede={
          <>
            <StatusDot tone={toneForRun(r.status)} label={RUN_STATUS_LABEL[r.status]} />
            {r.dry_run && <span className="chip chip--dry">dry run</span>}
            {report?.cancelled && <span className="chip">stopped early</span>}
          </>
        }
        actions={
          running && r.job_id ? (
            <button type="button" className="button button--ghost" onClick={onCancel} disabled={cancel.isPending}>
              <Square size={14} aria-hidden="true" /> Cancel
            </button>
          ) : (
            <button
              type="button"
              className="button"
              onClick={onUndo}
              disabled={!canUndo || undo.isPending}
              title={
                r.undone_at
                  ? "Already undone"
                  : r.dry_run
                    ? "A dry run changed nothing"
                    : !canUndo
                      ? "Nothing to restore"
                      : "Restore the tags this run overwrote"
              }
            >
              <RotateCcw size={14} aria-hidden="true" /> {r.undone_at ? "Undone" : "Undo run"}
            </button>
          )
        }
      />

      <div className="kv reveal" style={{ "--i": 1 } as React.CSSProperties}>
        <div><span className="label">Started</span><span className="mono">{dateTime(r.started_at)}</span></div>
        <div><span className="label">Finished</span><span className="mono">{dateTime(r.finished_at)}</span></div>
        {r.undone_at && <div><span className="label">Undone</span><span className="mono">{dateTime(r.undone_at)}</span></div>}
      </div>

      {running && (
        <Panel className="reveal" style={{ "--i": 2 } as React.CSSProperties} eyebrow="Live" title="In progress">
          {r.job_id ? (
            <>
              <LiveProgress progress={live.progress} status={live.status ?? "running"} />
              {live.lastError && (
                <p className="faint mono" style={{ marginTop: 12 }}>
                  last failure: <span className="tone-fail">{live.lastError.title}</span> — {live.lastError.error}
                </p>
              )}
              {!live.connected && (
                <p className="faint" style={{ marginTop: 12 }}>Reconnecting to the event stream…</p>
              )}
            </>
          ) : (
            <p className="muted">Running, but this process is not the one executing it — no live feed.</p>
          )}
        </Panel>
      )}

      {report ? (
        <div className="stat-grid stat-grid--dense reveal" style={{ "--i": 2 } as React.CSSProperties}>
          {cells.map(([label, value, tone]) => (
            <div key={label} className={`stat stat--sm ${tone ? `stat--${tone}` : ""}`}>
              <div className="label">{label}</div>
              <div className="stat__value">{value}</div>
            </div>
          ))}
        </div>
      ) : !running ? (
        <Panel className="reveal" style={{ "--i": 2 } as React.CSSProperties}>
          <p className="muted">This run never reported back — the process was interrupted before it finished.</p>
        </Panel>
      ) : null}

      {report && report.failures.length > 0 && (
        <Panel className="reveal" style={{ "--i": 3 } as React.CSSProperties} eyebrow="Failures" title={`${int(report.failed)} could not be resolved`} aside={report.failed > report.failures.length ? <span className="faint mono">showing first {report.failures.length}</span> : undefined}>
          <ul className="failures">
            {report.failures.map(([title, error], i) => (
              <li key={`${title}-${i}`}>
                <span className="failures__title">{title}</span>
                <span className="failures__error mono">{error}</span>
              </li>
            ))}
          </ul>
          <p className="faint" style={{ marginTop: 16 }}>
            Pin the right id with <code>plex-auto-genres bind "{r.library}" "&lt;title&gt;" &lt;provider&gt; &lt;id&gt;</code>.
          </p>
        </Panel>
      )}
    </div>
  );
}
