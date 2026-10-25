"""Channels and playlists to keep up with. Each remembers the videos it has already seen, so a
sync only ever offers the new ones, in the format picked when it was followed.

Stored as JSON next to the config.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import config
from .engine.probe import Entry, PlaylistInfo

# How far down a feed a sync looks. Newest first, so 30 covers weeks of most channels and
# keeps a sync to a second or two per source.
SYNC_DEPTH = 30
MAX_SEEN = 2000
# How long a startup sync waits after the last one.
SYNC_EVERY = 15 * 60


def following_path() -> Path:
    return config.config_path().parent / "following.json"


@dataclass
class Followed:
    url: str
    title: str
    kind: str = "video"
    fmt: str = "mp4"
    quality: str = "best"
    extras: dict = field(default_factory=dict)
    seen: list[str] = field(default_factory=list)  # video ids, newest last
    new: list[dict] = field(default_factory=list)  # Entry fields, waiting to be fetched
    checked: float = 0.0  # time.time() of the last sync
    error: str = ""  # why the last sync failed, if it did

    def choice(self):
        from .presets import Preset

        return Preset(self.title, self.kind, self.fmt, self.quality, self.extras).choice()

    @property
    def new_entries(self) -> list[Entry]:
        return [Entry(**e) for e in self.new]

    @property
    def checked_text(self) -> str:
        if not self.checked:
            return "never synced"
        ago = time.time() - self.checked
        if ago < 90:
            return "just now"
        if ago < 3600:
            return f"{int(ago // 60)}m ago"
        if ago < 86400:
            return f"{int(ago // 3600)}h ago"
        return f"{int(ago // 86400)}d ago"


def load(path: Path | None = None) -> list[Followed]:
    path = path or following_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    known = {f.name for f in fields(Followed)}
    out = []
    for item in data if isinstance(data, list) else []:
        try:
            out.append(Followed(**{k: v for k, v in item.items() if k in known}))
        except TypeError:
            continue
    return out


def save(items: list[Followed], path: Path | None = None) -> None:
    path = path or following_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(f) for f in items], indent=2), encoding="utf-8")
    tmp.replace(path)


def find(url: str, items: list[Followed] | None = None) -> Followed | None:
    items = load() if items is None else items
    return next((f for f in items if f.url == url), None)


def follow(feed: PlaylistInfo, choice, path: Path | None = None) -> Followed:
    """Start following `feed`. What's in it now counts as seen: only later videos are new."""
    from .presets import from_choice

    p = from_choice(feed.title, choice)
    item = Followed(
        url=feed.url,
        title=feed.title,
        kind=p.kind,
        fmt=p.fmt,
        quality=p.quality,
        extras=p.extras,
        seen=[e.id for e in feed.entries][-MAX_SEEN:],
        checked=time.time(),
    )
    items = [f for f in load(path) if f.url != feed.url]
    items.append(item)
    save(items, path)
    return item


def unfollow(url: str, path: Path | None = None) -> None:
    save([f for f in load(path) if f.url != url], path)


def apply_sync(item: Followed, feed: PlaylistInfo | None, error: str = "") -> int:
    """Record a sync of `item`: new videos join its waiting list. Returns how many are new."""
    item.checked = time.time()
    item.error = error
    if feed is None:
        return 0
    seen = set(item.seen) | {e["id"] for e in item.new}
    fresh = [e for e in feed.entries if e.id and e.id not in seen]
    # Feeds list newest first; keep the waiting list oldest first, the order to fetch them.
    item.new.extend(asdict(e) for e in reversed(fresh))
    return len(fresh)


def mark_fetched(item: Followed) -> list[Entry]:
    """Take the waiting videos off the list and remember them as seen."""
    entries = item.new_entries
    item.seen = (item.seen + [e.id for e in entries])[-MAX_SEEN:]
    item.new = []
    return entries


def update(item: Followed, path: Path | None = None) -> None:
    items = load(path)
    for i, f in enumerate(items):
        if f.url == item.url:
            items[i] = item
            break
    else:
        return  # unfollowed meanwhile
    save(items, path)


def total_new(items: list[Followed] | None = None) -> int:
    return sum(len(f.new) for f in (load() if items is None else items))


def due(items: list[Followed]) -> bool:
    return any(time.time() - f.checked > SYNC_EVERY for f in items)
