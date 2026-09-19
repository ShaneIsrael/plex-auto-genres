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
