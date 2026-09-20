"""Terminal output: colours, progress and run summaries."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from typing import TextIO

from .models import RunReport


def _supports_colour(stream: TextIO) -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("FORCE_COLOR"):
        return True
    return stream.isatty()


class Style:
    """ANSI helpers that degrade to plain text when not on a terminal."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.enabled = _supports_colour(stream or sys.stdout)

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def red(self, text: str) -> str:
        return self._wrap("91", text)

    def green(self, text: str) -> str:
        return self._wrap("92", text)

    def yellow(self, text: str) -> str:
        return self._wrap("93", text)

    def cyan(self, text: str) -> str:
        return self._wrap("96", text)


class ProgressBar:
    """A single-line progress bar that stays quiet when piped to a file."""

    def __init__(self, total: int, *, enabled: bool = True, stream: TextIO | None = None) -> None:
        self.total = max(total, 0)
        self.stream = stream or sys.stdout
        self.enabled = enabled and self.stream.isatty() and self.total > 0
        self.current = 0
        self._started = time.monotonic()
        self._last_render = 0.0

    def advance(self, step: int = 1, suffix: str = "") -> None:
        """Move the bar forward, redrawing at most ~20 times a second."""
        self.current = min(self.current + step, self.total)
        if not self.enabled:
            return
        now = time.monotonic()
        # Redraw at most ~20x/second, but never skip the final frame.
        if now - self._last_render < 0.05 and self.current < self.total:
            return
        self._last_render = now
        self._render(suffix)

    def _render(self, suffix: str) -> None:
        width = min(shutil.get_terminal_size((80, 20)).columns, 100)
        bar_width = max(width - 42, 10)
        fraction = self.current / self.total if self.total else 1.0
        filled = int(bar_width * fraction)
        rendered = "█" * filled + "─" * (bar_width - filled)

        elapsed = time.monotonic() - self._started
        eta = (elapsed / fraction - elapsed) if fraction > 0.01 else 0.0
        line = (
            f"\r  |{rendered}| {fraction * 100:5.1f}% "
            f"{self.current}/{self.total} ETA {_fmt_duration(eta)}"
        )
        if suffix:
            line += f" {suffix[:20]}"
        self.stream.write(line[:width].ljust(width))
        self.stream.flush()

    def close(self) -> None:
        if self.enabled:
            self.stream.write("\n")
            self.stream.flush()

    def __enter__(self) -> "ProgressBar":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _fmt_duration(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def print_report(report: RunReport, style: Style, *, stream: TextIO | None = None) -> None:
    """Print a human-readable summary of one run."""
    out = stream or sys.stdout
    tag = style.yellow("[dry-run] ") if report.dry_run else ""
    head = f"{tag}{style.bold(report.library)} — {report.action}"
    print(f"\n{head}", file=out)

    bits = []
    if report.written:
        bits.append(style.green(f"{report.written} written"))
    if report.unchanged:
        bits.append(style.dim(f"{report.unchanged} already correct"))
    if report.skipped:
        bits.append(style.dim(f"{report.skipped} cached"))
    if report.failed:
        bits.append(style.red(f"{report.failed} failed"))
    print("  " + (", ".join(bits) if bits else style.dim("nothing to do")), file=out)
    print(
        style.dim(
            f"  {report.plex_requests} Plex writes, {report.provider_requests} provider calls, "
            f"{_fmt_duration(report.duration_s)}"
        ),
        file=out,
    )

    if report.failures:
        print(style.red(f"  first {min(len(report.failures), 5)} failures:"), file=out)
        for title, error in report.failures[:5]:
            print(f"    - {title}: {error}", file=out)
        if report.failed > 5:
            print(
                style.dim(f"    ... run 'plex-auto-genres failures --library "
                          f"\"{report.library}\"' for the rest"),
                file=out,
            )
    if not report.dry_run and report.written:
        print(style.dim(f"  undo with: plex-auto-genres undo {report.run_id}"), file=out)


def print_json(report: RunReport, stream: TextIO | None = None) -> None:
    print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False), file=stream or sys.stdout)
