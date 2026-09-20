import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type {
  AuthStatus,
  BindingIn,
  BindingView,
  CandidateView,
  ConfigDocument,
  ConfigView,
  CronPreview,
  DoctorReport,
  Health,
  ItemStatusFilter,
  ItemsPage,
  JobEvent,
  JobProgress,
  JobStatus,
  JobView,
  LibraryView,
  MediaType,
  Problem,
  RunOptions,
  RunView,
  SaveResult,
  StartJobs,
  UndoResult,
  ValidationResult,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string | null;
  /** The full response body, for endpoints that put structure in an error (412, 422). */
  readonly body: unknown;

  constructor(problem: Problem, body: unknown = problem) {
    super(problem.detail ?? problem.title);
    this.name = "ApiError";
    this.status = problem.status;
    this.title = problem.title;
    this.detail = problem.detail ?? null;
    this.body = body;
  }
}

/** Fired when any API call comes back 401, so the shell can show the login screen. */
export const UNAUTHORIZED_EVENT = "pag:unauthorized";

function noteUnauthorized(status: number, path: string) {
  if (status === 401 && !path.startsWith("/api/v1/auth/")) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
}

type Params = Record<string, string | number | undefined>;

/** The one transport: every call, verb and error body goes through here. */
async function request<T>(
  method: "GET" | "POST" | "PUT" | "DELETE",
  path: string,
  { params, body, headers }: { params?: Params; body?: unknown; headers?: Record<string, string> } = {},
): Promise<T> {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
  }
  const response = await fetch(url, {
    method,
    headers: { Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...(headers ?? {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let problem: Problem = { title: response.statusText || "Request failed", status: response.status };
    let detail: unknown = problem;
    try {
      const parsed = (await response.json()) as Record<string, unknown>;
      problem = { ...problem, ...(parsed as Partial<Problem>) };
      detail = { ...problem, ...parsed };
    } catch {
      /* body was not JSON; keep the status-derived problem */
    }
    noteUnauthorized(response.status, path);
    throw new ApiError(problem, detail);
  }
  return (await response.json()) as T;
}

const get = <T>(path: string, params?: Params) => request<T>("GET", path, { params });
const send = <T>(method: "PUT" | "POST" | "DELETE", path: string, body?: unknown, headers?: Record<string, string>) =>
  request<T>(method, path, { body, headers });
const post = <T>(path: string, body?: unknown) => send<T>("POST", path, body);

export interface ItemsQuery {
  page?: number;
  size?: number;
  q?: string;
  status?: ItemStatusFilter;
  refresh?: boolean;
}

export const api = {
  authStatus: () => get<AuthStatus>("/api/v1/auth/status"),
  login: (password: string) => send<AuthStatus>("POST", "/api/v1/auth/login", { password }),
  logout: () => send<AuthStatus>("POST", "/api/v1/auth/logout", undefined),
  health: () => get<Health>("/api/v1/health"),
  items: (library: string, query: ItemsQuery = {}) =>
    get<ItemsPage>(`/api/v1/libraries/${encodeURIComponent(library)}/items`, {
      page: query.page,
      size: query.size,
      q: query.q,
      status: query.status,
      refresh: query.refresh ? "1" : undefined,
    }),
  forgetItem: (library: string, mediaKey: string) =>
    send<{ forgotten: boolean }>("POST", `/api/v1/libraries/${encodeURIComponent(library)}/items/forget?media_key=${encodeURIComponent(mediaKey)}`, undefined),
  refreshLibrary: (library: string) =>
    send<{ refreshed: boolean }>("POST", `/api/v1/libraries/${encodeURIComponent(library)}/refresh`, undefined),
  search: (params: { q: string; type: MediaType; provider?: string; year?: number | null; limit?: number }) =>
    get<CandidateView[]>("/api/v1/search", {
      q: params.q,
      type: params.type,
      provider: params.provider,
      year: params.year ?? undefined,
      limit: params.limit,
    }),
  createBinding: (body: BindingIn) => send<BindingView>("POST", "/api/v1/bindings", body),
  deleteBinding: (library: string, mediaKey: string) =>
    send<{ removed: boolean }>("DELETE", `/api/v1/bindings?library=${encodeURIComponent(library)}&media_key=${encodeURIComponent(mediaKey)}`, undefined),
  thumbUrl: (path: string | null) => (path ? `/api/v1/plex/thumb?path=${encodeURIComponent(path)}` : null),
  validateConfig: (doc: ConfigDocument) => send<ValidationResult>("POST", "/api/v1/config/validate", doc),
  saveConfig: (doc: ConfigDocument, etag: string | null) =>
    send<SaveResult>("PUT", "/api/v1/config", doc, etag ? { "If-Match": etag } : {}),
  jobs: () => get<JobView[]>("/api/v1/jobs"),
  job: (id: string) => get<JobView>(`/api/v1/jobs/${encodeURIComponent(id)}`),
  startJob: (library: string, options: RunOptions = {}) =>
    post<JobView>(`/api/v1/libraries/${encodeURIComponent(library)}/run`, options),
  startJobs: (body: StartJobs) => post<JobView[]>("/api/v1/jobs", body),
  cancelJob: (id: string) => post<JobView>(`/api/v1/jobs/${encodeURIComponent(id)}/cancel`),
  undoRun: (id: string) => post<UndoResult>(`/api/v1/runs/${encodeURIComponent(id)}/undo`),
  config: () => get<ConfigView>("/api/v1/config"),
  schema: () => get<Record<string, unknown>>("/api/v1/config/schema"),
  cronPreview: (cron: string) => get<CronPreview>("/api/v1/schedule/preview", { cron }),
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

// -- jobs ------------------------------------------------------------------

const ACTIVE: JobStatus[] = ["queued", "running"];

export const isActive = (job: Pick<JobView, "status">) => ACTIVE.includes(job.status);

/** Polls briskly while anything is queued or running, lazily otherwise. */
export const useJobs = () =>
  useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs,
    refetchInterval: (query) => (query.state.data?.some(isActive) ? 2_000 : 20_000),
  });

export const useJob = (id: string | null | undefined) =>
  useQuery({
    queryKey: ["job", id ?? ""],
    queryFn: () => api.job(id!),
    enabled: Boolean(id),
    refetchInterval: (query) => (query.state.data && isActive(query.state.data) ? 2_000 : false),
  });

/** Invalidate everything a finished job can have changed. */
function useInvalidateAfterJob() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["jobs"] });
    void qc.invalidateQueries({ queryKey: ["runs"] });
    void qc.invalidateQueries({ queryKey: ["run"] });
    void qc.invalidateQueries({ queryKey: ["libraries"] });
  };
}

export function useStartJob() {
  const invalidate = useInvalidateAfterJob();
  return useMutation({
    mutationFn: ({ library, options }: { library: string; options?: RunOptions }) =>
      api.startJob(library, options),
    onSuccess: invalidate,
  });
}

export function useStartJobs() {
  const invalidate = useInvalidateAfterJob();
  return useMutation({ mutationFn: (body: StartJobs) => api.startJobs(body), onSuccess: invalidate });
}

export function useCancelJob() {
  const invalidate = useInvalidateAfterJob();
  return useMutation({ mutationFn: (id: string) => api.cancelJob(id), onSuccess: invalidate });
}

export function useUndoRun() {
  const invalidate = useInvalidateAfterJob();
  return useMutation({ mutationFn: (id: string) => api.undoRun(id), onSuccess: invalidate });
}

export interface LiveJob {
  job: JobView | null;
  progress: JobProgress | null;
  status: JobStatus | null;
  /** The most recent failed item, for the ticker. */
  lastError: { title: string; error: string } | null;
  connected: boolean;
}

/**
 * Follow a job over SSE. Progress is strictly server -> client, so an
 * EventSource is enough; it reconnects on its own and we resync from the
 * `snapshot` event each time.
 */
const IDLE: LiveJob = { job: null, progress: null, status: null, lastError: null, connected: false };

export function useJobEvents(jobId: string | null | undefined): LiveJob {
  const [state, setState] = useState<LiveJob>(IDLE);
  const invalidate = useInvalidateAfterJob();
  const invalidateRef = useRef(invalidate);
  invalidateRef.current = invalidate;

  useEffect(() => {
    // A different job (or none): nothing from the previous stream may linger.
    setState(IDLE);
    if (!jobId) return;
    const source = new EventSource(`/api/v1/jobs/${encodeURIComponent(jobId)}/events`);

    const handle = (message: JobEvent) => {
      setState((prev) => {
        switch (message.event) {
          case "snapshot":
          case "status":
            return {
              ...prev,
              job: message.data,
              progress: message.data.progress,
              status: message.data.status,
              connected: true,
            };
          case "begin":
            return {
              ...prev,
              status: "running",
              progress: {
                action: message.data.action,
                run_id: message.data.run_id,
                total: message.data.total,
                pending: message.data.pending,
                done: 0,
                written: 0,
                unchanged: 0,
                failed: 0,
                title: null,
              },
            };
          case "item":
            return {
              ...prev,
              progress: prev.progress
                ? {
                    ...prev.progress,
                    done: message.data.done,
                    written: message.data.written,
                    unchanged: message.data.unchanged,
                    failed: message.data.failed,
                    title: message.data.title,
                  }
                : prev.progress,
              lastError:
                message.data.status === "failed"
                  ? { title: message.data.title, error: message.data.error ?? "failed" }
                  : prev.lastError,
            };
          case "report":
            return {
              ...prev,
              job: prev.job
                ? { ...prev.job, reports: [...prev.job.reports, message.data.report], run_ids: prev.job.run_ids.includes(message.data.run_id) ? prev.job.run_ids : [...prev.job.run_ids, message.data.run_id] }
                : prev.job,
            };
          case "end":
            return {
              ...prev,
              status: message.data.status,
              job: prev.job
                ? { ...prev.job, status: message.data.status, error: message.data.error, reports: message.data.reports, run_ids: message.data.run_ids, finished_at: Date.now() / 1000 }
                : prev.job,
            };
        }
      });
      if (message.event === "end") {
        source.close();
        setState((prev) => ({ ...prev, connected: false }));
        invalidateRef.current();
      }
    };

    for (const name of ["snapshot", "status", "begin", "item", "report", "end"] as const) {
      source.addEventListener(name, (raw) => {
        try {
          handle({ event: name, data: JSON.parse((raw as MessageEvent).data) } as JobEvent);
        } catch {
          /* malformed frame; ignore */
        }
      });
    }
    source.onopen = () => setState((prev) => ({ ...prev, connected: true }));
    source.onerror = () => setState((prev) => ({ ...prev, connected: false }));

    return () => {
      source.close();
    };
  }, [jobId]);

  return state;
}

// -- config editing ----------------------------------------------------------

export const useSchema = () =>
  useQuery({ queryKey: ["schema"], queryFn: api.schema, staleTime: Infinity });

/** Live check of a cron expression; the caller debounces. */
export const useCronPreview = (cron: string) =>
  useQuery({
    queryKey: ["cron", cron],
    queryFn: () => api.cronPreview(cron),
    enabled: cron.length > 0,
    staleTime: 60_000,
    retry: false,
  });

export function useSaveConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ doc, etag }: { doc: ConfigDocument; etag: string | null }) => api.saveConfig(doc, etag),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["config"] });
      void qc.invalidateQueries({ queryKey: ["doctor"] });
      void qc.invalidateQueries({ queryKey: ["libraries"] });
      void qc.invalidateQueries({ queryKey: ["health"] }); // the schedule lives in the config
    },
  });
}

// -- library browser -----------------------------------------------------------

export const useLibraryItems = (library: string, query: ItemsQuery) =>
  useQuery({
    queryKey: ["items", library, query.page ?? 1, query.size ?? 50, query.q ?? "", query.status ?? "all"],
    queryFn: () => api.items(library, query),
    enabled: Boolean(library),
    placeholderData: (prev) => prev,
  });

export const useCandidates = (params: { q: string; type: MediaType; provider?: string; year?: number | null } | null) =>
  useQuery({
    queryKey: ["candidates", params?.q ?? "", params?.type ?? "", params?.provider ?? "", params?.year ?? ""],
    queryFn: () => api.search(params!),
    enabled: Boolean(params && params.q.trim()),
    staleTime: 5 * 60_000,
    retry: false,
  });

function useInvalidateItems() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["items"] });
    void qc.invalidateQueries({ queryKey: ["bindings"] });
    void qc.invalidateQueries({ queryKey: ["libraries"] });
  };
}

export function useCreateBinding() {
  const invalidate = useInvalidateItems();
  return useMutation({ mutationFn: (body: BindingIn) => api.createBinding(body), onSuccess: invalidate });
}

export function useDeleteBinding() {
  const invalidate = useInvalidateItems();
  return useMutation({
    mutationFn: ({ library, mediaKey }: { library: string; mediaKey: string }) => api.deleteBinding(library, mediaKey),
    onSuccess: invalidate,
  });
}

export function useForgetItem() {
  const invalidate = useInvalidateItems();
  return useMutation({
    mutationFn: ({ library, mediaKey }: { library: string; mediaKey: string }) => api.forgetItem(library, mediaKey),
    onSuccess: invalidate,
  });
}

/** Drop the server's cached item list, then re-read: what "Refresh" promises. */
export function useRefreshLibrary() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (library: string) => api.refreshLibrary(library),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["items"] }),
  });
}

// -- auth ----------------------------------------------------------------------

export const useAuthStatus = () =>
  useQuery({ queryKey: ["auth"], queryFn: api.authStatus, staleTime: 0, retry: false });

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (password: string) => api.login(password),
    onSuccess: (status) => {
      // The response is the new truth for the gate; everything else was
      // fetched (or refused) under the old session.
      qc.setQueryData(["auth"], status);
      void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "auth" });
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.logout(),
    onSuccess: (status) => {
      // Never clear() here: it orphans the mounted auth query, so the gate
      // would keep its stale "authenticated" data while every poll 401s.
      qc.setQueryData(["auth"], status);
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth" });
    },
  });
}
