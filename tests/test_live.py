"""Real downloads. Opt in with CARROT_LIVE=1 (needs internet; YouTube can be flaky)."""

import os
import shutil
import subprocess

import pytest

from carrot_leaf.engine import probe, runner
from carrot_leaf.engine.options import DOWNLOAD, Choice

pytestmark = pytest.mark.skipif(not os.environ.get("CARROT_LIVE"), reason="set CARROT_LIVE=1")

# "Me at the zoo": 19 seconds, the first video on YouTube.
URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def ffprobe(path, entries):
    exe = shutil.which("ffprobe")
    if not exe:
        pytest.skip("ffprobe not on PATH")
    out = subprocess.run(
        [exe, "-v", "error", "-show_entries", entries, "-of", "default=nw=1:nk=1", path],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split()


def test_probe():
    info = probe.probe(URL)
    assert info.title == "Me at the zoo"
    assert info.duration == 19


@pytest.mark.parametrize(
    "choice, ext, codec",
    [
        (DOWNLOAD, "mp4", "h264"),
        (Choice("audio", "mp3", "192"), "mp3", "mp3"),
        (Choice("video", "gif", "240"), "gif", "gif"),
    ],
)
def test_download(tmp_path, choice, ext, codec):
    job = runner.Job(URL, "x", choice)
    path = runner.run_job(job, tmp_path, lambda p: None)
    assert path.endswith(f".{ext}")
    assert codec in ffprobe(path, "stream=codec_name")
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".carrot-leaf-")]
