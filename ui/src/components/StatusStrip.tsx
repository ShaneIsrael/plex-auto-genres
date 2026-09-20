import { LogOut } from "lucide-react";
import { Link } from "react-router-dom";
import { isActive, useAuthStatus, useDoctor, useHealth, useJobs, useLogout, useRuns } from "../api/client";
import { relTime } from "../lib/format";
import { StatusDot, toneForRun } from "./StatusDot";

/** The three things an operator glances at, plus where the process is. */
export function StatusStrip() {
  const health = useHealth();
  const doctor = useDoctor();
  const runs = useRuns(1);
  const jobs = useJobs();
  const auth = useAuthStatus();
  const logout = useLogout();

  const plex = health.data?.plex;
  const last = runs.data?.[0];
  const doc = doctor.data;
  const active = jobs.data?.find((j) => j.status === "running") ?? jobs.data?.find(isActive) ?? null;

  return (
    <div className="strip" role="region" aria-label="System status">
      <Link to="/config" className="strip__cell">
        <span className="label">Plex</span>
        {health.isPending ? (
          <StatusDot tone="muted" label="checking…" />
        ) : plex?.reachable ? (
          <StatusDot tone="ok" label={plex.server_name ?? "connected"} />
        ) : (
          <StatusDot tone="fail" label={health.isError ? "API unreachable" : "unreachable"} />
        )}
      </Link>

      <Link
        to={active?.progress.run_id ? `/runs/${active.progress.run_id}` : last ? `/runs/${last.run_id}` : "/runs"}
        className="strip__cell"
      >
        <span className="label">{active ? "Job" : "Last run"}</span>
        {active ? (
          <StatusDot
            tone={active.status === "running" ? "running" : "muted"}
            label={
              active.status === "running"
                ? `${active.library} · ${active.progress.pending ? `${active.progress.done}/${active.progress.pending}` : active.progress.action ?? "starting"}`
                : `${active.library} · queued`
            }
          />
        ) : runs.isPending ? (
          <StatusDot tone="muted" label="…" />
        ) : last ? (
          <StatusDot
            tone={toneForRun(last.status)}
            label={`${last.library} · ${last.status} · ${relTime(last.started_at)}`}
          />
        ) : (
          <StatusDot tone="muted" label="never" />
        )}
      </Link>

      <Link to="/config#doctor" className="strip__cell">
        <span className="label">Doctor</span>
        {doctor.isPending ? (
          <StatusDot tone="muted" label="…" />
        ) : doc ? (
          <StatusDot
            tone={doc.errors ? "fail" : doc.warnings ? "warn" : "ok"}
            label={doc.errors ? `${doc.errors} error${doc.errors > 1 ? "s" : ""}` : doc.warnings ? `${doc.warnings} warning${doc.warnings > 1 ? "s" : ""}` : "clean"}
          />
        ) : (
          <StatusDot tone="muted" label="unavailable" />
        )}
      </Link>

      <div className="strip__spacer" />

      <div className="strip__cell strip__cell--meta mono faint">
        {health.data?.scheduler ? (
          <span title={`cron ${health.data.scheduler.cron}`}>
            next {relTime(health.data.scheduler.next_fire_at)}
          </span>
        ) : (
          <span>no schedule</span>
        )}
        <span className="strip__sep" aria-hidden="true">·</span>
        <span>v{health.data?.version ?? "—"}</span>
      </div>
      {auth.data?.enabled && (
        <button type="button" className="strip__signout iconbtn" aria-label="Sign out" title="Sign out" onClick={() => logout.mutate()} disabled={logout.isPending}>
          <LogOut size={15} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
