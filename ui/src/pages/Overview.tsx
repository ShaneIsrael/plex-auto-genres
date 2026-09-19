import { Activity, AlertTriangle, Play, Square } from "lucide-react";
import { Link } from "react-router-dom";
import { isActive, useCancelJob, useDoctor, useHealth, useJobs, useLibraries, useRuns, useStartJobs } from "../api/client";
import { useConfirm } from "../components/ConfirmDialog";
import { LiveProgress } from "../components/LiveProgress";
import { useToast } from "../components/Toast";
import type { LibraryView, RunView } from "../api/types";
import { Empty, ErrorBlock } from "../components/Empty";
import { PageHeader, Panel } from "../components/Panel";
import { RunTape, TapeLegend } from "../components/RunTape";
import { Skeleton } from "../components/Skeleton";
import { Stat } from "../components/Stat";
import { RUN_STATUS_LABEL, StatusDot, toneForLevel, toneForRun } from "../components/StatusDot";
import { int, relTime } from "../lib/format";

function latestTagRuns(runs: RunView[]): Map<string, RunView> {
  const latest = new Map<string, RunView>();
  for (const run of runs) {
    if (run.action !== "genres" && run.action !== "collections") continue;
    if (!latest.has(run.library)) latest.set(run.library, run);
  }
  return latest;
}

export default function Overview() {
  const health = useHealth();
  const libraries = useLibraries();
  const runs = useRuns(200);
  const doctor = useDoctor();
  const jobs = useJobs();
  const startAll = useStartJobs();
  const cancel = useCancelJob();
  const confirm = useConfirm();
  const toast = useToast();
  const activeJobs = (jobs.data ?? []).filter(isActive);

  const libs = libraries.data ?? [];
  const configured = libs.filter((l) => l.configured);
  const enabled = configured.filter((l) => l.enabled);
  const allRuns = runs.data ?? [];

  const latest = latestTagRuns(allRuns);
  const taggedLastRun = [...latest.values()].reduce((n, r) => n + (r.report?.written ?? 0), 0);
  const pendingFailures = configured.reduce((n, l) => n + (l.stats.failed ?? 0), 0);
  const weekAgo = Date.now() / 1000 - 7 * 86400;
  const runsThisWeek = allRuns.filter((r) => r.started_at >= weekAgo).length;

  const serverName = health.data?.plex.server_name;

  const onRunAll = async () => {
    const names = enabled.map((l) => l.name);
    const ok = await confirm({
      title: "Run every enabled library?",
      body: (
        <>
          Queues one job each, in order: <strong>{names.join(", ")}</strong>. Items already cached under the current settings are skipped.
        </>
      ),
      confirmLabel: "Run all",
    });
    if (!ok) return;
    try {
      const created = await startAll.mutateAsync({});
      toast("ok", `${created.length} job${created.length === 1 ? "" : "s"} queued`);
    } catch (e) {
      toast("fail", "Could not start", (e as Error).message);
    }
  };

  return (
    <div className="page">
      <PageHeader
        eyebrow="01 · Overview"
        title={serverName ? <>{serverName}</> : "Master control"}
        lede={
          health.data?.plex.reachable
            ? `Plex ${health.data.plex.version ?? ""} · ${enabled.length} of ${configured.length} libraries enabled`
            : health.data?.plex.error ?? "Waiting for Plex…"
        }
        actions={
          <button
            type="button"
            className="button"
            onClick={onRunAll}
            disabled={startAll.isPending || enabled.length === 0 || activeJobs.length > 0}
            title={activeJobs.length ? "Jobs are already queued" : enabled.length ? "Queue a job for every enabled library" : "No enabled libraries"}
          >
            <Play size={14} aria-hidden="true" /> Run all
          </button>
        }
      />

      {activeJobs.length > 0 && (
        <Panel className="reveal" style={{ "--i": 1 } as React.CSSProperties} eyebrow="Jobs" title={`${activeJobs.length} in the queue`}>
          <div className="jobs">
            {activeJobs.map((job) => (
              <div key={job.job_id} className="job">
                <div className="job__who">
                  <StatusDot tone={job.status === "running" ? "running" : "muted"} label={job.status} />
                  <span className="job__lib">{job.library}</span>
                  {job.dry_run && <span className="chip chip--dry">dry</span>}
                  {job.source === "schedule" && <span className="chip">scheduled</span>}
                </div>
                <button
                  type="button"
                  className="button button--ghost button--sm"
                  onClick={async () => {
                    try {
                      await cancel.mutateAsync(job.job_id);
                      toast("warn", "Cancelling", job.library);
                    } catch (e) {
                      toast("fail", "Could not cancel", (e as Error).message);
                    }
                  }}
                >
                  <Square size={12} aria-hidden="true" /> Cancel
                </button>
                <div className="job__live">
                  <LiveProgress compact progress={job.progress} status={job.status} />
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      <div className="stat-grid">
        <Stat index={1} label="Tagged, last run" value={runs.isPending ? null : taggedLastRun} tone="amber" sub="across enabled libraries" />
        <Stat index={2} label="Libraries" value={libraries.isPending ? null : configured.length} sub={`${enabled.length} enabled`} />
        <Stat index={3} label="Failures pending" value={libraries.isPending ? null : pendingFailures} tone={pendingFailures ? "fail" : undefined} sub="retried with backoff" />
        <Stat index={4} label="Runs, 7 days" value={runs.isPending ? null : runsThisWeek} tone="teal" />
      </div>

      <Panel className="reveal" style={{ "--i": 5 } as React.CSSProperties} eyebrow="Tape" title="Last forty runs" aside={<TapeLegend />}>
        {runs.isPending ? (
          <Skeleton height={36} />
        ) : runs.isError ? (
          <ErrorBlock error={runs.error} onRetry={() => runs.refetch()} />
        ) : allRuns.length === 0 ? (
          <Empty icon={<Activity size={28} strokeWidth={1.5} />} title="No runs recorded yet" action={{ to: "/libraries", label: "Start one" }}>
            Press <strong>Run all</strong>, or start a single library, and the tape begins here.
          </Empty>
        ) : (
          <RunTape runs={allRuns} />
        )}
      </Panel>

      <div className="two-col">
        <Panel className="reveal" style={{ "--i": 6 } as React.CSSProperties} eyebrow="Libraries" title="Configured" aside={<Link to="/libraries">All libraries →</Link>}>
          {libraries.isPending ? (
            <div style={{ display: "grid", gap: 10 }}>
              <Skeleton /><Skeleton /><Skeleton width="70%" />
            </div>
          ) : libraries.isError ? (
            <ErrorBlock error={libraries.error} onRetry={() => libraries.refetch()} />
          ) : configured.length === 0 ? (
            <Empty title="Nothing configured" action={{ to: "/config", label: "See config" }}>
              Add a library to <code>config/config.json</code>.
            </Empty>
          ) : (
            <ul className="list">
              {configured.map((lib) => (
                <LibraryRow key={lib.name} lib={lib} />
              ))}
            </ul>
          )}
        </Panel>

        <div className="stack">
          <Panel className="reveal" style={{ "--i": 7 } as React.CSSProperties} eyebrow="Doctor" title="Checks" aside={<Link to="/config#doctor">Details →</Link>}>
            {doctor.isPending ? (
              <div style={{ display: "grid", gap: 10 }}><Skeleton /><Skeleton /><Skeleton width="60%" /></div>
            ) : doctor.isError ? (
              <ErrorBlock error={doctor.error} onRetry={() => doctor.refetch()} />
            ) : (
              <ul className="list list--tight">
                {doctor.data.checks.map((c) => (
                  <li key={c.id} className="list__row">
                    <StatusDot tone={toneForLevel(c.level)} label={c.title} />
                    {c.items.length > 0 && <span className="mono faint">{c.items.length}</span>}
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel className="reveal" style={{ "--i": 8 } as React.CSSProperties} eyebrow="Recent" title="Runs" aside={<Link to="/runs">History →</Link>}>
            {runs.isPending ? (
              <div style={{ display: "grid", gap: 10 }}><Skeleton /><Skeleton /><Skeleton width="80%" /></div>
            ) : allRuns.length === 0 ? (
              <p className="faint">—</p>
            ) : (
              <ul className="list list--tight">
                {allRuns.slice(0, 6).map((run) => (
                  <li key={run.run_id} className="list__row">
                    <Link to={`/runs/${run.run_id}`} className="list__main">
                      <StatusDot tone={toneForRun(run.status)} label={run.library} />
                      <span className="faint mono">{run.action}</span>
                    </Link>
                    <span className="list__meta mono faint" title={RUN_STATUS_LABEL[run.status]}>{relTime(run.started_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function LibraryRow({ lib }: { lib: LibraryView }) {
  const last = lib.last_run;
  const failed = lib.stats.failed ?? 0;
  return (
    <li className="list__row">
      <Link to="/libraries" className="list__main">
        <span className="list__title">{lib.name}</span>
        <span className="chip chip--type">{lib.type}</span>
        {!lib.enabled && <span className="chip">disabled</span>}
      </Link>
      <span className="list__meta mono">
        {failed > 0 && (
          <span className="tone-fail" title={`${failed} items could not be resolved`}>
            <AlertTriangle size={12} aria-hidden="true" /> {int(failed)}
          </span>
        )}
        {last ? (
          <StatusDot tone={toneForRun(last.status)} label={relTime(last.started_at)} />
        ) : (
          <span className="faint">never run</span>
        )}
      </span>
    </li>
  );
}
