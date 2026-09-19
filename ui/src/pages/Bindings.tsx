import { Link2 } from "lucide-react";
import { useBindings } from "../api/client";
import { Empty, ErrorBlock } from "../components/Empty";
import { PageHeader, Panel } from "../components/Panel";
import { Skeleton } from "../components/Skeleton";
import { dateTime, relTime } from "../lib/format";

export default function Bindings() {
  const bindings = useBindings();

  return (
    <div className="page">
      <PageHeader
        eyebrow="04 · Bindings"
        title="Manual bindings"
        lede="An item pinned to an exact provider id, overriding the automatic match. Editing arrives in phase 4."
      />

      <Panel className="reveal" style={{ "--i": 1 } as React.CSSProperties}>
        {bindings.isPending ? (
          <div style={{ display: "grid", gap: 12 }}><Skeleton /><Skeleton /><Skeleton width="70%" /></div>
        ) : bindings.isError ? (
          <ErrorBlock error={bindings.error} onRetry={() => bindings.refetch()} />
        ) : bindings.data.length === 0 ? (
          <Empty icon={<Link2 size={28} strokeWidth={1.5} />} title="No bindings">
            When the automatic match is wrong, pin the right one:<br />
            <code>plex-auto-genres bind "Animes" "Monster" mal 19</code>
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Library</th>
                  <th scope="col">Item</th>
                  <th scope="col">Bound to</th>
                  <th scope="col">Note</th>
                  <th scope="col">Added</th>
                </tr>
              </thead>
              <tbody>
                {bindings.data.map((b) => (
                  <tr key={`${b.library}/${b.media_key}`}>
                    <td>{b.library}</td>
                    <td className="mono">{b.media_key}</td>
                    <td className="mono tone-teal">{b.provider}://{b.provider_id}</td>
                    <td className="muted wrap">{b.note ?? "—"}</td>
                    <td className="mono muted" title={dateTime(b.created_at)}>{relTime(b.created_at)}</td>
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
