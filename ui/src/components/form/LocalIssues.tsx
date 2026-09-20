import { createContext, useContext, useEffect } from "react";

/**
 * Problems a form control can see but the server cannot, because the
 * document it receives has already lost them — a duplicate key in a
 * from→to list collapses before it is ever posted. Controls report their
 * count here and the page keeps Save disabled while any remain.
 */
export type ReportIssues = (id: string, count: number) => void;

const LocalIssuesContext = createContext<ReportIssues>(() => {});

export const LocalIssuesProvider = LocalIssuesContext.Provider;

/** Keep `count` registered under `id` for as long as the control is mounted. */
export function useReportIssues(id: string, count: number) {
  const report = useContext(LocalIssuesContext);
  useEffect(() => {
    report(id, count);
    return () => report(id, 0);
  }, [id, count, report]);
}
