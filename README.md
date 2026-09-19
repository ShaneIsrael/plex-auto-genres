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

```bash
mkdir -p plex-auto-genres/{config,logs} && cd plex-auto-genres
curl -o config/config.json https://raw.githubusercontent.com/Dim145/plex-auto-genres/master/config/config.json.example
$EDITOR config/config.json     # library names must match Plex exactly
```

```bash
docker run --rm -v "$PWD/config:/config" -v "$PWD/logs:/logs" -e PLEX_BASE_URL="http://192.168.1.10:32400" -e PLEX_TOKEN="xxxx" ghcr.io/dim145/plex-auto-genres doctor
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

Nothing is required: a v1 `config.json` is migrated in memory at load, the old
`logs/plex-*-*.txt` progress files are imported into the database on first run, and
`python plex-auto-genres.py --library X --type anime` still works.

To convert the file on disk:

```bash
plex-auto-genres migrate-config --out config/config.json
```

Then run `plex-auto-genres doctor` — it will flag config entries that no longer match
anything, including MAL genres that were renamed (`Cars` → `Racing`,
`Shoujo Ai` → `Girls Love`, `Thriller` → `Suspense`, …).

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

The layout is deliberately service-shaped rather than CLI-shaped, because a web UI is
planned:

```
plex_auto_genres/
  config.py      pydantic models -> validation and a JSON Schema for forms
  store.py       SQLite: cache, manual bindings, undo snapshots, run history
  pipeline.py    async orchestration, one library per run
  providers/     tmdb, jikan, anilist + the AniDB->MAL id mapping
  plexsvc/       reading libraries and writing tags back
  cli.py         argparse front end over the above
```

## Licence

MIT. Poster artwork from the upstream project.
