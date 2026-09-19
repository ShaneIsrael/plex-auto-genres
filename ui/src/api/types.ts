// Mirrors plex_auto_genres/server/schemas.py. Keep the two in step.

export type MediaType = "anime" | "standard-tv" | "standard-movie";
export type Level = "ok" | "warn" | "error";
export type RunStatus = "running" | "interrupted" | "ok" | "partial" | "failed" | "undone";

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
