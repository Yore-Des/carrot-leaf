"""Check PyPI for newer yt-dlp and Carrot Leaf releases and upgrade them in place.

yt-dlp is upgraded on request (press u). Carrot Leaf upgrades itself: a check runs while the
app is open, and if PyPI has a newer release the upgrade starts in the background on quit, so
the next launch is the new version. Only uv tool and pipx installs self-update.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from functools import cache

PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
APP_PYPI_URL = "https://pypi.org/pypi/carrot-leaf/json"


@cache
def installed_version() -> str:
    """yt-dlp's version, read once like an import would, without importing all of yt-dlp
    (which the status line would otherwise pay for at every launch)."""
    if module := sys.modules.get("yt_dlp.version"):
        return module.__version__
    import importlib.util

    try:
        spec = importlib.util.find_spec("yt_dlp")
        folder = next(iter(spec.submodule_search_locations or []))
        version_spec = importlib.util.spec_from_file_location(
            "_carrot_ytdlp_version", os.path.join(folder, "version.py")
        )
        module = importlib.util.module_from_spec(version_spec)
        version_spec.loader.exec_module(module)
        return module.__version__
    except Exception:
        from yt_dlp.version import __version__

        return __version__


def _key(version: str) -> tuple[int, ...]:
    # yt-dlp uses date versions (2026.08.19 on disk, 2026.8.19 on PyPI): compare numerically.
    return tuple(int(p) for p in version.split(".") if p.isdigit())


def latest_version(timeout: float = 4.0, url: str = PYPI_URL) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.load(resp)["info"]["version"]
    except Exception:
        return None


def update_available() -> str | None:
    """The newer version string, or None if up to date or offline."""
    latest = latest_version()
    if latest and _key(latest) > _key(installed_version()):
        return latest
    return None


def upgrade_command() -> list[str]:
    prefix = sys.prefix.replace("\\", "/")
    uv = shutil.which("uv")
    if uv and "/uv/tools/" in prefix:
        return [uv, "tool", "upgrade", "carrot-leaf"]
    pipx = shutil.which("pipx")
    if pipx and "/pipx/venvs/" in prefix:
        return [pipx, "runpip", "carrot-leaf", "install", "-U", "yt-dlp[default]"]
    if uv and not _has_pip():
        return [uv, "pip", "install", "--python", sys.executable, "-U", "yt-dlp[default]"]
    return [sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"]


def _has_pip() -> bool:
    try:
        import pip  # noqa: F401
    except ImportError:
        return False
    return True


def upgrade() -> tuple[bool, str]:
    """Run the upgrade. Returns (ok, output)."""
    cmd = upgrade_command()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


# ---------- Carrot Leaf itself ----------


def app_update_available() -> str | None:
    """The newer Carrot Leaf version, or None if up to date, offline or not self-updatable."""
    from .. import __version__

    if os.environ.get("CARROT_NO_AUTO_UPDATE") or self_upgrade_command() is None:
        return None
    latest = latest_version(url=APP_PYPI_URL)
    if latest and _key(latest) > _key(__version__):
        return latest
    return None


def self_upgrade_command() -> list[str] | None:
    """How to upgrade this install, or None for pip / dev installs we shouldn't touch."""
    prefix = sys.prefix.replace("\\", "/")
    uv = shutil.which("uv")
    if uv and "/uv/tools/" in prefix:
        return [uv, "tool", "upgrade", "carrot-leaf"]
    pipx = shutil.which("pipx")
    if pipx and "/pipx/venvs/" in prefix:
        return [pipx, "upgrade", "carrot-leaf"]
    return None


def start_self_upgrade() -> bool:
    """Start the upgrade detached from this process, so quitting isn't held up. True if started."""
    cmd = self_upgrade_command()
    if cmd is None:
        return False
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
    except OSError:
        return False
    return True
