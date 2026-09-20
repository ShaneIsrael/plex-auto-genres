import { ArrowLeft, Link2, Pin, RefreshCw, RotateCcw, Search, Unlink } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, useConfig, useDeleteBinding, useForgetItem, useLibraryItems, useRefreshLibrary } from "../api/client";
import type { ItemStatusFilter, ItemView } from "../api/types";
import { BindingPicker } from "../components/BindingPicker";
import { useConfirm } from "../components/ConfirmDialog";
import { Empty, ErrorBlock } from "../components/Empty";
import { Pager } from "../components/Pager";
import { PageHeader, Panel } from "../components/Panel";
import { Skeleton } from "../components/Skeleton";
import { StatusDot } from "../components/StatusDot";
import { useToast } from "../components/Toast";
import { int, relTime } from "../lib/format";
import { reveal } from "../lib/reveal";

const FILTERS: { key: ItemStatusFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "ok", label: "Resolved" },
  { key: "failed", label: "Failed" },
  { key: "unprocessed", label: "Not yet run" },
  { key: "bound", label: "Bound" },
];

const PAGE_SIZE = 50;

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function LibraryBrowser() {
  const { name = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const config = useConfig();
  const run = config.data?.libraries.find((l) => l.library.toLowerCase() === name.toLowerCase()) ?? null;
  // The config's spelling, so bindings and forgets land on the same rows the pipeline reads.
  const library = run?.library ?? name;

  const status = (params.get("status") as ItemStatusFilter) || "all";
  const rawPage = Number(params.get("page"));
  const page = Number.isFinite(rawPage) && rawPage >= 1 ? Math.floor(rawPage) : 1;
  const [text, setText] = useState(params.get("q") ?? "");
  const q = useDebounced(text.trim(), 300);
  useEffect(() => {
    // The URL is the source of truth, so a filtered view is shareable.
    const next = new URLSearchParams(params);
    if (q) next.set("q", q);
    else next.delete("q");
    if (next.get("q") !== params.get("q")) {
      next.delete("page");
      setParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const items = useLibraryItems(name, { page, size: PAGE_SIZE, q, status });
  const refresh = useRefreshLibrary();
  const forget = useForgetItem();
  const unbind = useDeleteBinding();
  const confirm = useConfirm();
  const toast = useToast();
  const [picking, setPicking] = useState<ItemView | null>(null);

  const setFilter = (key: ItemStatusFilter) => {
    const next = new URLSearchParams(params);
    if (key === "all") next.delete("status");
    else next.set("status", key);
    next.delete("page");
    setParams(next);
  };
  const setPage = (p: number) => {
    const next = new URLSearchParams(params);
    if (p <= 1) next.delete("page");
    else next.set("page", String(p));
    setParams(next);
  };

  const onUnbind = async (item: ItemView) => {
    const ok = await confirm({
      title: `Remove the binding on ${item.title}?`,
      body: "Its cached match goes with it; the next run resolves it again from the Plex GUID or by title.",
      confirmLabel: "Remove binding",
      danger: true,
    });
    if (!ok) return;
    try {
      await unbind.mutateAsync({ library, mediaKey: item.media_key });
      toast("ok", "Binding removed", item.title);
    } catch (e) {
      toast("fail", "Could not remove", (e as Error).message);
    }
  };

  const onForget = async (item: ItemView) => {
    try {
      await forget.mutateAsync({ library, mediaKey: item.media_key });
      toast("ok", "Will retry on the next run", item.title);
    } catch (e) {
      toast("fail", "Could not reset", (e as Error).message);
    }
  };

  const counts = items.data?.counts;

  return (
    <div className="page">
      <Link to="/libraries" className="backlink mono reveal"><ArrowLeft size={14} aria-hidden="true" /> libraries</Link>
      <PageHeader
        eyebrow={`03 · ${library}`}
        title={library}
        lede={
          run ? (
            <span>
              {run.type} · reads {(run.providers ?? (run.type === "anime" ? ["jikan"] : ["tmdb"])).join(" → ")} · writes {run.useGenres ? "genres" : "collections"}
              {counts ? ` · ${int(counts.all)} items` : ""}
            </span>
          ) : config.isPending ? (
            "…"
          ) : (
            "This library is not in the config."
          )
        }
        actions={
          <button
            type="button"
            className="button button--ghost"
            onClick={() => refresh.mutate(library)}
            disabled={!run || refresh.isPending || items.isFetching}
            title="Re-read the library from Plex"
          >
            <RefreshCw size={14} aria-hidden="true" className={refresh.isPending || items.isFetching ? "spin" : ""} /> Refresh
          </button>
        }
      />

      <div {...reveal(1, "browser-toolbar")}>
        <label className="sr-only" htmlFor="item-search">Search titles</label>
        <input id="item-search" className="input" type="search" placeholder="Search titles…" value={text} onChange={(e) => setText(e.target.value)} />
        <div className="filters" role="group" aria-label="Filter by status">
          {FILTERS.map((f) => (
            <button key={f.key} type="button" className={`filter ${status === f.key ? "is-on" : ""}`} onClick={() => setFilter(f.key)} aria-pressed={status === f.key}>
              {f.label}
              {counts && <span className="filter__n">{int(counts[f.key])}</span>}
            </button>
          ))}
        </div>
      </div>

      <Panel {...reveal(2)}>
        {items.isPending ? (
          <div style={{ display: "grid", gap: 14 }}>{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} height={40} />)}</div>
        ) : items.isError ? (
          <ErrorBlock error={items.error} onRetry={() => items.refetch()} />
        ) : items.data.items.length === 0 ? (
          <Empty icon={<Search size={28} strokeWidth={1.5} />} title={q ? `Nothing matching “${q}”` : "No items here"}>
            {status !== "all" ? "Try another filter." : "Plex reported nothing for this library."}
          </Empty>
        ) : (
          <>
            <ul className="items">
              {items.data.items.map((item) => (
                <ItemRow key={item.rating_key} item={item} onBind={() => setPicking(item)} onUnbind={() => void onUnbind(item)} onForget={() => void onForget(item)} busy={forget.isPending || unbind.isPending} />
              ))}
            </ul>
            <Pager page={items.data.page} size={items.data.size} total={items.data.total} onPage={setPage} />
          </>
        )}
      </Panel>

      {run && (
        <BindingPicker key={run.type} library={library} type={run.type} item={picking} preferredProvider={run.providers?.[0]} onClose={() => setPicking(null)} />
      )}
    </div>
  );
}

function ItemRow({ item, onBind, onUnbind, onForget, busy }: { item: ItemView; onBind: () => void; onUnbind: () => void; onForget: () => void; busy: boolean }) {
  const thumb = api.thumbUrl(item.thumb);
  const state = item.state;
  const match = item.match;
  const matchLabel = match === "binding" ? "bound" : match === "guid" ? "via GUID" : "by title search";
  const MatchIcon = match === "binding" ? Pin : match === "guid" ? Link2 : Search;

  return (
    <li className="item">
      {thumb ? <img className="item__thumb" src={thumb} alt="" loading="lazy" width={44} height={66} /> : <span className="item__thumb item__thumb--none" aria-hidden="true" />}
      <div className="item__main">
        <div className="item__title">
          {item.title} {item.year && <span className="muted">({item.year})</span>}
        </div>
        <div className="item__line">
          <span className={`match match--${match}`} title={item.guids.join(", ") || "no external ids"}>
            <MatchIcon size={12} aria-hidden="true" /> {matchLabel}
            {match === "binding" && item.binding && <span> · {item.binding.provider}://{item.binding.provider_id}</span>}
            {match !== "binding" && state?.provider && <span> · {state.provider}:{state.provider_id}</span>}
          </span>
          {state ? (
            <StatusDot tone={state.status === "ok" ? "ok" : "fail"} label={`${state.status} · ${relTime(state.updated_at)}`} />
          ) : (
            <StatusDot tone="muted" label="not yet run" />
          )}
        </div>
        {state?.status === "failed" && state.last_error && <div className="item__line item__error">{state.last_error}</div>}
        {state?.status === "ok" && state.genres.length > 0 && (
          <div className="item__genres">
            {state.genres.slice(0, 6).map((g) => <span key={g} className="chip chip--quiet">{g}</span>)}
            {state.genres.length > 6 && <span className="chip chip--quiet">+{state.genres.length - 6}</span>}
          </div>
        )}
      </div>
      <div className="item__actions">
        {state && (
          <button type="button" className="iconbtn" title="Forget the cached result; retried on the next run" aria-label={`Retry ${item.title} on the next run`} onClick={onForget} disabled={busy}>
            <RotateCcw size={15} aria-hidden="true" />
          </button>
        )}
        {item.binding ? (
          <button type="button" className="button button--ghost button--sm" onClick={onUnbind} disabled={busy}>
            <Unlink size={12} aria-hidden="true" /> Unbind
          </button>
        ) : (
          <button type="button" className="button button--ghost button--sm" onClick={onBind}>
            <Pin size={12} aria-hidden="true" /> Bind…
          </button>
        )}
      </div>
    </li>
  );
}
