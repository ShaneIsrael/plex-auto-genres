// Mirrors plex_auto_genres/server/schemas.py. Keep the two in step.

export type MediaType = "anime" | "standard-tv" | "standard-movie";
export type Level = "ok" | "warn" | "error";
export type RunStatus = "running" | "interrupted" | "ok" | "partial" | "failed" | "undone" | "cancelled";
export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";
export type Action = "tags" | "posters" | "sort" | "ratings" | "rating-collections";

export interface PlexStatus {
  reachable: boolean;
  server_name: string | null;
  version: string | null;
  error: string | null;
  checked_at: number | null;
}

export interface SchedulerStatus {
  cron: string;
  next_fire_at: number | null;
}

export interface Health {
  status: "ok" | "degraded";
  version: string;
  uptime_s: number;
  started_at: number;
  config_path: string;
  db_path: string;
  plex: PlexStatus;
  scheduler: SchedulerStatus | null;
}

export interface GenreRules {
  ignore: string[];
  replace: Record<string, string>;
  sortedPrefix: string;
  sortedCollections: string[];
  maxGenres: number | null;
}

export interface LibraryRun {
  library: string;
  type: MediaType;
  enabled: boolean;
  providers: string[] | null;
  useGenres: boolean;
  useKeywords: boolean;
  clearGenres: boolean;
  setPosters: boolean;
  sortCollections: boolean;
  rateAnime: boolean;
  createRatingCollections: boolean;
  overrides: GenreRules | null;
}

export interface Secrets {
  plex_token: boolean;
  plex_password: boolean;
  tmdb_api_key: boolean;
  plex_base_url: string | null;
  plex_server_name: string | null;
  collection_prefix: string;
}

export interface ConfigView {
  path: string;
  version: number;
  defaults: Partial<Record<MediaType, GenreRules>>;
  libraries: LibraryRun[];
  secrets: Secrets;
  providers: { tmdb_language: string; concurrency: number; max_attempts: number };
}

export interface Check {
  id: string;
  level: Level;
  title: string;
  detail: string | null;
  items: string[];
}

export interface DoctorReport {
  ok: boolean;
  errors: number;
  warnings: number;
  checks: Check[];
}

export interface RunReport {
  run_id: string;
  library: string;
  action: string;
  dry_run: boolean;
  written: number;
  unchanged: number;
  skipped: number;
  failed: number;
  total: number;
  plex_requests: number;
  provider_requests: number;
  duration_s: number;
  failures: [string, string][];
  cancelled: boolean;
}

export interface RunView {
  run_id: string;
  library: string;
  action: string;
  dry_run: boolean;
  status: RunStatus;
  started_at: number;
  finished_at: number | null;
  undone_at: number | null;
  report: RunReport | null;
  /** Set while the job that produced this run is still remembered by the server. */
  job_id: string | null;
}

export interface PlexSection {
  key: number;
  section_type: string;
  item_count: number | null;
  agent: string | null;
}

export interface LibraryView {
  name: string;
  configured: boolean;
  enabled: boolean | null;
  type: MediaType | null;
  providers: string[];
  useGenres: boolean | null;
  clearGenres: boolean | null;
  plex: PlexSection | null;
  stats: Partial<Record<"ok" | "failed", number>>;
  last_run: RunView | null;
}

export interface BindingView {
  library: string;
  media_key: string;
  provider: string;
  provider_id: string;
  note: string | null;
  created_at: number;
}

export interface Problem {
  title: string;
  status: number;
  detail?: string | null;
}

export interface JobProgress {
  action: string | null;
  run_id: string | null;
  total: number;
  pending: number;
  done: number;
  written: number;
  unchanged: number;
  failed: number;
  title: string | null;
}

export interface JobView {
  job_id: string;
  library: string;
  status: JobStatus;
  source: "api" | "schedule" | string;
  dry_run: boolean;
  force: boolean;
  only: Action[];
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  error: string | null;
  run_ids: string[];
  progress: JobProgress;
  reports: RunReport[];
}

export interface RunOptions {
  dry_run?: boolean;
  force?: boolean;
  only?: Action[];
}

export interface StartJobs extends RunOptions {
  libraries?: string[] | null;
}

export interface UndoResult {
  run_id: string;
  restored: number;
  skipped: number;
}

/** Server-sent events on /jobs/{id}/events. */
export type JobEvent =
  | { event: "snapshot"; data: JobView }
  | { event: "status"; data: JobView }
  | { event: "begin"; data: { job_id: string; library: string; action: string; run_id: string; total: number; pending: number } }
  | { event: "item"; data: { job_id: string; run_id: string; done: number; pending: number; written: number; unchanged: number; failed: number; title: string; status: string; error: string | null } }
  | { event: "report"; data: { job_id: string; run_id: string; report: RunReport } }
  | { event: "end"; data: { job_id: string; status: JobStatus; error: string | null; run_ids: string[]; reports: RunReport[] } };
