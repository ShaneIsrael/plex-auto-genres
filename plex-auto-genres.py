#!/usr/bin/env python3
"""Backwards-compatible entry point.

The implementation moved into the ``plex_auto_genres`` package in v2. This
shim keeps ``python plex-auto-genres.py ...`` working, and translates the v1
flags into their v2 equivalents so existing scripts and cron jobs do not break.
"""

from __future__ import annotations

import sys

from plex_auto_genres.cli import main

#: v1 flag -> v2 replacement. ``None`` means the flag is now the default.
_V1_FLAGS = {
    "--set-posters": ("run", "--only", "posters"),
    "--sort": ("run", "--only", "sort"),
    "--rate-anime": ("run", "--only", "ratings"),
    "--create-rating-collections": ("run", "--only", "rating-collections"),
}
_V1_SUBCOMMANDS = {"run", "query", "bind", "unbind", "bindings", "undo", "runs",
                   "failures", "doctor", "schema", "migrate-config", "schedule"}


def translate(argv: list[str]) -> list[str]:
    """Rewrite a v1 command line into the v2 grammar."""
    if not argv or argv[0] in _V1_SUBCOMMANDS or argv[0] in ("-h", "--help", "--version"):
        return argv

    out: list[str] = ["run"]
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in _V1_FLAGS:
            out.extend(_V1_FLAGS[arg][1:])
        elif arg == "--query":
            # --query took the place of a subcommand in v1. --type may sit on
            # either side of it (the v1 README puts it first).
            type_arg = _extract_value(argv, "--type")
            titles = _positional_words(argv[i + 1:])
            return ["query", *titles, *(["--type", type_arg] if type_arg else [])]
        elif arg in ("--use-genres", "--use-keywords", "--clear-genres"):
            print(
                f"note: {arg} is now a per-library setting in config/config.json; "
                "the config value is used.",
                file=sys.stderr,
            )
        else:
            out.append(arg)
        i += 1
    return out


def _extract_value(argv: list[str], flag: str) -> str | None:
    """The value following ``flag``, or ``None``."""
    if flag in argv:
        index = argv.index(flag)
        if index + 1 < len(argv):
            return argv[index + 1]
    return None


#: v1 flags that consume the argument after them.
_VALUE_FLAGS = {"--type", "--library"}


def _positional_words(argv: list[str]) -> list[str]:
    """Bare words, skipping both flags and the values they consume."""
    words: list[str] = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg.startswith("-"):
            skip = arg in _VALUE_FLAGS
            continue
        words.append(arg)
    return words


if __name__ == "__main__":
    raise SystemExit(main(translate(sys.argv[1:])))
