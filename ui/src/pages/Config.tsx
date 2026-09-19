import { useConfig, useDoctor } from "../api/client";
import type { GenreRules, MediaType } from "../api/types";
import { ErrorBlock } from "../components/Empty";
import { PageHeader, Panel } from "../components/Panel";
import { PageSkeleton } from "../components/Skeleton";
import { StatusDot, toneForLevel } from "../components/StatusDot";

const TYPES: MediaType[] = ["anime", "standard-tv", "standard-movie"];

export default function Config() {
  const config = useConfig();
  const doctor = useDoctor();

  if (config.isPending) return <PageSkeleton />;

  return (
    <div className="page">
      <PageHeader
        eyebrow="05 · Config"
        title="Configuration"
        lede={
          config.data ? (
            <>Read from <code>{config.data.path}</code>. Edit the file and the server picks it up; form editing arrives in phase 3.</>
          ) : (
            "The config could not be loaded."
          )
        }
      />

      {config.isError && <ErrorBlock error={config.error} onRetry={() => config.refetch()} />}

      {config.data && (
        <>
          <div className="two-col">
            <Panel className="reveal" style={{ "--i": 1 } as React.CSSProperties} eyebrow="Environment" title="Secrets and connection">
              <dl className="kv-list mono">
                <div><dt>PLEX_BASE_URL</dt><dd>{config.data.secrets.plex_base_url ?? <span className="faint">unset</span>}</dd></div>
                <div><dt>PLEX_TOKEN</dt><dd><Presence set={config.data.secrets.plex_token} /></dd></div>
                <div><dt>PLEX_PASSWORD</dt><dd><Presence set={config.data.secrets.plex_password} legacy /></dd></div>
                <div><dt>PLEX_SERVER_NAME</dt><dd>{config.data.secrets.plex_server_name ?? <span className="faint">unset</span>}</dd></div>
                <div><dt>TMDB_API_KEY</dt><dd><Presence set={config.data.secrets.tmdb_api_key} /></dd></div>
                <div><dt>PLEX_COLLECTION_PREFIX</dt><dd>{config.data.secrets.collection_prefix ? <code>{config.data.secrets.collection_prefix}</code> : <span className="faint">none</span>}</dd></div>
                <div><dt>TMDB_LANGUAGE</dt><dd>{config.data.providers.tmdb_language}</dd></div>
                <div><dt>PAG_CONCURRENCY</dt><dd>{config.data.providers.concurrency}</dd></div>
                <div><dt>PAG_MAX_ATTEMPTS</dt><dd>{config.data.providers.max_attempts}</dd></div>
              </dl>
            </Panel>

            <Panel className="reveal" style={{ "--i": 2 } as React.CSSProperties} eyebrow="Doctor" title="Checks">
              <span id="doctor" />
              {doctor.isPending ? (
                <p className="faint">Checking…</p>
              ) : doctor.isError ? (
                <ErrorBlock error={doctor.error} onRetry={() => doctor.refetch()} />
              ) : (
                <ul className="checks">
                  {doctor.data.checks.map((c) => (
                    <li key={c.id} className="checks__item">
                      <StatusDot tone={toneForLevel(c.level)} label={c.title} />
                      {c.detail && <p className="checks__detail muted">{c.detail}</p>}
                      {c.items.length > 0 && (
                        <ul className="checks__list mono">
                          {c.items.map((it) => <li key={it}>{it}</li>)}
                        </ul>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>

          <Panel className="reveal" style={{ "--i": 3 } as React.CSSProperties} eyebrow="Libraries" title={`${config.data.libraries.length} configured`}>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Library</th>
                    <th scope="col">Type</th>
                    <th scope="col">Providers</th>
                    <th scope="col">Writes</th>
                    <th scope="col">Options</th>
                    <th scope="col">Overrides</th>
                  </tr>
                </thead>
                <tbody>
                  {config.data.libraries.map((lib) => (
                    <tr key={lib.library} className={lib.enabled ? "" : "is-off"}>
                      <td>{lib.library}{!lib.enabled && <span className="chip">disabled</span>}</td>
                      <td className="mono muted">{lib.type}</td>
                      <td className="mono tone-teal">{(lib.providers ?? (lib.type === "anime" ? ["jikan"] : ["tmdb"])).join(" → ")}</td>
                      <td className="mono tone-amber">{lib.useGenres ? "genres" : "collections"}{lib.clearGenres ? " · replace" : ""}{lib.useKeywords ? " · keywords" : ""}</td>
                      <td className="mono muted">
                        {[lib.setPosters && "posters", lib.sortCollections && "sort", lib.rateAnime && "ratings", lib.createRatingCollections && "rating-collections"].filter(Boolean).join(", ") || "—"}
                      </td>
                      <td className="mono muted">{lib.overrides ? summarize(lib.overrides) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

          <div className="three-col">
            {TYPES.map((type, i) => {
              const rules = config.data.defaults[type];
              return (
                <Panel key={type} className="reveal" style={{ "--i": 4 + i } as React.CSSProperties} eyebrow="Defaults" title={type}>
                  {rules ? <Rules rules={rules} /> : <p className="faint">No defaults for this type.</p>}
                </Panel>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

function Presence({ set, legacy = false }: { set: boolean; legacy?: boolean }) {
  if (set) return <StatusDot tone="ok" label="set" />;
  return <StatusDot tone={legacy ? "muted" : "warn"} label={legacy ? "unset (legacy)" : "unset"} />;
}

function summarize(r: GenreRules): string {
  const bits: string[] = [];
  if (r.ignore.length) bits.push(`ignore ${r.ignore.length}`);
  if (Object.keys(r.replace).length) bits.push(`replace ${Object.keys(r.replace).length}`);
  if (r.maxGenres != null) bits.push(`max ${r.maxGenres}`);
  if (r.sortedPrefix) bits.push(`prefix "${r.sortedPrefix}"`);
  return bits.join(", ") || "empty";
}

function Rules({ rules }: { rules: GenreRules }) {
  const replace = Object.entries(rules.replace);
  return (
    <div className="rules">
      <div>
        <div className="label">Ignore</div>
        {rules.ignore.length ? <p className="chips">{rules.ignore.map((g) => <span key={g} className="chip">{g}</span>)}</p> : <p className="faint">—</p>}
      </div>
      <div>
        <div className="label">Replace</div>
        {replace.length ? (
          <dl className="kv-list mono kv-list--tight">
            {replace.map(([from, to]) => <div key={from}><dt>{from}</dt><dd>→ {to}</dd></div>)}
          </dl>
        ) : <p className="faint">—</p>}
      </div>
      <div>
        <div className="label">Sort prefix · max genres</div>
        <p className="mono">{rules.sortedPrefix ? <code>{rules.sortedPrefix}</code> : <span className="faint">none</span>} · {rules.maxGenres ?? <span className="faint">unlimited</span>}</p>
      </div>
      <div>
        <div className="label">Sorted collections</div>
        {rules.sortedCollections.length ? <p className="chips">{rules.sortedCollections.map((g) => <span key={g} className="chip chip--quiet">{g}</span>)}</p> : <p className="faint">—</p>}
      </div>
    </div>
  );
}
