"""Locate ffmpeg/ffprobe and a JavaScript runtime.

Binaries on PATH win. Otherwise fall back to Carrot Leaf's own copies: a static ffmpeg
build (downloaded once, by `carrot setup` or on first use) and the PyPI `deno` wheel.
Converting local files can use both ffmpegs: whichever has the encoder a format needs.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path

# Static ffmpeg + ffprobe builds, one zip per platform holding a folder named after the key.
# These are the builds the static-ffmpeg package (3.0) downloads; Carrot Leaf fetches them
# itself so it doesn't install that package's unrelated dependencies (twine, keyring, ...).
FFMPEG_ZIPS = {
    "win32": "https://github.com/zackees/ffmpeg_bins/raw/main/v8.0/win32.zip",
    "darwin": "https://github.com/zackees/ffmpeg_bins/raw/main/v8.0/darwin.zip",
    "darwin_arm64": "https://github.com/zackees/ffmpeg_bins/raw/main/v8.0/darwin_arm64.zip",
    "linux": "https://github.com/zackees/ffmpeg_bins/raw/main/v8.0/linux.zip",
    "linux_arm64": "https://github.com/zackees/ffmpeg_bins/raw/main/v8.0/linux_arm64.zip",
}
CRUMB = "installed.crumb"


@dataclass(frozen=True)
class Tool:
    path: str
    bundled: bool


def system_ffmpeg() -> Tool | None:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg and ffprobe and Path(ffmpeg).parent == Path(ffprobe).parent:
        return Tool(str(Path(ffmpeg).parent), bundled=False)
    return None


def platform_key() -> str:
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if sys.platform == "win32":
        return "win32"
    if sys.platform in ("darwin", "linux"):
        return f"{sys.platform}_arm64" if arm else sys.platform
    return sys.platform


def bundled_dir() -> Path:
    """Where the static build lives. Outside the app's environment, so it survives upgrades."""
    import platformdirs

    data = platformdirs.user_data_dir("carrot-leaf", appauthor=False)
    return Path(data) / "ffmpeg" / platform_key()


def bundled_ffmpeg_ready() -> bool:
    return (bundled_dir() / CRUMB).exists()


def bundled_ffmpeg() -> Tool | None:
    """The downloaded static build, if it's there."""
    return Tool(str(bundled_dir()), bundled=True) if bundled_ffmpeg_ready() else None


def ffmpeg_candidates() -> list[Tool]:
    """Every ffmpeg Carrot can use, the system's first."""
    return [t for t in (system_ffmpeg(), bundled_ffmpeg()) if t is not None]


def fetch_bundled_ffmpeg(show_progress: bool = False) -> Tool:
    """Download the static build if it isn't there yet (about 50 MB, once). Blocks.

    `show_progress` prints a percentage; leave it off inside the TUI. Safe to run from two
    processes at once: each unpacks into its own folder and the first to finish wins.
    """
    target = bundled_dir()
    if (target / CRUMB).exists():
        return Tool(str(target), bundled=True)
    key = platform_key()
    url = FFMPEG_ZIPS.get(key)
    if url is None:
        raise OSError(f"No ffmpeg build for {key}. Install ffmpeg yourself.")
    target.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".download-", dir=target.parent))
    try:
        archive = work / "ffmpeg.zip"
        _download(url, archive, show_progress)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(work / "unpacked")  # zipfile keeps members inside this folder
        archive.unlink()
        unpacked = work / "unpacked" / key
        exe = ".exe" if sys.platform == "win32" else ""
        for name in ("ffmpeg", "ffprobe"):
            path = unpacked / f"{name}{exe}"
            if not path.is_file():
                raise OSError(f"The ffmpeg download has no {path.name}.")
            if not exe:
                path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        (unpacked / CRUMB).write_text(f"installed from {url} on {datetime.now()}")
        if target.exists() and not (target / CRUMB).exists():
            shutil.rmtree(target, ignore_errors=True)  # a broken earlier attempt
        try:
            os.replace(unpacked, target)
        except OSError:
            if not (target / CRUMB).exists():  # not just another process winning the race
                raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return Tool(str(target), bundled=True)


def _download(url: str, dest: Path, show_progress: bool) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "carrot-leaf"})
    with (
        urllib.request.urlopen(request, timeout=60, context=_tls()) as resp,
        open(dest, "wb") as out,
    ):
        total = int(resp.headers.get("Content-Length") or 0)
        done, shown = 0, -1
        while chunk := resp.read(1 << 18):
            out.write(chunk)
            done += len(chunk)
            if show_progress and total and (pct := done * 100 // total) != shown:
                print(f"\r  {pct}% of {total / 2**20:.0f} MB", end="", flush=True)
                shown = pct
    if show_progress:
        print()


def _tls():
    """certifi's CA bundle when it's there (it comes with yt-dlp), else the system's."""
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def find_ffmpeg(fetch: bool = True) -> Tool | None:
    """Return the directory holding ffmpeg + ffprobe.

    With `fetch`, downloads the bundled build if nothing else is available
    (about 70 MB, once). That call blocks, so run it off the UI thread.
    """
    if tool := system_ffmpeg():
        return tool
    if not fetch:
        return bundled_ffmpeg()
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
