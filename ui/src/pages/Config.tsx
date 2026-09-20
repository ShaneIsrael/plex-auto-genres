import { Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, useConfig, useLibraries, useSaveConfig, useSchema } from "../api/client";
import type { ConfigDocument, GenreRules, LibraryRun, MediaType, ValidationIssue } from "../api/types";
import { useConfirm } from "../components/ConfirmDialog";
import { ErrorBlock } from "../components/Empty";
import { LocalIssuesProvider, type ReportIssues } from "../components/form/LocalIssues";
import { PageHeader, Panel } from "../components/Panel";
import { PageSkeleton } from "../components/Skeleton";
import { useToast } from "../components/Toast";
import { useUnsaved } from "../components/UnsavedGuard";
import { reveal } from "../lib/reveal";
import { TYPES, emptyRules, helpFrom, issuesUnder, newLibrary, same, toDocument, type JsonSchemaLike } from "./config/editor";
import { LibraryEditor } from "./config/LibraryEditor";
import { Doctor, Environment } from "./config/Reference";
import { RulesEditor } from "./config/RulesEditor";
import { SaveBar, type ValidationStatus } from "./config/SaveBar";

/** Server-side validation, debounced, ignoring out-of-order responses. */
function useLiveValidation(doc: ConfigDocument | null, active: boolean) {
  const [state, setState] = useState<{ status: ValidationStatus; errors: ValidationIssue[] }>({ status: "idle", errors: [] });
  const seq = useRef(0);
  useEffect(() => {
    if (!doc || !active) {
      setState({ status: "idle", errors: [] });
      return;
    }
    const mine = ++seq.current;
    setState((s) => ({ ...s, status: "validating" }));
    const handle = window.setTimeout(async () => {
      try {
        const result = await api.validateConfig(doc);
        if (mine === seq.current) setState({ status: result.ok ? "valid" : "invalid", errors: result.errors });
      } catch {
        if (mine === seq.current) setState({ status: "idle", errors: [] });
      }
    }, 350);
    return () => window.clearTimeout(handle);
  }, [doc, active]);
  const override = useCallback((errors: ValidationIssue[]) => setState({ status: errors.length ? "invalid" : "valid", errors }), []);
  return { ...state, override };
}

export default function Config() {
  const config = useConfig();
  const schema = useSchema();
  const libraries = useLibraries();
  const save = useSaveConfig();
  const confirm = useConfirm();
  const toast = useToast();
  const { setDirty } = useUnsaved();

  const [draft, setDraft] = useState<ConfigDocument | null>(null);
  const [baseline, setBaseline] = useState<ConfigDocument | null>(null);
  const [etag, setEtag] = useState<string | null>(null);
  const [focusNew, setFocusNew] = useState<number | null>(null);
  // One stable key per library card, moved and removed alongside the entry,
  // so per-card DOM state (an opened overrides panel, a caret) follows the
  // library rather than its slot when the list is reordered.
  const [libKeys, setLibKeys] = useState<string[]>([]);
  const keySeq = useRef(0);
  const newKey = () => `lib-${++keySeq.current}`;
  // Problems only the form can see (duplicate keys in a from→to list).
  const [localIssues, setLocalIssues] = useState<Record<string, number>>({});
  const reportIssue = useCallback<ReportIssues>((id, count) => {
    setLocalIssues((prev) => {
      if ((prev[id] ?? 0) === count) return prev;
      const next = { ...prev };
      if (count) next[id] = count;
      else delete next[id];
      return next;
    });
  }, []);
  const blockers = Object.values(localIssues).reduce((a, b) => a + b, 0);

  const dirty = draft !== null && baseline !== null && !same(draft, baseline);
  const validation = useLiveValidation(draft, dirty);
  const help = helpFrom(schema.data as JsonSchemaLike | undefined);

  // Adopt the server's document when we have none, or when it changed on disk
  // and we hold no edits. A dirty draft is never clobbered by a refetch.
  useEffect(() => {
    if (!config.data) return;
    if (baseline === null || (!dirty && config.data.etag !== etag)) {
      const doc = toDocument(config.data);
      setDraft(doc);
      setBaseline(doc);
      setEtag(config.data.etag);
      setLibKeys(doc.libraries.map(newKey));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.data, baseline, dirty, etag]);

  useEffect(() => {
    setDirty(dirty);
    return () => setDirty(false);
  }, [dirty, setDirty]);

  // The error branch comes first: with a failing GET the draft never
  // materialises, and a skeleton would sit there forever.
  if (config.isError && baseline === null) {
    return (
      <div className="page">
        <PageHeader eyebrow="05 · Config" title="Configuration" />
        <ErrorBlock error={config.error} onRetry={() => config.refetch()} />
      </div>
    );
  }
  if (config.isPending || draft === null) return <PageSkeleton />;

  const errors = validation.errors;
  const unconfiguredSections = (libraries.data ?? []).filter((l) => !l.configured).map((l) => l.name);

  const setLibraries = (next: LibraryRun[], keys?: string[]) => {
    setDraft({ ...draft, libraries: next });
    if (keys) setLibKeys(keys);
  };
  const setDefault = (type: MediaType, rules: GenreRules | null) => {
    const defaults = { ...draft.defaults };
    if (rules === null) delete defaults[type];
    else defaults[type] = rules;
    setDraft({ ...draft, defaults });
  };

  const addLibrary = () => {
    setLibraries([...draft.libraries, newLibrary(unconfiguredSections[0] ?? "")], [...libKeys, newKey()]);
    setFocusNew(draft.libraries.length);
  };

  const removeLibrary = async (index: number) => {
    const lib = draft.libraries[index];
    const ok = await confirm({
      title: `Remove ${lib?.library || "this library"} from the config?`,
      body: "Its cache and run history stay in the database; only the configuration entry goes. Nothing is written until you save.",
      confirmLabel: "Remove",
      danger: true,
    });
    if (ok) setLibraries(draft.libraries.filter((_, i) => i !== index), libKeys.filter((_, i) => i !== index));
  };

  const moveLibrary = (index: number, delta: -1 | 1) => {
    const next = [...draft.libraries];
    const keys = [...libKeys];
    const [item] = next.splice(index, 1);
    const [key] = keys.splice(index, 1);
    if (item) next.splice(index + delta, 0, item);
    if (key) keys.splice(index + delta, 0, key);
    setLibraries(next, keys);
  };

  const discard = async () => {
    const ok = await confirm({ title: "Discard your changes?", body: "The form goes back to what is on disk.", confirmLabel: "Discard", danger: true });
    if (ok && baseline) {
      setDraft(baseline);
      setLibKeys(baseline.libraries.map(newKey));
    }
  };

  const doSave = async () => {
    try {
      const result = await save.mutateAsync({ doc: draft, etag });
      setBaseline(draft);
      setEtag(result.etag);
      toast("ok", "Config saved", result.backup ? `Previous file kept as ${result.backup.split("/").pop()}.` : undefined);
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        const body = e.body as { errors?: ValidationIssue[] };
        validation.override(body.errors ?? []);
        toast("fail", "Not saved", "Fix the problems shown in the form.");
      } else if (e instanceof ApiError && e.status === 412) {
        const reload = await confirm({
          title: "The file changed on disk",
          body: "Someone — or something — edited config.json since you loaded it. Reload it and lose your edits, or keep editing and save again to overwrite.",
          confirmLabel: "Reload from disk",
          danger: true,
        });
        if (reload) {
          setBaseline(null);
          await config.refetch();
        } else {
          setEtag((e.body as { etag?: string }).etag ?? null);
        }
      } else {
        toast("fail", "Save failed", (e as Error).message);
      }
    }
  };

  return (
    <LocalIssuesProvider value={reportIssue}>
    <div className={`page ${dirty ? "page--editing" : ""}`}>
      <PageHeader
        eyebrow="05 · Config"
        title="Configuration"
        lede={
          // One flex item: the lede is a flex row (for status chips elsewhere),
          // and loose text nodes would pick up its gap as visible spaces.
          <span>
            Edits are checked as you type and written atomically to <code>{config.data?.path ?? "config.json"}</code>; the previous file is kept as <code>.bak</code>. Running jobs keep the config they started with.
          </span>
        }
      />

      <section {...reveal(1, "editor")}>
        <div className="section-head">
          <div>
            <h2>Libraries</h2>
            <p>One entry per Plex library to tag. Order is the order "Run all" and the schedule use.</p>
          </div>
          <button type="button" className="button button--ghost" onClick={addLibrary}>
            <Plus size={14} aria-hidden="true" /> Add library
          </button>
        </div>

        {draft.libraries.length === 0 && (
          <Panel><p className="muted">No libraries yet. Add one and point it at a Plex section.</p></Panel>
        )}

        {draft.libraries.map((lib, i) => (
          <LibraryEditor
            key={libKeys[i] ?? `slot-${i}`}
            lib={lib}
            index={i}
            errors={issuesUnder(errors, ["libraries", i])}
            help={help}
            plexSections={unconfiguredSections}
            onChange={(next) => setLibraries(draft.libraries.map((l, j) => (j === i ? next : l)))}
            onRemove={() => void removeLibrary(i)}
            onMove={(delta) => moveLibrary(i, delta)}
            canMoveUp={i > 0}
            canMoveDown={i < draft.libraries.length - 1}
            autoFocusName={focusNew === i}
          />
        ))}
      </section>

      <section {...reveal(2, "editor")}>
        <div className="section-head">
          <div>
            <h2>Defaults by type</h2>
            <p>Rules every library of a type inherits. A library's overrides are layered on top.</p>
          </div>
        </div>
        <div className="three-col">
          {TYPES.map((type) => {
            const rules = draft.defaults[type];
            return (
              <Panel key={type} eyebrow="Defaults" title={type} aside={rules ? <button type="button" className="button button--ghost button--sm" onClick={() => setDefault(type, null)}>Remove</button> : undefined}>
                {rules ? (
                  <RulesEditor id={`def-${type}`} rules={rules} onChange={(v) => setDefault(type, v)} errors={issuesUnder(errors, ["defaults", type])} loc={["defaults", type]} help={help} />
                ) : (
                  <button type="button" className="button button--ghost button--sm" onClick={() => setDefault(type, emptyRules())}>
                    <Plus size={12} aria-hidden="true" /> Add defaults for {type}
                  </button>
                )}
              </Panel>
            );
          })}
        </div>
      </section>

      <div className="two-col">
        {config.data && <Environment config={config.data} index={3} />}
        <Doctor index={4} />
      </div>

      {dirty && (
        <SaveBar
          doc={draft}
          status={validation.status}
          errors={errors}
          blocker={blockers ? `${blockers} duplicate key${blockers > 1 ? "s" : ""} in a replace list` : null}
          saving={save.isPending}
          onSave={() => void doSave()}
          onDiscard={() => void discard()}
        />
      )}
    </div>
    </LocalIssuesProvider>
  );
}
