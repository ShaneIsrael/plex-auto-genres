import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function Empty({
  icon,
  title,
  children,
  action,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: { to: string; label: string };
}) {
  return (
    <div className="empty">
      {icon && <div className="empty__icon" aria-hidden="true">{icon}</div>}
      <div className="empty__title">{title}</div>
      {children && <div className="empty__body muted">{children}</div>}
      {action && (
        <Link className="button button--ghost" to={action.to}>
          {action.label}
        </Link>
      )}
    </div>
  );
}

export function ErrorBlock({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error as { title?: string; detail?: string | null; message?: string };
  return (
    <div className="error-block" role="alert">
      <div className="error-block__title">{e.title ?? "Something went wrong"}</div>
      <div className="error-block__detail mono">{e.detail ?? e.message ?? String(error)}</div>
      {onRetry && (
        <button type="button" className="button button--ghost" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
