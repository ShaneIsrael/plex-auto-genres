import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useRun } from "../api/client";
import { ErrorBlock } from "../components/Empty";
import { PageHeader, Panel } from "../components/Panel";
import { PageSkeleton } from "../components/Skeleton";
import { RUN_STATUS_LABEL, StatusDot, toneForRun } from "../components/StatusDot";
import { dateTime, duration, int } from "../lib/format";

export default function RunDetail() {
  const { runId = "" } = useParams();
  const run = useRun(runId);

  if (run.isPending) return <PageSkeleton />;
  if (run.isError) {
    return (
      <div className="page">
        <Link to="/runs" className="backlink mono"><ArrowLeft size={14} aria-hidden="true" /> runs</Link>
        <ErrorBlock error={run.error} onRetry={() => run.refetch()} />
      </div>
    );
  }

  const r = run.data;
  const report = r.report;
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
        lede={<><StatusDot tone={toneForRun(r.status)} label={RUN_STATUS_LABEL[r.status]} />{r.dry_run && <span className="chip chip--dry">dry run</span>}</>}
        actions={
          <button type="button" className="button" disabled title="Arrives in phase 2. Until then: plex-auto-genres undo <run-id>">
            Undo run
          </button>
        }
      />

      <div className="kv reveal" style={{ "--i": 1 } as React.CSSProperties}>
        <div><span className="label">Started</span><span className="mono">{dateTime(r.started_at)}</span></div>
        <div><span className="label">Finished</span><span className="mono">{dateTime(r.finished_at)}</span></div>
        {r.undone_at && <div><span className="label">Undone</span><span className="mono">{dateTime(r.undone_at)}</span></div>}
      </div>

      {report ? (
        <div className="stat-grid stat-grid--dense reveal" style={{ "--i": 2 } as React.CSSProperties}>
          {cells.map(([label, value, tone]) => (
            <div key={label} className={`stat stat--sm ${tone ? `stat--${tone}` : ""}`}>
              <div className="label">{label}</div>
              <div className="stat__value">{value}</div>
            </div>
          ))}
        </div>
      ) : (
        <Panel className="reveal" style={{ "--i": 2 } as React.CSSProperties}>
          <p className="muted">
            {r.status === "running"
              ? "Still running. Live progress arrives in phase 2; this page refreshes when it finishes."
              : "This run never reported back — the process was interrupted before it finished."}
          </p>
        </Panel>
      )}

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
