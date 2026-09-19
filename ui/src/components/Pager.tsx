import { ChevronLeft, ChevronRight } from "lucide-react";
import { int } from "../lib/format";

export function Pager({
  page,
  size,
  total,
  onPage,
}: {
  page: number;
  size: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  const from = total === 0 ? 0 : (page - 1) * size + 1;
  const to = Math.min(total, page * size);
  return (
    <nav className="pager" aria-label="Pagination">
      <span className="pager__range mono muted">
        {int(from)}–{int(to)} of {int(total)}
      </span>
      <div className="pager__buttons">
        <button type="button" className="iconbtn" aria-label="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          <ChevronLeft size={16} aria-hidden="true" />
        </button>
        <span className="mono muted pager__page">
          {page} / {pages}
        </span>
        <button type="button" className="iconbtn" aria-label="Next page" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          <ChevronRight size={16} aria-hidden="true" />
        </button>
      </div>
    </nav>
  );
}
