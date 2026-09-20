import { Plus, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useReportIssues } from "./LocalIssues";

interface Row {
  key: string;
  value: string;
}

/**
 * An ordered list of (from → to) pairs backing a Record<string, string>.
 * Rows are kept as an array while editing so a half-typed key does not
 * collide with, or delete, another entry. Two rows with the same key are
 * flagged rather than silently collapsed, and the page refuses to save
 * until one of them goes.
 */
export function KeyValueRows({
  id,
  value,
  onChange,
  keyPlaceholder = "from",
  valuePlaceholder = "to",
  keyLabel,
  valueLabel,
}: {
  id: string;
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
  keyLabel: string;
  valueLabel: string;
}) {
  const [rows, setRows] = useState<Row[]>(() => Object.entries(value).map(([key, v]) => ({ key, value: v })));

  // Resync only when the outside value no longer matches what we emitted
  // (e.g. the draft was discarded), not on every keystroke.
  useEffect(() => {
    const emitted = toRecord(rows);
    if (JSON.stringify(emitted) !== JSON.stringify(value)) {
      setRows(Object.entries(value).map(([key, v]) => ({ key, value: v })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const duplicates = useMemo(() => {
    const seen = new Map<string, number>();
    for (const row of rows) {
      const key = row.key.trim().toLowerCase();
      if (key) seen.set(key, (seen.get(key) ?? 0) + 1);
    }
    return new Set([...seen.entries()].filter(([, n]) => n > 1).map(([k]) => k));
  }, [rows]);
  useReportIssues(id, duplicates.size);

  const update = (next: Row[]) => {
    setRows(next);
    onChange(toRecord(next));
  };

  return (
    <div className="kvrows" id={id}>
      {rows.map((row, i) => {
        const dup = duplicates.has(row.key.trim().toLowerCase());
        return (
          <div key={i} className={`kvrow ${dup ? "kvrow--dup" : ""}`}>
            <input
              className="input mono"
              value={row.key}
              placeholder={keyPlaceholder}
              aria-label={`${keyLabel} ${i + 1}`}
              aria-invalid={dup || undefined}
              title={dup ? "This key appears more than once; only one of them can be kept." : undefined}
              onChange={(e) => update(rows.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)))}
            />
            <span className="kvrow__arrow" aria-hidden="true">→</span>
            <input
              className="input mono"
              value={row.value}
              placeholder={valuePlaceholder}
              aria-label={`${valueLabel} ${i + 1}`}
              onChange={(e) => update(rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))}
            />
            <button type="button" className="iconbtn" aria-label={`Remove row ${i + 1}`} onClick={() => update(rows.filter((_, j) => j !== i))}>
              <X size={14} aria-hidden="true" />
            </button>
          </div>
        );
      })}
      {duplicates.size > 0 && (
        <div className="field__error" role="alert">
          Duplicate key{duplicates.size > 1 ? "s" : ""}: {[...duplicates].join(", ")} — remove one before saving.
        </div>
      )}
      <button type="button" className="button button--ghost button--sm" onClick={() => update([...rows, { key: "", value: "" }])}>
        <Plus size={12} aria-hidden="true" /> Add
      </button>
    </div>
  );
}

function toRecord(rows: Row[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (key) out[key] = row.value;
  }
  return out;
}
