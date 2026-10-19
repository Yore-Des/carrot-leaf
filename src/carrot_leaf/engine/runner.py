"""Run one download job with yt-dlp and report progress through callbacks."""

from __future__ import annotations

import itertools
import shutil
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled

from . import deps
from .options import Choice, build_options

_ids = itertools.count(1)

QUEUED, PREPARING, DOWNLOADING, CONVERTING, DONE, FAILED, CANCELLED = (
    "queued",
    "preparing",
    "downloading",
    "converting",
    "done",
    "failed",
    "cancelled",
)
FINISHED = (DONE, FAILED, CANCELLED)

# yt-dlp reports postprocessors by short name (the class name minus "FFmpeg" and "PP").
_PP_LABELS = {
    "ExtractAudio": "Converting audio",
    "VideoConvertor": "Converting video",
    "VideoRemuxer": "Packaging",
    "Merger": "Merging video and audio",
    "Metadata": "Adding details",
    "EmbedThumbnail": "Adding cover art",
}


@dataclass
class Job:
    url: str
    title: str
    choice: Choice
    id: int = field(default_factory=lambda: next(_ids))
    state: str = QUEUED
    status: str = "Waiting"
    fraction: float | None = 0.0
    speed: float | None = None
    eta: float | None = None
    filepath: str | None = None
    error: Exception | None = None
    # A local.FileInfo when this job converts a file on disk rather than downloading `url`.
    file: Any = field(default=None, repr=False)
    # Set from the UI thread; the download thread checks it at every progress update.
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()


@dataclass
class Progress:
    state: str
    status: str
    fraction: float | None = None
    speed: float | None = None
    eta: float | None = None


def run_job(
    job: Job,
    download_dir: Path,
    on_progress: Callable[[Progress], None],
    cookies_browser: str = "",
) -> str:
    """Download and convert `job`. Blocks; returns the final file path.

    Raises DownloadCancelled soon after `job.cancel()` is called.
    """
    on_progress(Progress(PREPARING, "Getting ready"))
    ffmpeg = deps.find_ffmpeg(fetch=False)
    if ffmpeg is None:
        on_progress(Progress(PREPARING, "Fetching ffmpeg (one time, ~70 MB)"))
        ffmpeg = deps.find_ffmpeg(fetch=True)

    download_dir.mkdir(parents=True, exist_ok=True)
    # Work in a private folder so intermediate files never clash with (or delete)
    # files already in the user's download folder; move the result out at the end.
    work = Path(tempfile.mkdtemp(prefix=".carrot-leaf-", dir=download_dir))
    try:
        return _run(job, work, download_dir, ffmpeg, on_progress, cookies_browser)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run(job, work, download_dir, ffmpeg, on_progress, cookies_browser) -> str:
    opts = build_options(
        job.choice,
        str(work),
        ffmpeg_location=ffmpeg.path if ffmpeg else None,
        js_runtimes=deps.js_runtimes(),
        cookies_browser=cookies_browser,
        native_vorbis=bool(ffmpeg) and not deps.ffmpeg_has_encoder(ffmpeg.path, "libvorbis"),
    )

    streams = {"n": 0, "seen": set()}

    def progress_hook(d: dict) -> None:
        if job.cancelled:
            raise DownloadCancelled("Cancelled")
        fmt = (d.get("info_dict") or {}).get("format_id")
        if fmt not in streams["seen"]:
            streams["seen"].add(fmt)
            streams["n"] += 1
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            label = "Downloading" if streams["n"] <= 1 else f"Downloading part {streams['n']}"
            on_progress(
                Progress(
                    DOWNLOADING,
                    label,
                    fraction=(done / total) if total else None,
                    speed=d.get("speed"),
                    eta=d.get("eta"),
                )
            )

    def pp_hook(d: dict) -> None:
        if job.cancelled:
            raise DownloadCancelled("Cancelled")
        if d["status"] == "started" and (label := _PP_LABELS.get(d.get("postprocessor"))):
            on_progress(Progress(CONVERTING, label, fraction=None))

    opts["logger"] = deps.SilentLogger()
    opts["progress_hooks"] = [progress_hook]
    opts["postprocessor_hooks"] = [pp_hook]

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(job.url, download=True)
    produced = Path(final_path(info))
    if not produced.is_file():
        raise RuntimeError("Download finished but no file was produced.")
    target = unique_path(download_dir / produced.name)
    shutil.move(str(produced), target)
    return str(target)


def unique_path(path: Path) -> Path:
    """`path`, or `name (2).ext`, `name (3).ext`... if it is already taken."""
    if not path.exists():
        return path
    for n in itertools.count(2):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise AssertionError("unreachable")


def final_path(info: dict) -> str:
    for d in info.get("requested_downloads") or []:
        if d.get("filepath"):
            return d["filepath"]
    return info.get("filepath") or info.get("_filename") or ""
