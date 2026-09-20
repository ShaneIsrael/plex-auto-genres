# Web UI — design notes

Research for the planned web interface. Nothing here is implemented yet; the point is to
record what the v2 rewrite already supports, what is genuinely missing, and which choices
are worth making early because they are expensive to reverse.

**Target scope** (as stated):

1. Bulk metadata management across Plex libraries — not only genres.
2. Task management: see runs, re-run them, trigger them by hand.
3. Manual binding: *this Plex series = that id at that provider*.
4. Server configuration through intuitive forms rather than hand-edited JSON.

---

## 1. What v2 already gives you

The rewrite was shaped around this goal, so a fair amount of the groundwork exists.

| Requirement | Already there | Where |
|---|---|---|
| Config forms | `AppConfig.model_json_schema()` emits JSON Schema 2020-12 with types, enums, `required`, and every field's `description` as help text | `config.py:config_json_schema` |
| Config validation | pydantic with `extra="forbid"`, plus `_format_validation_error` turning failures into per-field messages a form can attach to inputs | `config.py` |
| Manual binding | `bindings` table + `set_binding` / `get_binding` / `delete_binding`; the pipeline already prefers a binding over the Plex GUID, and setting one invalidates the cached match | `store.py`, `pipeline.py:_resolve` |
| Run history | `runs` table with per-run counters and a JSON report | `store.py:recent_runs` |
| Undo | `snapshots` table records before/after per item; `undo_run` restores | `store.py`, `plexsvc/writer.py` |
| Live progress | `Pipeline.tag_library(progress=...)` fires a callback per item — the hook an SSE stream needs | `pipeline.py` |
| Concurrent-safe state | SQLite in WAL mode, `check_same_thread=False`, 30 s busy timeout | `store.py` |
| Structured output | Every CLI command supports `--json` | `cli.py` |

The service layer is already decoupled from `cli.py`: `Pipeline`, `Store` and `plexsvc`
have no knowledge of argparse or terminals. A web server is a second front end over the
same objects, not a rewrite.

## 2. What is actually missing

Honest gap list. This is the real work.

### 2.1 A job runner

Today a run is a coroutine awaited by the CLI until it finishes. A web UI needs runs that:

- start from an HTTP request and outlive it;
- report progress while in flight;
- can be **cancelled** (not currently possible — there is no cancellation token, though
  `asyncio.Task.cancel()` plus the existing per-item structure gets most of the way);
- cannot be started twice concurrently for the same library (Plex will not enjoy two
  writers on one section).

Smallest thing that works: an in-process `JobManager` holding `asyncio.Task`s keyed by
job id, with an `asyncio.Lock` per library and a bounded `deque` of recent progress
events per job. No Celery, no Redis — this is a single-user homelab app, and an external
broker would be more operational burden than the problem deserves.

Persist job rows in the existing `runs` table so history survives a restart. Mark rows
that were in flight at shutdown as `interrupted` on boot (`cmd_runs` already renders a
missing report as "interrupted").

### 2.2 Writing the config back

Config loading is read-only today. Writing needs:

- validate the posted document **before** touching disk (`AppConfig.model_validate`);
- write to a temp file in the same directory and `os.replace` it — atomic on POSIX, so a
  crash mid-write cannot leave a truncated config;
- keep the previous version (`config.json.bak`, or a small `config_versions` table if you
  want history in the UI);
- reload the in-memory config without restarting the process;
- decide what happens to `//` comment keys. `strip_comments` discards them on load, so a
  round trip through the UI would silently delete the user's annotations. Either preserve
  them (read the raw document alongside the parsed one and merge on save) or accept the
  loss and say so in the UI. **Preserving them is the better call** — people annotate
  configs and silently eating that is the kind of thing that erodes trust in a tool.

### 2.3 A library browser

The binding UI needs "show me this library's items and how each one currently matches",
which no code path produces. `plexsvc.client.iter_library` returns `MediaItem`s with
GUIDs; joining that against `media_state` and `bindings` gives:

```
title | year | matched provider + id | source (guid / search / manual) | genres written | status
```

This wants pagination and a title filter — `iter_library` currently reads the whole
section, which is fine for a nightly batch and wrong for a UI on a 5 000-item library.
`fetchItems` already accepts `container_start`/`container_size`, so paging is a small
change rather than a redesign.

### 2.4 Provider search returning *candidates*

`cmd_query` resolves to a single best match. The binding UI needs a ranked list to choose
from, with a poster, year and synopsis per candidate. The providers already fetch that
data in `search()` and then throw the alternatives away in `pick_best`. Extracting a
`search_candidates() -> list[ProviderResult]` alongside the existing `resolve()` is
mostly refactoring, not new logic.

### 2.5 Bulk operations beyond genres

"Bulk metadata management" is the largest unknown in the stated scope, because it is the
one thing v2 does *not* already model. Today the write path is genre/collection tags plus
ratings. Editing titles, summaries, posters, sort titles, labels or content ratings in
bulk means a general "field edit" abstraction over `plexapi`'s `edit()`.

The snapshot table is already field-agnostic (`field TEXT`), so undo extends to new fields
for free. That was deliberate. **Recommendation: do not build this speculatively.** Ship
genres first, then add fields as you actually want them — each one needs its own UI
affordance anyway, and a generic "edit any Plex field" grid is a much bigger product than
it looks.

### 2.6 Authentication

There is none, and the app holds a Plex token that grants full control of the server.
Even on a LAN, an unauthenticated write API is a bad default. Minimum viable: a single
app password/session cookie, `PAG_WEB_PASSWORD`, refusing to start if unset unless
`PAG_WEB_INSECURE=1`. Do not build user accounts — there is one user.

## 3. Recommended stack

### Backend: FastAPI

Not a close call:

- it is pydantic-native, so `AppConfig`, `LibraryRun` and `GenreRules` become request and
  response models **with no duplication** — the single biggest reason;
- it generates OpenAPI from those same models, which is a second schema source the UI can
  consume;
- it is async, matching `Pipeline`'s existing model, so the job runner shares one event
  loop instead of bridging two concurrency worlds;
- `StaticFiles` serves the built frontend from the same container and port, so deployment
  stays one image.

Add `fastapi` + `uvicorn[standard]`. Both are pure Python / have musl wheels, so the
97 MB Alpine image stays compiler-free.

### Frontend: React + TypeScript (Vite), forms from JSON Schema

The config editor is the part where the schema pays off. `@rjsf/core` renders
`config_json_schema()` directly — field types, enums as dropdowns, `description` as help
text, `required` markers — so **the form follows the models automatically** and cannot
drift from what the CLI validates. Add a `uiSchema` for ordering and widget hints only.

Everything else (run dashboard, library browser, binding picker) is ordinary UI and
wants real components, which argues against HTMX here despite its appeal for the simple
parts. Mixing two paradigms to save a build step is not worth it.

If you would rather avoid a JS toolchain entirely, the honest fallback is server-rendered
Jinja templates plus HTMX, accepting a clunkier binding picker. It is a legitimate choice
for a homelab tool — just decide once rather than drifting.

### Live progress: Server-Sent Events

`GET /api/jobs/{id}/events` streaming `text/event-stream`. Progress is strictly
server→client, so WebSockets buy nothing and cost reconnection logic. SSE reconnects
natively and survives a reverse proxy with `proxy_buffering off`.

### Storage: keep SQLite

One user, WAL mode, writes already serialised through one connection. Postgres would be
pure overhead. The one thing to watch: `Store` holds a single connection shared across
threads, which is safe for SQLite's serialized threading mode but means concurrent writes
queue. At this scale that is correct behaviour, not a bottleneck.

## 4. Proposed API surface

```
GET    /api/config                    current config (+ raw text for comment preservation)
PUT    /api/config                    validate, back up, atomically replace, reload
GET    /api/config/schema             JSON Schema, for the form generator
POST   /api/config/validate           dry validation, for live form feedback

GET    /api/libraries                 Plex sections + whether each is configured
GET    /api/libraries/{name}/items    paginated: title, year, match, source, status
POST   /api/libraries/{name}/run      start a job -> {job_id}

GET    /api/jobs                      run history (the runs table)
GET    /api/jobs/{id}                 one run + its report
GET    /api/jobs/{id}/events          SSE progress stream
POST   /api/jobs/{id}/cancel
POST   /api/jobs/{id}/undo            wraps undo_run

GET    /api/search?provider=&q=&type= ranked candidates for the binding picker
GET    /api/bindings
PUT    /api/bindings/{library}/{key}
DELETE /api/bindings/{library}/{key}

GET    /api/doctor                    the doctor checks, as structured JSON
GET    /api/health
```

Most of these are thin wrappers over methods that already exist, which is the point.

## 5. Suggested order

| Phase | Deliverable | Why this order |
|---|---|---|
| 1 | FastAPI app, health, `GET /api/config`, `GET /api/doctor`, static file serving | Proves the packaging and deployment story before any feature depends on it |
| 2 | `JobManager` + run/cancel + SSE + the run history page | The highest-value screen, and it forces the concurrency design early |
| 3 | Config editor via RJSF, with atomic write and comment preservation | Removes the main reason to SSH into the box |
| 4 | Library browser (paginated) + provider candidate search + binding editor | The most involved UI, and it depends on phases 2–3 existing |
| 5 | Auth, then bulk field editing beyond genres | Auth before exposing it anywhere; bulk editing last, because it is the least specified |

## 6. Decisions worth making now

- **Keep the CLI a first-class front end.** Do not let the web app become the only way to
  drive the tool; headless runs and cron are how most people will actually use it.
  Practically: no logic in `api/`, only translation between HTTP and the service layer.
- **Version the API** (`/api/v1/...`) from the first commit. Renaming later is far worse
  than an unused prefix now.
- **Do not let the UI write raw JSON.** Every write goes through `AppConfig` validation,
  so the UI cannot produce a config the CLI would reject.
- **Preserve config comments** rather than silently dropping them on save.
- **Do not add a task broker.** In-process `asyncio` tasks plus the `runs` table cover
  every stated requirement; Redis/Celery would be the largest operational regression
  available.

---

## Status

### Phase 1 — done

Shipped: `plex_auto_genres/server/` (FastAPI) and `ui/` (Vite + React + TS), wired into
the Docker image with the scheduler in the same process. Read-only, as planned.

Decisions taken while building it, for whoever picks up phase 2:

- **`runner.run_libraries`** is the seam. `cli.cmd_run`, the `schedule` command and the
  server's scheduled pass all call it; the job manager will too. It takes callbacks for
  "library started" and "report ready" rather than knowing about progress bars or SSE.
- **`doctor.run_doctor`** returns structured checks; the CLI and `/api/v1/doctor` render
  the same object.
- **Run status is derived, not stored**: `undone` > `running` / `interrupted` (an
  unfinished run older than this process is interrupted) > `failed` / `partial` / `ok`
  from the report. Phase 2 should keep it derived and simply update `finished_at`.
- **Secrets never leave the process.** `/api/v1/config` reports `set`/`unset` booleans
  plus the non-secret connection fields. Keep it that way when the config becomes
  writable: accept a token on PUT, never echo it back.
- **SPA hosting**: `/api/*` is matched first; any other GET returns `index.html` unless
  it names a real file under the static dir. An explicit `--static-dir` that holds no
  build is an error state (503 with instructions), not a silent fallback.
- **Docker**: the UI builds in a `node:22-alpine` stage with `PAG_UI_OUT=/ui/dist`, and
  the runtime reads `PAG_STATIC_DIR=/app/static`. pnpm ≥ 10 refuses install scripts
  unless allow-listed, so `ui/pnpm-workspace.yaml` carries `allowBuilds: { esbuild: true }`.
- **Polling cadence** in the UI is deliberately slow (health 30 s, runs 15 s, libraries
  60 s). SSE replaces the runs poll in phase 2; the others can stay.
- **No auth yet.** The compose file says so in a comment and binds nothing beyond the
  port; do not put this on the internet.

### Phase 2 — done

Shipped: `plex_auto_genres/jobs.py` (queue + worker + subscribers), the job/SSE/undo
routes, and the UI controls (run menu per library, "Run all", live progress on cards and
on the run page, cancel, undo behind a confirmation).

Decisions taken, and one bug found on the way:

- **One job at a time, globally** — not one lock per library as first sketched. The
  provider rate limiters are per run; two concurrent runs would each assume the whole
  Jikan/TMDB quota. A single FIFO worker is also exactly what the nightly pass always
  did. Queued jobs are visible and cancellable.
- **Jobs are ephemeral, runs are durable.** A job is in memory (last 50 kept) and
  records the run ids it produced; `RunView.job_id` joins them while the job is
  remembered. Nothing about jobs was added to SQLite, on purpose.
- **Cancellation closes the run row.** `Pipeline._abandon` marks the report
  `cancelled` and calls `finish_run` *before* waiting on in-flight items, so a second
  cancel cannot leave the row dangling. Status derivation gained `cancelled`.
- **The runner grew a `RunObserver` protocol** (`begin(run, action, run_id, total,
  pending)`, `item`, `report`). The CLI's progress bar and the job manager are two
  implementations; the bar now sizes itself to `pending`, which also fixed a v2 quirk
  where it never reached 100% on a mostly cached library.
- **SSE, not WebSockets.** `snapshot` on connect, then `begin` / `item` / `report` /
  `end`, with a `: ping` comment every 15 s of silence. Late subscribers get the
  snapshot rather than a replay, so a 5 000-item run does not replay 5 000 events.
  The browser hook resyncs from the snapshot on every reconnect.
- **Found and fixed: the Store was not thread-safe.** One SQLite connection shared with
  `check_same_thread=False` while the writer ran in `asyncio.to_thread` and the loop
  did bookkeeping. The interpreter segfaulted the first time two overlapped. Every
  Store method now takes a re-entrant lock. This predates the web UI — the CLI had the
  same race with a narrower window.
- **`pytest-timeout`** is now a dev dependency with a 60 s default: the crash above
  first presented as a run that never returned.

### Phase 3 — done

Shipped: `PUT /api/v1/config` and `POST /api/v1/config/validate`, `etag` on `GET`, and
the editor on the Config page.

Decisions taken, one of them a deliberate departure from this document:

- **No RJSF.** The plan was to generate the form from the JSON Schema. For this schema
  that produces a generic "add a key" widget for `defaults` and `replace`, and a wall of
  checkboxes for a library — correct, and the opposite of intuitive. The editor is
  hand-built instead: segmented controls for the choices that are really choices (type,
  provider order, genres vs collections), switches for the booleans, chips for the tag
  lists, from→to rows for `replace`. What it *does* take from the schema is every help
  text and enum, via `/api/v1/config/schema`, so the copy cannot drift from the models.
  Validation stays server-side and is the only source of truth; the form never
  re-implements a rule.
- **Live validation, debounced 350 ms**, through the same function `PUT` uses.
  Out-of-order responses are discarded by sequence number. Errors carry pydantic's
  `loc` in the file's own key names, so they land on the right field; model-level ones
  (`clearGenres requires useGenres`) become a banner on the card, root-level ones (a
  duplicate library) appear in the save bar.
- **Comments survive.** `merge_preserving_comments` carries `//` keys over at their
  original position; library entries are matched by name, so reordering or deleting one
  does not shuffle the others' notes. Removed keys stay removed.
- **Atomic write with a `.bak`**, temp file + `os.replace` in the same directory. A v1
  file becomes v2 on its first save, with the v1 original in the backup.
- **ETag / If-Match.** `GET /config` returns a content hash; the UI sends it back on
  `PUT`; a mismatch is a 412 with the current hash, and the UI offers to reload. Without
  the header the write proceeds (scripts and `curl`).
- **Secrets stay in the environment**, read-only in the UI. Putting them in
  `config.json` would move them into a mounted file that this project tells people
  never to commit; putting them in the database would put a Plex token next to run
  history. Either is a real decision for the owner, not a default to slip in — so it is
  parked, explicitly.
- **Unsaved edits are guarded** both ways: `beforeunload` for reloads and closed tabs,
  a confirmation on in-app navigation. A refetch never clobbers a dirty draft; a clean
  draft follows the file when it changes on disk.
- Running jobs keep the config they started with; queued ones pick up the new one.

### Phase 4 — done

Shipped: `GET /libraries/{name}/items`, `GET /search`, `POST`/`DELETE /bindings`,
`POST /libraries/{name}/items/forget`, a poster proxy, and the item browser with its
binding picker.

Decisions taken:

- **Filter in-process, not in Plex.** The filters that matter — failed, not yet run,
  bound — live in our database, so Plex's own paging cannot serve them. The server
  reads the whole library once (a handful of paged requests), caches it for a minute,
  and joins it against `media_state` and `bindings` with one query each. Search and
  paging are then instant, and "Refresh" bypasses the cache.
- **Match provenance is derived per row**: a binding beats a usable GUID, which beats a
  title search. "Usable" means a scheme one of the library's providers resolves
  directly (`GUID_SCHEMES`, read from the provider classes), plus `anidb` for anime
  because the mapping table translates it.
- **Candidates are the ranking `pick_best` already computed.** `rank_candidates` is the
  same scoring, returned whole instead of `[0]`, with poster, synopsis excerpt, score and
  genres pulled from the search payloads the providers were already fetching. AniList's
  query grew `coverImage` and `description`.
- **Posters are proxied.** `/api/v1/plex/thumb?path=` fetches with the server's token and
  only accepts `/library/…` paths, so the token never reaches the browser and the
  endpoint is not an open proxy.
- **A binding invalidates the cached match, in both directions.** Creating one drops the
  automatic result; removing one drops the result that was produced through it. The
  next run re-resolves either way. The browser also offers *forget* on any cached row.
- **Item keys are the pipeline's own** (`media_key`: GUID first, else title + year),
  exposed as-is, so the CLI's `bind` and the UI address the same thing.

### Phase 5 — done

Shipped: `plex_auto_genres/server/auth.py`, the login screen, sign-out, and the bind
check in `serve`.

Decisions taken:

- **Stateless sessions, keyed to both the password and a persisted secret.** The token
  is `v1.<expiry>.<hmac>`; the HMAC key is `pbkdf2(password, salt=secret from the kv
  table)`. Sessions survive restarts, a password change logs everyone out, and the
  database alone cannot forge a cookie. Logout clears the cookie; the token simply
  expires. No session table, no revocation list — one operator does not need them.
- **Bearer for scripts, never Basic.** A `WWW-Authenticate: Basic` challenge makes
  browsers pop their own credentials dialog on a failed `fetch`; the API answers 401
  with a `Bearer` challenge instead and accepts the password as a bearer token.
- **Deny by default, in ASGI.** `AuthMiddleware` guards everything under `/api/` except
  the three auth endpoints; it is a raw ASGI middleware rather than
  `BaseHTTPMiddleware` so server-sent events are not buffered. The SPA shell and its
  assets stay public: they contain nothing, and the login screen lives in the SPA.
- **Two locks on CSRF.** `SameSite=Lax` on the cookie, and unsafe methods are refused
  when the browser reports `Sec-Fetch-Site: cross-site`. No CORS is configured.
- **The bind check is in `serve`, not in the app.** `create_app` is also used by tests
  and by anyone embedding it; the refusal to expose an open API belongs where the
  host is chosen.
- **The container now requires a password** in serve mode (or `PAG_WEB_INSECURE=1`):
  it listens on every interface. The healthcheck sends the bearer header. This is a
  breaking change for nobody — the UI has not been released.
- **401 is handled once**, in `AuthGate`: any API call that comes back 401 dispatches
  an event, the gate refetches the status and shows the login screen. Pages do not
  know about sessions.

### What is deliberately not here

- TLS. Terminate it in a reverse proxy; the cookie's `Secure` flag follows
  `X-Forwarded-Proto` or `PAG_WEB_SECURE_COOKIE`.
- Multiple users, roles, tokens with scopes. One operator, one password.

### Next

Decide where secrets should live if they are ever to be edited from the UI — the
options and their costs are in the phase 3 notes — and only then consider bulk field
editing beyond genres. Both are product decisions before they are engineering ones.
