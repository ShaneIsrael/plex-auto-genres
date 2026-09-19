import { ExternalLink, Link2, Search } from "lucide-react";
import { useEffect, useId, useState, type FormEvent } from "react";
import { useCandidates, useCreateBinding } from "../api/client";
import type { BindingProvider, ItemView, MediaType } from "../api/types";
import { Field } from "./form/Field";
import { Segmented } from "./form/Segmented";
import { Modal } from "./Modal";
import { useToast } from "./Toast";

const SEARCHABLE: Record<MediaType, string[]> = {
  anime: ["jikan", "anilist"],
  "standard-tv": ["tmdb"],
  "standard-movie": ["tmdb"],
};

/** The id scheme a search provider's results bind as. */
const BIND_AS: Record<string, BindingProvider> = { jikan: "mal", anilist: "anilist", tmdb: "tmdb" };

const MANUAL: Record<MediaType, BindingProvider[]> = {
  anime: ["mal", "anilist", "anidb"],
  "standard-tv": ["tmdb", "tvdb", "imdb"],
  "standard-movie": ["tmdb", "imdb"],
};

/**
 * Pick the provider record an item should resolve to. Search first (ranked
 * candidates with posters), or type an id straight in.
 */
export function BindingPicker({
  library,
  type,
  item,
  preferredProvider,
  onClose,
}: {
  library: string;
  type: MediaType;
  item: ItemView | null;
  preferredProvider?: string;
  onClose: () => void;
}) {
  const id = useId();
  const toast = useToast();
  const create = useCreateBinding();
  const providers = SEARCHABLE[type];
  const [provider, setProvider] = useState(preferredProvider && providers.includes(preferredProvider) ? preferredProvider : providers[0]!);
  const [text, setText] = useState("");
  const [year, setYear] = useState<string>("");
  const [submitted, setSubmitted] = useState<{ q: string; year: number | null } | null>(null);
  const [manualProvider, setManualProvider] = useState<BindingProvider>(MANUAL[type][0]!);
  const [manualId, setManualId] = useState("");
  const [note, setNote] = useState("");

  // Reset the form for each item and search right away with its own title.
  useEffect(() => {
    if (!item) return;
    setText(item.title);
    setYear(item.year ? String(item.year) : "");
    setSubmitted({ q: item.title, year: item.year });
    setManualId("");
    setNote("");
  }, [item]);

  const candidates = useCandidates(submitted ? { q: submitted.q, type, provider, year: submitted.year } : null);

  const search = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted({ q: text.trim(), year: year ? Number(year) : null });
  };

  const bind = async (bindProvider: BindingProvider, providerId: string, why?: string) => {
    if (!item) return;
    try {
      await create.mutateAsync({ library, media_key: item.media_key, provider: bindProvider, provider_id: providerId, note: why || null });
      toast("ok", "Bound", `${item.title} → ${bindProvider}://${providerId}. Applied on the next run.`);
      onClose();
    } catch (err) {
      toast("fail", "Could not bind", (err as Error).message);
    }
  };

  return (
    <Modal open={item !== null} onClose={onClose} eyebrow={`Bind · ${library}`} title={item ? <>{item.title} {item.year && <span className="muted">({item.year})</span>}</> : ""} wide>
      {item && (
        <div className="picker">
          <form className="picker__search" onSubmit={search}>
            {providers.length > 1 && (
              <Segmented name={`${id}-prov`} ariaLabel="Search provider" value={provider} onChange={setProvider} options={providers.map((p) => ({ value: p, label: p }))} />
            )}
            <input className="input" aria-label="Title to search" value={text} onChange={(e) => setText(e.target.value)} placeholder="title…" />
            <input className="input picker__year" aria-label="Year" inputMode="numeric" value={year} onChange={(e) => setYear(e.target.value.replace(/\D/g, "").slice(0, 4))} placeholder="year" />
            <button type="submit" className="button" disabled={!text.trim()}>
              <Search size={14} aria-hidden="true" /> Search
            </button>
          </form>

          <div className="picker__results" aria-live="polite">
            {candidates.isPending && submitted ? (
              <p className="faint mono">Searching {provider}…</p>
            ) : candidates.isError ? (
              <p className="tone-fail mono">{(candidates.error as Error).message}</p>
            ) : candidates.data && candidates.data.length === 0 ? (
              <p className="muted">Nothing on {provider} for “{submitted?.q}”. Try another spelling, drop the year, or enter an id below.</p>
            ) : (
              <ul className="cands">
                {(candidates.data ?? []).map((c) => (
                  <li key={`${c.provider}-${c.provider_id}`} className="cand">
                    {c.image ? <img className="cand__img" src={c.image} alt="" loading="lazy" width={60} height={90} /> : <span className="cand__img cand__img--none" aria-hidden="true" />}
                    <div className="cand__main">
                      <div className="cand__title">
                        {c.title} {c.year && <span className="muted">({c.year})</span>}
                        {c.score != null && <span className="chip chip--quiet">{c.score}/10</span>}
                      </div>
                      <div className="cand__meta mono faint">
                        {c.provider}:{c.provider_id}
                        {c.url && (
                          <a href={c.url} target="_blank" rel="noreferrer" className="cand__link">
                            open <ExternalLink size={11} aria-hidden="true" />
                          </a>
                        )}
                      </div>
                      {c.synopsis && <p className="cand__synopsis">{c.synopsis}</p>}
                      {c.genres.length > 0 && <p className="chips">{c.genres.slice(0, 6).map((g) => <span key={g} className="chip chip--quiet">{g}</span>)}</p>}
                    </div>
                    <button type="button" className="button button--sm" disabled={create.isPending} onClick={() => bind(BIND_AS[c.provider] ?? "mal", c.provider_id, `picked from ${c.provider} search`)}>
                      <Link2 size={12} aria-hidden="true" /> Use this
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <form
            className="picker__manual"
            onSubmit={(e) => {
              e.preventDefault();
              if (manualId.trim()) void bind(manualProvider, manualId.trim(), note.trim());
            }}
          >
            <div className="label">Or enter an id</div>
            <div className="picker__manual-row">
              <Segmented name={`${id}-manual`} ariaLabel="Id scheme" value={manualProvider} onChange={setManualProvider} options={MANUAL[type].map((p) => ({ value: p, label: p }))} />
              <input className="input mono" aria-label="Provider id" value={manualId} onChange={(e) => setManualId(e.target.value)} placeholder="id" />
              <button type="submit" className="button button--ghost" disabled={!manualId.trim() || create.isPending}>
                Bind
              </button>
            </div>
            <Field id={`${id}-note`} label="Note" help="Why this binding exists; shown in the bindings list.">
              <input id={`${id}-note`} className="input input--sm" value={note} onChange={(e) => setNote(e.target.value)} placeholder="optional" />
            </Field>
          </form>

          {item.binding && (
            <p className="faint mono">
              currently bound to {item.binding.provider}://{item.binding.provider_id}
              {item.binding.note ? ` — ${item.binding.note}` : ""}
            </p>
          )}
          <p className="faint">
            Keyed by <code className="mono">{item.media_key}</code>. The next run resolves this item through the binding instead of its Plex GUID or a title search.
          </p>
        </div>
      )}
    </Modal>
  );
}
