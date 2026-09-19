export function Skeleton({ width = "100%", height = 14, className = "" }: { width?: string | number; height?: number; className?: string }) {
  return <span className={`skeleton ${className}`} style={{ width, height }} aria-hidden="true" />;
}

export function PageSkeleton() {
  return (
    <div className="page" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading</span>
      <div style={{ display: "grid", gap: 12, maxWidth: 520 }}>
        <Skeleton width={120} height={12} />
        <Skeleton width={320} height={28} />
        <Skeleton width={440} />
      </div>
      <div className="stat-grid" style={{ marginTop: 40 }}>
        {[0, 1, 2, 3].map((i) => (
          <div className="panel" key={i}>
            <div className="panel__body" style={{ display: "grid", gap: 12 }}>
              <Skeleton width={90} height={12} />
              <Skeleton width={110} height={44} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
