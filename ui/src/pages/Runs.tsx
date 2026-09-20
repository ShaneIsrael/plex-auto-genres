import { History } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useLibraries, useRuns } from "../api/client";
import { Empty, ErrorBlock } from "../components/Empty";
import { PageHeader, Panel } from "../components/Panel";
import { Skeleton } from "../components/Skeleton";
import { StatusDot, toneForRun } from "../components/StatusDot";
import { dateTime, duration, int, relTime, shortId } from "../lib/format";
import { reveal } from "../lib/reveal";

export default function Runs() {
  const [library, setLibrary] = useState("");
  const runs = useRuns(200, library || undefined);
  const libraries = useLibraries();
  const names = (libraries.data ?? []).filter((l) => l.configured).map((l) => l.name);

  return (
    <div className="page">
      <PageHeader
        eyebrow="02 · Runs"
        title="History"
        lede="Every run is recorded with what it wrote, and can be undone from its detail page."
        actions={
          <label className="field field--inline">
            <span className="label">Library</span>
            <select className="select" value={library} onChange={(e) => setLibrary(e.target.value)}>
              <option value="">All</option>
              {names.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
        }
      />

      <Panel {...reveal(1)}>
        {runs.isPending ? (
          <div style={{ display: "grid", gap: 12 }}>{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} />)}</div>
        ) : runs.isError ? (
          <ErrorBlock error={runs.error} onRetry={() => runs.refetch()} />
        ) : runs.data.length === 0 ? (
          <Empty icon={<History size={28} strokeWidth={1.5} />} title={library ? `No runs for ${library}` : "No runs yet"} action={{ to: "/libraries", label: "Go to libraries" }}>
            Runs appear here as soon as a job executes — start one from Libraries.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Status</th>
                  <th scope="col">Run</th>
                  <th scope="col">Library</th>
                  <th scope="col">Action</th>
                  <th scope="col">Started</th>
                  <th scope="col">Result</th>
                  <th scope="col" className="num">Duration</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.map((run) => (
                  <tr key={run.run_id}>
                    <td><StatusDot tone={toneForRun(run.status)} label={run.status} /></td>
                    <td className="mono">
                      <Link to={`/runs/${run.run_id}`} title={run.run_id}>{shortId(run.run_id)}</Link>
                      {run.dry_run && <span className="chip chip--dry">dry</span>}
                    </td>
                    <td>{run.library}</td>
                    <td className="mono muted">{run.action}</td>
                    <td className="mono" title={dateTime(run.started_at)}>{relTime(run.started_at)}</td>
                    <td className="mono">
                      {run.report ? (
                        <>
                          <span>{int(run.report.written)} written</span>
                          {run.report.failed > 0 && <span className="tone-fail"> · {int(run.report.failed)} failed</span>}
                          {run.report.error && <span className="tone-fail" title={run.report.error}> · aborted</span>}
                        </>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="mono num muted">{duration(run.report?.duration_s)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
