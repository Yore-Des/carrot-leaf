"""The bits of the desktop around the terminal: clipboard, notifications, Finder/Explorer.

Everything here is best effort. A missing tool (no `xclip`, no `notify-send`) means the
feature quietly does nothing rather than an error.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], *, input: str | None = None, timeout: float = 2.0) -> str | None:
    try:
        out = subprocess.run(
            cmd, input=input, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def read_clipboard() -> str:
    """The clipboard's text, or "" when it can't be read."""
    if os.environ.get("CARROT_NO_CLIPBOARD"):
        return ""
    if sys.platform == "darwin":
        cmds = [["pbpaste"]]
    elif sys.platform == "win32":
        cmds = [["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"]]
    else:
        cmds = [["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"]]
        cmds.append(["xsel", "--clipboard", "--output"])
    for cmd in cmds:
        if shutil.which(cmd[0]) and (text := _run(cmd)) is not None:
            return text.strip()
    return ""


def write_clipboard(text: str) -> bool:
    if sys.platform == "darwin":
        cmds = [["pbcopy"]]
    elif sys.platform == "win32":
        cmds = [["clip"]]
    else:
        cmds = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard"]]
    return any(shutil.which(cmd[0]) and _run(cmd, input=text) is not None for cmd in cmds)


def notify(title: str, message: str) -> None:
    """A system notification. Returns straight away; nothing waits for it."""
    if os.environ.get("CARROT_NO_NOTIFY"):
        return
    if sys.platform == "darwin":
        script = f"display notification {_applescript(message)} with title {_applescript(title)}"
        cmd = ["osascript", "-e", script]
    elif sys.platform == "win32":
        cmd = [
            "powershell",
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-Command",
            _toast(title, message),
        ]
    elif shutil.which("notify-send"):
        cmd = ["notify-send", "--app-name=Carrot Leaf", title, message]
    else:
        return
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _applescript(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toast(title: str, message: str) -> str:
    """A balloon tip from the tray: works on every Windows without extra modules."""

    def ps(text: str) -> str:
        return "'" + text.replace("'", "''") + "'"

    return (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        f"$n.BalloonTipTitle = {ps(title)}; $n.BalloonTipText = {ps(message)};"
        "$n.Visible = $true; $n.ShowBalloonTip(5000); Start-Sleep -Seconds 6; $n.Dispose()"
    )


def reveal(path: str) -> None:
    """Show a file in Finder / Explorer / the default file manager."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", path])
        else:
            subprocess.Popen(["xdg-open", str(Path(path).parent)])
    except OSError:
        pass


def open_file(path: str) -> None:
    """Open a file in the app the system uses for it."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


# Players that can start at a given second, and how to ask them.
PLAYERS = (
    ("mpv", lambda path, t: ["mpv", f"--start={t:.0f}", path]),
    ("iina", lambda path, t: ["iina", f"--mpv-start={t:.0f}", path]),
    ("vlc", lambda path, t: ["vlc", f"--start-time={t:.0f}", path]),
)
MAC_PLAYERS = (
    (
        "/Applications/IINA.app",
        lambda path, t: ["open", "-a", "IINA", "--args", f"--mpv-start={t:.0f}", path],
    ),
    (
        "/Applications/VLC.app/Contents/MacOS/VLC",
        lambda path, t: ["/Applications/VLC.app/Contents/MacOS/VLC", f"--start-time={t:.0f}", path],
    ),
)


def play_at(path: str, seconds: float) -> bool:
    """Play a file from `seconds` in a player that can start there. Falls back to opening it
    normally and returns False, so the caller can say where to skip to."""
    for exe, build in PLAYERS:
        if shutil.which(exe):
            try:
                subprocess.Popen(
                    build(path, seconds), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                return True
            except OSError:
                continue
    if sys.platform == "darwin":
        for app, build in MAC_PLAYERS:
            if Path(app).exists():
                try:
                    subprocess.Popen(
                        build(path, seconds), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    return True
                except OSError:
                    continue
    open_file(path)
    return False
