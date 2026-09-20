"""Writing tags, ratings, posters and sort titles back to Plex.

This module is where v1's three worst bugs lived, so the tag payload is built
explicitly here rather than delegated to ``plexapi.editTags``:

* v1 looped ``for genre in genres: media.addGenre(genre)``. ``editTags``
  rebuilds the full tag list from the object's *cached* attribute, and plexapi
  only auto-reloads that cache when it is empty. So each PUT overwrote the
  previous one and only the last genre of the loop survived.
* v1 "cleared" genres with ``editTags('genre', [], locked=True)``. With
  ``remove=False`` that resolves to ``existing + []`` -- it resent the existing
  tags unchanged. ``clearGenres`` did nothing at all.
* v1 called ``media.rate(str(score))``; ``rate()`` rejects non-numerics with
  ``BadRequest``, which killed the whole ``--rate-anime`` loop on its first item.

Every write also records a before/after snapshot so a run can be undone.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from ..models import MediaItem, TagField
from ..store import Store

log = logging.getLogger(__name__)


@dataclass(slots=True)
class WriteOutcome:
    changed: bool
    requests: int
    before: list[str]
    after: list[str]


def build_tag_edits(
    field: TagField, current: list[str], desired: list[str], *, locked: bool = True
) -> dict[str, object]:
    """Build the Plex edit payload that makes ``field`` hold exactly ``desired``.

    Indexed ``field[i].tag.tag`` params set the full list -- that is why
    plexapi has to prepend existing tags when it wants to *add* one. Tags being
    dropped are additionally listed in ``field[].tag.tag-`` so the result is
    identical whether the server treats the indexed form as a replacement or
    as a merge. ``locked`` keeps Plex's agent from overwriting the field on
    its next refresh; an undo passes the lock state the item had before.
    """
    edits: dict[str, object] = {f"{field.value}.locked": int(locked)}
    for index, tag in enumerate(desired):
        edits[f"{field.value}[{index}].tag.tag"] = tag

    removed = [t for t in current if t not in desired]
    if removed:
        edits[f"{field.value}[].tag.tag-"] = ",".join(quote(str(t)) for t in removed)
    return edits


def plan_tags(
    current: list[str],
    incoming: list[str],
    *,
    clear: bool,
    prefix: str = "",
) -> list[str]:
    """Work out the final tag list for an item.

    ``clear`` replaces the existing tags outright; otherwise the new ones are
    merged in, preserving the order Plex already had.
    """
    prefixed = [f"{prefix}{tag}" for tag in incoming]
    if clear:
        desired = prefixed
    else:
        desired = list(current)
        known = {t.casefold() for t in desired}
        for tag in prefixed:
            if tag.casefold() not in known:
                known.add(tag.casefold())
                desired.append(tag)
    # De-duplicate case-insensitively while keeping first-seen order.
    out: list[str] = []
    seen: set[str] = set()
    for tag in desired:
        key = tag.casefold()
        if key not in seen:
            seen.add(key)
            out.append(tag)
    return out


class PlexWriter:
    """Applies changes to Plex, honouring dry-run and recording undo data."""

    def __init__(
        self,
        store: Store,
        run_id: str,
        library: str,
        *,
        dry_run: bool = False,
    ) -> None:
        self._store = store
        self._run_id = run_id
        self._library = library
        self.dry_run = dry_run
        self.requests = 0

    def write_tags(
        self,
        item: MediaItem,
        field: TagField,
        incoming: list[str],
        *,
        clear: bool,
        prefix: str = "",
    ) -> WriteOutcome:
        """Make ``field`` hold the right tags, in a single Plex request."""
        current = list(item.current_tags(field))
        desired = plan_tags(current, incoming, clear=clear, prefix=prefix)

        if desired == current:
            return WriteOutcome(changed=False, requests=0, before=current, after=desired)

        if not self.dry_run:
            edits = build_tag_edits(field, current, desired)
            # One PUT for the whole set, however many tags it holds.
            item.handle.edit(**edits)  # type: ignore[attr-defined]
            self.requests += 1
            self._store.add_snapshot(
                self._run_id, self._library, item.rating_key, item.title,
                field.value, current, desired,
                locked_before=field.value in item.locked_fields,
            )
            # Keep the in-memory item consistent for any later write.
            item.locked_fields.add(field.value)
            if field is TagField.GENRE:
                item.current_genres = list(desired)
            else:
                item.current_collections = list(desired)

        return WriteOutcome(changed=True, requests=1, before=current, after=desired)

    def set_rating(self, item: MediaItem, score: float | None) -> bool:
        """Set the user rating from a provider score (0-10)."""
        if score is None:
            return False
        try:
            value = float(score)
        except (TypeError, ValueError):
            log.debug("Skipping non-numeric score %r for %s", score, item.title)
            return False
        if not 0.0 <= value <= 10.0:
            log.debug("Skipping out-of-range score %s for %s", value, item.title)
            return False
        current = getattr(item.handle, "userRating", None)
        if isinstance(current, (int, float)) and abs(float(current) - value) < 0.05:
            return False  # already there; v1 re-sent every rating on every run
        if self.dry_run:
            return True
        item.handle.rate(value)  # type: ignore[attr-defined]
        self.requests += 1
        # A rating is as much the user's data as a tag: keep the old one so
        # the run can be undone.
        self._store.add_snapshot(
            self._run_id, self._library, item.rating_key, item.title, "rating",
            [] if current is None else [str(current)], [str(value)],
        )
        return True


def undo_run(server, store: Store, run_id: str, *, dry_run: bool = False) -> tuple[int, int]:
    """Restore the tags, ratings and sort titles a previous run overwrote.

    Returns ``(restored, skipped)``. Items whose values changed since the run
    are still restored -- the snapshot is the authoritative "before" state.
    """
    snapshots = store.snapshots_for(run_id)
    restored = skipped = 0
    for snap in snapshots:
        before = json.loads(snap["before"])
        after = json.loads(snap["after"])
        try:
            item = server.fetchItem(int(snap["rating_key"]))
        except Exception as exc:
            log.warning("Cannot restore %s: %s", snap["title"], exc)
            skipped += 1
            continue
        if not dry_run:
            _restore(item, snap["field"], before, after, snap["locked_before"])
        restored += 1
    if not dry_run and restored:
        store.mark_undone(run_id)
    return restored, skipped


def _restore(item, field_name: str, before: list, after: list, locked_before) -> None:
    """Put one snapshotted value back, including the lock state it had."""
    if field_name == "rating":
        item.rate(float(before[0]) if before else None)  # None = unrated
        return
    if field_name == "titleSort":
        # The sort title goes back and is unlocked so the agent owns it again.
        item.edit(**{"titleSort.value": before[0] if before else "", "titleSort.locked": 0})
        return
    # Rows written before the lock state was recorded stay locked, as they were left.
    locked = True if locked_before is None else bool(locked_before)
    item.edit(**build_tag_edits(TagField(field_name), after, before, locked=locked))


def upload_posters(
    section,
    posters_dir: str,
    prefix: str,
    *,
    dry_run: bool = False,
) -> tuple[int, list[str]]:
    """Upload ``<posters_dir>/<collection-name>.png`` for each collection."""
    root = Path(posters_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Poster directory not found: {posters_dir}")

    uploaded = 0
    missing: list[str] = []
    for collection in section.collections():
        title = collection.title
        if prefix and title.startswith(prefix):
            title = title[len(prefix):]
        path = root / (title.lower().replace(" ", "-") + ".png")
        if not path.is_file():
            missing.append(title)
            continue
        if not dry_run:
            collection.uploadPoster(filepath=str(path))
        uploaded += 1
    return uploaded, missing


#: Called with ``(collection, previous sort title, new sort title)`` after a write.
SortRecorder = Callable[[object, str, str], None]


def sort_collections(
    section,
    sort_prefix: str,
    names: list[str],
    *,
    collection_prefix: str = "",
    dry_run: bool = False,
    record: SortRecorder | None = None,
) -> tuple[int, list[str]]:
    """Prefix the sort title of the named collections.

    Names are matched as written and with ``collection_prefix`` in front,
    since that is how this tool names the collections it creates. v1 issued
    ``section(library).collections(title=...)`` once per name, which refetched
    the whole section list every time; here the collections are read once and
    matched in memory.
    """
    existing = {c.title.casefold(): c for c in section.collections()}
    updated = 0
    not_found: list[str] = []
    for name in names:
        wanted = name.strip()
        collection = existing.get(wanted.casefold()) or existing.get(
            f"{collection_prefix}{wanted}".casefold()
        )
        if collection is None:
            not_found.append(name)
            continue
        sort_title = f"{sort_prefix}{collection.title}"
        current = getattr(collection, "titleSort", None) or ""
        if current == sort_title:
            continue
        if not dry_run:
            collection.edit(**{"titleSort.value": sort_title, "titleSort.locked": 1})
            if record is not None:
                record(collection, current, sort_title)
        updated += 1
    return updated, not_found
