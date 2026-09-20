import type { ReactNode } from "react";

/** Label above, help below, error under that. The control gets the ids via props. */
export function Field({
  id,
  label,
  help,
  error,
  children,
}: {
  id: string;
  label: ReactNode;
  help?: ReactNode;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className={`field ${error ? "field--invalid" : ""}`}>
      <label className="field__label label" htmlFor={id}>
        {label}
      </label>
      {children}
      {help && !error && (
        <div id={`${id}-help`} className="field__help">
          {help}
        </div>
      )}
      {error && (
        <div id={`${id}-error`} className="field__error" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}

/** aria-describedby value for a control inside a Field. */
export const describedBy = (id: string, help?: unknown, error?: unknown) =>
  [error ? `${id}-error` : null, help && !error ? `${id}-help` : null].filter(Boolean).join(" ") || undefined;
