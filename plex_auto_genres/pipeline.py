"""Run orchestration.

One :class:`Pipeline` processes one library. Provider lookups run concurrently
under the per-provider rate limits; Plex writes are dispatched to a small
thread pool as each lookup lands, so fetching and writing overlap instead of
running in two serial phases.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from .config import AppConfig, LibraryRun
from .errors import PlexConnectionError, ProviderError, ProviderNotFound
from .models import ItemOutcome, MediaItem, RunReport, TagField
from .plexsvc import client as plex_client
from .plexsvc.writer import PlexWriter, sort_collections, upload_posters
from .providers import AniDbMapper, LookupRequest, Provider, ProviderPool, build_providers
from .store import Store

log = logging.getLogger(__name__)

ProgressFn = Callable[[ItemOutcome], None]
#: Called once an action knows its scope: (run_id, total items, items to process).
BeginFn = Callable[[str, int, int], None]


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
        started = time.monotonic()
        action = "genres" if run.use_genres else "collections"
        run_id = self.store.start_run(run.library, action, dry_run=self.dry_run)
        report = RunReport(
            run_id=run_id, library=run.library, action=action, dry_run=self.dry_run
        )

        fingerprint = self.config.fingerprint(run)
        rules = self.config.rules_for(run)
        field = TagField.GENRE if run.use_genres else TagField.COLLECTION
        writer = PlexWriter(self.store, run_id, run.library, dry_run=self.dry_run)

        items = await asyncio.to_thread(plex_client.iter_library, self.server, run.library)
        log.debug("%s: %d items in library", run.library, len(items))

        pending = self._pending(items, run, fingerprint)
        report.skipped = len(items) - len(pending)
        if on_begin is not None:
            on_begin(run_id, len(items), len(pending))

        if not pending:
            report.duration_s = time.monotonic() - started
            self.store.finish_run(report)
            return report

        settings = self.config.providers
        pool = build_providers(run.resolved_providers, run.type, settings)
        mapper = AniDbMapper(self.store, enabled=run.type.is_anime)

        fetch_sem = asyncio.Semaphore(settings.concurrency)
        # Plex is a single home server; a couple of concurrent writes is plenty.
        write_sem = asyncio.Semaphore(2)

        async def handle(item: MediaItem) -> ItemOutcome:
            key = self._media_key(item)
            try:
                async with fetch_sem:
                    result = await self._resolve(item, run, pool, mapper)
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
            try:
                for coro in asyncio.as_completed(tasks):
                    outcome = await coro
                    self._tally(report, outcome)
                    if progress is not None:
                        progress(outcome)
            except asyncio.CancelledError:
                await self._abandon(tasks, report, pool, writer, started)
                raise
            report.provider_requests = pool.request_count

        report.plex_requests = writer.requests
        report.duration_s = time.monotonic() - started
        self.store.finish_run(report)
        return report

    def _pending(
        self, items: list[MediaItem], run: LibraryRun, fingerprint: str
    ) -> list[MediaItem]:
        """The items the cache says still need work under these settings."""
        return [
            item for item in items
            if self.store.should_process(
                run.library, self._media_key(item), fingerprint, force=self.force
            )
        ]

    async def _abandon(self, tasks, report: RunReport, pool, writer, started: float) -> None:
        """Wind a run down after cancellation.

        Nothing new is scheduled; the run row is closed with what did happen
        (marked ``cancelled``) *before* waiting on in-flight items, so a second
        cancel cannot leave it dangling; then the workers are let settle.
        """
        for task in tasks:
            task.cancel()
        report.cancelled = True
        report.provider_requests = pool.request_count
        report.plex_requests = writer.requests
        report.duration_s = time.monotonic() - started
        self.store.finish_run(report)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _resolve(
        self,
        item: MediaItem,
        run: LibraryRun,
        pool: ProviderPool,
        mapper: AniDbMapper,
    ):
        """Try each configured provider in order until one answers."""
        external_ids = await mapper.expand(item.guids)
        binding = self.store.get_binding(run.library, self._media_key(item))

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
    def _media_key(item: MediaItem) -> str:
        """Cache key: a stable GUID when Plex has one, else title + year.

        Using the GUID means renaming a file in Plex no longer orphans its
        cache entry, which is what the v1 ``"Title (Year)"`` key did.
        """
        for scheme in ("tmdb", "mal", "anilist", "tvdb", "anidb", "imdb"):
            found = item.find_id(scheme)
            if found is not None:
                return str(found)
        return item.identifier

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

    # -- post-processing actions -----------------------------------------

    async def rate_library(
        self,
        run: LibraryRun,
        *,
        progress: ProgressFn | None = None,
        on_begin: BeginFn | None = None,
    ) -> RunReport:
        """Overwrite Plex ratings with provider scores."""
        started = time.monotonic()
        run_id = self.store.start_run(run.library, "ratings", dry_run=self.dry_run)
        report = RunReport(
            run_id=run_id, library=run.library, action="ratings", dry_run=self.dry_run
        )
        writer = PlexWriter(self.store, run_id, run.library, dry_run=self.dry_run)

        items = await asyncio.to_thread(plex_client.iter_library, self.server, run.library)
        if on_begin is not None:
            on_begin(run_id, len(items), len(items))
        pool = build_providers(run.resolved_providers, run.type, self.config.providers)
        mapper = AniDbMapper(self.store, enabled=run.type.is_anime)
        fetch_sem = asyncio.Semaphore(self.config.providers.concurrency)

        async def handle(item: MediaItem) -> ItemOutcome:
            try:
                async with fetch_sem:
                    result = await self._resolve(item, run, pool, mapper)
                if result.score is None:
                    return ItemOutcome(item=item, status="unchanged")
                ok = await asyncio.to_thread(writer.set_rating, item, result.score)
                return ItemOutcome(item=item, status="written" if ok else "unchanged")
            except ProviderError as exc:
                return ItemOutcome(item=item, status="failed", error=str(exc))

        async with pool:
            tasks = [asyncio.create_task(handle(i)) for i in items]
            try:
                for coro in asyncio.as_completed(tasks):
                    outcome = await coro
                    self._tally(report, outcome)
                    if progress is not None:
                        progress(outcome)
            except asyncio.CancelledError:
                await self._abandon(tasks, report, pool, writer, started)
                raise
            report.provider_requests = pool.request_count

        report.plex_requests = writer.requests
        report.duration_s = time.monotonic() - started
        self.store.finish_run(report)
        return report

    async def rating_collections(
        self,
        run: LibraryRun,
        *,
        progress: ProgressFn | None = None,
        on_begin: BeginFn | None = None,
    ) -> RunReport:
        """Bucket media into '1 Star Rating' ... '5 Star Rating' collections."""
        started = time.monotonic()
        run_id = self.store.start_run(run.library, "rating-collections", dry_run=self.dry_run)
        report = RunReport(
            run_id=run_id, library=run.library, action="rating-collections",
            dry_run=self.dry_run,
        )
        writer = PlexWriter(self.store, run_id, run.library, dry_run=self.dry_run)
        items = await asyncio.to_thread(plex_client.iter_library, self.server, run.library)
        if on_begin is not None:
            on_begin(run_id, len(items), len(items))

        for item in items:
            rating = getattr(item.handle, "rating", None) or getattr(
                item.handle, "audienceRating", None
            )
            bucket = rating_bucket(rating)
            if bucket is None:
                report.skipped += 1
                if progress is not None:
                    progress(ItemOutcome(item=item, status="skipped"))
                continue
            outcome = await asyncio.to_thread(
                writer.write_tags, item, TagField.COLLECTION, [bucket], clear=False
            )
            report.written += int(outcome.changed)
            report.unchanged += int(not outcome.changed)
            if progress is not None:
                progress(ItemOutcome(
                    item=item, status="written" if outcome.changed else "unchanged"
                ))

        report.plex_requests = writer.requests
        report.duration_s = time.monotonic() - started
        self.store.finish_run(report)
        return report

    async def set_posters(
        self, run: LibraryRun, posters_dir: str, *, on_begin: BeginFn | None = None
    ) -> RunReport:
        """Upload collection artwork from a poster directory."""
        started = time.monotonic()
        run_id = self.store.start_run(run.library, "posters", dry_run=self.dry_run)
        report = RunReport(
            run_id=run_id, library=run.library, action="posters", dry_run=self.dry_run
        )
        if on_begin is not None:
            on_begin(run_id, 0, 0)
        section = await asyncio.to_thread(plex_client.get_section, self.server, run.library)
        uploaded, missing = await asyncio.to_thread(
            upload_posters, section, posters_dir,
            self.config.plex.collection_prefix, dry_run=self.dry_run,
        )
        report.written = uploaded
        report.skipped = len(missing)
        report.duration_s = time.monotonic() - started
        self.store.finish_run(report)
        return report

    async def sort(self, run: LibraryRun, *, on_begin: BeginFn | None = None) -> RunReport:
        """Prefix the sort titles of the configured collections."""
        started = time.monotonic()
        run_id = self.store.start_run(run.library, "sort", dry_run=self.dry_run)
        report = RunReport(
            run_id=run_id, library=run.library, action="sort", dry_run=self.dry_run
        )
        if on_begin is not None:
            on_begin(run_id, 0, 0)
        rules = self.config.rules_for(run)
        if not rules.sorted_prefix:
            raise ValueError(
                f"No sortedPrefix configured for {run.library!r}; nothing to sort."
            )
        section = await asyncio.to_thread(plex_client.get_section, self.server, run.library)
        updated, not_found = await asyncio.to_thread(
            sort_collections, section, rules.sorted_prefix,
            rules.sorted_collections, dry_run=self.dry_run,
        )
        report.written = updated
        report.skipped = len(not_found)
        report.failures = [(name, "no such collection") for name in not_found]
        report.duration_s = time.monotonic() - started
        self.store.finish_run(report)
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
