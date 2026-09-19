import { useQuery } from "@tanstack/react-query";
import type {
  BindingView,
  ConfigView,
  DoctorReport,
  Health,
  LibraryView,
  Problem,
  RunView,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string | null;

  constructor(problem: Problem) {
    super(problem.detail ?? problem.title);
    this.name = "ApiError";
    this.status = problem.status;
    this.title = problem.title;
    this.detail = problem.detail ?? null;
  }
}

async function get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
  }
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    let problem: Problem = { title: response.statusText || "Request failed", status: response.status };
    try {
      problem = { ...problem, ...(await response.json()) };
    } catch {
      /* body was not JSON; keep the status-derived problem */
    }
    throw new ApiError(problem);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => get<Health>("/api/v1/health"),
  config: () => get<ConfigView>("/api/v1/config"),
  schema: () => get<Record<string, unknown>>("/api/v1/config/schema"),
  doctor: () => get<DoctorReport>("/api/v1/doctor"),
  libraries: () => get<LibraryView[]>("/api/v1/libraries"),
  runs: (limit = 50, library?: string) => get<RunView[]>("/api/v1/runs", { limit, library }),
  run: (id: string) => get<RunView>(`/api/v1/runs/${encodeURIComponent(id)}`),
  bindings: (library?: string) => get<BindingView[]>("/api/v1/bindings", { library }),
};

// Hooks. Refetch intervals are deliberately slow: this is a console, not a
// trading terminal, and Plex does not like being polled.
export const useHealth = () =>
  useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 30_000 });

export const useConfig = () => useQuery({ queryKey: ["config"], queryFn: api.config });

export const useDoctor = () =>
  useQuery({ queryKey: ["doctor"], queryFn: api.doctor, staleTime: 5 * 60_000 });

export const useLibraries = () =>
  useQuery({ queryKey: ["libraries"], queryFn: api.libraries, refetchInterval: 60_000 });

export const useRuns = (limit = 50, library?: string) =>
  useQuery({
    queryKey: ["runs", limit, library ?? ""],
    queryFn: () => api.runs(limit, library),
    refetchInterval: 15_000,
  });

export const useRun = (id: string) =>
  useQuery({ queryKey: ["run", id], queryFn: () => api.run(id), enabled: Boolean(id) });

export const useBindings = (library?: string) =>
  useQuery({ queryKey: ["bindings", library ?? ""], queryFn: () => api.bindings(library) });
