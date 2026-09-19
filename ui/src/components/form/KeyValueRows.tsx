import { Plus, X } from "lucide-react";
import { useEffect, useState } from "react";

interface Row {
  key: string;
  value: string;
}

/**
 * An ordered list of (from → to) pairs backing a Record<string, string>.
 * Rows are kept as an array while editing so a half-typed key does not
 * collide with, or delete, another entry.
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

  const update = (next: Row[]) => {
    setRows(next);
    onChange(toRecord(next));
  };

  return (
    <div className="kvrows" id={id}>
      {rows.map((row, i) => (
        <div key={i} className="kvrow">
          <input
            className="input mono"
            value={row.key}
            placeholder={keyPlaceholder}
            aria-label={`${keyLabel} ${i + 1}`}
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
      ))}
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
