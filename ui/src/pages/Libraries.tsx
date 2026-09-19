import { Library } from "lucide-react";
import { Link } from "react-router-dom";
import { useHealth, useLibraries } from "../api/client";
import type { LibraryView } from "../api/types";
import { Empty, ErrorBlock } from "../components/Empty";
import { PageHeader } from "../components/Panel";
import { Skeleton } from "../components/Skeleton";
import { RUN_STATUS_LABEL, StatusDot, toneForRun } from "../components/StatusDot";
import { int, relTime } from "../lib/format";

export default function Libraries() {
  const libraries = useLibraries();
  const health = useHealth();
  const plexDown = health.data ? !health.data.plex.reachable : false;

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
            <LibraryCard key={lib.name} lib={lib} index={i + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

function LibraryCard({ lib, index }: { lib: LibraryView; index: number }) {
  const ok = lib.stats.ok ?? 0;
  const failed = lib.stats.failed ?? 0;
  const total = lib.plex?.item_count ?? null;
  const coverage = total ? Math.min(100, Math.round((ok / total) * 100)) : null;

  return (
    <article className={`card reveal ${!lib.configured ? "card--ghost" : ""} ${lib.enabled === false ? "card--off" : ""}`} style={{ "--i": index } as React.CSSProperties}>
      <header className="card__head">
        <h2 className="card__title">{lib.name}</h2>
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
            {lib.last_run ? (
              <Link to={`/runs/${lib.last_run.run_id}`} className="card__lastrun">
                <StatusDot tone={toneForRun(lib.last_run.status)} label={`${RUN_STATUS_LABEL[lib.last_run.status]} · ${relTime(lib.last_run.started_at)}`} />
              </Link>
            ) : (
              <span className="faint mono">never run</span>
            )}
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
