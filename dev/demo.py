#!/usr/bin/env python3
"""A self-contained demo stack for trying the web console by hand.

The real server, job manager, store and pipeline run on top of an in-memory
fake Plex (five libraries, hundreds of items you can tag, sort and rate) and
slowed-down fake metadata providers, so every screen has something to show
and nothing here ever touches the network::

    python dev/demo.py            # http://127.0.0.1:8095, password "demo"
    python dev/demo.py --reset    # throw the demo data away and reseed
    python dev/demo.py --port 9000 --password s3cret

Data lives in ``dev/.demo/`` (git-ignored): the config the Config page edits,
the SQLite state with seeded history, and a posters directory. Build the UI
first (``pnpm --dir ui build``) or the server will tell you to.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import shutil
import struct
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = Path(__file__).resolve().parent / ".demo"
PORT = 8095

# Every value the pipeline needs is fake; the doctor's checks want them set.
os.environ.setdefault("PLEX_BASE_URL", "http://plex.demo.invalid:32400")
os.environ.setdefault("PLEX_TOKEN", "demo-token")
os.environ.setdefault("TMDB_API_KEY", "demo-key")
os.environ.setdefault("PAG_CONCURRENCY", "3")

# Fictional titles: an adjective, a noun, sometimes a number. No real works,
# no real people.
ADJECTIVES = ["Silent", "Crimson", "Hollow", "Paper", "Iron", "Velvet", "Lunar", "Wandering",
              "Glass", "Northern", "Last", "Electric", "Quiet", "Golden", "Broken", "Endless",
              "Painted", "Distant", "Little", "Wild"]
NOUNS = ["Harbor", "Orbit", "Garden", "Signal", "Kingdom", "Archive", "Meadow", "Circuit",
         "Lantern", "Compass", "Station", "Theatre", "Summer", "Voyage", "Machine", "Tide",
         "Mountain", "Letter", "Chorus", "Frontier"]
ANIME_GENRES = ["Action", "Adventure", "Comedy", "Drama", "Fantasy", "Mystery", "Sci-Fi",
                "Slice of Life", "Supernatural", "Romance", "Mecha", "Isekai", "Sports", "Kids"]
TMDB_GENRES = ["Action & Adventure", "Animation", "Comedy", "Crime", "Documentary", "Drama",
               "Family", "Sci-Fi & Fantasy", "Mystery", "Western", "War & Politics", "Talk"]
KEYWORDS = ["time travel", "space opera", "heist", "coming of age", "dystopia", "small town",
            "road trip", "robot", "ghost", "school", "detective", "island"]
RATING_BUCKETS = ["1 Star Rating", "2 Star Rating", "3 Star Rating", "4 Star Rating", "5 Star Rating"]


def title_for(seed: int) -> str:
    rng = random.Random(seed)
    name = f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS)}"
    return f"{name} {rng.randint(2, 9)}" if rng.random() < 0.25 else name


# ---------------------------------------------------------------------------
# Fake Plex
# ---------------------------------------------------------------------------


class Tag:
    def __init__(self, tag: str) -> None:
        self.tag = tag


class FieldLock:
    def __init__(self, name: str, locked: bool) -> None:
        self.name, self.locked = name, locked


class Guid:
    def __init__(self, id_: str) -> None:
        self.id = id_


class Item:
    """One library entry. ``edit``/``rate`` change it in place, so a run's
    writes are visible on the next read, exactly as with a real server."""

    def __init__(self, key: int, title: str, year: int, guids: list[str], genres: list[str],
                 collections: list[str], rating: float | None) -> None:
        self.ratingKey, self.title, self.year = key, title, year
        self.guid = f"plex://item/{key}"
        self.guids = [Guid(g) for g in guids]
        self.genres = [Tag(g) for g in genres]
        self.collections = [Tag(c) for c in collections]
        self.rating = rating            # the agent's rating
        self.audienceRating = None
        self.userRating: float | None = None
        self.titleSort = title
        self.thumb = f"/library/metadata/{key}/thumb/1"
        self._locked: dict[str, bool] = {}
        self.edits: list[dict] = []

    @property
    def fields(self):
        return [FieldLock(name, locked) for name, locked in self._locked.items()]

    def edit(self, **kwargs):
        self.edits.append(kwargs)
        for field, attr in (("genre", "genres"), ("collection", "collections")):
            indexed = sorted(
                (int(k.split("[")[1].split("]")[0]), v)
                for k, v in kwargs.items()
                if k.startswith(f"{field}[") and "].tag.tag" in k and not k.endswith("-")
            )
            if indexed:
                setattr(self, attr, [Tag(v) for _, v in indexed])
            if f"{field}.locked" in kwargs:
                self._locked[field] = bool(int(kwargs[f"{field}.locked"]))
        if "titleSort.value" in kwargs:
            self.titleSort = kwargs["titleSort.value"]
        return self

    def rate(self, rating=None):
        self.userRating = None if rating is None or rating < 0 else float(rating)
        return self


class Collection:
    def __init__(self, key: int, title: str) -> None:
        self.ratingKey, self.title, self.titleSort = key, title, title
        self.posters: list[str] = []

    def edit(self, **kwargs):
        if "titleSort.value" in kwargs:
            self.titleSort = kwargs["titleSort.value"]
        return self

    def uploadPoster(self, filepath=None, url=None):
        self.posters.append(str(filepath or url))


class Section:
    def __init__(self, key: int, title: str, type_: str, agent: str | None, items: list[Item],
                 collections: list[Collection]) -> None:
        self.key, self.title, self.type, self.agent = key, title, type_, agent
        self._items, self._collections = items, collections
        self.totalSize = len(items)

    def all(self):
        return list(self._items)

    def collections(self):
        return list(self._collections)


class Library:
    def __init__(self, sections: list[Section]) -> None:
        self._sections = sections

    def sections(self):
        return list(self._sections)

    def section(self, name: str) -> Section:
        for section in self._sections:
            if section.title.casefold() == name.casefold():
                return section
        from plexapi.exceptions import NotFound

        raise NotFound(name)


class FakePlex:
    friendlyName = "Home Media Server"
    version = "1.41.9.9961"

    def __init__(self, sections: list[Section], base_url: str) -> None:
        self.library = Library(sections)
        self._base_url = base_url
        self._by_key = {}
        for section in sections:
            for item in section.all():
                self._by_key[item.ratingKey] = item
            for collection in section.collections():
                self._by_key[collection.ratingKey] = collection

    def fetchItems(self, ekey: str, params=None, container_size=None, **_kw):
        key = int(ekey.split("/sections/")[1].split("/")[0])
        return next(s for s in self.library.sections() if s.key == key).all()

    def fetchItem(self, rating_key):
        return self._by_key[int(rating_key)]

    def query(self, path: str):
        return None  # always alive

    def url(self, path: str, includeToken: bool = False) -> str:
        # Posters come from the demo server itself (see poster_route).
        return f"{self._base_url}/demo/poster?path={path}"


def build_sections() -> list[Section]:
    rng = random.Random(1)
    sections: list[Section] = []
    specs = [
        # key, title, type, agent, count, guid scheme(s), genre pool
        (1, "Animes", "show", "com.plexapp.agents.hama", 120, ("anidb", "mal", None), ANIME_GENRES),
        (2, "Cartoons", "show", "tv.plex.agents.series", 60, ("tmdb", "tvdb"), TMDB_GENRES),
        (3, "Anime Films", "show", "tv.plex.agents.series", 40, ("tmdb", None), TMDB_GENRES),
        (4, "Films", "movie", "tv.plex.agents.movie", 300, ("tmdb", "imdb"), TMDB_GENRES),
        (5, "TV Shows", "show", "tv.plex.agents.series", 180, ("tmdb",), TMDB_GENRES),
    ]
    for key, title, type_, agent, count, schemes, pool in specs:
        items: list[Item] = []
        for i in range(count):
            rating_key = key * 1000 + i
            scheme = rng.choice(schemes)
            guids = [f"{scheme}://{rating_key}"] if scheme else []
            if scheme == "anidb" and rng.random() < 0.5:
                guids.append(f"mal://{rating_key}")     # HAMA often carries both
            genres = rng.sample(pool, rng.randint(0, 3))
            collections = rng.sample(RATING_BUCKETS, 1) if rng.random() < 0.2 else []
            rating = round(rng.uniform(4.5, 9.2), 1) if rng.random() < 0.6 else None
            items.append(Item(rating_key, title_for(rating_key), rng.randint(1994, 2025), guids,
                              genres, collections, rating))
        collections = [Collection(key * 1000 + 900 + n, name)
                       for n, name in enumerate(["Action", "Comedy", "Drama", "Adventure", *RATING_BUCKETS])]
        sections.append(Section(key, title, type_, agent, items, collections))
    sections.append(Section(6, "Photos", "photo", None, [], []))
    return sections


# ---------------------------------------------------------------------------
# Fake providers: deterministic per title, slow enough to watch, a few misses
# ---------------------------------------------------------------------------


def install_fake_providers() -> None:
    from plex_auto_genres.errors import ProviderNotFound, ProviderRateLimited
    from plex_auto_genres.models import Candidate, ProviderResult
    from plex_auto_genres.providers import anilist, jikan, tmdb

    def rng_for(text: str) -> random.Random:
        return random.Random(int(hashlib.sha256(text.encode()).hexdigest()[:8], 16))

    def genres_for(name: str, title: str, keywords: bool) -> list[str]:
        rng = rng_for(f"{name}:{title}")
        pool = KEYWORDS if keywords else (ANIME_GENRES if name != "tmdb" else TMDB_GENRES)
        return rng.sample(pool, rng.randint(1, 4 if not keywords else 9))

    async def fake_resolve(self, request):
        await asyncio.sleep(random.uniform(0.12, 0.35))
        self.transport.request_count += 1
        rng = rng_for(f"{self.name}:{request.title}")
        if request.pinned is not None:
            source, provider_id = "binding", request.pinned.value
        elif request.id_for(*self.guid_schemes) is not None:
            source, provider_id = "guid", request.id_for(*self.guid_schemes).value
        else:
            if rng.random() < 0.08:
                raise ProviderNotFound(f"{self.name}: no match for {request.title!r}")
            source, provider_id = "search", str(rng.randint(1000, 99999))
        if random.random() < 0.01:
            raise ProviderRateLimited(f"{self.name}: rate limited", retry_after=2.0)
        result = ProviderResult(
            provider=self.name, provider_id=provider_id, title=request.title,
            genres=genres_for(self.name, request.title, request.use_keywords),
            score=round(rng.uniform(5.0, 9.4), 2),
            url=f"https://example.invalid/{self.name}/{provider_id}",
        )
        result.matched_by = source
        return result

    async def fake_candidates(self, request, limit: int = 8):
        await asyncio.sleep(random.uniform(0.2, 0.5))
        self.transport.request_count += 1
        rng = rng_for(f"cand:{self.name}:{request.title}")
        out = []
        for n in range(rng.randint(2, min(limit, 6))):
            provider_id = str(rng.randint(1000, 99999))
            out.append(Candidate(
                provider=self.name, provider_id=provider_id,
                title=request.title if n == 0 else f"{request.title} {rng.choice(['II', 'Movie', 'Remake', 'Zero'])}",
                year=(request.year or 2010) + (0 if n == 0 else rng.randint(-8, 8)),
                url=f"https://example.invalid/{self.name}/{provider_id}",
                image=f"{BASE_URL}/demo/poster?path=/candidate/{provider_id}",
                synopsis=f"A {rng.choice(ADJECTIVES).lower()} story about a {rng.choice(NOUNS).lower()}.",
                score=round(rng.uniform(5.0, 9.4), 1),
                genres=genres_for(self.name, request.title, False),
            ))
        return out

    for cls in (jikan.JikanProvider, anilist.AniListProvider, tmdb.TmdbProvider):
        cls.resolve = fake_resolve
        cls.search_candidates = fake_candidates


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

CONFIG_TEXT = """{
    "// about": "Demo configuration. Edit it here or from the Config page; comments survive.",
    "version": 2,
    "defaults": {
        "anime": {
            "ignore": ["Kids"],
            "replace": {"sci-fi": "Science Fiction", "cars": "Racing"},
            "sortedPrefix": "*",
            "sortedCollections": ["action", "adventure", "comedy", "drama"]
        },
        "standard-tv": {
            "replace": {"sci-fi": "Science Fiction"},
            "sortedPrefix": "*",
            "sortedCollections": ["action", "drama"]
        },
        "standard-movie": {"ignore": ["Talk"], "sortedPrefix": "*"}
    },
    "libraries": [
        {"library": "Animes", "type": "anime", "useGenres": true, "clearGenres": true,
         "providers": ["jikan", "anilist"], "rateAnime": true, "createRatingCollections": true,
         "sortCollections": true},
        {"// note": "Collections rather than the genre field, with a per-library override.",
         "library": "Cartoons", "type": "standard-tv", "sortCollections": true, "setPosters": true,
         "overrides": {"ignore": ["Talk"]}},
        {"library": "Anime Films", "type": "standard-tv", "useGenres": true, "clearGenres": true,
         "useKeywords": true, "overrides": {"maxGenres": 8}},
        {"library": "Films", "type": "standard-movie", "enabled": false},
        {"library": "TV Shows", "type": "standard-tv"}
    ]
}
"""


def write_png(path: Path, rgb: tuple[int, int, int], size: int = 8) -> None:
    """A tiny solid PNG, enough for the posters action to have files to upload."""
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def seed(data: Path, sections: list[Section]) -> None:
    from plex_auto_genres.config import load_config
    from plex_auto_genres.models import RunReport
    from plex_auto_genres.pipeline import media_key
    from plex_auto_genres.plexsvc.client import iter_library
    from plex_auto_genres.store import Store

    (data / "config").mkdir(parents=True, exist_ok=True)
    (data / "logs").mkdir(exist_ok=True)
    (data / "config" / "config.json").write_text(CONFIG_TEXT, encoding="utf-8")
    for type_, names in (("standard-tv", ["action", "drama"]), ("anime", ["action", "comedy"])):
        folder = data / "posters" / type_
        folder.mkdir(parents=True, exist_ok=True)
        for n, name in enumerate(names):
            write_png(folder / f"{name}.png", (220, 120 + 40 * n, 50))

    config = load_config(data / "config" / "config.json")
    rng = random.Random(2)
    now = time.time()
    plex = FakePlex(sections, BASE_URL)

    with Store(data / "logs" / "state.db") as store:
        # Offline caches, so neither doctor nor the AniDB mapper touch the network.
        store.kv_set("mal_taxonomy_v1", json.dumps(sorted(ANIME_GENRES + ["Racing", "Suspense"])),
                     30 * 86400)
        anime = sections[0]
        store.kv_set("anidb_mapping_v1", json.dumps({
            str(i.ratingKey): {"mal": str(i.ratingKey)} for i in anime.all()
            if any(g.id.startswith("anidb://") for g in i.guids)
        }), 7 * 86400)

        # A previous life: most of two libraries already processed, a few failures.
        for run in config.libraries[:2]:
            store.kv_set(f"legacy_imported::{run.library}", "1")
            fingerprint = config.fingerprint(run)
            items = iter_library(plex, run.library)
            for item in items:
                roll = rng.random()
                if roll < 0.65:
                    genres = rng.sample(ANIME_GENRES if run.type.is_anime else TMDB_GENRES, rng.randint(1, 3))
                    provider = run.resolved_providers[0]
                    store.record_success(
                        run.library, media_key(item), fingerprint=fingerprint, title=item.title,
                        year=item.year, rating_key=item.rating_key, genres=genres,
                        provider=provider, provider_id=str(rng.randint(1000, 99999)),
                        score=round(rng.uniform(5.0, 9.4), 2),
                        source="guid" if item.guids else "search",
                    )
                    # Make the fake library agree with what was "written".
                    handle = item.handle
                    if run.use_genres:
                        handle.genres = [Tag(g) for g in genres]
                    else:
                        handle.collections = [Tag(g) for g in genres]
                elif roll < 0.72:
                    store.record_failure(
                        run.library, media_key(item), fingerprint=fingerprint, title=item.title,
                        year=item.year, rating_key=item.rating_key,
                        error=f"{run.resolved_providers[0]}: no match for {item.title!r}",
                    )
            # Backdate the failures so some are due for a retry.
            with store._tx() as conn:  # noqa: SLF001 - demo seeding
                conn.execute("UPDATE media_state SET updated_at = updated_at - ? WHERE status = 'failed'",
                             (2 * 86400,))

        store.set_binding("Animes", media_key(iter_library(plex, "Animes")[3]), "mal", "20",
                          note="the automatic match picked the wrong season")
        store.set_binding("Cartoons", media_key(iter_library(plex, "Cartoons")[5]), "tmdb", "1399",
                          note="pinned by hand")

        # Run history: a few weeks of nightly passes with the occasional problem.
        libraries = [r.library for r in config.libraries if r.enabled]
        for i in range(42):
            library = rng.choice(libraries)
            action = rng.choices(["genres", "collections", "ratings", "sort", "posters",
                                  "rating-collections"], [6, 3, 2, 1, 1, 1])[0]
            dry = i % 13 == 0
            run_id = store.start_run(library, action, dry_run=dry)
            started = now - (42 - i) * 3600 * rng.uniform(5, 12)
            kind = rng.choices(["ok", "partial", "failed", "undone", "interrupted", "cancelled", "error"],
                               [12, 3, 1, 1, 1, 1, 1])[0]
            written = rng.randint(0, 40) if kind not in ("failed", "error") else 0
            failed = {"partial": rng.randint(1, 6), "failed": rng.randint(3, 12)}.get(kind, 0)
            if kind != "interrupted":
                report = RunReport(
                    run_id=run_id, library=library, action=action, dry_run=dry, written=written,
                    unchanged=rng.randint(10, 200), skipped=rng.randint(0, 150), failed=failed,
                    plex_requests=written, provider_requests=written * 2 + failed,
                    duration_s=rng.uniform(4, 190),
                    failures=[(title_for(9000 + k), "jikan: no match for the title")
                              for k in range(min(failed, 5))],
                    cancelled=(kind == "cancelled"),
                    error="PlexConnectionError: Plex stopped answering" if kind == "error" else None,
                )
                store.finish_run(report)
            if kind == "undone":
                store.mark_undone(run_id)
            with store._tx() as conn:  # noqa: SLF001 - demo seeding
                conn.execute(
                    "UPDATE runs SET started_at = ?, finished_at = CASE WHEN finished_at IS NULL "
                    "THEN NULL ELSE ? END, undone_at = CASE WHEN undone_at IS NULL THEN NULL "
                    "ELSE ? END WHERE run_id = ?",
                    (started, started + rng.uniform(4, 190), started + 3600, run_id),
                )


# ---------------------------------------------------------------------------
# Posters served by the demo itself
# ---------------------------------------------------------------------------


def poster_route(app) -> None:
    """``GET /demo/poster?path=...`` -> an SVG poster derived from the path."""
    from starlette.responses import Response
    from starlette.routing import Route

    async def poster(request):
        path = request.query_params.get("path", "")
        rng = random.Random(path)
        hue = rng.randint(0, 359)
        label = path.rsplit("/", 2)[-2] if "/thumb/" in path else path.rsplit("/", 1)[-1]
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="270" viewBox="0 0 180 270">'
            f'<rect width="180" height="270" fill="hsl({hue} 45% 28%)"/>'
            f'<circle cx="{rng.randint(30, 150)}" cy="{rng.randint(40, 230)}" r="{rng.randint(30, 70)}" '
            f'fill="hsl({(hue + 40) % 360} 60% 55%)" opacity="0.7"/>'
            f'<text x="12" y="250" font-family="monospace" font-size="16" fill="#fff" opacity="0.9">#{label}</text>'
            "</svg>"
        )
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})

    # Ahead of the SPA catch-all, which would otherwise swallow the path.
    app.router.routes.insert(0, Route("/demo/poster", poster))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

BASE_URL = f"http://127.0.0.1:{PORT}"


def main() -> int:
    global BASE_URL, PORT  # noqa: PLW0603 - simple script

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--password", default=os.getenv("PAG_WEB_PASSWORD") or "demo",
                        help='Login password (default "demo"; empty string disables the login).')
    parser.add_argument("--data", type=Path, default=DATA, help="Where the demo state lives.")
    parser.add_argument("--reset", action="store_true", help="Discard the demo state and reseed.")
    args = parser.parse_args()

    PORT = args.port
    BASE_URL = f"http://{args.host}:{args.port}"

    import uvicorn

    from plex_auto_genres.server import create_app
    from plex_auto_genres.server import state as state_module
    from plex_auto_genres.server.auth import AuthSettings

    sections = build_sections()
    if args.reset and args.data.exists():
        shutil.rmtree(args.data)
    fresh = not (args.data / "logs" / "state.db").exists()
    if fresh:
        seed(args.data, sections)

    state_module.plex_client.connect = lambda settings: FakePlex(sections, BASE_URL)
    install_fake_providers()

    static = ROOT / "plex_auto_genres" / "server" / "static"
    if not (static / "index.html").is_file():
        print("The UI is not built: run `pnpm --dir ui build` first (the API still works).", file=sys.stderr)

    app = create_app(
        args.data / "config" / "config.json", args.data / "logs" / "state.db",
        cron="0 3 * * *", posters_dir=args.data / "posters",
        static_dir=static if (static / "index.html").is_file() else None,
        auth=AuthSettings(password=args.password or None, insecure=not args.password),
    )
    poster_route(app)

    print(f"plex-auto-genres demo on {BASE_URL}  (password: {args.password or 'none'};"
          f" data: {args.data}{' - freshly seeded' if fresh else ''})")
    print("Fake Plex 'Home Media Server' with five libraries; providers answer in ~0.3 s and miss ~8%.")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
