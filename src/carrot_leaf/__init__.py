"""Carrot Leaf: a terminal video downloader and converter built on yt-dlp."""


def __getattr__(name: str):
    # Looked up on first use: importlib.metadata adds ~15 ms to every launch otherwise.
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            value = version("carrot-leaf")
        except PackageNotFoundError:  # pragma: no cover - running from a source tree
            value = "0.0.0"
        globals()["__version__"] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
