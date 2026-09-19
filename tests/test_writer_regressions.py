"""Regression tests for the three v1 write bugs.

Each of these reproduces the exact v1 behaviour first, so the test documents
what was wrong as well as what is now right.
"""

from __future__ import annotations

import pytest

from plex_auto_genres.models import TagField
from plex_auto_genres.plexsvc.writer import PlexWriter, build_tag_edits, plan_tags
from plex_auto_genres.store import Store

from .conftest import FakePlexItem


# -- bug #1: only the last genre of the loop survived ----------------------


def test_v1_loop_behaviour_is_reproduced_by_plexapi():
    """Documents the original defect using plexapi's real editTags."""
    from plexapi.mixins import GenreMixin

    class StaleCachedItem(GenreMixin):
        def __init__(self, existing):
            self.genres = list(existing)
            self.sent = []

        def _edit(self, **kwargs):
            self.sent.append([v for k, v in sorted(kwargs.items()) if k.endswith(".tag.tag")])
            return self

    victim = StaleCachedItem(["Animation", "Comedy"])
    for genre in ["Action", "Fantasy", "Shounen"]:
        victim.addGenre(genre)  # what v1 did, once per genre

    assert len(victim.sent) == 3, "v1 issued one HTTP write per genre"
    assert victim.sent[-1] == ["Animation", "Comedy", "Shounen"]
    assert "Action" not in victim.sent[-1], "the earlier genres were overwritten"


def test_all_genres_are_written_in_one_request(store: Store, item):
    writer = PlexWriter(store, "run1", "Animes")
    outcome = writer.write_tags(
        item, TagField.GENRE, ["Action", "Fantasy", "Shounen"], clear=False
    )

    assert outcome.changed
    assert writer.requests == 1, "one PUT, however many genres"
    assert item.handle.last_tags == ["Animation", "Comedy", "Action", "Fantasy", "Shounen"]


def test_existing_tags_are_preserved_when_not_clearing(store: Store, item):
    writer = PlexWriter(store, "run1", "Animes")
    writer.write_tags(item, TagField.GENRE, ["Action"], clear=False)
    assert "Animation" in item.handle.last_tags
    assert "Comedy" in item.handle.last_tags


# -- bug #2: clearGenres did nothing ---------------------------------------


def test_clear_replaces_instead_of_appending(store: Store, item):
    writer = PlexWriter(store, "run1", "Animes")
    writer.write_tags(item, TagField.GENRE, ["Action", "Drama"], clear=True)

    assert item.handle.last_tags == ["Action", "Drama"]
    assert "Animation" not in item.handle.last_tags
    assert "Comedy" not in item.handle.last_tags


def test_clear_emits_an_explicit_removal_for_dropped_tags():
    edits = build_tag_edits(TagField.GENRE, ["Animation", "Comedy"], ["Action"])
    assert edits["genre[0].tag.tag"] == "Action"
    # Belt and braces: the dropped tags are also named in the remove param, so
    # the result is right whether Plex treats the indexed form as a replace or
    # as a merge.
    assert edits["genre[].tag.tag-"] == "Animation,Comedy"


def test_plan_tags_clear_true_is_a_replacement():
    assert plan_tags(["A", "B"], ["C"], clear=True) == ["C"]


def test_plan_tags_clear_false_is_a_union():
    assert plan_tags(["A", "B"], ["B", "C"], clear=False) == ["A", "B", "C"]


def test_plan_tags_deduplicates_case_insensitively():
    assert plan_tags(["Action"], ["action", "ACTION", "Drama"], clear=False) == ["Action", "Drama"]


def test_prefix_is_applied_to_new_tags_only():
    assert plan_tags([], ["Action", "Drama"], clear=True, prefix="*") == ["*Action", "*Drama"]


# -- bug #3: rate() was handed a string ------------------------------------


def test_rating_is_sent_as_a_float(store: Store, item):
    writer = PlexWriter(store, "run1", "Animes")
    assert writer.set_rating(item, 8.7) is True
    assert item.handle.ratings == [8.7]


def test_v1_string_score_would_have_raised():
    handle = FakePlexItem()
    with pytest.raises(ValueError):
        handle.rate("8.7")  # exactly what v1 passed


def test_out_of_range_and_missing_scores_are_skipped(store: Store, item):
    writer = PlexWriter(store, "run1", "Animes")
    assert writer.set_rating(item, None) is False
    assert writer.set_rating(item, 42.0) is False
    assert item.handle.ratings == []


# -- general write behaviour -----------------------------------------------


def test_no_request_when_tags_already_correct(store: Store, item):
    writer = PlexWriter(store, "run1", "Animes")
    outcome = writer.write_tags(item, TagField.GENRE, ["Animation", "Comedy"], clear=True)
    assert outcome.changed is False
    assert writer.requests == 0
    assert item.handle.edits == []


def test_dry_run_never_touches_plex(store: Store, item):
    writer = PlexWriter(store, "run1", "Animes", dry_run=True)
    outcome = writer.write_tags(item, TagField.GENRE, ["Action"], clear=True)
    assert outcome.changed is True          # still reports what would happen
    assert item.handle.edits == []          # but wrote nothing
    assert writer.requests == 0
    assert writer.set_rating(item, 9.0) is True
    assert item.handle.ratings == []


def test_writes_record_an_undo_snapshot(store: Store, item):
    writer = PlexWriter(store, "run-abc", "Animes")
    writer.write_tags(item, TagField.GENRE, ["Action"], clear=True)

    snaps = store.snapshots_for("run-abc")
    assert len(snaps) == 1
    import json
    assert json.loads(snaps[0]["before"]) == ["Animation", "Comedy"]
    assert json.loads(snaps[0]["after"]) == ["Action"]
