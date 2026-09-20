"""Run orchestration.

One :class:`Pipeline` processes one library. Provider lookups run concurrently
under the per-provider rate limits; Plex writes are dispatched to a small
thread pool as each lookup lands, so fetching and writing overlap instead of
running in two serial phases.

Every action opens a row in the ``runs`` table and closes it on *every* exit:
a normal return, a cancellation, or an exception that escapes the item loop.
An open row is what the UI renders as "running", so leaving one behind on an
error path would show a phantom run until the next restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from .config import AppConfig, LibraryRun
from .errors import PlexConnectionError, ProviderError, ProviderNotFound
from .models import ExternalId, ItemOutcome, MediaItem, ProviderResult, RunReport, TagField
from .plexsvc import client as plex_client
from .plexsvc.writer import PlexWriter, sort_collections, upload_posters
from .providers import AniDbMapper, LookupRequest, Provider, ProviderPool, build_providers
from .store import CachedState, Store

log = logging.getLogger(__name__)


def media_key(item: MediaItem) -> str:
    """Cache key: a stable GUID when Plex has one, else title + year.

    Using the GUID means renaming a file in Plex no longer orphans its cache
    entry, which is what the v1 ``"Title (Year)"`` key did. Bindings are keyed
    the same way, so the UI can address an item by it.
    """
    for scheme in ("tmdb", "mal", "anilist", "tvdb", "anidb", "imdb"):
        found = item.find_id(scheme)
        if found is not None:
            return str(found)
    return item.identifier


ProgressFn = Callable[[ItemOutcome], None]
#: Called once an action knows its scope: (run_id, total items, items to process).
BeginFn = Callable[[str, int, int], None]
#: ``media_key -> (provider name, pinned id)``, loaded once per action.
Bindings = dict[str, tuple[str, ExternalId]]


class RunScope:
    """One open run row and the report that will close it."""

    __slots__ = ("_closed", "_started", "_store", "report")

    def __init__(self, store: Store, report: RunReport) -> None:
        self.report = report
        self._store = store
        self._started = time.monotonic()
        self._closed = False

    def close(self) -> None:
        """Write the final report. Idempotent: the first close wins."""
        if self._closed:
            return
        self._closed = True
        self.report.duration_s = time.monotonic() - self._started
        self._store.finish_run(self.report)


class Pipeline:
    """Applies one :class:`LibraryRun` against a Plex server."""

    def __init__(
        self,
        config: AppConfig,
        store: Store,
        server,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> None:
        self.config = config
        self.store = store
        self.server = server
        self.dry_run = dry_run
        self.force = force

    # -- run bookkeeping ------------------------------------------------

    @contextlib.contextmanager
    def _open_run(self, run: LibraryRun, action: str) -> Iterator[RunScope]:
        """Open a run row; whatever leaves the block closes it.

        Cancellation marks the report ``cancelled``; any other exception is
        recorded as the report's ``error`` and re-raised. Either way the row
        gets a finish time.
        """
        run_id = self.store.start_run(run.library, action, dry_run=self.dry_run)
        report = RunReport(run_id=run_id, library=run.library, action=action, dry_run=self.dry_run)
        scope = RunScope(self.store, report)
        try:
            yield scope
        except asyncio.CancelledError:
            report.cancelled = True
            scope.close()
            raise
        except Exception as exc:
            report.error = f"{type(exc).__name__}: {exc}"
            log.error("%s/%s aborted: %s", run.library, action, report.error)
            scope.close()
            raise
        scope.close()

    async def _drain(
        self,
        scope: RunScope,
        tasks: list[asyncio.Task],
        progress: ProgressFn | None,
        *,
        pool: ProviderPool | None,
        writer: PlexWriter,
    ) -> None:
        """Tally every task as it lands. On any interruption, wind down first."""
        try:
            for coro in asyncio.as_completed(tasks):
                outcome = await coro
                self._tally(scope.report, outcome)
                if progress is not None:
                    progress(outcome)
        except asyncio.CancelledError:
            scope.report.cancelled = True
            await self._settle(scope, tasks, pool, writer, close=True)
            raise
        except Exception:
            await self._settle(scope, tasks, pool, writer, close=False)
            raise

    async def _settle(
        self,
        scope: RunScope,
        tasks: list[asyncio.Task],
        pool: ProviderPool | None,
        writer: PlexWriter,
        *,
        close: bool,
    ) -> None:
        """Stop in-flight items and record what did happen.

        The row is closed *before* waiting on the workers, so a second cancel
        arriving mid-wait cannot leave it dangling.
        """
        for task in tasks:
            task.cancel()
        scope.report.provider_requests = pool.request_count if pool is not None else 0
        scope.report.plex_requests = writer.requests
        if close:
            scope.close()
        await asyncio.gather(*tasks, return_exceptions=True)

    # -- genre / collection tagging --------------------------------------

    async def tag_library(
        self,
        run: LibraryRun,
        *,
        progress: ProgressFn | None = None,
        on_begin: BeginFn | None = None,
    ) -> RunReport:
        """Fetch genres for every item that needs them and write them to Plex.

        Items already cached under the current settings fingerprint are skipped
        without a provider call. The rest are resolved concurrently, and each
        result is written as soon as it arrives rather than after the whole
        library has been fetched.
        """
        action = "genres" if run.use_genres else "collections"
        with self._open_run(run, action) as scope:
            report = scope.report
            fingerprint = self.config.fingerprint(run)
            rules = self.config.rules_for(run)
            field = TagField.GENRE if run.use_genres else TagField.COLLECTION
            writer = PlexWriter(self.store, report.run_id, run.library, dry_run=self.dry_run)

            items = await asyncio.to_thread(plex_client.iter_library, self.server, run.library)
            log.debug("%s: %d items in library", run.library, len(items))

            states = self.store.states_for_library(run.library)
            pending = self._pending(items, run, fingerprint, states)
            report.skipped = len(items) - len(pending)
            if on_begin is not None:
                on_begin(report.run_id, len(items), len(pending))
            if not pending:
                return report

            settings = self.config.providers
            pool = build_providers(run.resolved_providers, run.type, settings)
            mapper = AniDbMapper(self.store, enabled=run.type.is_anime)
            bindings = self.store.bindings_for_library(run.library)

            fetch_sem = asyncio.Semaphore(settings.concurrency)
            # Plex is a single home server; a couple of concurrent writes is plenty.
            write_sem = asyncio.Semaphore(2)

            async def handle(item: MediaItem) -> ItemOutcome:
                key = media_key(item)
                try:
                    async with fetch_sem:
                        result = await self._resolve(item, run, pool, mapper, bindings)
                    genres = rules.apply(result.genres)
                    if not genres:
                        raise ProviderNotFound(
                            f"{result.provider} returned no usable genres "
                            f"(all filtered by your ignore rules?)"
                        )

                    async with write_sem:
                        outcome = await asyncio.to_thread(
                            writer.write_tags,
                            item,
                            field,
                            genres,
                            clear=run.clear_genres,
                            prefix=self.config.plex.collection_prefix,
                        )
                        if run.rate_media and result.score is not None:
                            await asyncio.to_thread(writer.set_rating, item, result.score)

                    if not self.dry_run:
                        self.store.record_success(
                            run.library, key,
                            fingerprint=fingerprint, title=item.title, year=item.year,
                            rating_key=item.rating_key, genres=outcome.after,
                            provider=result.provider, provider_id=result.provider_id,
                            score=result.score, source=result.matched_by,
                        )
                    return ItemOutcome(
                        item=item,
                        status="written" if outcome.changed else "unchanged",
                        genres=outcome.after,
                        provider=result.provider,
                        provider_id=result.provider_id,
                    )

                except (ProviderError, PlexConnectionError) as exc:
                    if not self.dry_run:
                        self.store.record_failure(
                            run.library, key,
                            fingerprint=fingerprint, title=item.title, year=item.year,
                            rating_key=item.rating_key, error=str(exc),
                        )
                    return ItemOutcome(
                        item=item, status="failed", error=str(exc),
                        retryable=getattr(exc, "retryable", True),
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    log.exception("Unexpected failure on %s", item.title)
                    if not self.dry_run:
                        self.store.record_failure(
                            run.library, key,
                            fingerprint=fingerprint, title=item.title, year=item.year,
                            rating_key=item.rating_key, error=f"{type(exc).__name__}: {exc}",
                        )
                    return ItemOutcome(item=item, status="failed", error=str(exc))

            async with pool:
                tasks = [asyncio.create_task(handle(item)) for item in pending]
                await self._drain(scope, tasks, progress, pool=pool, writer=writer)
                report.provider_requests = pool.request_count

            report.plex_requests = writer.requests
            return report

    def _pending(
        self,
        items: list[MediaItem],
        run: LibraryRun,
        fingerprint: str,
        states: dict[str, CachedState],
    ) -> list[MediaItem]:
        """The items the cache says still need work under these settings.

        ``states`` is the library's whole cache, loaded in one query; the skip
        rule itself is arithmetic. Rows imported from v1's progress files are
        keyed ``"Title (Year)"``; when an item now has a GUID key, its legacy
        row is adopted and moved under the new key so the import counts.
        """
        now = time.time()
        promotions: dict[str, str] = {}
        pending: list[MediaItem] = []
        for item in items:
            key = media_key(item)
            state = states.get(key)
            if state is None and key != item.identifier:
                state = states.get(item.identifier)
                if state is not None:
                    promotions[item.identifier] = key
            if Store.needs_work(state, fingerprint, force=self.force, now=now):
                pending.append(item)
        if promotions:
            moved = self.store.rename_media_keys(run.library, promotions)
            log.info("%s: adopted %d v1 cache entries under their GUID keys", run.library, moved)
        return pending

    async def _resolve(
        self,
        item: MediaItem,
        run: LibraryRun,
        pool: ProviderPool,
        mapper: AniDbMapper,
        bindings: Bindings,
    ) -> ProviderResult:
        """Try each configured provider in order until one answers."""
        external_ids = await mapper.expand(item.guids)
        binding = bindings.get(media_key(item))

        request = LookupRequest(
            title=item.title,
            year=item.year,
            media_type=run.type,
            use_keywords=run.use_keywords,
            external_ids=external_ids,
            pinned=binding[1] if binding else None,
        )

        errors: list[str] = []
        providers: list[Provider] = list(pool.providers)
        if binding is not None:
            # A manual binding names its provider; try that one first.
            providers.sort(key=lambda p: p.name != binding[0])

        for provider in providers:
            try:
                return await provider.resolve(request)
            except ProviderNotFound as exc:
                errors.append(str(exc))
            except ProviderError as exc:
                errors.append(str(exc))
        raise ProviderNotFound("; ".join(errors) or "no provider could resolve this title")

    @staticmethod
    def _tally(report: RunReport, outcome: ItemOutcome) -> None:
        if outcome.status == "written":
            report.written += 1
        elif outcome.status == "unchanged":
            report.unchanged += 1
        elif outcome.status == "skipped":
            report.skipped += 1
        else:
            report.failed += 1
            report.failures.append((outcome.item.identifier, outcome.error or "unknown"))

    @staticmethod
    def _cached_score(state: CachedState | None, fingerprint: str) -> float | None:
        """The score the tag action already fetched, if it is still current."""
        if state is None or state.status != "ok" or state.fingerprint != fingerprint:
            return None
        return state.score

    # -- post-processing actions -----------------------------------------

    async def rate_library(
        self,
        run: LibraryRun,
        *,
        progress: ProgressFn | None = None,
        on_begin: BeginFn | None = None,
    ) -> RunReport:
        """Overwrite Plex ratings with provider scores.

        The tag action stores each item's score with its cache entry, so a
        nightly run rates from the cache; only items it has not seen (or whose
        settings changed) go back to the provider.
        """
        with self._open_run(run, "ratings") as scope:
            report = scope.report
            writer = PlexWriter(self.store, report.run_id, run.library, dry_run=self.dry_run)

            items = await asyncio.to_thread(plex_client.iter_library, self.server, run.library)
            if on_begin is not None:
                on_begin(report.run_id, len(items), len(items))
            fingerprint = self.config.fingerprint(run)
            states = self.store.states_for_library(run.library)
            bindings = self.store.bindings_for_library(run.library)
            pool = build_providers(run.resolved_providers, run.type, self.config.providers)
            mapper = AniDbMapper(self.store, enabled=run.type.is_anime)
            fetch_sem = asyncio.Semaphore(self.config.providers.concurrency)
            write_sem = asyncio.Semaphore(2)

            async def handle(item: MediaItem) -> ItemOutcome:
                try:
                    score = self._cached_score(states.get(media_key(item)), fingerprint)
                    if score is None:
                        async with fetch_sem:
                            result = await self._resolve(item, run, pool, mapper, bindings)
                        score = result.score
                    if score is None:
                        return ItemOutcome(item=item, status="unchanged")
                    async with write_sem:
                        ok = await asyncio.to_thread(writer.set_rating, item, score)
                    return ItemOutcome(item=item, status="written" if ok else "unchanged")
                except (ProviderError, PlexConnectionError) as exc:
                    return ItemOutcome(
                        item=item, status="failed", error=str(exc),
                        retryable=getattr(exc, "retryable", True),
                    )
                except Exception as exc:  # a Plex-side refusal must not abort the pass
                    log.exception("Unexpected failure rating %s", item.title)
                    return ItemOutcome(
                        item=item, status="failed", error=f"{type(exc).__name__}: {exc}"
                    )

            async with pool:
                tasks = [asyncio.create_task(handle(i)) for i in items]
                await self._drain(scope, tasks, progress, pool=pool, writer=writer)
                report.provider_requests = pool.request_count

            report.plex_requests = writer.requests
            return report

    async def rating_collections(
        self,
        run: LibraryRun,
        *,
        progress: ProgressFn | None = None,
        on_begin: BeginFn | None = None,
    ) -> RunReport:
        """Bucket media into '1 Star Rating' ... '5 Star Rating' collections.

        Plex's own rating is used when the agent filled it; otherwise the
        provider score cached by the tag action (anime agents rarely set a
        Plex rating, and v1 asked MyAnimeList directly for exactly this).
        """
        with self._open_run(run, "rating-collections") as scope:
            report = scope.report
            writer = PlexWriter(self.store, report.run_id, run.library, dry_run=self.dry_run)
            items = await asyncio.to_thread(plex_client.iter_library, self.server, run.library)
            if on_begin is not None:
                on_begin(report.run_id, len(items), len(items))
            states = self.store.states_for_library(run.library)
            write_sem = asyncio.Semaphore(2)

            async def handle(item: MediaItem) -> ItemOutcome:
                rating = getattr(item.handle, "rating", None) or getattr(
                    item.handle, "audienceRating", None
                )
                if rating is None:
                    cached = states.get(media_key(item))
                    rating = cached.score if cached is not None else None
                bucket = rating_bucket(rating)
                if bucket is None:
                    return ItemOutcome(item=item, status="skipped")
                try:
                    async with write_sem:
                        outcome = await asyncio.to_thread(
                            writer.write_tags, item, TagField.COLLECTION, [bucket], clear=False
                        )
                except Exception as exc:  # one refused write must not abort the pass
                    log.warning("Could not set the rating collection of %s: %s", item.title, exc)
                    return ItemOutcome(
                        item=item, status="failed", error=f"{type(exc).__name__}: {exc}"
                    )
                return ItemOutcome(item=item, status="written" if outcome.changed else "unchanged")

            tasks = [asyncio.create_task(handle(i)) for i in items]
            await self._drain(scope, tasks, progress, pool=None, writer=writer)
            report.plex_requests = writer.requests
            return report

    async def set_posters(
        self, run: LibraryRun, posters_dir: str, *, on_begin: BeginFn | None = None
    ) -> RunReport:
        """Upload collection artwork from a poster directory."""
        with self._open_run(run, "posters") as scope:
            report = scope.report
            if on_begin is not None:
                on_begin(report.run_id, 0, 0)
            if not await asyncio.to_thread(Path(posters_dir).is_dir):
                # A missing directory is a configuration gap, not a reason to
                # abort the whole job: report it and let the next action run.
                report.error = f"Poster directory not found: {posters_dir}"
                log.warning("%s: %s", run.library, report.error)
                return report
            section = await asyncio.to_thread(plex_client.get_section, self.server, run.library)
            uploaded, missing = await asyncio.to_thread(
                upload_posters, section, posters_dir,
                self.config.plex.collection_prefix, dry_run=self.dry_run,
            )
            report.written = uploaded
            report.skipped = len(missing)
            return report

    async def sort(self, run: LibraryRun, *, on_begin: BeginFn | None = None) -> RunReport:
        """Prefix the sort titles of the configured collections."""
        with self._open_run(run, "sort") as scope:
            report = scope.report
            if on_begin is not None:
                on_begin(report.run_id, 0, 0)
            rules = self.config.rules_for(run)
            if not rules.sorted_prefix:
                report.error = f"No sortedPrefix configured for {run.library!r}; nothing to sort."
                log.warning("%s", report.error)
                return report
            section = await asyncio.to_thread(plex_client.get_section, self.server, run.library)

            def record(collection, before: str, after: str) -> None:
                self.store.add_snapshot(
                    report.run_id, run.library, int(collection.ratingKey), collection.title,
                    "titleSort", [before], [after],
                )

            updated, not_found = await asyncio.to_thread(
                sort_collections, section, rules.sorted_prefix, rules.sorted_collections,
                collection_prefix=self.config.plex.collection_prefix,
                dry_run=self.dry_run, record=record,
            )
            report.written = updated
            report.skipped = len(not_found)
            report.failures = [(name, "no such collection") for name in not_found]
            return report


def rating_bucket(rating: float | None) -> str | None:
    """Map a 0-10 rating onto Plex's five star buckets."""
    if rating is None:
        return None
    try:
        value = float(rating)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    stars = max(1, min(5, round(value / 2)))
    return f"{stars} Star Rating"
