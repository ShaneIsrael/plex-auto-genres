"""Provider tests, including the two v1 metadata bugs."""

from __future__ import annotations

import httpx
import pytest
import respx

from plex_auto_genres.errors import ProviderNotFound, ProviderRateLimited
from plex_auto_genres.models import ExternalId, MediaType
from plex_auto_genres.providers.anilist import AniListProvider
from plex_auto_genres.providers.base import HttpTransport, LookupRequest, pick_best
from plex_auto_genres.providers.jikan import JikanProvider, clean_anime_title
from plex_auto_genres.providers.tmdb import TmdbProvider, split_compound_genres
from plex_auto_genres.ratelimit import LimitSpec


def transport(name="test", attempts=2) -> HttpTransport:
    # A very permissive limiter keeps the tests fast.
    limiter = LimitSpec(((1000, 1.0),)).build()
    return HttpTransport(httpx.AsyncClient(), limiter, max_attempts=attempts, name=name)


# -- bug #4: compound TV genres lost their second half ---------------------


def test_compound_genres_keep_both_halves():
    assert split_compound_genres(["Sci-Fi & Fantasy"]) == ["Sci-Fi", "Fantasy"]
    assert split_compound_genres(["Action & Adventure"]) == ["Action", "Adventure"]
    assert split_compound_genres(["War & Politics"]) == ["War", "Politics"]


def test_v1_split_dropped_the_second_half():
    """What v1 computed, kept here so the regression cannot come back quietly."""
    names = ["Sci-Fi & Fantasy", "Action & Adventure", "War & Politics"]
    v1 = [y[0] for y in [n.split(" & ") for n in names]]
    assert v1 == ["Sci-Fi", "Action", "War"]
    assert set(split_compound_genres(names)) - set(v1) == {"Fantasy", "Adventure", "Politics"}


def test_compound_split_deduplicates():
    assert split_compound_genres(["Action & Adventure", "Action"]) == ["Action", "Adventure"]


# -- bug #5: keywords unwrapped differently for movies and TV --------------


@respx.mock
async def test_movie_keywords_are_read_from_the_keywords_key():
    respx.get("https://api.themoviedb.org/3/movie/603").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 603, "title": "The Matrix", "vote_average": 8.2,
                "genres": [{"name": "Action"}],
                # /movie/{id} nests keywords under "keywords".
                "keywords": {"keywords": [{"name": "dystopia"}, {"name": "cyberpunk"}]},
            },
        )
    )
    provider = TmdbProvider(transport("tmdb"), api_key="k")
    request = LookupRequest("The Matrix", 1999, MediaType.STANDARD_MOVIE, use_keywords=True)
    result = await provider.fetch_by_id(ExternalId("tmdb", "603"), request)
    assert result.genres == ["dystopia", "cyberpunk"]


@respx.mock
async def test_tv_keywords_are_read_from_the_results_key():
    respx.get("https://api.themoviedb.org/3/tv/1396").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 1396, "name": "Breaking Bad",
                "genres": [{"name": "Drama"}],
                # /tv/{id} nests them under "results" instead.
                "keywords": {"results": [{"name": "drug"}, {"name": "new mexico"}]},
            },
        )
    )
    provider = TmdbProvider(transport("tmdb"), api_key="k")
    request = LookupRequest("Breaking Bad", 2008, MediaType.STANDARD_TV, use_keywords=True)
    result = await provider.fetch_by_id(ExternalId("tmdb", "1396"), request)
    assert result.genres == ["drug", "new mexico"]


@respx.mock
async def test_guid_lookup_skips_the_search_request():
    """Bug #1 of the improvement list: use the id Plex already has."""
    details = respx.get("https://api.themoviedb.org/3/tv/1396").mock(
        return_value=httpx.Response(
            200, json={"id": 1396, "name": "Breaking Bad",
                       "genres": [{"name": "Drama"}], "keywords": {"results": []}}
        )
    )
    search = respx.get("https://api.themoviedb.org/3/search/tv")

    provider = TmdbProvider(transport("tmdb"), api_key="k")
    request = LookupRequest(
        "Breaking Bad", 2008, MediaType.STANDARD_TV,
        external_ids=[ExternalId("tmdb", "1396")],
    )
    result = await provider.resolve(request)

    assert result.genres == ["Drama"]
    assert details.called
    assert not search.called, "no search needed when Plex already knows the TMDB id"


@respx.mock
async def test_search_disambiguates_by_year():
    respx.get("https://api.themoviedb.org/3/search/movie").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": 1, "title": "The Thing", "release_date": "2011-10-14"},
            {"id": 2, "title": "The Thing", "release_date": "1982-06-25"},
        ]})
    )
    respx.get("https://api.themoviedb.org/3/movie/2").mock(
        return_value=httpx.Response(200, json={
            "id": 2, "title": "The Thing", "genres": [{"name": "Horror"}]})
    )
    provider = TmdbProvider(transport("tmdb"), api_key="k")
    result = await provider.search(
        LookupRequest("The Thing", 1982, MediaType.STANDARD_MOVIE)
    )
    # v1 took results[0] unconditionally, which is the 2011 remake.
    assert result.provider_id == "2"


@respx.mock
async def test_tmdb_search_retries_without_the_year_filter():
    route = respx.get("https://api.themoviedb.org/3/search/tv")
    route.side_effect = [
        httpx.Response(200, json={"results": []}),               # with year
        httpx.Response(200, json={"results": [{"id": 9, "name": "Show"}]}),  # without
    ]
    respx.get("https://api.themoviedb.org/3/tv/9").mock(
        return_value=httpx.Response(200, json={"id": 9, "name": "Show",
                                               "genres": [{"name": "Drama"}]})
    )
    provider = TmdbProvider(transport("tmdb"), api_key="k")
    result = await provider.search(LookupRequest("Show", 1999, MediaType.STANDARD_TV))
    assert result.provider_id == "9"


# -- Jikan -----------------------------------------------------------------


def test_anime_titles_are_cleaned():
    assert clean_anime_title("Monster [BD 1080p]") == "Monster"
    assert len(clean_anime_title(" ".join(f"w{i}" for i in range(20))).split()) == 10


@respx.mock
async def test_jikan_merges_genres_themes_and_demographics():
    respx.get("https://api.jikan.moe/v4/anime/1").mock(
        return_value=httpx.Response(200, json={"data": {
            "mal_id": 1, "title": "Cowboy Bebop", "score": 8.75,
            "genres": [{"name": "Action"}],
            "themes": [{"name": "Space"}],
            "demographics": [{"name": "Seinen"}],
        }})
    )
    provider = JikanProvider(transport("jikan"))
    result = await provider.fetch_by_id(
        ExternalId("mal", "1"), LookupRequest("Cowboy Bebop", 1998, MediaType.ANIME)
    )
    assert result.genres == ["Action", "Space", "Seinen"]
    assert result.score == 8.75


@respx.mock
async def test_rate_limit_is_retried_then_surfaced():
    respx.get("https://api.jikan.moe/v4/anime/1").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "0"})
    )
    provider = JikanProvider(transport("jikan", attempts=2))
    with pytest.raises(ProviderRateLimited):
        await provider.fetch_by_id(
            ExternalId("mal", "1"), LookupRequest("x", None, MediaType.ANIME)
        )


@respx.mock
async def test_404_becomes_not_found():
    respx.get("https://api.jikan.moe/v4/anime/999").mock(return_value=httpx.Response(404))
    provider = JikanProvider(transport("jikan"))
    with pytest.raises(ProviderNotFound):
        await provider.fetch_by_id(
            ExternalId("mal", "999"), LookupRequest("x", None, MediaType.ANIME)
        )


@respx.mock
async def test_server_error_is_retried():
    route = respx.get("https://api.jikan.moe/v4/anime/1")
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(200, json={"data": {"mal_id": 1, "title": "OK",
                                           "genres": [{"name": "Action"}]}}),
    ]
    provider = JikanProvider(transport("jikan", attempts=3))
    result = await provider.fetch_by_id(
        ExternalId("mal", "1"), LookupRequest("x", None, MediaType.ANIME)
    )
    assert result.genres == ["Action"]
    assert route.call_count == 2


# -- AniList ---------------------------------------------------------------


@respx.mock
async def test_anilist_resolves_by_mal_id_and_scales_the_score():
    respx.post("https://graphql.anilist.co").mock(
        return_value=httpx.Response(200, json={"data": {"Media": {
            "id": 1, "idMal": 1, "title": {"romaji": "Cowboy Bebop"},
            "genres": ["Action", "Sci-Fi"],
            "tags": [{"name": "Space", "rank": 90, "isGeneralSpoiler": False},
                     {"name": "Noise", "rank": 20, "isGeneralSpoiler": False},
                     {"name": "Spoiler", "rank": 99, "isGeneralSpoiler": True}],
            "averageScore": 86, "startDate": {"year": 1998},
            "siteUrl": "https://anilist.co/anime/1",
        }}})
    )
    provider = AniListProvider(transport("anilist"))
    result = await provider.fetch_by_id(
        ExternalId("mal", "1"), LookupRequest("Cowboy Bebop", 1998, MediaType.ANIME)
    )
    assert result.genres == ["Action", "Sci-Fi", "Space"]   # low-rank and spoiler tags dropped
    assert result.score == 8.6                              # 86/100 -> Plex's 0-10 scale


# -- candidate selection ---------------------------------------------------


def test_pick_best_prefers_an_exact_title():
    candidates = [("Monster Musume", 2015, "a"), ("Monster", 2004, "b")]
    assert pick_best(candidates, "Monster", None) == "b"


def test_pick_best_falls_back_to_the_first_when_nothing_matches():
    candidates = [("Something Else", 2001, "a"), ("Other", 2002, "b")]
    assert pick_best(candidates, "Unrelated", None) in {"a", "b"}


def test_pick_best_returns_none_for_no_candidates():
    assert pick_best([], "x", None) is None


# -- review regressions ---------------------------------------------------------


@respx.mock
async def test_auth_failures_become_provider_auth_errors():
    from plex_auto_genres.errors import ProviderAuthError

    respx.get("https://api.jikan.moe/v4/anime/1").mock(return_value=httpx.Response(401))
    with pytest.raises(ProviderAuthError):
        await JikanProvider(transport("jikan")).fetch_by_id(
            ExternalId("mal", "1"), LookupRequest("x", None, MediaType.ANIME)
        )


@respx.mock
async def test_other_client_errors_are_provider_errors_not_httpx_ones():
    from plex_auto_genres.errors import ProviderError

    respx.get("https://api.jikan.moe/v4/anime/1").mock(return_value=httpx.Response(418))
    with pytest.raises(ProviderError):
        await JikanProvider(transport("jikan")).fetch_by_id(
            ExternalId("mal", "1"), LookupRequest("x", None, MediaType.ANIME)
        )


@respx.mock
async def test_results_say_how_they_matched():
    respx.get("https://api.jikan.moe/v4/anime/1").mock(return_value=httpx.Response(200, json={
        "data": {"mal_id": 1, "title": "X", "genres": [{"name": "Action"}]}}))
    result = await JikanProvider(transport("jikan")).resolve(
        LookupRequest("X", None, MediaType.ANIME, external_ids=[ExternalId("mal", "1")])
    )
    assert result.matched_by == "guid"


async def test_the_rate_limiter_is_shared_per_provider_within_a_process():
    from plex_auto_genres.ratelimit import JIKAN_LIMITS, TMDB_LIMITS, shared_limiter

    assert shared_limiter("jikan", JIKAN_LIMITS) is shared_limiter("jikan", JIKAN_LIMITS)
    assert shared_limiter("jikan", JIKAN_LIMITS) is not shared_limiter("tmdb", TMDB_LIMITS)


@respx.mock
async def test_the_anidb_mapping_is_downloaded_once_under_concurrency(store):
    import asyncio

    from plex_auto_genres.providers.anidb_map import MAPPING_URL, AniDbMapper

    route = respx.get(MAPPING_URL).mock(
        return_value=httpx.Response(200, json=[{"anidb_id": 1, "mal_id": 20}])
    )
    mapper = AniDbMapper(store)
    results = await asyncio.gather(*(mapper.expand([ExternalId("anidb", "1")]) for _ in range(5)))
    assert route.call_count == 1
    assert all(ExternalId("mal", "20") in ids for ids in results)
