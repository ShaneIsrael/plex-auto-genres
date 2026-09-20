#!/usr/bin/env python3
"""Backwards-compatible automation entry point.

v1 shelled out to ``plex-auto-genres.py`` once per library, then once more per
post-processing action, sleeping 45 seconds between them to dodge a Plex rate
limit. v2 does all of that in-process, so this is now a thin alias for
``plex-auto-genres run``.
"""

from __future__ import annotations

import sys

from plex_auto_genres.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "--yes", "--no-progress", *sys.argv[1:]]))
