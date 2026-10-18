"""User settings, stored as TOML in the platform config directory."""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import platformdirs
import tomli_w

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

APP_NAME = "carrot-leaf"


def default_download_dir() -> str:
    return str(Path(platformdirs.user_downloads_dir()))


def config_path() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False)) / "config.toml"


@dataclass
class Settings:
    download_dir: str = ""
    # Last choice made in the Convert dialog, so it opens where the user left it.
    last_kind: str = "video"
    last_video_format: str = "mp4"
    last_video_quality: str = "best"
    last_audio_format: str = "mp3"
    last_audio_quality: str = "best"
    # Browser cookies stay off until the user opts in for a login-only video.
    cookies_browser: str = ""

    def __post_init__(self) -> None:
        if not self.download_dir:
            self.download_dir = default_download_dir()

    @property
    def download_path(self) -> Path:
        return Path(self.download_dir).expanduser()


def load(path: Path | None = None) -> Settings:
    path = path or config_path()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return Settings()
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in data.items() if k in known and isinstance(v, str)})


def save(settings: Settings, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(asdict(settings)), encoding="utf-8")
