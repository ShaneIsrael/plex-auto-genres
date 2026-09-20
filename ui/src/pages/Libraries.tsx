import { Library } from "lucide-react";
import { Link } from "react-router-dom";
import { isActive, useHealth, useJobs, useLibraries } from "../api/client";
import type { JobView, LibraryView } from "../api/types";
import { LiveProgress } from "../components/LiveProgress";
import { RunMenu } from "../components/RunMenu";
import { Empty, ErrorBlock } from "../components/Empty";
import { PageHeader } from "../components/Panel";
import { Skeleton } from "../components/Skeleton";
import { StatusDot, toneForRun } from "../components/StatusDot";
import { int, relTime } from "../lib/format";
import { reveal } from "../lib/reveal";

export default function Libraries() {
  const libraries = useLibraries();
  const health = useHealth();
  const jobs = useJobs();
  const plexDown = health.data ? !health.data.plex.reachable : false;
  const activeFor = (name: string) =>
    jobs.data?.find((j) => isActive(j) && j.library.toLowerCase() === name.toLowerCase()) ?? null;

  return (
    <div className="page">
      <PageHeader
        eyebrow="03 · Libraries"
        title="Libraries"
        lede={plexDown ? "Plex is unreachable — showing configuration only." : "What is configured, what Plex has, and how the two line up."}
      />

      {libraries.isPending ? (
        <div className="card-grid">{[0, 1, 2].map((i) => <div key={i} className="panel"><div className="panel__body" style={{ display: "grid", gap: 12 }}><Skeleton width={160} height={20} /><Skeleton /><Skeleton width="60%" /></div></div>)}</div>
      ) : libraries.isError ? (
        <ErrorBlock error={libraries.error} onRetry={() => libraries.refetch()} />
      ) : libraries.data.length === 0 ? (
        <Empty icon={<Library size={28} strokeWidth={1.5} />} title="No libraries" action={{ to: "/config", label: "See config" }}>
          Nothing is configured and Plex reported no movie or show sections.
        </Empty>
      ) : (
        <div className="card-grid">
          {libraries.data.map((lib, i) => (
            <LibraryCard key={lib.name} lib={lib} index={i + 1} active={activeFor(lib.name)} />
          ))}
        </div>
      )}
    </div>
  );
}

function LibraryCard({ lib, index, active }: { lib: LibraryView; index: number; active: JobView | null }) {
  const ok = lib.stats.ok ?? 0;
  const failed = lib.stats.failed ?? 0;
  const total = lib.plex?.item_count ?? null;
  const coverage = total ? Math.min(100, Math.round((ok / total) * 100)) : null;

  return (
    <article {...reveal(index, `card ${!lib.configured ? "card--ghost" : ""} ${lib.enabled === false ? "card--off" : ""}`)}>
      <header className="card__head">
        <h2 className="card__title">
          {lib.configured ? <Link to={`/libraries/${encodeURIComponent(lib.name)}`} className="card__titlelink">{lib.name}</Link> : lib.name}
        </h2>
        {lib.configured ? (
          lib.enabled === false ? <span className="chip">disabled</span> : <span className="chip chip--type">{lib.type}</span>
        ) : (
          <span className="chip chip--warn">not configured</span>
        )}
      </header>

      {lib.configured ? (
        <>
          <dl className="card__facts mono">
            <div>
              <dt className="label">Writes</dt>
              <dd className="tone-amber">{lib.useGenres ? "genres" : "collections"}{lib.clearGenres ? " · replace" : " · merge"}</dd>
            </div>
            <div>
              <dt className="label">Reads</dt>
              <dd className="tone-teal">{lib.providers.join(" → ")}</dd>
            </div>
            <div>
              <dt className="label">Plex</dt>
              <dd>{lib.plex ? `${int(lib.plex.item_count)} ${lib.plex.section_type === "show" ? "shows" : "items"}` : <span className="tone-fail">not on server</span>}</dd>
            </div>
            <div>
              <dt className="label">Cache</dt>
              <dd>{int(ok)} ok{failed > 0 && <span className="tone-fail"> · {int(failed)} failed</span>}</dd>
            </div>
          </dl>

          {coverage !== null && lib.enabled !== false && (
            <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={coverage} aria-label={`${coverage}% of items resolved`}>
              <span className="meter__fill" style={{ width: `${coverage}%` }} />
              <span className="meter__label mono faint">{coverage}% resolved</span>
            </div>
          )}

          <footer className="card__foot">
            {active ? (
              <LiveProgress compact progress={active.progress} status={active.status} />
            ) : null}
            <div className="card__actions" style={active ? { marginTop: 12 } : undefined}>
              {active ? (
                <Link to={active.progress.run_id ? `/runs/${active.progress.run_id}` : "/runs"} className="card__lastrun">
                  <StatusDot tone={active.status === "running" ? "running" : "muted"} label={active.status === "running" ? "running now" : "queued"} />
                </Link>
              ) : lib.last_run ? (
                <Link to={`/runs/${lib.last_run.run_id}`} className="card__lastrun">
                  <StatusDot tone={toneForRun(lib.last_run.status)} label={`${lib.last_run.status} · ${relTime(lib.last_run.started_at)}`} />
                </Link>
              ) : (
                <span className="faint mono">never run</span>
              )}
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <Link to={`/libraries/${encodeURIComponent(lib.name)}`} className="button button--ghost button--sm">Items</Link>
                <RunMenu library={lib.name} active={active} />
              </div>
            </div>
          </footer>
        </>
      ) : (
        <p className="muted card__hint">
          A {lib.plex?.section_type} section with {int(lib.plex?.item_count)} items that is not in <code>config.json</code>. Add an entry to start tagging it.
        </p>
      )}
    </article>
  );
}
