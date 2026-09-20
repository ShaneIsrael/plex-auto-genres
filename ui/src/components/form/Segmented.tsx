export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  hint?: string;
}

/** A radio group drawn as connected segments. Keyboard: arrows move, space selects. */
export function Segmented<T extends string>({
  name,
  value,
  options,
  onChange,
  ariaLabel,
}: {
  name: string;
  value: T;
  options: SegmentOption<T>[];
  onChange: (next: T) => void;
  ariaLabel: string;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={ariaLabel}>
      {options.map((opt) => {
        const id = `${name}-${opt.value}`;
        return (
          <label key={opt.value} className={`segmented__opt ${opt.value === value ? "is-on" : ""}`} htmlFor={id} title={opt.hint}>
            <input
              type="radio"
              id={id}
              name={name}
              value={opt.value}
              checked={opt.value === value}
              onChange={() => onChange(opt.value)}
              className="sr-only"
            />
            {opt.label}
          </label>
        );
      })}
    </div>
  );
}
