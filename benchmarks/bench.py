"""Measure Carrot Leaf's startup, memory, CPU and install size.

    uv run python benchmarks/bench.py [--out results.json]

Runs the real app in a pseudo-terminal (as a user would), plus a few headless runs for
the Convert files tab. Needs ffmpeg/ffprobe on PATH. Not shipped with the package.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import pty
import re
import select
import signal
import statistics
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = {
    **{k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"},
    "CARROT_NO_UPDATE_CHECK": "1",
    "CARROT_NO_AUTO_UPDATE": "1",
    "CARROT_NO_FFMPEG_FETCH": "1",
    "TERM": "xterm-256color",
    "COLORTERM": "truecolor",
}
PY = sys.executable
# A throwaway HOME: default settings and an empty data folder for every version tested.
_HOME = tempfile.mkdtemp(prefix="carrot-bench-home-")
ENV["HOME"] = _HOME


def ps(pid: int) -> tuple[float, int]:
    """(CPU seconds used so far, resident memory in KB) for `pid`."""
    out = subprocess.run(
        ["ps", "-o", "cputime=,rss=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.split()
    m, s = out[0].split(":")[-2:]
    hours = int(out[0].split(":")[0]) if out[0].count(":") == 2 else 0
    return hours * 3600 + int(m) * 60 + float(s), int(out[1])


class Tui:
    """The app running in a pty at 120x46, its output drained in the background."""

    def __init__(self, *args: str, env: dict | None = None) -> None:
        self.started = time.perf_counter()
        pid, fd = pty.fork()
        if pid == 0:  # child
            os.execve(PY, [PY, "-m", "carrot_leaf", *args], env or ENV)
        self.pid, self.fd = pid, fd
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 46, 120, 0, 0))
        self.buf = bytearray()
        self.bytes_out = 0
        self.alive = True
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        while self.alive:
            r, _, _ = select.select([self.fd], [], [], 0.1)
            if r:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    return
                self.bytes_out += len(data)
                self.buf += data
                if len(self.buf) > 1_000_000:
                    del self.buf[:-200_000]

    def wait_for(self, text: str, timeout: float = 30.0) -> float:
        """Seconds since launch when `text` first showed on screen."""
        plain = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]")
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if text.encode() in plain.sub(b"", bytes(self.buf)):
                return time.perf_counter() - self.started
            time.sleep(0.01)
        raise TimeoutError(text)

    def send(self, keys: str) -> None:
        os.write(self.fd, keys.encode())

    def close(self) -> None:
        self.alive = False
        try:
            os.kill(self.pid, signal.SIGTERM)
            os.waitpid(self.pid, 0)
        except OSError:
            pass
        os.close(self.fd)


def first_launch(runs: int) -> dict:
    """Startup with no cached bytecode, as on the first run after `uv tool install`."""
    times = []
    for _ in range(runs):
        with tempfile.TemporaryDirectory() as cache:
            tui = Tui(env={**ENV, "PYTHONPYCACHEPREFIX": cache, "PYTHONDONTWRITEBYTECODE": "1"})
            times.append(tui.wait_for("Paste a link above"))
            tui.close()
    return {"first_launch_no_bytecode_s": round(statistics.median(times), 3)}


def startup(runs: int) -> dict:
    times, rss = [], []
    Tui().close()  # warm the bytecode cache
    for _ in range(runs):
        tui = Tui()
        times.append(tui.wait_for("Paste a link above"))
        time.sleep(1.0)  # let startup work (workers, first paints) settle
        rss.append(ps(tui.pid)[1])
        tui.close()
    return {
        "first_frame_s": round(statistics.median(times), 3),
        "rss_after_start_mb": round(statistics.median(rss) / 1024, 1),
    }


def idle(seconds: float, *args: str, ready: str = "Paste a link above") -> dict:
    tui = Tui(*args)
    tui.wait_for(ready)
    time.sleep(2.0)
    cpu0, _ = ps(tui.pid)
    out0 = tui.bytes_out
    time.sleep(seconds)
    cpu1, rss = ps(tui.pid)
    out1 = tui.bytes_out
    tui.close()
    return {
        "idle_cpu_pct": round((cpu1 - cpu0) / seconds * 100, 2),
        "idle_bytes_to_terminal_per_s": round((out1 - out0) / seconds),
        "rss_mb": round(rss / 1024, 1),
    }


def convert_in_tui(clip: Path) -> dict:
    """Convert a 60 s 720p clip to 360p WebM in the real app; Carrot's own CPU, not ffmpeg's."""
    tui = Tui(str(clip))
    tui.wait_for("1280×720")  # listed, so it has been read
    time.sleep(0.5)
    tui.send("\t")  # leave the file bar so letter keys are shortcuts
    time.sleep(0.3)
    tui.send("c")
    tui.wait_for("Same as original")
    time.sleep(0.3)
    tui.send("\x1b[B\x1b[B\r")  # MP4 (the default), MKV, WebM -> quality list
    time.sleep(0.3)
    tui.send("\x1b[B\x1b[B\x1b[B")  # down to 360p
    time.sleep(0.3)
    cpu0, _ = ps(tui.pid)
    out0, t0 = tui.bytes_out, time.perf_counter()
    tui.send("\r")  # convert
    tui.wait_for("Saved", timeout=300)
    wall = time.perf_counter() - t0
    cpu1, rss = ps(tui.pid)
    out1 = tui.bytes_out
    tui.close()
    for f in clip.parent.glob("clip*.webm"):
        f.unlink()
    return {
        "tui_convert_wall_s": round(wall, 2),
        "tui_convert_app_cpu_s": round(cpu1 - cpu0, 2),
        "tui_convert_app_cpu_pct": round((cpu1 - cpu0) / wall * 100, 2),
        "tui_convert_bytes_to_terminal": out1 - out0,
        "tui_convert_rss_mb": round(rss / 1024, 1),
    }


def cli_version(runs: int) -> dict:
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        subprocess.run([PY, "-m", "carrot_leaf", "--version"], env=ENV, capture_output=True)
        times.append(time.perf_counter() - t)
    return {"carrot_version_s": round(statistics.median(times), 3)}


def import_time(runs: int) -> dict:
    times = []
    for _ in range(runs):
        out = subprocess.run(
            [PY, "-c", "import time;t=time.perf_counter();import carrot_leaf.app;"
             "print(time.perf_counter()-t)"],
            env=ENV, capture_output=True, text=True,
        )  # fmt: skip
        times.append(float(out.stdout))
    return {"import_app_s": round(statistics.median(times), 3)}


def make_media(folder: Path) -> list[Path]:
    clip = folder / "clip.mov"
    photo = folder / "photo.png"
    song = folder / "song.flac"
    ff = ["ffmpeg", "-v", "error", "-y"]
    subprocess.run(
        [*ff, "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=60", "-f", "lavfi",
         "-i", "sine=duration=60", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt",
         "yuv420p", "-c:a", "aac", "-shortest", str(clip)],
        check=True,
    )  # fmt: skip
    subprocess.run(
        [*ff, "-f", "lavfi", "-i", "testsrc2=size=3000x2000", "-frames:v", "1", str(photo)],
        check=True,
    )
    subprocess.run(
        [*ff, "-f", "lavfi", "-i", "sine=duration=30", "-c:a", "flac", str(song)], check=True
    )
    (folder / "photos").mkdir()
    for n in range(20):
        subprocess.run(
            [*ff, "-f", "lavfi", "-i", f"testsrc2=size=1200x800:duration={n + 1}", "-frames:v",
             "1", str(folder / "photos" / f"p{n:02}.png")],
            check=True,
        )  # fmt: skip
    return [clip, photo, song]


HEADLESS = r"""
import asyncio, json, resource, sys, time
from pathlib import Path
from carrot_leaf import config
config.config_path = lambda: Path(sys.argv[2]) / "config.toml"
from carrot_leaf.app import CarrotLeafApp
from carrot_leaf.engine import runner
from carrot_leaf.engine.options import Choice
from carrot_leaf.screens.files import FilesPane

files = [Path(p) for p in json.loads(sys.argv[1])]
out = {}

async def main():
    app = CarrotLeafApp()
    async with app.run_test(size=(120, 46)) as pilot:
        pane = app.screen.query_one(FilesPane)
        t = time.perf_counter()
        app.screen.add_files(files)
        while len(pane.files) < len(files):
            await pilot.pause(0.005)
        out["read_3_files_s"] = round(time.perf_counter() - t, 3)

        photos = sorted(Path(sys.argv[3]).glob("*.png"))
        t = time.perf_counter()
        app.screen.add_files(photos)
        while len(pane.files) < len(files) + len(photos):
            await pilot.pause(0.005)
        out["read_20_photos_s"] = round(time.perf_counter() - t, 3)
        for _ in photos:
            pane.files.pop()
        pane._render_files()

        t = time.perf_counter()
        pane.convert()
        while app.screen.__class__.__name__ != "ConvertScreen" or not app.screen.is_mounted:
            await pilot.pause(0.005)
        await pilot.pause()
        out["open_convert_dialog_s"] = round(time.perf_counter() - t, 3)
        app.pop_screen()
        await pilot.pause()

        # A real re-encode: the clip to WebM. Carrot's own CPU, not ffmpeg's.
        cpu, t = time.process_time(), time.perf_counter()
        pane.enqueue(pane.files[0], Choice("video", "webm", "360"))
        while not pane.jobs or pane.jobs[-1].state not in runner.FINISHED:
            await pilot.pause(0.05)
        wall = time.perf_counter() - t
        out["convert_wall_s"] = round(wall, 2)
        out["convert_app_cpu_pct"] = round((time.process_time() - cpu) / wall * 100, 2)
        out["convert_state"] = pane.jobs[-1].state
    out["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 1)

asyncio.run(main())
print(json.dumps(out))
"""


def headless(files: list[Path], runs: int) -> dict:
    results = []
    for _ in range(runs):
        with tempfile.TemporaryDirectory() as cfg:
            out = subprocess.run(
                [PY, "-c", HEADLESS, json.dumps([str(f) for f in files]), cfg,
                 str(files[0].parent / "photos")],
                env=ENV, capture_output=True, text=True, cwd=ROOT,
            )  # fmt: skip
            if out.returncode:
                raise RuntimeError(out.stderr)
            results.append(json.loads(out.stdout.strip().splitlines()[-1]))
        for f in files[0].parent.glob("clip*.webm"):
            f.unlink()
    keys = [k for k in results[0] if isinstance(results[0][k], (int, float))]
    merged = {k: round(statistics.median(r[k] for r in results), 3) for k in keys}
    merged["convert_state"] = results[-1]["convert_state"]
    return merged


def size() -> dict:
    """Wheel size and a fresh install's size, built from this checkout."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        subprocess.run(["uv", "build", "--wheel", "-o", str(tmp / "dist")], cwd=ROOT,
                       capture_output=True, check=True)  # fmt: skip
        wheel = next((tmp / "dist").glob("*.whl"))
        subprocess.run(["uv", "venv", "-q", "--python", "3.12", str(tmp / "venv")], check=True)
        subprocess.run(
            ["uv", "pip", "install", "-q", "--python", str(tmp / "venv" / "bin" / "python"),
             str(wheel)],
            check=True, capture_output=True,
        )  # fmt: skip
        site = next((tmp / "venv" / "lib").glob("python*/site-packages"))
        total = sum(f.stat().st_size for f in site.rglob("*") if f.is_file())
        env = sum(f.stat().st_size for f in (tmp / "venv").rglob("*") if f.is_file())
        packages = len(list(site.glob("*.dist-info")))
        return {
            "wheel_kb": round(wheel.stat().st_size / 1024, 1),
            "installed_packages": packages,
            "site_packages_mb": round(total / 2**20, 1),
            "installed_env_mb": round(env / 2**20, 1),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    results: dict = {}
    results |= import_time(args.runs)
    results |= cli_version(args.runs)
    results |= startup(args.runs)
    results |= first_launch(3)
    results |= {f"download_tab_{k}": v for k, v in idle(10).items()}
    with tempfile.TemporaryDirectory() as tmp:
        media = make_media(Path(tmp))
        results |= {
            f"files_tab_{k}": v for k, v in idle(10, str(media[0]), ready="Drop files here").items()
        }
        results |= headless(media, max(1, args.runs // 2))
        results |= convert_in_tui(media[0])
    results |= size()
    print(json.dumps(results, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
