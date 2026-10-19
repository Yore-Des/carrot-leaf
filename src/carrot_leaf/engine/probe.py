"""Fetch a video's details without downloading it."""

from __future__ import annotations

import io
import urllib.request
from dataclasses import dataclass, field

from . import deps


class PlaylistError(Exception):
    """Raised for a URL that points at a playlist or channel rather than one video."""


@dataclass
class VideoInfo:
    url: str
    id: str
    title: str
    channel: str
    duration: int | None
    upload_date: str | None  # YYYYMMDD
    thumbnail: str | None
    extractor: str
    max_height: int | None
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def duration_text(self) -> str:
        if not self.duration:
            return "live" if self.raw.get("is_live") else "—"
        h, rem = divmod(int(self.duration), 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"

    @property
    def date_text(self) -> str:
        d = self.upload_date
        if not d or len(d) != 8:
            return "—"
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def max_video_height(formats: list[dict]) -> int | None:
    """Largest resolution on offer, using the short side so vertical videos count right."""
    best = 0
    for f in formats or []:
        if f.get("vcodec") in (None, "none") and not f.get("height"):
            continue
        w, h = f.get("width"), f.get("height")
        side = min(w, h) if w and h else h
        if side:
            best = max(best, int(side))
    return best or None


def pick_thumbnail(info: dict) -> str | None:
    """A small widescreen jpg is plenty for a terminal preview and loads fast.

    YouTube's default `hqdefault` is 4:3 with black bars, so prefer a ~16:9 one.
    """
    candidates = []
    for t in info.get("thumbnails") or []:
        w, h, url = t.get("width"), t.get("height"), t.get("url")
        if not (w and h and url) or w < 300:
            continue
        widescreen = abs(w / h - 16 / 9) < 0.1
        candidates.append((not widescreen, ".webp" in url, w, url))
    if candidates:
        return min(candidates)[3]
    return info.get("thumbnail")


def probe(url: str, cookies_browser: str = "") -> VideoInfo:
    from yt_dlp import YoutubeDL  # loaded with the first link, not at startup

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "js_runtimes": deps.js_runtimes(),
        "logger": deps.SilentLogger(),
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise ValueError("No video found at that link.")
    if info.get("_type") == "playlist":
        raise PlaylistError("That's a playlist or channel. Paste a link to a single video.")
    return VideoInfo(
        url=info.get("webpage_url") or url,
        id=info.get("id", ""),
        title=info.get("title") or "Untitled",
        channel=info.get("channel") or info.get("uploader") or "—",
        duration=info.get("duration"),
        upload_date=info.get("upload_date"),
        thumbnail=pick_thumbnail(info),
        extractor=info.get("extractor_key") or "",
        max_height=max_video_height(info.get("formats") or []),
        raw=info,
    )


def fetch_thumbnail(url: str, timeout: float = 8.0):
    """Download a thumbnail as a PIL image, or None if it can't be fetched."""
    from PIL import Image

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 carrot-leaf"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
