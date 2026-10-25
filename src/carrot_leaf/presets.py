"""Named Convert setups ("Podcast: MP3 128k, even volume"), kept as JSON next to the config.

A preset can also be the default for one site, so `d` on a link from there uses it instead
of best MP4.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from . import config
from .engine.options import Choice, Extras

MAX_PRESETS = 9  # one per number key


def presets_path() -> Path:
    return config.config_path().parent / "presets.json"


def site_key(extractor: str) -> str:
    """One name per site from yt-dlp's extractor: YoutubeTab and Youtube are both "youtube"."""
    m = re.match(r"[A-Z]?[a-z0-9]+", extractor or "")
    return m.group(0).lower() if m else ""


def site_name(key: str) -> str:
    return {"youtube": "YouTube", "soundcloud": "SoundCloud", "tiktok": "TikTok"}.get(
        key, key.capitalize()
    )


@dataclass
class Preset:
    name: str
    kind: str
    fmt: str
    quality: str = "best"
    extras: dict = field(default_factory=dict)  # Extras fields; never a clip
    site: str = ""  # site_key() this preset is the default for, or ""

    def choice(self) -> Choice:
        known = {f.name for f in fields(Extras)}
        extras = Extras(**{k: v for k, v in self.extras.items() if k in known})
        return Choice(self.kind, self.fmt, self.quality, extras)

    def matches(self, choice: Choice) -> bool:
        return without_clip(choice) == self.choice()


def without_clip(choice: Choice) -> Choice:
    """Clips belong to one video, so presets never keep them."""
    return replace(choice, extras=replace(choice.extras, start=None, end=None))


def from_choice(name: str, choice: Choice, site: str = "") -> Preset:
    c = without_clip(choice)
    defaults = asdict(Extras())
    extras = {k: v for k, v in asdict(c.extras).items() if v != defaults[k]}
    return Preset(name.strip(), c.kind, c.fmt, c.quality, extras, site)


def load(path: Path | None = None) -> list[Preset]:
    path = path or presets_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    known = {f.name for f in fields(Preset)}
    out = []
    for item in data if isinstance(data, list) else []:
        try:
            out.append(Preset(**{k: v for k, v in item.items() if k in known}))
        except TypeError:
            continue
    return out[:MAX_PRESETS]


def save(presets: list[Preset], path: Path | None = None) -> None:
    path = path or presets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(p) for p in presets], indent=2), encoding="utf-8")
    tmp.replace(path)


def upsert(preset: Preset, path: Path | None = None) -> list[Preset]:
    """Add `preset`, replacing one with the same name. Only one preset per site default."""
    presets = [p for p in load(path) if p.name.lower() != preset.name.lower()]
    if preset.site:
        presets = [replace(p, site="") if p.site == preset.site else p for p in presets]
    presets.append(preset)
    presets = presets[-MAX_PRESETS:]
    save(presets, path)
    return presets


def remove(name: str, path: Path | None = None) -> list[Preset]:
    presets = [p for p in load(path) if p.name != name]
    save(presets, path)
    return presets


def default_for(extractor: str, presets: list[Preset] | None = None) -> Preset | None:
    key = site_key(extractor)
    if not key:
        return None
    return next((p for p in (presets if presets is not None else load()) if p.site == key), None)
