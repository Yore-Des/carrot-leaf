"""Carrot Leaf: a terminal video downloader and converter built on yt-dlp."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("carrot-leaf")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0"
