"""Fetch a video's details without downloading it."""

from __future__ import annotations

import io
import re
import urllib.request
from dataclasses import dataclass, field

from . import deps

# Channels list their newest videos first; past this many, the picker gets unwieldy and
# YouTube takes a long time to page through them.
PLAYLIST_LIMIT = 500
# A YouTube channel's home page lists its tabs rather than videos; these go to the videos.
_CHANNEL_TABS = ("/videos", "/shorts", "/streams", "/live", "/podcasts", "/releases")


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
        return _duration_text(self.duration)

    @property
    def chapters(self) -> list[dict]:
        return self.raw.get("chapters") or []

    @property
    def is_youtube(self) -> bool:
        return self.extractor.lower().startswith("youtube")

    @property
    def date_text(self) -> str:
        d = self.upload_date
        if not d or len(d) != 8:
            return "—"
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"


@dataclass
class Entry:
    url: str
    id: str
    title: str
    duration: int | None = None

    @property
    def duration_text(self) -> str:
        return _duration_text(self.duration)


@dataclass
class PlaylistInfo:
    url: str
    id: str
    title: str
    channel: str
    entries: list[Entry]
    thumbnail: str | None
    extractor: str
    total: int | None = None  # the full count when there are more than PLAYLIST_LIMIT

    @property
    def truncated(self) -> bool:
        return bool(self.total) and self.total > len(self.entries)

    @property
    def duration(self) -> int | None:
        known = [e.duration for e in self.entries if e.duration]
        return sum(known) if known else None


def _duration_text(seconds: float | None) -> str:
    if not seconds:
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


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


def probe(url: str, cookies_browser: str = "") -> VideoInfo | PlaylistInfo:
    """Look up a video, or the list of videos in a playlist or channel."""
    from yt_dlp import YoutubeDL  # loaded with the first link, not at startup

    opts = {
        "quiet": True,
        "no_warnings": True,
        # A watch link that's also in a playlist means the one video.
        "noplaylist": True,
        # A playlist lists its videos without looking each one up, which takes seconds, not minutes.
        "extract_flat": "in_playlist",
        "playlistend": PLAYLIST_LIMIT,
        "skip_download": True,
        "js_runtimes": deps.js_runtimes(),
        "logger": deps.SilentLogger(),
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(channel_home_to_videos(url), download=False)
        if info and info.get("_type") == "playlist" and (tab := channel_videos_tab(info)):
            info = ydl.extract_info(tab, download=False)
    if info is None:
        raise ValueError("No video found at that link.")
    if info.get("_type") == "playlist":
        return playlist_info(videos_tab(info), url)
    return video_info(info, url)


_YT_CHANNEL = re.compile(
    r"^(https?://(?:www\.|m\.)?youtube\.com/(?:@[^/?#]+|channel/[\w-]+|c/[^/?#]+|user/[^/?#]+))/?(?:[?#].*)?$"
)


def channel_home_to_videos(url: str) -> str:
    """A YouTube channel's home page lists every tab, which takes three times as long to
    read; go straight to its videos."""
    m = _YT_CHANNEL.match(url.strip())
    return f"{m.group(1)}/videos" if m else url


def channel_videos_tab(info: dict) -> str | None:
    """For a channel home page whose entries are links to its tabs, the videos tab's URL."""
    urls = [e.get("url") or "" for e in info.get("entries") or [] if e]
    tabs = [u for u in urls if u.rstrip("/").endswith(_CHANNEL_TABS)]
    if not urls or len(tabs) != len(urls):
        return None
    return next((u for u in tabs if u.rstrip("/").endswith("/videos")), tabs[0])


def videos_tab(info: dict) -> dict:
    """A channel home page comes back as one playlist per tab (Videos, Live, Shorts); use
    the videos. Anything else is returned as it is."""
    entries = [e for e in info.get("entries") or [] if e]
    if not entries or not all(e.get("_type") == "playlist" for e in entries):
        return info
    for e in entries:
        where = (e.get("webpage_url") or e.get("original_url") or "").rstrip("/")
        if where.endswith("/videos") or (e.get("title") or "").endswith(" - Videos"):
            return {**e, "title": info.get("title") or e.get("title")}
    return {**entries[0], "title": info.get("title") or entries[0].get("title")}


def playlist_info(info: dict, url: str) -> PlaylistInfo:
    entries = []
    for e in info.get("entries") or []:
        if not e:
            continue  # yt-dlp leaves None for private or deleted videos
        link = e.get("url") or e.get("webpage_url")
        if not link:
            continue
        if "://" not in link and e.get("ie_key") == "Youtube":
            link = f"https://www.youtube.com/watch?v={link}"
        title = e.get("title") or ""
        if title in ("[Private video]", "[Deleted video]"):
            continue
        duration = e.get("duration")
        entries.append(
            Entry(link, e.get("id") or "", title or "Untitled", int(duration) if duration else None)
        )
    if not entries:
        raise ValueError("That playlist has no videos Carrot Leaf can get.")
    total = info.get("playlist_count")
    return PlaylistInfo(
        url=info.get("webpage_url") or url,
        id=info.get("id") or "",
        # A channel's videos tab is called "Name - Videos"; the folder should just be "Name".
        title=re.sub(r" - Videos$", "", info.get("title") or "") or "Untitled playlist",
        channel=info.get("channel") or info.get("uploader") or "—",
        entries=entries,
        thumbnail=pick_thumbnail(info) or _first_thumbnail(info),
        extractor=info.get("extractor_key") or "",
        total=total if isinstance(total, int) else None,
    )


def _first_thumbnail(info: dict) -> str | None:
    for e in info.get("entries") or []:
        if e and (thumb := pick_thumbnail(e)):
            return thumb
    return None


def video_info(info: dict, url: str) -> VideoInfo:
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
