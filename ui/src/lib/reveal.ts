import type { CSSProperties } from "react";

/**
 * Staggered entrance: block `index` fades in `index × --stagger` after the
 * page header (index 0). Spread onto any element or Panel: `{...reveal(2)}`.
 */
export const reveal = (index: number, className = ""): { className: string; style: CSSProperties } => ({
  className: `reveal ${className}`.trim(),
  style: { "--i": index } as CSSProperties,
});
