"""Everything Carrot Leaf has saved, newest last, one JSON object per line next to the config.

A line is appended per finished download or conversion, so a crash never loses earlier
ones. The file is trimmed to MAX_ENTRIES now and then.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import config

MAX_ENTRIES = 1000


def history_path() -> Path:
    return config.config_path().parent / "history.jsonl"


@dataclass
class Entry:
    title: str
    path: str
    label: str  # Choice.label: "MP3 · 320k"
    source: str  # the link, or the original file for a conversion
    kind: str = "download"  # or "convert"
    when: float = 0.0  # time.time()

    @property
    def exists(self) -> bool:
        return Path(self.path).exists()

    @property
    def is_link(self) -> bool:
        return "://" in self.source

    @property
    def when_text(self) -> str:
        if not self.when:
            return ""
        now = time.time()
        if (
            now - self.when < 86400
            and time.localtime(now).tm_mday == time.localtime(self.when).tm_mday
        ):
            return time.strftime("%H:%M", time.localtime(self.when))
        return time.strftime("%Y-%m-%d", time.localtime(self.when))


def add(entry: Entry, path: Path | None = None) -> None:
    path = path or history_path()
    if not entry.when:
        entry.when = time.time()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        if path.stat().st_size > MAX_ENTRIES * 600:  # roughly MAX_ENTRIES lines; trim
            save(load(path)[-MAX_ENTRIES:], path)
    except OSError:
        pass  # history is a convenience; a read-only disk shouldn't fail the download


def load(path: Path | None = None) -> list[Entry]:
    """Oldest first. Broken lines (a crash mid-write, a hand edit) are skipped."""
    path = path or history_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    known = {f.name for f in fields(Entry)}
    entries = []
    for line in lines:
        try:
            data = json.loads(line)
            entries.append(Entry(**{k: v for k, v in data.items() if k in known}))
        except (ValueError, TypeError):
            continue
    return entries


def save(entries: list[Entry], path: Path | None = None) -> None:
    path = path or history_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        "".join(json.dumps(asdict(e), ensure_ascii=False) + "\n" for e in entries),
        encoding="utf-8",
    )
    tmp.replace(path)


def remove(entry: Entry, path: Path | None = None) -> None:
    entries = [e for e in load(path) if (e.when, e.path) != (entry.when, entry.path)]
    try:
        save(entries, path)
    except OSError:
        pass
