import type { ReactNode } from "react";
import { reveal } from "../lib/reveal";

export function Panel({
  title,
  eyebrow,
  aside,
  children,
  className = "",
  style,
}: {
  title?: string;
  eyebrow?: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <section className={`panel ${className}`} style={style}>
      {(title || aside) && (
        <header className="panel__head">
          <div>
            {eyebrow && <div className="label">{eyebrow}</div>}
            {title && <h2 className="panel__title">{title}</h2>}
          </div>
          {aside && <div className="panel__aside">{aside}</div>}
        </header>
      )}
      <div className="panel__body">{children}</div>
    </section>
  );
}

export function PageHeader({
  eyebrow,
  title,
  lede,
  actions,
}: {
  eyebrow: string;
  title: ReactNode;
  lede?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header {...reveal(0, "page-head")}>
      <div>
        <div className="label page-head__eyebrow">{eyebrow}</div>
        <h1 className="page-head__title">{title}</h1>
        {lede && <p className="page-head__lede muted">{lede}</p>}
      </div>
      {actions && <div className="page-head__actions">{actions}</div>}
    </header>
  );
}
