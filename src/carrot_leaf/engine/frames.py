"""Frames of a video at any moment, for picking a clip by eye.

For a link, YouTube's storyboards: sheets of small thumbnails, one every second or two, that
the site already makes for its own scrub bar. For a file on disk, ffmpeg grabs the frame.
Both are slow enough (a download, a process) to call from a worker thread only.
"""

from __future__ import annotations

import io
import subprocess
import threading
import urllib.request
from pathlib import Path


class FrameSource:
    duration: float
    step: float  # the finest jump that shows a different picture, in seconds

    def frame(self, t: float):
        """A PIL image of the moment `t`, or None."""
        raise NotImplementedError


class StoryboardFrames(FrameSource):
    def __init__(self, fmt: dict, duration: float) -> None:
        self.duration = duration
        self.rows, self.cols = int(fmt["rows"]), int(fmt["columns"])
        self.fps = float(fmt["fps"])
        self.step = 1 / self.fps if self.fps else 2.0
        self.width, self.height = int(fmt["width"]), int(fmt["height"])
        self.sheets = [f["url"] for f in fmt["fragments"]]
        self._cache: dict[int, object] = {}
        self._lock = threading.Lock()

    def frame(self, t: float):
        index = int(max(0.0, min(t, self.duration)) * self.fps)
        per_sheet = self.rows * self.cols
        sheet_no, cell = divmod(index, per_sheet)
        sheet_no = min(sheet_no, len(self.sheets) - 1)
        sheet = self._sheet(sheet_no)
        if sheet is None:
            return None
        row, col = divmod(cell, self.cols)
        box = (col * self.width, row * self.height, (col + 1) * self.width, (row + 1) * self.height)
        if box[2] > sheet.width or box[3] > sheet.height:
            return None  # past the last thumbnail on a short final sheet
        return sheet.crop(box)

    def _sheet(self, n: int):
        with self._lock:
            if n in self._cache:
                return self._cache[n]
        from PIL import Image

        try:
            req = urllib.request.Request(self.sheets[n], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                image = Image.open(io.BytesIO(resp.read())).convert("RGB")
        except Exception:
            return None
        with self._lock:
            self._cache[n] = image
        return image


class FileFrames(FrameSource):
    def __init__(self, path: Path, duration: float, ffmpeg_dir: str | None = None) -> None:
        self.path, self.duration, self.ffmpeg_dir = path, duration, ffmpeg_dir
        self.step = 1.0

    def frame(self, t: float):
        from PIL import Image

        from .local import _exe

        cmd = [_exe("ffmpeg", self.ffmpeg_dir), "-v", "error", "-ss", f"{max(t, 0):.2f}"]
        cmd += ["-i", str(self.path), "-frames:v", "1", "-vf", "scale=320:-2"]
        cmd += ["-f", "image2pipe", "-c:v", "png", "-"]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=15, check=False)
            return Image.open(io.BytesIO(out.stdout)).convert("RGB") if out.stdout else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None


def best_storyboard(formats: list[dict]) -> dict | None:
    """The sharpest storyboard yt-dlp lists (sb0 is YouTube's largest)."""
    boards = [
        f
        for f in formats or []
        if f.get("format_note") == "storyboard"
        and f.get("fragments")
        and f.get("rows")
        and f.get("columns")
        and f.get("fps")
    ]
    return max(boards, key=lambda f: f.get("width") or 0, default=None)


def for_video(info) -> FrameSource | None:
    """Frames for a looked-up link (probe.VideoInfo), if the site makes storyboards."""
    board = best_storyboard(info.raw.get("formats") or [])
    if board is None or not info.duration:
        return None
    return StoryboardFrames(board, float(info.duration))


def for_file(info, ffmpeg_dir: str | None = None) -> FrameSource | None:
    """Frames for a video on disk (local.FileInfo)."""
    from .local import VIDEO

    if info.media != VIDEO or not info.duration:
        return None
    return FileFrames(info.path, float(info.duration), ffmpeg_dir)
