import type { ConfigView } from "../../api/types";
import { useDoctor } from "../../api/client";
import { ErrorBlock } from "../../components/Empty";
import { Panel } from "../../components/Panel";
import { StatusDot, toneForLevel } from "../../components/StatusDot";
import { reveal } from "../../lib/reveal";

function Presence({ set, legacy = false }: { set: boolean; legacy?: boolean }) {
  if (set) return <StatusDot tone="ok" label="set" />;
  return <StatusDot tone={legacy ? "muted" : "warn"} label={legacy ? "unset (legacy)" : "unset"} />;
}

/** Environment and doctor: read-only reference below the editor. */
export function Environment({ config, index }: { config: ConfigView; index: number }) {
  return (
    <Panel {...reveal(index)} eyebrow="Environment" title="Secrets and connection" aside={<span className="faint mono">read-only</span>}>
      <p className="field__help" style={{ marginBottom: 12 }}>
        Credentials come from the environment (<code>.env</code> or the container), never from <code>config.json</code>, and are not edited here.
      </p>
      <dl className="kv-list mono">
        <div><dt>PLEX_BASE_URL</dt><dd>{config.secrets.plex_base_url ?? <span className="faint">unset</span>}</dd></div>
        <div><dt>PLEX_TOKEN</dt><dd><Presence set={config.secrets.plex_token} /></dd></div>
        <div><dt>PLEX_PASSWORD</dt><dd><Presence set={config.secrets.plex_password} legacy /></dd></div>
        <div><dt>PLEX_SERVER_NAME</dt><dd>{config.secrets.plex_server_name ?? <span className="faint">unset</span>}</dd></div>
        <div><dt>TMDB_API_KEY</dt><dd><Presence set={config.secrets.tmdb_api_key} /></dd></div>
        <div><dt>PLEX_COLLECTION_PREFIX</dt><dd>{config.secrets.collection_prefix ? <code>{config.secrets.collection_prefix}</code> : <span className="faint">none</span>}</dd></div>
        <div><dt>TMDB_LANGUAGE</dt><dd>{config.providers.tmdb_language}</dd></div>
        <div><dt>PAG_CONCURRENCY</dt><dd>{config.providers.concurrency}</dd></div>
        <div><dt>PAG_MAX_ATTEMPTS</dt><dd>{config.providers.max_attempts}</dd></div>
      </dl>
    </Panel>
  );
}

export function Doctor({ index }: { index: number }) {
  const doctor = useDoctor();
  return (
    <Panel {...reveal(index)} eyebrow="Doctor" title="Checks">
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
  );
}
