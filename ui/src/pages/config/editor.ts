import type { ConfigDocument, ConfigView, GenreRules, LibraryRun, MediaType, ValidationIssue } from "../../api/types";

export const TYPES: MediaType[] = ["anime", "standard-tv", "standard-movie"];

export const emptyRules = (): GenreRules => ({
  ignore: [],
  replace: {},
  sortedPrefix: "",
  sortedCollections: [],
  maxGenres: null,
});

export const newLibrary = (library = ""): LibraryRun => ({
  library,
  type: "anime",
  enabled: true,
  providers: null,
  useGenres: false,
  useKeywords: false,
  clearGenres: false,
  setPosters: false,
  sortCollections: false,
  rateAnime: false,
  createRatingCollections: false,
  overrides: null,
});

export function toDocument(view: ConfigView): ConfigDocument {
  return { version: view.version, defaults: view.defaults, libraries: view.libraries, schedule: view.schedule };
}

/** Common schedules; anything else is "custom". */
export const SCHEDULE_PRESETS: { key: string; label: string; cron: string }[] = [
  { key: "night1", label: "01:00 nightly", cron: "0 1 * * *" },
  { key: "night4", label: "04:00 nightly", cron: "0 4 * * *" },
  { key: "6h", label: "every 6 h", cron: "0 */6 * * *" },
  { key: "weekly", label: "Sun 03:00", cron: "0 3 * * 0" },
];

/** Structural equality; objects are built in a stable key order so this is enough. */
export const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export const rulesEmpty = (r: GenreRules | null | undefined) =>
  !r ||
  (r.ignore.length === 0 &&
    Object.keys(r.replace).length === 0 &&
    !r.sortedPrefix &&
    r.sortedCollections.length === 0 &&
    r.maxGenres == null);

const locEq = (a: (string | number)[], b: (string | number)[]) =>
  a.length === b.length && a.every((v, i) => v === b[i]);

const locStartsWith = (loc: (string | number)[], prefix: (string | number)[]) =>
  loc.length >= prefix.length && prefix.every((v, i) => v === loc[i]);

/** The message attached exactly to `loc`, if any. */
export const issueAt = (errors: ValidationIssue[], loc: (string | number)[]) =>
  errors.find((e) => locEq(e.loc, loc))?.msg;

/** Every issue at or below `prefix`. */
export const issuesUnder = (errors: ValidationIssue[], prefix: (string | number)[]) =>
  errors.filter((e) => locStartsWith(e.loc, prefix));

/** Issues at `prefix` itself (model-level), not at one of its fields. */
export const issuesOwn = (errors: ValidationIssue[], prefix: (string | number)[]) =>
  errors.filter((e) => locEq(e.loc, prefix));

/** Human label for an issue location, for the summary in the save bar. */
export function describeLoc(loc: (string | number)[], doc: ConfigDocument): string {
  if (loc[0] === "libraries" && typeof loc[1] === "number") {
    const name = doc.libraries[loc[1]]?.library || `library ${loc[1] + 1}`;
    const field = loc.slice(2).join(".");
    return field ? `${name} · ${field}` : name;
  }
  if (loc[0] === "defaults") return `defaults · ${loc.slice(1).join(".")}`;
  return loc.join(".") || "config";
}

/** Provider order presets for anime; standard libraries only have TMDB. */
export const ANIME_PROVIDER_PRESETS: { key: string; label: string; value: string[] | null; hint: string }[] = [
  { key: "default", label: "jikan", value: null, hint: "MyAnimeList via Jikan (default)" },
  { key: "jikan,anilist", label: "jikan → anilist", value: ["jikan", "anilist"], hint: "AniList as a fallback" },
  { key: "anilist,jikan", label: "anilist → jikan", value: ["anilist", "jikan"], hint: "AniList first" },
  { key: "anilist", label: "anilist", value: ["anilist"], hint: "AniList only" },
];

export const providerPresetKey = (providers: string[] | null) => {
  if (!providers || providers.length === 0) return "default";
  const key = providers.join(",");
  return ANIME_PROVIDER_PRESETS.some((p) => p.key === key) ? key : "default";
};

// -- JSON Schema access (help text and enums stay in step with the models) ----

interface SchemaProperty {
  description?: string;
  type?: string;
  enum?: string[];
  default?: unknown;
}

export interface JsonSchemaLike {
  $defs?: Record<string, { properties?: Record<string, SchemaProperty> }>;
}

export type Help = (def: "LibraryRun" | "GenreRules" | "ScheduleSettings", field: string) => string | undefined;

export const helpFrom =
  (schema: JsonSchemaLike | undefined): Help =>
  (def, field) =>
    schema?.$defs?.[def]?.properties?.[field]?.description;
