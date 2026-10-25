"""What was said in each download, kept so it can be searched.

Each downloaded video's captions (the creator's, or the site's automatic ones) are saved as
one small JSON file per video in a `transcripts` folder next to the config. Search reads
them all; a few hundred hours of talk is a few megabytes and searches in milliseconds.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config

# Caption formats to try, best first. json3 is YouTube's: clean words with exact timings.
FORMATS = ("json3", "vtt", "srt")


def transcripts_dir() -> Path:
    return config.config_path().parent / "transcripts"


@dataclass
class Cue:
    start: float
    text: str


@dataclass
class Transcript:
    id: str
    title: str
    path: str  # the downloaded file
    url: str
    lang: str = ""
    cues: list[Cue] = field(default_factory=list)


@dataclass
class Hit:
    transcript: Transcript
    index: int  # into transcript.cues

    @property
    def cue(self) -> Cue:
        return self.transcript.cues[self.index]


# ---------- picking and reading a caption track ----------


def pick_track(info: dict) -> tuple[str, str, str] | None:
    """(language, format, url) of the best caption track for search, or None.

    The creator's captions in the video's own language first, then the automatic ones in
    that language ("en-orig" is YouTube's untranslated original), then English.
    """
    lang = (info.get("language") or "").split("-")[0]
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    # What was said, in the language it was said in, beats a translation.
    candidates = []
    if lang:
        candidates += [(manual, lang), (auto, f"{lang}-orig"), (auto, lang)]
    candidates += [(manual, "en"), (auto, "en-orig"), (auto, "en")]
    if manual and not lang:
        candidates.append((manual, next(iter(manual))))
    for tracks, key in candidates:
        for fmt in FORMATS:
            for t in tracks.get(key) or []:
                if t.get("ext") == fmt and t.get("url"):
                    return key, fmt, t["url"]
    return None


def parse(fmt: str, data: str) -> list[Cue]:
    if fmt == "json3":
        return parse_json3(data)
    return parse_vtt(data)


def parse_json3(data: str) -> list[Cue]:
    try:
        events = json.loads(data).get("events") or []
    except (ValueError, AttributeError):
        return []
    cues = []
    for ev in events:
        text = "".join(s.get("utf8", "") for s in ev.get("segs") or []).replace("\n", " ").strip()
        if text:
            cues.append(Cue(round(ev.get("tStartMs", 0) / 1000, 2), _clean(text)))
    return _merge(cues)


_TIME = re.compile(r"(?:(\d+):)?(\d+):(\d+)[.,](\d+)\s+-->")
_TAGS = re.compile(r"<[^>]+>")


def parse_vtt(data: str) -> list[Cue]:
    """WebVTT or SRT. Automatic captions repeat each line as it scrolls; repeats are dropped."""
    cues: list[Cue] = []
    start = None
    seen_lines: set[str] = set()
    for line in data.splitlines():
        if m := _TIME.search(line):
            h, mi, s, frac = m.groups()
            start = int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(frac) / 10 ** len(frac)
            continue
        text = _clean(_TAGS.sub("", line))
        if start is None or not text or text.isdigit() or text in seen_lines:
            continue
        seen_lines.add(text)
        cues.append(Cue(round(start, 2), text))
    return _merge(cues)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("&gt;", ">").replace("&amp;", "&")).strip()


def _merge(cues: list[Cue], target: int = 90) -> list[Cue]:
    """Join short fragments into lines of about `target` characters, so a hit has context."""
    out: list[Cue] = []
    for c in cues:
        if out and len(out[-1].text) + len(c.text) < target and c.start - out[-1].start < 8:
            out[-1].text += " " + c.text
        else:
            out.append(Cue(c.start, c.text))
    return out


def fetch(ydl, info: dict) -> tuple[str, list[Cue]] | None:
    """Download and parse the best caption track with an open YoutubeDL (its cookies, its
    headers). None when the video has no captions or they can't be read."""
    track = pick_track(info)
    if track is None:
        return None
    lang, fmt, url = track
    try:
        with ydl.urlopen(url) as resp:
            data = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    cues = parse(fmt, data)
    return (lang.replace("-orig", ""), cues) if cues else None


# ---------- storing and searching ----------


def _file(video_id: str) -> Path:
    safe = re.sub(r"[^\w.-]", "_", video_id)[:100] or "video"
    return transcripts_dir() / f"{safe}.json"


def save(t: Transcript) -> None:
    try:
        path = _file(t.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(t), ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # search is a bonus; never fail a download over it


def load_all() -> list[Transcript]:
    out = []
    try:
        files = sorted(
            transcripts_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
    except OSError:
        return []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["cues"] = [Cue(**c) for c in data.get("cues") or []]
            out.append(Transcript(**data))
        except (OSError, ValueError, TypeError):
            continue
    return out


def search(query: str, transcripts: list[Transcript], limit: int = 200) -> list[Hit]:
    """Cues containing every word of `query` (any order, any case), newest videos first.
    A phrase in quotes must appear as written."""
    wanted = needles(query)
    if not wanted:
        return []
    hits = []
    for t in transcripts:
        for i, cue in enumerate(t.cues):
            low = cue.text.lower()
            if all(n in low for n in wanted):
                hits.append(Hit(t, i))
                if len(hits) >= limit:
                    return hits
    return hits


def needles(query: str) -> list[str]:
    q = query.strip().lower()
    return re.findall(r'"([^"]+)"', q) + [w for w in re.sub(r'"[^"]*"', " ", q).split() if w]


def count() -> int:
    try:
        return sum(1 for _ in transcripts_dir().glob("*.json"))
    except OSError:
        return 0
