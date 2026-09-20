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
from .config import (
    AppConfig,
    CONFIG_VERSION,
    LibraryRun,
    config_json_schema,
    load_config,
    migrate_v1,
)
from .doctor import DoctorReport, run_doctor
from .errors import ConfigError, PagError, PlexConnectionError
from .models import MediaType, RunReport
from .plexsvc import client as plex_client
from .plexsvc.writer import undo_run
from .providers import LookupRequest, build_providers
from .reporting import ProgressBar, Style, print_report
from .runner import ACTIONS, run_libraries
from .scheduler import Scheduler, validate_cron
from .store import Store, run_status

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
    run.add_argument("--only", choices=list(ACTIONS), action="append",
                     help="Run only these actions. Repeatable.")
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

    doctor = sub.add_parser("doctor", help="Validate the config and flag stale genre names.")
    doctor.add_argument("--offline", action="store_true",
                        help="Skip checks that need the network (the MAL genre list).")
    sub.add_parser("schema", help="Print the config JSON Schema (for tooling and web UIs).")

    migrate = sub.add_parser("migrate-config", help="Rewrite a v1 config file in the v2 format.")
    migrate.add_argument("--out", help="Write here instead of stdout.")

    schedule = sub.add_parser("schedule", help="Run on a cron schedule, in the foreground.")
    schedule.add_argument("--cron", default="0 1 * * *",
                          help="Five-field cron expression; the config's schedule block wins.")
    schedule.add_argument("--now", action="store_true", help="Also run once on start.")
    schedule.add_argument("--posters-dir", default="posters")

    serve = sub.add_parser("serve", help="Start the web UI and API (and optionally the scheduler).")
    serve.add_argument("--host", default="127.0.0.1",
                       help="Bind address. Use 0.0.0.0 inside a container.")
    serve.add_argument("--port", type=int, default=8095)
    serve.add_argument("--cron", help="Also run the scheduler in this process.")
    serve.add_argument("--now", action="store_true", help="With --cron: run once on start.")
    serve.add_argument("--posters-dir", default="posters")
    serve.add_argument("--static-dir", help="Built UI directory (defaults to the package's).")

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
    if type_override and len(names) != 1:
        raise ConfigError("--type can only be used with exactly one --library.")

    selected = []
    for name in names:
        found = config.find(name)
        if found is None and type_override:
            # v1 ran any Plex library from the command line alone; keep that.
            found = LibraryRun(library=name, type=MediaType(type_override))
        if found is None:
            raise ConfigError(
                f"Library {name!r} is not in the config. Known: "
                f"{', '.join(r.library for r in config.libraries) or '(none)'}. "
                "Add --type to run it with the defaults for that type."
            )
        selected.append(found)

    if type_override:
        selected = [selected[0].model_copy(update={"type": MediaType(type_override)})]
    return selected


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


class _CliObserver:
    """Progress bars and per-action summaries for a terminal."""

    def __init__(self, style: Style, *, no_progress: bool, as_json: bool) -> None:
        self._style = style
        self._no_progress = no_progress
        self._as_json = as_json
        self._bar: ProgressBar | None = None

    def begin(self, _run, _action: str, _run_id: str, _total: int, pending: int) -> None:
        """Open a fresh bar sized to the items this action will process."""
        self._close()
        if pending:
            # The bar counts the items actually being processed, so it fills
            # to 100% even when most of the library is already cached.
            self._bar = ProgressBar(pending, enabled=not self._no_progress)

    def item(self, _run, outcome) -> None:
        if self._bar is not None:
            self._bar.advance(suffix=outcome.item.title)

    def report(self, report: RunReport) -> None:
        self._close()
        if not self._as_json:
            print_report(report, self._style)

    def _close(self) -> None:
        if self._bar is not None:
            self._bar.close()
            self._bar = None


async def cmd_run(args, config: AppConfig, store: Store, style: Style) -> int:
    """Process the selected libraries and their post-processing actions."""
    runs = _selected_runs(config, args.library, args.type)
    if not runs:
        print(style.yellow("No enabled libraries in the config; nothing to do."))
        return 0

    if not args.yes and not args.dry and sys.stdin.isatty():
        names = ", ".join(style.cyan(r.library) for r in runs)
        target = "genre tags" if any(r.use_genres for r in runs) else "collections"
        print(f"About to update {target} for: {names}")
        if not _confirm("Continue?"):
            return 130

    server = await asyncio.to_thread(plex_client.connect, config.plex)
    reports = await run_libraries(
        config, store, server, runs,
        dry_run=args.dry, force=args.force, only=set(args.only or ()),
        posters_dir=args.posters_dir,
        observer=_CliObserver(style, no_progress=args.no_progress, as_json=args.json),
    )

    if args.json:
        print(json.dumps([r.as_dict() for r in reports], indent=2, ensure_ascii=False))
    return 1 if any(r.failed for r in reports if r.action in ("genres", "collections")) else 0


async def cmd_query(args, config: AppConfig, style: Style) -> int:
    """Show what the providers return for a title, without writing anything."""
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


def _library_name(config: AppConfig, name: str) -> str:
    """The name as the config spells it, so the store's rows match the pipeline's."""
    entry = config.find(name)
    return entry.library if entry is not None else name


def cmd_bind(args, config: AppConfig, store: Store, style: Style) -> int:
    """Pin a Plex item to a provider id."""
    library = _library_name(config, args.library)
    store.set_binding(library, args.title, args.provider, args.provider_id, args.note)
    print(
        f"{style.green('bound')} {style.bold(args.title)} in {library} "
        f"-> {args.provider}://{args.provider_id}"
    )
    print(style.dim("  Its cache entry was cleared; the next run will use this id."))
    return 0


def cmd_unbind(args, config: AppConfig, store: Store, style: Style) -> int:
    """Remove a manual binding."""
    library = _library_name(config, args.library)
    if store.delete_binding(library, args.title):
        print(f"{style.green('removed')} binding for {args.title} in {library}")
        return 0
    print(style.yellow(f"No binding for {args.title!r} in {library!r}."))
    return 1


def cmd_bindings(args, config: AppConfig, store: Store, style: Style, as_json: bool) -> int:
    """List manual bindings."""
    rows = store.list_bindings(_library_name(config, args.library) if args.library else None)
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
        # The same ladder the API uses; the CLI cannot know whether an open
        # row belongs to a live process, so it never says "running".
        status = run_status(row, live=False)
        if status in ("undone", "cancelled"):
            result = style.yellow(status)
        elif status == "interrupted":
            result = style.red(status)
        else:
            result = (f"{report.get('written', 0)} written, "
                      f"{report.get('failed', 0)} failed")
            if report.get("error"):
                result += style.red(f"  {report['error']}")
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


def cmd_doctor(
    config_path: str, store: Store, style: Style, as_json: bool = False, *, offline: bool = False
) -> int:
    """Check the config, the credentials and the anime genre names."""
    report = run_doctor(config_path, store, check_taxonomy=not offline)
    if as_json:
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
        return 0 if report.ok else 1
    _print_doctor(report, style)
    return 0 if report.ok else 1


def _print_doctor(report: DoctorReport, style: Style) -> None:
    badge = {"ok": style.green("OK"), "warn": style.yellow("!!"), "error": style.red("!!")}
    for check in report.checks:
        line = f"{badge[check.level]} {check.title}"
        if check.detail and check.level == "ok":
            line += style.dim(f" ({check.detail})")
        print(line)
        if check.detail and check.level != "ok":
            print(f"   {check.detail}")
        for item in check.items:
            print(f"     {item}")
    print()
    if report.errors:
        print(style.red(f"{report.errors} error(s), {report.warnings} warning(s)."))
    elif report.warnings:
        print(style.yellow(f"{report.warnings} thing(s) to look at."))
    else:
        print(style.green("Everything looks fine."))


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
    """Run on a schedule in the foreground, replacing the container's crond.

    The config's ``schedule`` block (editable from the UI) overrides ``--cron``
    and can pause the pass; both are re-read while running.
    """
    try:
        validate_cron(args.cron)
    except ValueError as exc:
        print(style.red(str(exc)))
        return 2

    print(f"Scheduler started. Fallback cron: {style.cyan(args.cron)}")
    announced: list[float | None] = [None]

    def announce(fire_at: float) -> None:
        if announced[0] == fire_at:
            return  # the loop re-plans every minute; only say it once
        announced[0] = fire_at
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(fire_at))
        print(style.dim(f"Next run at {when} (in {int(fire_at - time.time())}s)"))

    def settings() -> tuple[str | None, bool] | None:
        schedule = load_config(config_path).schedule
        return schedule.cron, schedule.enabled

    async def one_pass() -> None:
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

    scheduler = Scheduler(one_pass, settings=settings, fallback=args.cron, on_schedule=announce)
    try:
        await scheduler.run_forever(run_now=args.now)
    except asyncio.CancelledError:
        print("\nScheduler stopped.")
    return 0


def cmd_serve(args, style: Style) -> int:
    """Start uvicorn with the app; the scheduler rides along when --cron is set."""
    import uvicorn

    from .server import create_app
    from .server.auth import AuthSettings, check_bind

    if args.cron:
        try:
            validate_cron(args.cron)
        except ValueError as exc:
            print(style.red(str(exc)))
            return 2

    auth = AuthSettings.from_env()
    check_bind(auth, args.host)  # raises ConfigError; main() prints it
    if not auth.enabled:
        print(style.yellow(
            "No PAG_WEB_PASSWORD set: the console is open to anyone who can reach it."
        ))

    app = create_app(
        args.config, args.db,
        cron=args.cron, run_on_start=args.now,
        posters_dir=args.posters_dir, static_dir=args.static_dir,
        auth=auth,
    )
    print(f"plex-auto-genres UI on {style.cyan(f'http://{args.host}:{args.port}')}"
          f"  (API docs: /api/docs, login: {'required' if auth.enabled else 'off'})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=False)
    return 0


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and dispatch. Returns the process exit code."""
    load_dotenv()
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_argv)
    if args.command is None:
        # "run" is the default command; keep the global options typed before it.
        args = parser.parse_args([*raw_argv, "run"])

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
                return cmd_doctor(args.config, store, style, args.json, offline=args.offline)
            if args.command in ("bind", "unbind", "bindings"):
                # Bindings are keyed by the config's spelling of the library;
                # the config itself is optional for them, as in v1.
                config = load_config(args.config, missing_ok=True)
                if args.command == "bind":
                    return cmd_bind(args, config, store, style)
                if args.command == "unbind":
                    return cmd_unbind(args, config, store, style)
                return cmd_bindings(args, config, store, style, args.json)
            if args.command == "runs":
                return cmd_runs(args, store, style, args.json)
            if args.command == "failures":
                return cmd_failures(args, store, style, args.json)
            if args.command == "schedule":
                return asyncio.run(cmd_schedule(args, args.config, args.db, style))
            if args.command == "serve":
                store.close()  # the app's lifespan opens its own connection
                return cmd_serve(args, style)

            # `run --library X --type T` never needed a config file in v1.
            adhoc = args.command == "run" and bool(args.library) and bool(args.type)
            config = load_config(args.config, missing_ok=adhoc or args.command == "query")
            if args.command == "query":
                return asyncio.run(cmd_query(args, config, style))
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
