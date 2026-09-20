"""Library browser, candidate search, bindings CRUD and the poster proxy."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from plex_auto_genres.models import MediaType
from plex_auto_genres.providers.anilist import AniListProvider
from plex_auto_genres.providers.base import HttpTransport, LookupRequest, rank_candidates, short
from plex_auto_genres.providers.jikan import JikanProvider
from plex_auto_genres.providers.tmdb import TmdbProvider
from plex_auto_genres.ratelimit import LimitSpec
from plex_auto_genres.store import Store

from .conftest import FakePlexItem
from .test_server import _job_app


def transport(name="test") -> HttpTransport:
    return HttpTransport(httpx.AsyncClient(), LimitSpec(((1000, 1.0),)).build(), name=name)


# -- ranking and excerpts -------------------------------------------------------


def test_rank_puts_the_exact_title_and_closest_year_first():
    ranked = rank_candidates(
        [("Monster Musume", 2015, "a"), ("Monster", 2004, "b"), ("Monster", 1998, "c")],
        "Monster", 2004,
    )
    assert ranked == ["b", "c", "a"]


def test_short_cuts_at_a_word_boundary():
    text = "word " * 100
    out = short(text, limit=40)
    assert out.endswith("…") and len(out) <= 41 and not out[:-1].endswith(" ")
    assert short(None) is None and short("tiny") == "tiny"


# -- provider candidates ---------------------------------------------------------


@respx.mock
async def test_jikan_candidates_carry_poster_synopsis_and_genres():
    respx.get("https://api.jikan.moe/v4/anime").mock(return_value=httpx.Response(200, json={
        "data": [
            {"mal_id": 1, "title": "Monster Musume", "year": 2015, "score": 7.0,
             "images": {"jpg": {"image_url": "https://img/1.jpg"}}, "synopsis": "x " * 300,
             "genres": [{"name": "Comedy"}], "url": "https://mal/1"},
            {"mal_id": 19, "title": "Monster", "year": 2004, "score": 8.9,
             "images": {"jpg": {"image_url": "https://img/19.jpg"}}, "synopsis": "A surgeon.",
             "genres": [{"name": "Drama"}, {"name": "Mystery"}], "url": "https://mal/19"},
        ]
    }))
    found = await JikanProvider(transport("jikan")).search_candidates(
        LookupRequest("Monster", 2004, MediaType.ANIME), limit=5
    )
    assert [c.provider_id for c in found] == ["19", "1"]      # exact title first
    best = found[0]
    assert best.image == "https://img/19.jpg" and best.genres == ["Drama", "Mystery"]
    assert best.score == 8.9 and best.url == "https://mal/19" and best.synopsis == "A surgeon."
    assert len(found[1].synopsis) <= 241                     # trimmed


@respx.mock
async def test_tmdb_candidates_prefix_the_poster_path():
    respx.get("https://api.themoviedb.org/3/search/tv").mock(return_value=httpx.Response(200, json={
        "results": [{"id": 7, "name": "Show", "first_air_date": "2010-01-01",
                     "poster_path": "/p.jpg", "overview": "About a show.", "vote_average": 6.5}]
    }))
    found = await TmdbProvider(transport("tmdb"), api_key="k").search_candidates(
        LookupRequest("Show", 2010, MediaType.STANDARD_TV)
    )
    assert found[0].image == "https://image.tmdb.org/t/p/w185/p.jpg"
    assert found[0].year == 2010 and found[0].score == 6.5
    assert found[0].url == "https://www.themoviedb.org/tv/7"


@respx.mock
async def test_anilist_candidates_strip_html_from_the_description():
    respx.post("https://graphql.anilist.co").mock(return_value=httpx.Response(200, json={
        "data": {"Page": {"media": [{
            "id": 3, "idMal": 19, "title": {"romaji": "Monster"}, "genres": ["Drama"],
            "tags": [], "averageScore": 89, "startDate": {"year": 2004}, "siteUrl": "https://al/3",
            "coverImage": {"medium": "https://al/img.jpg"},
            "description": "A <b>surgeon</b> saves<br>a boy.",
        }]}}
    }))
    found = await AniListProvider(transport("anilist")).search_candidates(
        LookupRequest("Monster", 2004, MediaType.ANIME)
    )
    assert found[0].synopsis == "A surgeon saves a boy."
    assert found[0].image == "https://al/img.jpg" and found[0].score == 8.9


# -- store -----------------------------------------------------------------------


def test_states_for_library_and_forget(store: Store):
    store.record_success("Lib", "mal://1", fingerprint="f", title="A", year=None, rating_key=1,
                         genres=["Action"], provider="jikan", provider_id="1")
    store.record_failure("Lib", "mal://2", fingerprint="f", title="B", year=None, rating_key=2,
                         error="nope")
    states = store.states_for_library("Lib")
    assert states["mal://1"].status == "ok" and states["mal://2"].status == "failed"
    assert store.providers_for_library("Lib")["mal://1"] == ("jikan", "1")
    assert store.forget("Lib", "mal://2") is True and store.forget("Lib", "mal://2") is False
    assert "mal://2" not in store.states_for_library("Lib")


def test_deleting_a_binding_also_drops_the_cached_match(store: Store):
    store.set_binding("Lib", "mal://1", "mal", "19")
    store.record_success("Lib", "mal://1", fingerprint="f", title="A", year=None, rating_key=1,
                         genres=["Drama"], provider="jikan", provider_id="19")
    assert store.delete_binding("Lib", "mal://1") is True
    assert store.get_state("Lib", "mal://1") is None


# -- API: items ------------------------------------------------------------------


@pytest.fixture
def browser(tmp_path, config_file, monkeypatch):
    monkeypatch.setenv("TMDB_API_KEY", "k")   # so a wrong-type provider fails on type, not on the key
    app, server = _job_app(tmp_path, config_file, monkeypatch)
    items = server._section.all()
    items[0].thumb = "/library/metadata/1/thumb/9"
    loose = FakePlexItem(3, "Loose Cannon", 1999)      # no usable GUID -> search
    loose.guids = []
    items.append(loose)
    server.url = lambda path, includeToken=True: f"http://plex:32400{path}?X-Plex-Token=t"
    with TestClient(app) as c:
        yield c, server, Store(tmp_path / "state.db")


def test_items_page_joins_match_state_and_bindings(browser):
    c, _, store = browser
    store.record_success("Animes", "mal://1", fingerprint="f", title="One", year=2001, rating_key=1,
                         genres=["Action"], provider="jikan", provider_id="1")
    # Bind first: setting a binding drops the cached match on purpose, so a
    # failure recorded *after* it stands for a run that went through the binding.
    store.set_binding("Animes", "mal://2", "mal", "19", note="picked by hand")
    store.record_failure("Animes", "mal://2", fingerprint="f", title="Two", year=2002, rating_key=2,
                         error="jikan: no anime matching 'Two'")

    body = c.get("/api/v1/libraries/Animes/items").json()
    assert body["total"] == 3 and body["counts"] == {
        "all": 3, "ok": 1, "failed": 1, "unprocessed": 1, "bound": 1}
    by_title = {i["title"]: i for i in body["items"]}

    one = by_title["One"]
    assert one["match"] == "guid" and one["media_key"] == "mal://1"
    assert one["state"]["status"] == "ok" and one["state"]["genres"] == ["Action"]
    assert one["state"]["provider"] == "jikan" and one["thumb"] == "/library/metadata/1/thumb/9"
    assert one["current_genres"] == ["Old"]

    two = by_title["Two"]
    assert two["match"] == "binding" and two["binding"]["provider_id"] == "19"
    assert two["state"]["status"] == "failed" and "no anime" in two["state"]["last_error"]

    loose = by_title["Loose Cannon"]
    assert loose["match"] == "search" and loose["state"] is None
    assert loose["media_key"] == "Loose Cannon (1999)"


def test_items_filter_search_and_paginate(browser):
    c, _, store = browser
    store.record_failure("Animes", "mal://2", fingerprint="f", title="Two", year=2002, rating_key=2,
                         error="x")
    assert [i["title"] for i in c.get("/api/v1/libraries/Animes/items", params={"status": "failed"}).json()["items"]] == ["Two"]
    assert [i["title"] for i in c.get("/api/v1/libraries/Animes/items", params={"q": "loose"}).json()["items"]] == ["Loose Cannon"]
    page = c.get("/api/v1/libraries/Animes/items", params={"size": 1, "page": 2}).json()
    assert page["total"] == 3 and [i["title"] for i in page["items"]] == ["One"]   # sorted by title
    unprocessed = c.get("/api/v1/libraries/Animes/items", params={"status": "unprocessed"}).json()
    assert sorted(i["title"] for i in unprocessed["items"]) == ["Loose Cannon", "One"]


def test_items_of_an_unconfigured_library_is_404(browser):
    c, _, _ = browser
    assert c.get("/api/v1/libraries/Nope/items").status_code == 404


def test_forget_clears_one_cache_entry(browser):
    c, _, store = browser
    store.record_success("Animes", "mal://1", fingerprint="f", title="One", year=2001, rating_key=1,
                         genres=[], provider=None, provider_id=None)
    assert c.post("/api/v1/libraries/Animes/items/forget", params={"media_key": "mal://1"}).json() == {"forgotten": True}
    assert c.get("/api/v1/libraries/Animes/items").json()["counts"]["ok"] == 0


# -- API: search ------------------------------------------------------------------


def test_search_returns_ranked_candidates(browser):
    c, _, _ = browser
    with respx.mock:
        respx.get("https://api.jikan.moe/v4/anime").mock(return_value=httpx.Response(200, json={
            "data": [{"mal_id": 19, "title": "Monster", "year": 2004, "score": 8.9,
                      "images": {"jpg": {"image_url": "https://img/19.jpg"}}, "synopsis": "s",
                      "genres": [], "url": "https://mal/19"}]}))
        body = c.get("/api/v1/search", params={"q": "Monster", "type": "anime", "year": 2004}).json()
    assert body[0]["provider"] == "jikan" and body[0]["provider_id"] == "19"
    assert body[0]["image"] == "https://img/19.jpg"


def test_search_rejects_a_provider_that_cannot_serve_the_type(browser):
    c, _, _ = browser
    response = c.get("/api/v1/search", params={"q": "x", "type": "anime", "provider": "tmdb"})
    assert response.status_code == 400 and "cannot serve" in response.json()["detail"]


def test_search_surfaces_provider_failures_as_502(browser):
    c, _, _ = browser
    with respx.mock:
        respx.get("https://api.jikan.moe/v4/anime").mock(return_value=httpx.Response(500))
        response = c.get("/api/v1/search", params={"q": "x", "type": "anime"})
    assert response.status_code == 502


# -- API: bindings ----------------------------------------------------------------


def test_binding_round_trip_through_the_api(browser):
    c, _, store = browser
    store.record_success("Animes", "mal://1", fingerprint="f", title="One", year=2001, rating_key=1,
                         genres=["Wrong"], provider="jikan", provider_id="1")

    created = c.post("/api/v1/bindings", json={
        "library": "Animes", "media_key": "mal://1", "provider": "mal", "provider_id": "19",
        "note": "hand-picked"})
    assert created.status_code == 201
    assert created.json()["provider_id"] == "19" and created.json()["note"] == "hand-picked"
    assert store.get_state("Animes", "mal://1") is None       # stale match dropped

    items = {i["title"]: i for i in c.get("/api/v1/libraries/Animes/items").json()["items"]}
    assert items["One"]["match"] == "binding"

    assert c.delete("/api/v1/bindings", params={"library": "Animes", "media_key": "mal://1"}).json() == {"removed": True}
    assert c.delete("/api/v1/bindings", params={"library": "Animes", "media_key": "mal://1"}).status_code == 404
    assert c.get("/api/v1/bindings").json() == []


def test_binding_for_an_unconfigured_library_is_404(browser):
    c, _, _ = browser
    response = c.post("/api/v1/bindings", json={
        "library": "Nope", "media_key": "k", "provider": "mal", "provider_id": "1"})
    assert response.status_code == 404


def test_binding_body_is_validated(browser):
    c, _, _ = browser
    response = c.post("/api/v1/bindings", json={
        "library": "Animes", "media_key": "k", "provider": "imdbx", "provider_id": "1"})
    assert response.status_code == 422


# -- API: poster proxy ------------------------------------------------------------


def test_thumb_proxy_only_serves_library_paths(browser):
    c, _, _ = browser
    assert c.get("/api/v1/plex/thumb", params={"path": "/etc/passwd"}).status_code == 400
    assert c.get("/api/v1/plex/thumb", params={"path": "/library/../x"}).status_code == 400


def test_thumb_proxy_fetches_with_the_token_and_caches(browser):
    c, _, _ = browser
    with respx.mock:
        route = respx.get("http://plex:32400/library/metadata/1/thumb/9").mock(
            return_value=httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"}))
        response = c.get("/api/v1/plex/thumb", params={"path": "/library/metadata/1/thumb/9"})
    assert response.status_code == 200 and response.content == b"\x89PNG"
    assert response.headers["content-type"] == "image/png"
    assert "max-age=86400" in response.headers["cache-control"]
    assert route.calls[0].request.url.params["X-Plex-Token"] == "t"


# -- review regressions ---------------------------------------------------------


def test_bindings_are_addressed_by_any_spelling_of_the_library(browser):
    c, _, store = browser
    store.set_binding("Animes", "mal://1", "mal", "1")
    assert c.get("/api/v1/bindings", params={"library": "animes"}).json()[0]["media_key"] == "mal://1"
    assert c.delete("/api/v1/bindings",
                    params={"library": "ANIMES", "media_key": "mal://1"}).status_code == 200
    assert store.list_bindings("Animes") == []


def test_refresh_drops_the_item_cache(browser):
    c, server, _ = browser
    assert c.get("/api/v1/libraries/Animes/items").json()["total"] == 3
    server._section.all().append(FakePlexItem(4, "New Arrival", 2020))
    assert c.get("/api/v1/libraries/Animes/items").json()["total"] == 3   # the minute cache
    assert c.post("/api/v1/libraries/Animes/refresh").json() == {"refreshed": True}
    assert c.get("/api/v1/libraries/Animes/items").json()["total"] == 4
    assert c.post("/api/v1/libraries/Nope/refresh").status_code == 404


def test_match_provenance_comes_from_the_run_that_resolved_the_item(browser):
    c, _, store = browser
    store.record_success("Animes", "mal://1", fingerprint="f", title="One", year=2001, rating_key=1,
                         genres=["Action"], provider="jikan", provider_id="1", source="search")
    by_title = {i["title"]: i for i in c.get("/api/v1/libraries/Animes/items").json()["items"]}
    assert by_title["One"]["match"] == "search", "what happened, not what the GUID suggests"
