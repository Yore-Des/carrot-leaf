"""Command-line entry point: `carrot [URL]`, `carrot update`, `carrot --version`."""

from __future__ import annotations

import argparse
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["update"]:
        return _update()

    parser = argparse.ArgumentParser(
        prog="carrot",
        description="Download or convert videos from YouTube and 1,300+ other sites.",
        epilog="Run `carrot update` to update yt-dlp.",
    )
    parser.add_argument("url", nargs="?", default="", help="video link to open straight away")
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
