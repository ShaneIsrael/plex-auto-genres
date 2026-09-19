"""Command line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .config import AppConfig, CONFIG_VERSION, config_json_schema, load_config, migrate_v1
from .errors import ConfigError, PagError, PlexConnectionError
from .models import ItemOutcome, MediaType, RunReport
from .pipeline import Pipeline
from .plexsvc import client as plex_client
from .plexsvc.writer import undo_run
from .providers import LookupRequest, build_providers
from .reporting import ProgressBar, Style, print_report
from .store import Store
from .taxonomy import check_names, fetch_live_genres

log = logging.getLogger("plex_auto_genres")

DEFAULT_CONFIG = "config/config.json"
DEFAULT_DB = "logs/plex-auto-genres.db"


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for every subcommand."""
    parser = argparse.ArgumentParser(
        prog="plex-auto-genres",
        description="Tag your Plex media with genres from TMDB, MyAnimeList or AniList.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  plex-auto-genres run                       # every enabled library in the config\n"
            "  plex-auto-genres run --library Animes --dry # preview one library\n"
            "  plex-auto-genres query 'Cowboy Bebop' --type anime\n"
            "  plex-auto-genres bind Animes 'Monster' mal 19\n"
            "  plex-auto-genres undo 4f2a1c9b0e77\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Path to config.json.")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to the state database.")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="-v info, -vv debug.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")

    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Process libraries (the default command).")
    run.add_argument("--library", action="append", help="Only this library. Repeatable.")
    run.add_argument("--type", choices=[t.value for t in MediaType],
                     help="Override the library type (requires a single --library).")
    run.add_argument("--dry", "--dry-run", dest="dry", action="store_true",
                     help="Report what would change without touching Plex.")
    run.add_argument("-f", "--force", action="store_true",
                     help="Ignore the cache and reprocess everything.")
    run.add_argument("-y", "--yes", action="store_true", help="Do not prompt for confirmation.")
    run.add_argument("--no-progress", action="store_true", help="Disable the progress bar.")
    run.add_argument("--only", choices=["tags", "posters", "sort", "ratings", "rating-collections"],
                     action="append", help="Run only these actions. Repeatable.")
    run.add_argument("--posters-dir", default="posters", help="Root of the poster directories.")

    query = sub.add_parser("query", help="Look up a title without changing anything.")
    query.add_argument("title", nargs="+")
    query.add_argument("--type", required=True, choices=[t.value for t in MediaType])
    query.add_argument("--year", type=int)
    query.add_argument("--provider", action="append", help="Override the provider order.")
    query.add_argument("--keywords", action="store_true", help="TMDB: fetch keywords, not genres.")

    bind = sub.add_parser("bind", help="Pin a Plex item to a specific provider id.")
    bind.add_argument("library")
    bind.add_argument("title", help="Plex title, or the cache key shown by 'failures'.")
    bind.add_argument("provider", choices=["tmdb", "mal", "anilist", "anidb", "tvdb", "imdb"])
    bind.add_argument("provider_id")
    bind.add_argument("--note", help="Free-text reminder of why this binding exists.")

    unbind = sub.add_parser("unbind", help="Remove a manual binding.")
    unbind.add_argument("library")
    unbind.add_argument("title")

    bindings = sub.add_parser("bindings", help="List manual bindings.")
    bindings.add_argument("--library")

    undo = sub.add_parser("undo", help="Restore the tags a run overwrote.")
    undo.add_argument("run_id")
    undo.add_argument("--dry", action="store_true")

    runs = sub.add_parser("runs", help="Show recent runs.")
    runs.add_argument("--library")
    runs.add_argument("--limit", type=int, default=20)

    failures = sub.add_parser("failures", help="Show items that could not be resolved.")
    failures.add_argument("--library", required=True)
    failures.add_argument("--limit", type=int, default=50)
    failures.add_argument("--retry", action="store_true",
                          help="Clear their backoff so the next run retries them.")

    sub.add_parser("doctor", help="Validate the config and flag stale genre names.")
    sub.add_parser("schema", help="Print the config JSON Schema (for tooling and web UIs).")

    migrate = sub.add_parser("migrate-config", help="Rewrite a v1 config file in the v2 format.")
    migrate.add_argument("--out", help="Write here instead of stdout.")

    schedule = sub.add_parser("schedule", help="Run on a cron schedule, in the foreground.")
    schedule.add_argument("--cron", default="0 1 * * *", help="Five-field cron expression.")
    schedule.add_argument("--now", action="store_true", help="Also run once on start.")
    schedule.add_argument("--posters-dir", default="posters")

    return parser


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _setup_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # plexapi is extremely chatty at DEBUG.
    logging.getLogger("plexapi").setLevel(max(level, logging.INFO))
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _confirm(prompt: str) -> bool:
    try:
        while True:
            answer = input(f"{prompt} [y/N] ").strip().lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("", "n", "no"):
                return False
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _selected_runs(config: AppConfig, names: list[str] | None, type_override: str | None):
    if not names:
        return [r for r in config.libraries if r.enabled]

    selected = []
    for name in names:
        found = config.find(name)
        if found is None:
            raise ConfigError(
                f"Library {name!r} is not in the config. Known: "
                f"{', '.join(r.library for r in config.libraries) or '(none)'}."
            )
        selected.append(found)

    if type_override:
        if len(selected) != 1:
            raise ConfigError("--type can only be used with exactly one --library.")
        selected = [selected[0].model_copy(update={"type": MediaType(type_override)})]
    return selected


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


async def cmd_run(args, config: AppConfig, store: Store, style: Style) -> int:
    """Process the selected libraries and their post-processing actions."""
    runs = _selected_runs(config, args.library, args.type)
    if not runs:
        print(style.yellow("No enabled libraries in the config; nothing to do."))
        return 0

    only = set(args.only or [])
    if not args.yes and not args.dry and sys.stdin.isatty():
        names = ", ".join(style.cyan(r.library) for r in runs)
        target = "genre tags" if any(r.use_genres for r in runs) else "collections"
        print(f"About to update {target} for: {names}")
        if not _confirm("Continue?"):
            return 130

    server = await asyncio.to_thread(plex_client.connect, config.plex)
    pipeline = Pipeline(config, store, server, dry_run=args.dry, force=args.force)

    reports: list[RunReport] = []
    exit_code = 0

    for run in runs:
        _import_legacy_once(store, config, run)

        if not only or "tags" in only:
            total = await asyncio.to_thread(_library_size, server, run.library)
            with ProgressBar(total, enabled=not args.no_progress) as progress:
                def tick(outcome: ItemOutcome, _p=progress) -> None:
                    _p.advance(suffix=outcome.item.title)

                report = await pipeline.tag_library(run, progress=tick)
            reports.append(report)
            if report.failed:
                exit_code = 1

        if (not only and run.rate_media) or "ratings" in only:
            reports.append(await pipeline.rate_library(run))
        if (not only and run.create_rating_collections) or "rating-collections" in only:
            reports.append(await pipeline.rating_collections(run))
        if (not only and run.set_posters) or "posters" in only:
            posters_dir = str(Path(args.posters_dir) / run.type.value)
            reports.append(await pipeline.set_posters(run, posters_dir))
        if (not only and run.sort_collections) or "sort" in only:
            reports.append(await pipeline.sort(run))

    if args.json:
        print(json.dumps([r.as_dict() for r in reports], indent=2, ensure_ascii=False))
    else:
        for report in reports:
            print_report(report, style)
    return exit_code


def _library_size(server, library: str) -> int:
    try:
        return plex_client.get_section(server, library).totalSize
    except Exception:
        return 0


def _import_legacy_once(store: Store, config: AppConfig, run) -> None:
    """Seed the database from v1's logs/*.txt the first time a library runs."""
    key = f"legacy_imported::{run.library}"
    if store.kv_get(key):
        return
    imported = store.import_legacy_logs(
        "logs", run.library, run.type.value, config.fingerprint(run)
    )
    store.kv_set(key, "1")
    if imported:
        log.info("Imported %d entries from the v1 progress files for %s", imported, run.library)


async def cmd_query(args, config: AppConfig, store: Store, style: Style) -> int:
    """Show what the providers return for a title, without writing anything."""
    del store  # kept for signature symmetry with the other cmd_* functions
    media_type = MediaType(args.type)
    providers = tuple(args.provider) if args.provider else None
    from .config import DEFAULT_PROVIDERS

    names = providers or DEFAULT_PROVIDERS[media_type]
    title = " ".join(args.title)

    pool = build_providers(names, media_type, config.providers)
    request = LookupRequest(
        title=title, year=args.year, media_type=media_type, use_keywords=args.keywords
    )

    async with pool:
        for provider in pool.providers:
            try:
                result = await provider.resolve(request)
            except PagError as exc:
                print(f"{style.dim(provider.name)}: {style.red(str(exc))}")
                continue

            origin = style.dim(f"[{result.provider}:{result.provider_id}]")
            print(f"\n{style.bold(result.title)}  {origin}")
            if result.url:
                print(f"  {style.dim(result.url)}")
            if result.score is not None:
                print(f"  score: {style.cyan(f'{result.score}/10')}")
            print(f"  raw:      {', '.join(result.genres) or '(none)'}")

            run = config.find(args.library) if getattr(args, "library", None) else None
            rules = config.rules_for(run) if run else None
            if rules is None:
                defaults = config.defaults.get(media_type)
                rules = defaults
            if rules is not None:
                filtered = ", ".join(rules.apply(result.genres)) or "(none)"
                print(f"  filtered: {style.green(filtered)}")
            return 0
    return 1


def cmd_bind(args, store: Store, style: Style) -> int:
    """Pin a Plex item to a provider id."""
    store.set_binding(args.library, args.title, args.provider, args.provider_id, args.note)
    print(
        f"{style.green('bound')} {style.bold(args.title)} in {args.library} "
        f"-> {args.provider}://{args.provider_id}"
    )
    print(style.dim("  Its cache entry was cleared; the next run will use this id."))
    return 0


def cmd_unbind(args, store: Store, style: Style) -> int:
    """Remove a manual binding."""
    if store.delete_binding(args.library, args.title):
        print(f"{style.green('removed')} binding for {args.title} in {args.library}")
        return 0
    print(style.yellow(f"No binding for {args.title!r} in {args.library!r}."))
    return 1


def cmd_bindings(args, store: Store, style: Style, as_json: bool) -> int:
    """List manual bindings."""
    rows = store.list_bindings(args.library)
    if as_json:
        print(json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print(style.dim("No manual bindings."))
        return 0
    for row in rows:
        note = f"  {style.dim(row['note'])}" if row["note"] else ""
        print(f"{row['library']:20} {row['media_key']:40} -> "
              f"{row['provider']}://{row['provider_id']}{note}")
    return 0


async def cmd_undo(args, config: AppConfig, store: Store, style: Style) -> int:
    """Restore the tag values a previous run overwrote."""
    row = store.get_run(args.run_id)
    if row is None:
        print(style.red(f"No run with id {args.run_id!r}. See 'plex-auto-genres runs'."))
        return 1
    if row["undone_at"]:
        print(style.yellow("That run was already undone."))
        return 1

    snapshots = store.snapshots_for(args.run_id)
    if not snapshots:
        print(style.yellow("That run recorded no changes, so there is nothing to undo."))
        return 0

    print(f"About to restore {style.bold(str(len(snapshots)))} item(s) "
          f"in {style.cyan(row['library'])} to their pre-run tags.")
    if not args.dry and sys.stdin.isatty() and not _confirm("Continue?"):
        return 130

    server = await asyncio.to_thread(plex_client.connect, config.plex)
    restored, skipped = await asyncio.to_thread(
        undo_run, server, store, args.run_id, dry_run=args.dry
    )
    print(f"{style.green(str(restored))} restored, {skipped} skipped.")
    return 0


def cmd_runs(args, store: Store, style: Style, as_json: bool) -> int:
    """Print the run history."""
    rows = store.recent_runs(args.limit, args.library)
    if as_json:
        print(json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print(style.dim("No runs recorded yet."))
        return 0
    print(f"{'RUN ID':14} {'WHEN':17} {'LIBRARY':20} {'ACTION':18} RESULT")
    for row in rows:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["started_at"]))
        report = json.loads(row["report"]) if row["report"] else {}
        if row["undone_at"]:
            result = style.yellow("undone")
        elif not report:
            result = style.red("interrupted")
        else:
            result = (f"{report.get('written', 0)} written, "
                      f"{report.get('failed', 0)} failed")
        flag = style.dim(" (dry)") if row["dry_run"] else ""
        print(f"{row['run_id']:14} {when:17} {row['library']:20} "
              f"{row['action'] + flag:18} {result}")
    return 0


def cmd_failures(args, store: Store, style: Style, as_json: bool) -> int:
    """Print the items a library could not resolve."""
    rows = store.failures(args.library, args.limit)
    if as_json:
        print(json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print(style.green(f"No failures recorded for {args.library}."))
        return 0
    for row in rows:
        title = f"{row['title']} ({row['year']})" if row["year"] else row["title"]
        attempts = style.dim(f"attempt {row['attempts']}")
        print(f"{style.red('x')} {style.bold(title)}  {attempts}")
        print(f"    {row['last_error']}")
    print(style.dim(
        f"\n{len(rows)} shown. Pin a correct id with: "
        f"plex-auto-genres bind '{args.library}' '<title>' tmdb <id>"
    ))
    if args.retry:
        store.clear_library(args.library)
        print(style.green("Cleared the cache for this library; the next run retries everything."))
    return 0


def cmd_doctor(config_path: str, store: Store, style: Style) -> int:
    """Check the config, the credentials and the anime genre names."""
    problems = 0
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(style.red(str(exc)))
        return 1
    print(f"{style.green('OK')} config parses ({len(config.libraries)} libraries)")

    try:
        config.plex.validate_usable()
        mode = "token" if config.plex.uses_token_auth else "username/password"
        print(f"{style.green('OK')} Plex credentials present ({mode})")
    except ConfigError as exc:
        print(style.red("!!") + f" {exc}")
        problems += 1

    needs_tmdb = any(r.type is not MediaType.ANIME for r in config.libraries)
    if needs_tmdb and not config.providers.tmdb_api_key:
        print(style.red("!!") + " TMDB_API_KEY is unset but you have non-anime libraries.")
        problems += 1

    live = fetch_live_genres(store)
    if live is None:
        print(style.yellow("??") + " Could not reach MyAnimeList to check genre names.")
    else:
        anime_rules = config.defaults.get(MediaType.ANIME)
        if anime_rules is not None:
            names = [*anime_rules.sorted_collections, *anime_rules.ignore,
                     *anime_rules.replace.keys()]
            stale = check_names(names, live)
            if stale:
                problems += 1
                print(style.yellow("!!") + f" {len(stale)} anime genre name(s) MAL no longer uses:")
                for name, replacement in stale:
                    hint = (
                        f" -> {style.green(replacement)}"
                        if replacement
                        else style.dim(" (retired)")
                    )
                    print(f"     {name}{hint}")
            else:
                print(f"{style.green('OK')} anime genre names match the current MAL taxonomy")

    for run in config.libraries:
        defaults = config.defaults.get(run.type)
        capped = run.overrides is not None or (
            defaults is not None and defaults.max_genres is not None
        )
        if run.use_keywords and not capped:
            print(
                style.yellow("??")
                + f" {run.library}: useKeywords with no maxGenres. "
                "TMDB can return 50+ keywords per title."
            )

    print()
    if problems:
        print(style.yellow(f"{problems} thing(s) to look at."))
        return 1
    print(style.green("Everything looks fine."))
    return 0


def cmd_migrate_config(config_path: str, out: str | None, style: Style) -> int:
    """Rewrite a v1 config file in the v2 format."""
    raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if int(raw.get("version", 1)) >= CONFIG_VERSION:
        print(style.yellow(f"{config_path} is already version {raw['version']}."))
        return 0
    migrated = migrate_v1(raw)
    AppConfig.model_validate({**migrated, "plex": {}, "providers": {}})  # validate before writing
    text = json.dumps(migrated, indent=4, ensure_ascii=False)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
        print(style.green(f"Wrote {out}."))
    else:
        print(text)
    return 0


async def cmd_schedule(args, config_path: str, db_path: str, style: Style) -> int:
    """Run on a schedule in the foreground, replacing the container's crond."""
    from croniter import croniter

    if not croniter.is_valid(args.cron):
        print(style.red(f"Invalid cron expression: {args.cron!r}"))
        return 2

    print(f"Scheduler started. Cron: {style.cyan(args.cron)}")
    if args.now:
        await _scheduled_pass(args, config_path, db_path, style)

    while True:
        now = time.time()
        nxt = croniter(args.cron, now).get_next(float)
        wait = max(nxt - now, 1.0)
        print(style.dim(
            f"Next run at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(nxt))} "
            f"(in {int(wait)}s)"
        ))
        try:
            await asyncio.sleep(wait)
        except asyncio.CancelledError:
            print("\nScheduler stopped.")
            return 0
        await _scheduled_pass(args, config_path, db_path, style)


async def _scheduled_pass(args, config_path: str, db_path: str, style: Style) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{style.bold(f'--- scheduled run {stamp} ---')}")
    try:
        config = load_config(config_path)
        with Store(db_path) as store:
            run_args = argparse.Namespace(
                library=None, type=None, dry=False, force=False, yes=True,
                no_progress=True, only=None, posters_dir=args.posters_dir, json=False,
            )
            await cmd_run(run_args, config, store, style)
    except PagError as exc:
        print(style.red(f"Run failed: {exc}"))
    except Exception as exc:  # keep the scheduler alive across failures
        log.exception("Scheduled run crashed")
        print(style.red(f"Run crashed: {exc}"))


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and dispatch. Returns the process exit code."""
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args([*(argv or []), "run"])

    _setup_logging(args.verbose)
    style = Style()

    try:
        if args.command == "schema":
            print(json.dumps(config_json_schema(), indent=2))
            return 0
        if args.command == "migrate-config":
            return cmd_migrate_config(args.config, args.out, style)

        with Store(args.db) as store:
            if args.command == "doctor":
                return cmd_doctor(args.config, store, style)
            if args.command == "bind":
                return cmd_bind(args, store, style)
            if args.command == "unbind":
                return cmd_unbind(args, store, style)
            if args.command == "bindings":
                return cmd_bindings(args, store, style, args.json)
            if args.command == "runs":
                return cmd_runs(args, store, style, args.json)
            if args.command == "failures":
                return cmd_failures(args, store, style, args.json)
            if args.command == "schedule":
                return asyncio.run(cmd_schedule(args, args.config, args.db, style))

            config = load_config(args.config)
            if args.command == "query":
                return asyncio.run(cmd_query(args, config, store, style))
            if args.command == "undo":
                return asyncio.run(cmd_undo(args, config, store, style))
            return asyncio.run(cmd_run(args, config, store, style))

    except KeyboardInterrupt:
        print(style.yellow("\nInterrupted. Progress up to this point has been saved."))
        return 130
    except (ConfigError, PlexConnectionError) as exc:
        print(style.red(str(exc)), file=sys.stderr)
        return 2
    except PagError as exc:
        print(style.red(f"Error: {exc}"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
