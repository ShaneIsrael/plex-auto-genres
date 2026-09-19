"""Plex access layer: reading libraries and writing metadata back."""

from .client import connect, get_section, iter_library, parse_guid
from .writer import (
    PlexWriter,
    WriteOutcome,
    build_tag_edits,
    plan_tags,
    sort_collections,
    undo_run,
    upload_posters,
)

__all__ = [
    "PlexWriter",
    "WriteOutcome",
    "build_tag_edits",
    "connect",
    "get_section",
    "iter_library",
    "parse_guid",
    "plan_tags",
    "sort_collections",
    "undo_run",
    "upload_posters",
]
