"""Python Carrot Leaf vs the Rust port, measured the same way, side by side.

    uv run python benchmarks/compare.py [--runs 5] [--out compare.json]

Both apps run in a pseudo-terminal at 120x46, as a user would run them, with the same
throwaway HOME. Needs ffmpeg on PATH and `cargo build --release` done in rust/.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench  # noqa: E402  (the pty driver, ps() and the test media)

ROOT = bench.ROOT
RUST = ROOT / "rust" / "target" / "release" / "carrot"
VARIANTS = {
    "python": [bench.PY, "-m", "carrot_leaf"],
    "rust": [str(RUST)],
}


class Tui(bench.Tui):
    """bench.Tui, but for any command."""

    def __init__(self, cmd: list[str], *args: str, env: dict | None = None) -> None:
        import fcntl
        import os
        import pty
        import struct
        import termios
        import threading

        self.started = time.perf_counter()
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(cmd[0], [*cmd, *args], env or bench.ENV)
        self.pid, self.fd = pid, fd
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 46, 120, 0, 0))
        self.buf = bytearray()
        self.bytes_out = 0
        self.alive = True
        threading.Thread(target=self._drain, daemon=True).start()


def version(cmd: list[str], runs: int) -> dict:
    subprocess.run([*cmd, "--version"], env=bench.ENV, capture_output=True)  # warm caches
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        subprocess.run([*cmd, "--version"], env=bench.ENV, capture_output=True)
        times.append(time.perf_counter() - t)
    return {"version_s": round(statistics.median(times), 3)}


def startup(cmd: list[str], runs: int) -> dict:
    Tui(cmd).close()  # warm caches (bytecode for Python, page cache for both)
    times, rss = [], []
    for _ in range(runs):
        tui = Tui(cmd)
        times.append(tui.wait_for("Paste a link above"))
        time.sleep(1.0)
        rss.append(bench.ps(tui.pid)[1])
        tui.close()
    return {
        "first_frame_s": round(statistics.median(times), 3),
        "rss_after_start_mb": round(statistics.median(rss) / 1024, 1),
    }


def first_launch(cmd: list[str], runs: int) -> dict:
    """Python with no cached bytecode, as right after install. Rust has nothing to cache."""
    times = []
    for _ in range(runs):
        with tempfile.TemporaryDirectory() as cache:
            env = {**bench.ENV, "PYTHONPYCACHEPREFIX": cache, "PYTHONDONTWRITEBYTECODE": "1"}
            tui = Tui(cmd, env=env)
            times.append(tui.wait_for("Paste a link above"))
            tui.close()
    return {"first_launch_s": round(statistics.median(times), 3)}


def idle(cmd: list[str], seconds: float, *args: str, ready: str) -> dict:
    tui = Tui(cmd, *args)
    tui.wait_for(ready)
    time.sleep(2.0)
    cpu0, _ = bench.ps(tui.pid)
    out0 = tui.bytes_out
    time.sleep(seconds)
    cpu1, rss = bench.ps(tui.pid)
    out1 = tui.bytes_out
    tui.close()
    return {
        "idle_cpu_pct": round((cpu1 - cpu0) / seconds * 100, 2),
        "idle_bytes_per_s": round((out1 - out0) / seconds),
        "rss_mb": round(rss / 1024, 1),
    }


def convert(cmd: list[str], clip: Path) -> dict:
    """The 60 s 720p clip to 360p WebM through the real app's keys; the app's CPU only.

    A fresh HOME each time: the dialog reopens on the last format used, so a shared one
    would make the second app's key presses pick a different format."""
    home = tempfile.mkdtemp(prefix="carrot-bench-convert-")
    tui = Tui(cmd, str(clip), env={**bench.ENV, "HOME": home})
    tui.wait_for("1280×720")
    time.sleep(0.5)
    tui.send("\t")
    time.sleep(0.3)
    t_open = time.perf_counter()
    tui.send("c")
    tui.wait_for("Same as original")
    open_s = time.perf_counter() - t_open
    time.sleep(0.3)
    tui.send("\x1b[B\x1b[B\r")
    time.sleep(0.3)
    tui.send("\x1b[B\x1b[B\x1b[B")
    time.sleep(0.3)
    cpu0, _ = bench.ps(tui.pid)
    out0, t0 = tui.bytes_out, time.perf_counter()
    tui.send("\r")
    tui.wait_for("Saved", timeout=300)
    wall = time.perf_counter() - t0
    cpu1, rss = bench.ps(tui.pid)
    out1 = tui.bytes_out
    tui.close()
    for f in clip.parent.glob("clip*.webm"):
        f.unlink()
    return {
        "open_convert_dialog_s": round(open_s, 3),
        "convert_wall_s": round(wall, 2),
        "convert_app_cpu_s": round(cpu1 - cpu0, 2),
        "convert_app_cpu_pct": round((cpu1 - cpu0) / wall * 100, 2),
        "convert_bytes_to_terminal": out1 - out0,
        "convert_rss_mb": round(rss / 1024, 1),
    }


def sizes() -> dict:
    py = bench.size()
    return {
        "python": {"install_mb": py["installed_env_mb"], "packages": py["installed_packages"]},
        "rust": {"install_mb": round(RUST.stat().st_size / 2**20, 1), "packages": 0},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--out")
    parser.add_argument("--no-size", action="store_true")
    args = parser.parse_args()
    if not RUST.exists():
        sys.exit("Build the Rust port first: cd rust && cargo build --release")
    results: dict = {name: {} for name in VARIANTS}
    with tempfile.TemporaryDirectory() as tmp:
        media = bench.make_media(Path(tmp))
        for name, cmd in VARIANTS.items():
            r = results[name]
            print(f"· {name}", file=sys.stderr)
            r |= version(cmd, args.runs)
            r |= startup(cmd, args.runs)
            r |= first_launch(cmd, 3)
            r |= {f"download_tab_{k}": v for k, v in idle(cmd, 10, ready="Paste a link above").items()}
            r |= {
                f"files_tab_{k}": v
                for k, v in idle(cmd, 10, str(media[0]), ready="Drop files here").items()
            }
            runs = [convert(cmd, media[0]) for _ in range(3)]
            r |= {k: round(statistics.median(x[k] for x in runs), 3) for k in runs[0]}
    if not args.no_size:
        for name, v in sizes().items():
            results[name] |= v
    print(json.dumps(results, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
