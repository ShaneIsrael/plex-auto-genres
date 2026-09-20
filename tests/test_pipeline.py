"""End-to-end pipeline behaviour against a fake Plex server and mocked HTTP."""

from __future__ import annotations

import httpx
import respx

from plex_auto_genres.config import AppConfig
from plex_auto_genres.pipeline import Pipeline, rating_bucket
from plex_auto_genres.store import Store

from .conftest import FakePlexItem


class FakeSection:
    def __init__(self, key, items, collections=()):
        self.key = key
        self.title = "Animes"
        self._items = items
        self._collections = list(collections)
        self.totalSize = len(items)

    def all(self):
        return self._items

    def collections(self):
        return self._collections


class FakeLibrary:
    def __init__(self, section):
        self._section = section

    def section(self, name):
        return self._section

    def sections(self):
        return [self._section]


class FakeServer:
    def __init__(self, items, collections=()):
        self._section = FakeSection(1, items, collections)
        self.library = FakeLibrary(self._section)

    def fetchItems(self, ekey, params=None, container_size=None):
        return self._section.all()

    def fetchItem(self, rating_key):
        return next(i for i in self._section.all() if i.ratingKey == int(rating_key))


def make_config(**library_kwargs) -> AppConfig:
    base = {"library": "Animes", "type": "anime", "useGenres": True}
    base.update(library_kwargs)
    return AppConfig.model_validate({
        "version": 2,
        "defaults": {"anime": {"ignore": ["Kids"], "replace": {"sci-fi": "science fiction"}}},
        "libraries": [base],
        "providers": {"concurrency": 4},
    })


def jikan_ok(mal_id=1, genres=("Action", "Kids", "Sci-Fi"), score=8.0):
    return httpx.Response(200, json={"data": {
        "mal_id": mal_id, "title": "Anime", "score": score,
        "genres": [{"name": g} for g in genres],
    }})


@respx.mock
async def test_happy_path_writes_filtered_genres_in_one_request(store: Store):
    respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(
        return_value=jikan_ok()
    )
    handle = FakePlexItem(1, "Cowboy Bebop", 1998, genres=("Old",))
    handle.guids = [type("G", (), {"id": "mal://1"})()]
    server = FakeServer([handle])

    config = make_config(clearGenres=True)
    report = await Pipeline(config, store, server).tag_library(config.libraries[0])

    assert report.written == 1 and report.failed == 0
    assert handle.last_tags == ["Action", "science fiction"]   # Kids ignored, Sci-Fi renamed
    assert report.plex_requests == 1, "one write per item regardless of genre count"


@respx.mock
async def test_second_run_is_a_no_op_thanks_to_the_cache(store: Store):
    route = respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(
        return_value=jikan_ok()
    )
    handle = FakePlexItem(1, "Cowboy Bebop", 1998)
    handle.guids = [type("G", (), {"id": "mal://1"})()]
    server = FakeServer([handle])
    config = make_config()

    first = await Pipeline(config, store, server).tag_library(config.libraries[0])
    second = await Pipeline(config, store, server).tag_library(config.libraries[0])

    assert first.written == 1
    assert second.skipped == 1 and second.written == 0
    assert route.call_count == 1, "the provider is not called again"


@respx.mock
async def test_changing_settings_forces_a_reprocess(store: Store):
    respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(
        return_value=jikan_ok()
    )
    handle = FakePlexItem(1, "Cowboy Bebop", 1998)
    handle.guids = [type("G", (), {"id": "mal://1"})()]
    server = FakeServer([handle])

    await Pipeline(make_config(), store, server).tag_library(make_config().libraries[0])

    changed = make_config(clearGenres=True)      # different fingerprint
    report = await Pipeline(changed, store, server).tag_library(changed.libraries[0])
    assert report.skipped == 0


@respx.mock
async def test_dry_run_writes_nothing_and_caches_nothing(store: Store):
    respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(
        return_value=jikan_ok()
    )
    handle = FakePlexItem(1, "Cowboy Bebop", 1998)
    handle.guids = [type("G", (), {"id": "mal://1"})()]
    server = FakeServer([handle])
    config = make_config()

    report = await Pipeline(config, store, server, dry_run=True).tag_library(config.libraries[0])

    assert report.written == 1          # reports the intent
    assert handle.edits == []           # but changed nothing
    assert store.get_state("Animes", "mal://1") is None


@respx.mock
async def test_a_failing_item_does_not_stop_the_others(store: Store):
    """v1's bare `except Exception` around the whole loop aborted the run."""
    respx.get("https://api.jikan.moe/v4/anime/1").mock(return_value=httpx.Response(404))
    respx.get("https://api.jikan.moe/v4/anime/2").mock(return_value=jikan_ok(2))

    good = FakePlexItem(2, "Good", 2000)
    good.guids = [type("G", (), {"id": "mal://2"})()]
    bad = FakePlexItem(1, "Bad", 1999)
    bad.guids = [type("G", (), {"id": "mal://1"})()]
    server = FakeServer([bad, good])

    config = make_config()
    report = await Pipeline(config, store, server).tag_library(config.libraries[0])

    assert report.written == 1 and report.failed == 1
    assert good.edits, "the healthy item was still processed"
    assert store.get_state("Animes", "mal://1").status == "failed"


@respx.mock
async def test_a_manual_binding_overrides_the_guid(store: Store):
    respx.get("https://api.jikan.moe/v4/anime/19").mock(
        return_value=jikan_ok(19, genres=("Psychological",))
    )
    wrong = respx.get("https://api.jikan.moe/v4/anime/1")

    handle = FakePlexItem(1, "Monster", 2004)
    handle.guids = [type("G", (), {"id": "mal://1"})()]   # the wrong auto-match
    server = FakeServer([handle])

    store.set_binding("Animes", "mal://1", "mal", "19")
    config = make_config()
    report = await Pipeline(config, store, server).tag_library(config.libraries[0])

    assert report.written == 1
    assert handle.last_tags == ["Psychological"]
    assert not wrong.called


@respx.mock
async def test_undo_restores_the_previous_tags(store: Store):
    from plex_auto_genres.plexsvc.writer import undo_run

    respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(
        return_value=jikan_ok()
    )
    handle = FakePlexItem(1, "Cowboy Bebop", 1998, genres=("Original", "Tags"))
    handle.guids = [type("G", (), {"id": "mal://1"})()]
    server = FakeServer([handle])

    config = make_config(clearGenres=True)
    report = await Pipeline(config, store, server).tag_library(config.libraries[0])
    assert handle.last_tags == ["Action", "science fiction"]

    restored, skipped = undo_run(server, store, report.run_id)
    assert (restored, skipped) == (1, 0)
    assert handle.last_tags == ["Original", "Tags"]


@respx.mock
async def test_all_genres_filtered_out_counts_as_a_failure(store: Store):
    respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(
        return_value=jikan_ok(genres=("Kids",))    # the only genre is ignored
    )
    handle = FakePlexItem(1, "Kids Show", 2001)
    handle.guids = [type("G", (), {"id": "mal://1"})()]
    server = FakeServer([handle])

    config = make_config()
    report = await Pipeline(config, store, server).tag_library(config.libraries[0])
    assert report.failed == 1
    assert "no usable genres" in report.failures[0][1]


@respx.mock
async def test_falls_back_to_search_when_there_is_no_guid(store: Store):
    search = respx.get("https://api.jikan.moe/v4/anime").mock(
        return_value=httpx.Response(200, json={"data": [{
            "mal_id": 7, "title": "Cowboy Bebop", "year": 1998,
            "genres": [{"name": "Action"}],
        }]})
    )
    handle = FakePlexItem(1, "Cowboy Bebop", 1998)
    handle.guids = []
    server = FakeServer([handle])

    config = make_config()
    report = await Pipeline(config, store, server).tag_library(config.libraries[0])
    assert report.written == 1
    assert search.called


def test_rating_buckets():
    assert rating_bucket(9.4) == "5 Star Rating"
    assert rating_bucket(0.5) == "1 Star Rating"
    assert rating_bucket(0) is None
    assert rating_bucket(None) is None
    assert rating_bucket("nonsense") is None


# -- review regressions: run rows always close, caches are reused ----------------


import json  # noqa: E402

import pytest  # noqa: E402

from plex_auto_genres.errors import PlexConnectionError  # noqa: E402
from plex_auto_genres.plexsvc.writer import undo_run  # noqa: E402


class FakeCollection:
    def __init__(self, rating_key, title, title_sort=""):
        self.ratingKey, self.title, self.titleSort = rating_key, title, title_sort
        self.edits: list[dict] = []

    def edit(self, **kwargs):
        self.edits.append(kwargs)
        self.titleSort = kwargs.get("titleSort.value", self.titleSort)
        return self


class ServerWithCollections(FakeServer):
    def fetchItem(self, rating_key):
        for collection in self._section.collections():
            if collection.ratingKey == int(rating_key):
                return collection
        return super().fetchItem(rating_key)


def guid_item(rating_key=1, title="Cowboy Bebop", year=1998, genres=("Old",)) -> FakePlexItem:
    handle = FakePlexItem(rating_key, title, year, genres=genres)
    handle.guids = [type("G", (), {"id": f"mal://{rating_key}"})()]
    return handle


@respx.mock
async def test_a_plex_refusal_during_ratings_closes_the_run_row(store: Store):
    respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(return_value=jikan_ok())
    handle = guid_item()

    def refuse(rating=None):
        raise RuntimeError("Plex said no")

    handle.rate = refuse
    config = make_config(rateAnime=True)
    run = config.libraries[0]

    report = await Pipeline(config, store, FakeServer([handle])).rate_library(run)

    assert report.failed == 1 and report.written == 0
    assert "Plex said no" in report.failures[0][1]
    assert store.get_run(report.run_id)["finished_at"] is not None


async def test_a_plex_failure_before_the_item_loop_still_closes_the_row(store: Store):
    from plexapi.exceptions import NotFound

    class GoneLibrary:
        def section(self, name):
            raise NotFound("gone")

        def sections(self):
            return []

    server = FakeServer([])
    server.library = GoneLibrary()
    config = make_config()

    with pytest.raises(PlexConnectionError):
        await Pipeline(config, store, server).tag_library(config.libraries[0])

    row = store.recent_runs(1, "Animes")[0]
    assert row["finished_at"] is not None
    assert "No Plex library" in json.loads(row["report"])["error"]


async def test_sort_without_a_prefix_reports_instead_of_raising(store: Store):
    server = ServerWithCollections([], collections=[FakeCollection(1, "Action")])
    config = make_config(sortCollections=True)   # the anime defaults set no sortedPrefix

    report = await Pipeline(config, store, server).sort(config.libraries[0])

    assert report.error and "sortedPrefix" in report.error
    assert store.get_run(report.run_id)["finished_at"] is not None


async def test_missing_poster_directory_is_reported_not_raised(store: Store, tmp_path):
    config = make_config(setPosters=True)
    report = await Pipeline(config, store, FakeServer([])).set_posters(
        config.libraries[0], str(tmp_path / "nope")
    )
    assert report.error and "not found" in report.error
    assert store.get_run(report.run_id)["finished_at"] is not None


async def test_sort_matches_prefixed_collections_and_is_undoable(store: Store):
    action = FakeCollection(101, "PAG-Action")
    server = ServerWithCollections([], collections=[action])
    config = AppConfig.model_validate({
        "version": 2,
        "defaults": {"anime": {"sortedPrefix": "*", "sortedCollections": ["action"]}},
        "libraries": [{"library": "Animes", "type": "anime", "sortCollections": True}],
        "plex": {"collection_prefix": "PAG-"},
    })

    report = await Pipeline(config, store, server).sort(config.libraries[0])
    assert (report.written, report.skipped) == (1, 0)
    assert action.titleSort == "*PAG-Action"

    assert undo_run(server, store, report.run_id) == (1, 0)
    assert action.titleSort == "" and action.edits[-1]["titleSort.locked"] == 0


@respx.mock
async def test_v1_progress_rows_are_adopted_under_the_guid_key(store: Store):
    handle = guid_item()
    config = make_config()
    run = config.libraries[0]
    # Exactly what import_legacy_logs writes: keyed by "Title (Year)".
    store.record_success(
        "Animes", "Cowboy Bebop (1998)", fingerprint=config.fingerprint(run),
        title="Cowboy Bebop", year=1998, rating_key=None, genres=[], provider=None,
        provider_id=None,
    )

    report = await Pipeline(config, store, FakeServer([handle])).tag_library(run)

    assert (report.skipped, report.written) == (1, 0)   # no provider route was needed
    assert store.get_state("Animes", "mal://1") is not None
    assert store.get_state("Animes", "Cowboy Bebop (1998)") is None


@respx.mock
async def test_ratings_come_from_the_cache_after_a_tag_run(store: Store):
    route = respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(
        return_value=jikan_ok(score=8.0)
    )
    handle = guid_item()
    config = make_config(rateAnime=True)
    run = config.libraries[0]
    pipeline = Pipeline(config, store, FakeServer([handle]))

    await pipeline.tag_library(run)
    assert route.call_count == 1 and handle.ratings == [8.0]

    handle.userRating = 8.0                       # what Plex now reports
    report = await pipeline.rate_library(run)
    assert route.call_count == 1, "the cached score was used; no provider call"
    assert report.unchanged == 1 and handle.ratings == [8.0], "an equal rating is not re-sent"


@respx.mock
async def test_rating_collections_fall_back_to_the_cached_provider_score(store: Store):
    respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(
        return_value=jikan_ok(score=8.0)
    )
    handle = guid_item()                          # no Plex rating on the item
    config = make_config(createRatingCollections=True)
    run = config.libraries[0]
    pipeline = Pipeline(config, store, FakeServer([handle]))

    await pipeline.tag_library(run)
    report = await pipeline.rating_collections(run)

    assert report.written == 1 and handle.last_tags == ["4 Star Rating"]


@respx.mock
async def test_a_provider_auth_failure_falls_through_to_the_next_provider(store: Store):
    respx.post("https://graphql.anilist.co").mock(return_value=httpx.Response(403))
    respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(return_value=jikan_ok())
    handle = guid_item()
    config = make_config(providers=["anilist", "jikan"])

    report = await Pipeline(config, store, FakeServer([handle])).tag_library(config.libraries[0])

    assert report.written == 1 and report.failed == 0
    assert store.get_state("Animes", "mal://1").source == "guid"
