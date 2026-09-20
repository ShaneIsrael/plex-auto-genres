# plex-auto-genres

Tags your Plex media with genres from **TMDB**, **MyAnimeList** (Jikan) or **AniList** —
as Plex genre tags or as collections.

> **v2 rewrite.** Fork of the upstream [ShaneIsrael/plex-auto-genres](https://github.com/ShaneIsrael/plex-auto-genres),
> which has been unmaintained since December 2022. v2 fixes several silent data bugs,
> is roughly an order of magnitude faster, and every run is reversible.
> Your existing `config.json` and `logs/` still work — see [Upgrading](#upgrading-from-v1).

---

## What it does

| | |
|---|---|
| **Genres from three sources** | TMDB for films and TV, Jikan and AniList for anime. Providers are tried in order until one answers. |
| **Uses the ids Plex already has** | Reads `tmdb://`, `mal://`, `anidb://`… straight off the Plex item, so no title search and no wrong match. |
| **Genre tags or collections** | Per library. |
| **Reversible** | Every write is snapshotted. `plex-auto-genres undo <run-id>` puts it back. |
| **Manual binding** | Pin an item to an exact provider id when the automatic match is wrong. |
| **Rating collections & posters** | `1–5 Star Rating` collections, collection artwork, sort-title prefixes. |
| **Built-in scheduler** | No cron inside the container. |

---

## Quick start

### Docker (recommended)

The image is published by CI as `ghcr.io/<github-user>/plex-auto-genres`; `OWNER`
below stands for that account.

```bash
mkdir -p plex-auto-genres/{config,logs} && cd plex-auto-genres
cp /path/to/checkout/config/config.json.example config/config.json
$EDITOR config/config.json     # library names must match Plex exactly
```

```bash
docker run --rm -v "$PWD/config:/config" -v "$PWD/logs:/logs" -e PLEX_BASE_URL="http://192.168.1.10:32400" -e PLEX_TOKEN="xxxx" ghcr.io/OWNER/plex-auto-genres doctor
```

Once `doctor` is happy, use the [compose file](docker/docker-compose.yml):

```bash
docker compose up -d
```

The image is published for **linux/amd64 and linux/arm64** (Raspberry Pi, ARM Synology,
Apple Silicon).

### Locally

```bash
pip install -e .
cp .env.example .env && $EDITOR .env
plex-auto-genres doctor
```

---

## Web UI

`plex-auto-genres serve` hosts the console on http://127.0.0.1:8095 — overview, run
history with the last-forty-runs tape, libraries with coverage, manual bindings, and the
config with the doctor checks. From there you can **start a run** for one library or all
of them (normal, dry, or forced), **watch it live**, **cancel** it, and **undo** a
finished tag, rating or sort run. The Docker image runs the console whenever a login is
configured and keeps the nightly scheduler in the same process; scheduled passes show up
as jobs like any other.

Jobs run one at a time, in the order they were queued: the provider rate limits live
inside each run, so two at once would trip 429s and gain nothing.

```bash
plex-auto-genres serve --cron "0 1 * * *"      # UI + API + scheduler
plex-auto-genres serve --host 0.0.0.0           # reachable from the LAN
```

| Env (container) | Default | Purpose |
|---|---|---|
| `PAG_MODE` | auto | `serve` = UI + scheduler; `schedule` = headless, as v1. Unset: `serve` when `PAG_WEB_PASSWORD` or `PAG_WEB_INSECURE` is set, else `schedule` |
| `PAG_WEB_PORT` | `8095` | Port the UI listens on |
| `PAG_WEB_PASSWORD` | — | Login password. Required unless the bind is loopback or `PAG_WEB_INSECURE=1` |
| `PAG_WEB_INSECURE` | — | `1` to run without a login on a network you trust |
| `PAG_WEB_SESSION_DAYS` | `30` | Session lifetime |
| `PAG_WEB_SECURE_COOKIE` | auto | Force the cookie's `Secure` flag (behind an https proxy) |
| `PAG_WEB_TRUSTED_PROXIES` | — | Comma-separated proxy addresses whose `X-Forwarded-For` is believed for the login rate limit |
| `CRON_SCHEDULE` | `0 1 * * *` | Fallback schedule. The `schedule` block in `config.json` — editable on the Config page, where it can also be paused — takes precedence |

The API is documented at `/api/docs` once signed in.

The **Config** page edits `config.json` itself: libraries (type, providers, what to
write, post-actions, per-library overrides), the per-type defaults, and the **schedule**
of the automatic pass — a preset or a cron expression checked live, with a switch to
pause it without losing the expression; the running scheduler picks the change up as
soon as it is saved. The server validates every keystroke. Saving writes the file atomically, keeps the previous one as
`config.json.bak`, preserves any `//` comments you wrote by hand, and refuses to overwrite
a file that changed on disk since you loaded it. Credentials stay in the environment and
are shown read-only.

Each library has an **item browser**: every title with how it matched (a manual binding,
the id Plex already had, or a title search), what was written, and why it failed if it
did. From there you can search a provider for the right record — ranked candidates with
posters and synopses — and **bind** the item to it, or type an id straight in. Bindings
can also be removed, and a cached result forgotten so the next run retries it.

### Security

The console asks for a password: set `PAG_WEB_PASSWORD`. Sessions are an `HttpOnly`,
`SameSite=Lax` cookie that survives restarts and is invalidated by changing the password.
Scripts send the same password as a bearer token:

```bash
curl -H "Authorization: Bearer $PAG_WEB_PASSWORD" http://127.0.0.1:8095/api/v1/health
```

`serve` **refuses to listen on anything but loopback without a password**; on a private
network you trust, `PAG_WEB_INSECURE=1` overrides that, loudly. Failed logins are limited
to five a minute per client and fifty a minute overall. The client is the connecting
peer: `X-Forwarded-For` is only believed when the peer is listed in
`PAG_WEB_TRUSTED_PROXIES`, since anyone can send that header. Behind a TLS-terminating
reverse proxy, set `PAG_WEB_SECURE_COOKIE=1`, forward `X-Forwarded-Proto` so the cookie
is marked `Secure`, and list the proxy in `PAG_WEB_TRUSTED_PROXIES`. There are no user
accounts, on purpose; there is one operator.

Everything the UI does is plain HTTP — `POST /api/v1/libraries/{name}/run`,
`GET /api/v1/jobs/{id}/events` (server-sent events), `POST /api/v1/jobs/{id}/cancel`,
`POST /api/v1/runs/{id}/undo`, `PUT /api/v1/config` with `If-Match`,
`GET /api/v1/libraries/{name}/items`, `GET /api/v1/search`, `POST`/`DELETE /api/v1/bindings`
— so it scripts as easily as the CLI.

---

## Commands

```bash
plex-auto-genres run                              # every enabled library
plex-auto-genres run --library Animes --dry       # preview, writes nothing
plex-auto-genres run --library Animes --force     # ignore the cache
plex-auto-genres run --only posters --only sort   # just those actions

plex-auto-genres query "Cowboy Bebop" --type anime
plex-auto-genres doctor                           # validate config + credentials
plex-auto-genres runs                             # run history
plex-auto-genres failures --library Animes
plex-auto-genres undo 4f2a1c9b0e77                # restore a run's previous tags

plex-auto-genres bind Animes "Monster" mal 19     # pin a provider id
plex-auto-genres bindings
plex-auto-genres schema                           # config JSON Schema
```

`--dry` is honoured by **every** action, and `--json` makes any command emit
machine-readable output.

### When a match is wrong

```bash
plex-auto-genres failures --library Animes        # see what could not be resolved
plex-auto-genres query "Monster" --type anime     # find the right id
plex-auto-genres bind Animes "Monster" mal 19     # pin it; clears the cached match
plex-auto-genres run --library Animes
```

---

## Configuration

Two files: **`.env`** holds credentials, **`config/config.json`** holds behaviour.
Start from [`config/config.json.example`](config/config.json.example), which documents
itself with `//` keys (they are ignored on load).

Genre rules are defined per **type** under `defaults`, and any library can override them:

```jsonc
{
  "version": 2,
  "defaults": {
    "standard-tv": { "replace": { "sci-fi": "Science Fiction" } }
  },
  "libraries": [
    { "library": "TV Shows",  "type": "standard-tv", "useGenres": true },
    { "library": "Kids TV",   "type": "standard-tv", "useGenres": true,
      "overrides": { "ignore": ["Horror"] } }
  ]
}
```

That override layer is new in v2. In v1, rules were keyed on media *type* alone, so two
libraries of the same type could not be configured separately — and, worse, shared one
progress file.

`plex-auto-genres schema` prints the JSON Schema, so editors can autocomplete it.

### Environment

| Variable | Purpose |
|---|---|
| `PLEX_BASE_URL`, `PLEX_TOKEN` | Preferred auth. [Finding your token](https://support.plex.tv/articles/204059436) |
| `PLEX_USERNAME`, `PLEX_PASSWORD`, `PLEX_SERVER_NAME` | Legacy auth, used only if no token |
| `PLEX_COLLECTION_PREFIX` | Prepended to every tag this tool writes |
| `TMDB_API_KEY` | Required for `standard-tv` / `standard-movie` |
| `PAG_CONCURRENCY` | Parallel provider lookups (default 4) |
| `CRON_SCHEDULE`, `RUN_ON_START`, `TZ` | Container scheduling |

---

## Upgrading from v1

Pull the new image (or `pip install -U`) and keep your volumes and environment as they
are. The first start does the rest and narrates it in the console:

```
Config /config/config.json was in the v1 layout: converted to v2. The original is kept as /config/config.json.v1.
  library 'Animes' (anime): genre field, replace existing genres, ratings
  library 'Films' (standard-movie): collections, sort
  defaults for anime: 1 ignored, 3 replaced, sort prefix '*', 4 sorted collections
Imported 812 v1 progress entries for 'Animes' from plex-anime-successful.txt and plex-anime-failures.txt
Renamed plex-anime-successful.txt to plex-anime-successful.txt.imported (kept as a backup; v2 keeps its state in the database)
v1 files not needed by v2, left untouched: plex-anime-ratings-progress.txt, plex-auto-genres-automate.log
```

What that means, step by step:

- **`config.json`** is rewritten in the v2 layout; the v1 file stays next to it as
  `config.json.v1` (a second upgrade never overwrites it). v1's per-*type* rules become
  the type defaults, so behaviour is unchanged; per-library overrides are opt-in.
- **`logs/plex-<type>-*.txt`** progress files are imported into `logs/plex-auto-genres.db`
  for every configured library of that type, then renamed `*.imported`. Items keep
  their "done" status; as each library is read they are re-keyed by Plex GUID so a
  renamed file no longer orphans them. The rating and rating-collection progress files
  are not needed (v2 derives both from its cache) and are left where they are.
- **Environment**: every v1 variable keeps its name and meaning (`PLEX_USERNAME` /
  `PLEX_PASSWORD` / `PLEX_SERVER_NAME`, or `PLEX_BASE_URL` / `PLEX_TOKEN`, `TMDB_API_KEY`,
  `PLEX_COLLECTION_PREFIX`). Everything new is optional: `PAG_WEB_PASSWORD` turns the web
  UI on, `CRON_SCHEDULE` / `TZ` / `RUN_ON_START` tune the nightly pass, `PUID` / `PGID`
  pick the user the app runs as.
- **Ownership**: the v1 image ran as root; this one runs as `PUID:PGID` (default
  `1000:1000`) and, on start, hands the mounted `/config`, `/logs` and `/posters` to that
  user. `PUID=0` keeps everything as root.
- **Mode**: with no `PAG_WEB_PASSWORD` the container runs headless on its schedule,
  exactly as v1 did. Set the password (or `PAG_MODE=serve` with `PAG_WEB_INSECURE=1`) to
  get the console on port 8095.

`plex-auto-genres doctor` on a v1 file says so before anything is touched, and after the
upgrade flags config entries that no longer match anything, including MAL genres that
were renamed (`Cars` → `Racing`, `Shoujo Ai` → `Girls Love`, `Thriller` → `Suspense`, …).

**Rolling back** is renaming: `config.json.v1` back to `config.json` and the
`*.imported` files back to their names; the database can stay. From the command line,
`python plex-auto-genres.py --library X --type anime` still works, config file or not,
and `plex-auto-genres migrate-config --out …` converts a file without running anything.

### What changed, and why it matters

| v1 behaviour | v2 |
|---|---|
| `for genre in genres: media.addGenre(genre)` — each write rebuilt the tag list from a stale cache, so **only the last genre survived**, at one HTTP request each | One request per item, all genres kept |
| `clearGenres: true` called `editTags('genre', [])`, which re-sent the existing tags — **it cleared nothing** | Actually replaces the tag set |
| `--rate-anime` passed a *string* to `rate()`, which rejects non-numerics — **it aborted on the first item** | Ratings are sent as floats |
| `"Sci-Fi & Fantasy"` was split and only the first half kept, silently **discarding Fantasy, Adventure and Politics** | Both halves are kept |
| `--use-keywords` read `.results` for movies too, where the field is `.keywords` — **`AttributeError` on every film** | Correct per media type |
| Progress keyed on media *type*, so two libraries of one type shared and poisoned a cache | Keyed on library + item GUID + a settings fingerprint |
| A failure blacklisted a title permanently unless `--force` wiped everything | Retried with exponential backoff |
| Searched by title and took `results[0]` | Uses the item's Plex GUID; falls back to a title + year search |
| A blind `sleep(4)` twice per anime — 8 s of dead time per title | Token buckets at the providers' real limits, requests in parallel |
| Destructive and irreversible | Every run snapshotted and undoable |
| `--dry` only honoured in one code path | Honoured everywhere |
| amd64 only, 571 MB image, `gcc`/`g++` pulled in for numpy — which concatenated a list of 9 strings | amd64 + arm64, 97 MB, no compilers |

---

## Development

```bash
pip install -r requirements-dev.txt && pip install -e .
pytest -q
pylint plex_auto_genres
```

The UI is a Vite + React + TypeScript app in `ui/`:

```bash
pnpm --dir ui install
pnpm --dir ui dev          # http://localhost:5173, proxies /api to :8095
pnpm --dir ui build        # emits into plex_auto_genres/server/static/
```

Run `plex-auto-genres serve` alongside `pnpm dev` for live reload. The Docker build
compiles the UI in its own stage, so node never enters the runtime image. Design notes:
[docs/design-system.md](docs/design-system.md).

### Demo stack

To try the console without a Plex server or any API key:

```bash
pnpm --dir ui build && python dev/demo.py
```

That is the real server, job manager and pipeline on top of an in-memory fake Plex
("Home Media Server": five libraries, several hundred items with GUIDs, genres, ratings and
posters) and fake metadata providers that answer in about a third of a second and miss a
few titles on purpose. It comes seeded with weeks of run history, a partly processed
cache, two bindings and a config with comments. Open http://127.0.0.1:8095, password
`demo`. Runs, cancels, undos, bindings and config edits all work and persist in
`dev/.demo/` (git-ignored); `--reset` starts over, `--port` / `--password` do what they say.
Nothing in the demo touches the network.

The layout is deliberately service-shaped rather than CLI-shaped, because a web UI is
planned:

```
plex_auto_genres/
  config.py      pydantic models -> validation and a JSON Schema for forms
  store.py       SQLite: cache, manual bindings, undo snapshots, run history
  pipeline.py    async orchestration, one library per run
  providers/     tmdb, jikan, anilist + the AniDB->MAL id mapping
  plexsvc/       reading libraries and writing tags back
  server/        FastAPI: /api/v1 read-only routes, SPA hosting, in-process scheduler
  runner.py      run libraries end to end; shared by the CLI and the server
  cli.py         argparse front end over the above
ui/              Vite + React + TypeScript console
```

## Licence

MIT. Poster artwork from the upstream project.
