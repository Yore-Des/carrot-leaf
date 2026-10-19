"""Locate ffmpeg/ffprobe and a JavaScript runtime.

Binaries on PATH win. Otherwise fall back to the copies that ship with the
package: static-ffmpeg (downloaded once, by `carrot setup` or on first use) and the
PyPI `deno` wheel. Converting local files can use both ffmpegs: whichever has the
encoder a format needs.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path


@dataclass(frozen=True)
class Tool:
    path: str
    bundled: bool


def system_ffmpeg() -> Tool | None:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg and ffprobe and Path(ffmpeg).parent == Path(ffprobe).parent:
        return Tool(str(Path(ffmpeg).parent), bundled=False)
    return None


def bundled_ffmpeg_ready() -> bool:
    from static_ffmpeg import run

    return os.path.exists(os.path.join(run.get_platform_dir(), "installed.crumb"))


def bundled_ffmpeg() -> Tool | None:
    """The downloaded static build, if it's there."""
    if not bundled_ffmpeg_ready():
        return None
    from static_ffmpeg import run

    return Tool(run.get_platform_dir(), bundled=True)


def ffmpeg_candidates() -> list[Tool]:
    """Every ffmpeg Carrot can use, the system's first."""
    return [t for t in (system_ffmpeg(), bundled_ffmpeg()) if t is not None]


def fetch_bundled_ffmpeg(show_progress: bool = False) -> Tool:
    """Download the static build if it isn't there yet (about 50 MB, once). Blocks.

    `show_progress` lets static-ffmpeg print its progress bar; leave it off inside the TUI.
    """
    from static_ffmpeg import run

    if show_progress:
        ffmpeg, _ = run.get_or_fetch_platform_executables_else_raise()
    else:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            ffmpeg, _ = run.get_or_fetch_platform_executables_else_raise()
    return Tool(str(Path(ffmpeg).parent), bundled=True)


def find_ffmpeg(fetch: bool = True) -> Tool | None:
    """Return the directory holding ffmpeg + ffprobe.

    With `fetch`, downloads the bundled build if nothing else is available
    (about 70 MB, once). That call blocks, so run it off the UI thread.
    """
    if tool := system_ffmpeg():
        return tool
    if not fetch:
        return bundled_ffmpeg()
    # static-ffmpeg prints download progress to stdout, which would scribble over the TUI.
    return fetch_bundled_ffmpeg()


def find_deno() -> Tool | None:
    if path := shutil.which("deno"):
        return Tool(path, bundled=False)
    try:
        from deno import find_deno_bin

        return Tool(str(find_deno_bin()), bundled=True)
    except Exception:
        return None


def js_runtimes() -> dict[str, dict[str, str]]:
    """yt-dlp `js_runtimes` option: needed for full YouTube support."""
    if deno := find_deno():
        return {"deno": {"path": deno.path}}
    # Let yt-dlp try its own discovery (node, bun, ...) if Deno is somehow missing.
    return {name: {} for name in ("deno", "node", "bun") if shutil.which(name)}


@cache
def ffmpeg_encoders(ffmpeg_dir: str) -> frozenset[str] | None:
    """Every encoder this ffmpeg build has, or None if it couldn't be asked."""
    exe = shutil.which("ffmpeg", path=ffmpeg_dir) or "ffmpeg"
    try:
        out = subprocess.run(
            [exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return frozenset(parts[1] for line in out.splitlines() if len(parts := line.split()) > 1)


def ffmpeg_has_encoder(ffmpeg_dir: str, encoder: str) -> bool:
    names = ffmpeg_encoders(ffmpeg_dir)
    return True if names is None else encoder in names


class SilentLogger:
    """yt-dlp logger that drops everything; errors still arrive as exceptions.

    Without it yt-dlp writes "ERROR: ..." to stderr even in quiet mode, which
    would scribble over the full-screen UI.
    """

    def debug(self, msg: str) -> None:
        pass

    info = warning = error = debug
