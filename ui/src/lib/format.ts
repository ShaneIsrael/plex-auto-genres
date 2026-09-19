const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const dtf = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});
const nf = new Intl.NumberFormat();

/** "3 hours ago" / "in 2 days". Input is a unix timestamp in seconds. */
export function relTime(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "—";
  const delta = ts - now;
  const abs = Math.abs(delta);
  if (abs < 45) return delta < 0 ? "just now" : "in a moment";
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["minute", 60],
    ["hour", 3600],
    ["day", 86400],
    ["week", 604800],
    ["month", 2629800],
  ];
  let unit: Intl.RelativeTimeFormatUnit = "minute";
  let size = 60;
  for (const [u, s] of units) {
    if (abs >= s) {
      unit = u;
      size = s;
    }
  }
  return rtf.format(Math.round(delta / size), unit);
}

export function dateTime(ts: number | null | undefined): string {
  return ts ? dtf.format(new Date(ts * 1000)) : "—";
}

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

export function int(n: number | null | undefined): string {
  return n == null ? "—" : nf.format(n);
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}
