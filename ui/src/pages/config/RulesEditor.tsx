import type { GenreRules, ValidationIssue } from "../../api/types";
import { Field, describedBy } from "../../components/form/Field";
import { KeyValueRows } from "../../components/form/KeyValueRows";
import { TagInput } from "../../components/form/TagInput";
import { issueAt, type Help } from "./editor";

/** ignore / replace / sort prefix / sorted collections / max genres. */
export function RulesEditor({
  id,
  rules,
  onChange,
  errors,
  loc,
  help,
}: {
  id: string;
  rules: GenreRules;
  onChange: (next: GenreRules) => void;
  errors: ValidationIssue[];
  loc: (string | number)[];
  help: Help;
}) {
  const set = <K extends keyof GenreRules>(key: K, value: GenreRules[K]) => onChange({ ...rules, [key]: value });
  const err = (field: string) => issueAt(errors, [...loc, field]);

  return (
    <div className="editor-grid">
      <Field id={`${id}-ignore`} label="Ignore" help={help("GenreRules", "ignore")} error={err("ignore")}>
        <TagInput id={`${id}-ignore`} value={rules.ignore} onChange={(v) => set("ignore", v)} placeholder="genre to drop…" describedBy={describedBy(`${id}-ignore`, true, err("ignore"))} />
      </Field>

      <Field id={`${id}-sorted`} label="Sorted collections" help={help("GenreRules", "sortedCollections")} error={err("sortedCollections")}>
        <TagInput id={`${id}-sorted`} value={rules.sortedCollections} onChange={(v) => set("sortedCollections", v)} placeholder="collection name…" />
      </Field>

      <div className="span-2">
        <Field id={`${id}-replace`} label="Replace" help={help("GenreRules", "replace")} error={err("replace")}>
          <KeyValueRows id={`${id}-replace`} value={rules.replace} onChange={(v) => set("replace", v)} keyLabel="Genre to replace" valueLabel="Replacement" keyPlaceholder="sci-fi" valuePlaceholder="Science Fiction" />
        </Field>
      </div>

      <Field id={`${id}-prefix`} label="Sort prefix" help={help("GenreRules", "sortedPrefix")} error={err("sortedPrefix")}>
        <input id={`${id}-prefix`} className="input input--sm mono" style={{ maxWidth: 120 }} value={rules.sortedPrefix} maxLength={4} onChange={(e) => set("sortedPrefix", e.target.value)} placeholder="*" />
      </Field>

      <Field id={`${id}-max`} label="Max genres" help={help("GenreRules", "maxGenres")} error={err("maxGenres")}>
        <input
          id={`${id}-max`}
          className="input input--sm"
          type="number"
          min={1}
          inputMode="numeric"
          value={rules.maxGenres ?? ""}
          placeholder="unlimited"
          onChange={(e) => set("maxGenres", e.target.value === "" ? null : Math.max(1, Number(e.target.value)))}
        />
      </Field>
    </div>
  );
}
