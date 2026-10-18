"""Check PyPI for a newer yt-dlp and upgrade it in whichever way Carrot Leaf was installed."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.request

PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"


def installed_version() -> str:
    from yt_dlp.version import __version__

    return __version__


def _key(version: str) -> tuple[int, ...]:
    # yt-dlp uses date versions (2026.08.19 on disk, 2026.8.19 on PyPI): compare numerically.
    return tuple(int(p) for p in version.split(".") if p.isdigit())


def latest_version(timeout: float = 4.0) -> str | None:
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=timeout) as resp:
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
