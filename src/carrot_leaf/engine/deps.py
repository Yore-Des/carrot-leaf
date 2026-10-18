"""Locate ffmpeg/ffprobe and a JavaScript runtime.

Binaries on PATH win. Otherwise fall back to the copies that ship with the
package: static-ffmpeg (downloaded once on first use) and the PyPI `deno` wheel.
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


def find_ffmpeg(fetch: bool = True) -> Tool | None:
    """Return the directory holding ffmpeg + ffprobe.

    With `fetch`, downloads the bundled build if nothing else is available
    (about 70 MB, once). That call blocks, so run it off the UI thread.
    """
    if tool := system_ffmpeg():
        return tool
    if not fetch and not bundled_ffmpeg_ready():
        return None
    from static_ffmpeg import run

    # static-ffmpeg prints download progress to stdout, which would scribble over the TUI.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        ffmpeg, _ = run.get_or_fetch_platform_executables_else_raise()
    return Tool(str(Path(ffmpeg).parent), bundled=True)


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
def ffmpeg_has_encoder(ffmpeg_dir: str, encoder: str) -> bool:
    exe = shutil.which("ffmpeg", path=ffmpeg_dir) or "ffmpeg"
    try:
        out = subprocess.run(
            [exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return True
    return any(line.split()[1:2] == [encoder] for line in out.splitlines() if line.strip())


class SilentLogger:
    """yt-dlp logger that drops everything; errors still arrive as exceptions.

    Without it yt-dlp writes "ERROR: ..." to stderr even in quiet mode, which
    would scribble over the full-screen UI.
    """

    def debug(self, msg: str) -> None:
        pass

    info = warning = error = debug
