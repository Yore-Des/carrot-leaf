"""Command-line entry point: `carrot [URL or FILE]`, `carrot get`, `carrot convert`, `carrot sync`,
`carrot setup`, `carrot update`, `carrot --version`."""

from __future__ import annotations

import argparse
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["update"]:
        return _update()
    if argv[:1] == ["setup"]:
        return _setup()
    if argv[:1] in (["get"], ["convert"], ["sync"]):
        from . import batch

        return batch.main(argv)

    parser = argparse.ArgumentParser(
        prog="carrot",
        description="Download or convert videos from YouTube and 1,300+ other sites.",
        epilog=(
            "Without the app: `carrot get URL --format mp3` downloads, `carrot convert FILE "
            "--format webp` converts (see `carrot get --help`). `carrot setup` downloads "
            "ffmpeg now, `carrot update` updates yt-dlp."
        ),
    )
    parser.add_argument(
        "url", nargs="?", default="", help="video link (or file to convert) to open straight away"
    )
    parser.add_argument("--version", action="version", version=_version_text())
    args = parser.parse_args(argv)

    from .app import CarrotLeafApp

    app = CarrotLeafApp(initial_url=args.url)
    app.run()
    if app.pending_app_update:
        from .engine import update

        if update.start_self_upgrade():
            print(
                f"Updating Carrot Leaf {__version__} → {app.pending_app_update} in the background."
                " It'll be ready next time you run carrot."
            )
    return 0


def _version_text() -> str:
    from .engine.update import installed_version

    return f"carrot-leaf {__version__} (yt-dlp {installed_version()})"


def _setup() -> int:
    """Download Carrot Leaf's own ffmpeg now rather than on first use. The installers run this."""
    from .engine import deps, local

    if deps.bundled_ffmpeg_ready():
        print("ffmpeg is already set up.")
    else:
        print("Getting ffmpeg for Carrot Leaf (one time, about 50 MB)…")
    try:
        tool = deps.fetch_bundled_ffmpeg(show_progress=True)
    except Exception as exc:
        print(f"Couldn't download ffmpeg: {exc}")
        print("Carrot Leaf will try again the first time it needs ffmpeg.")
        return 1
    print(f"✓ ffmpeg is in {tool.path}")
    if system := deps.system_ffmpeg():
        print(f"  Your own ffmpeg in {system.path} is used too, for anything it can do.")

    dirs = [t.path for t in deps.ffmpeg_candidates()]

    def has(encoder: str) -> bool:
        return any(deps.ffmpeg_has_encoder(d, encoder) for d in dirs)

    for kind in ("video", "audio", "image"):
        formats = local.formats_for(kind)
        missing = [f.upper() for f in formats if local.missing_encoder(kind, f, has)]
        line = f"  {len(formats) - len(missing)} {kind} formats ready"
        print(line + (f" (not available: {', '.join(missing)})" if missing else ""))
    return 0


def _update() -> int:
    from .engine import update

    current = update.installed_version()
    latest = update.latest_version()
    if latest and update._key(latest) <= update._key(current):
        print(f"yt-dlp {current} is already the latest version.")
        return 0
    print(f"Updating yt-dlp {current} → {latest or 'latest'}…")
    print("$ " + " ".join(update.upgrade_command()))
    ok, output = update.upgrade()
    if output:
        print(output)
    print("Done." if ok else "Update failed.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
