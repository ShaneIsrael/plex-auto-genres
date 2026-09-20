import type { ReactNode } from "react";

export function Toggle({
  id,
  checked,
  onChange,
  label,
  help,
  disabled = false,
}: {
  id: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  label: ReactNode;
  help?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <div className={`toggle ${disabled ? "toggle--disabled" : ""}`}>
      <button
        type="button"
        id={id}
        role="switch"
        aria-checked={checked}
        aria-describedby={help ? `${id}-help` : undefined}
        disabled={disabled}
        className="toggle__track"
        onClick={() => onChange(!checked)}
      >
        <span className="toggle__thumb" aria-hidden="true" />
      </button>
      <label className="toggle__text" htmlFor={id}>
        <span className="toggle__label">{label}</span>
        {help && (
          <span id={`${id}-help`} className="toggle__help">
            {help}
          </span>
        )}
      </label>
    </div>
  );
}
