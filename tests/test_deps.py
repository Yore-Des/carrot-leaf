"""Carrot Leaf's own ffmpeg download, and what loads at startup. Offline."""

import subprocess
import sys
import zipfile

import pytest

from carrot_leaf.engine import deps


def fake_build(tmp_path, key, exe=""):
    """A zip laid out like the real ones: a folder named after the platform key."""
    archive = tmp_path / "build.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name in ("ffmpeg", "ffprobe"):
            zf.writestr(f"{key}/{name}{exe}", "#!/bin/sh\necho fake\n")
    return archive


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    key = deps.platform_key()
    archive = fake_build(tmp_path, key, ".exe" if sys.platform == "win32" else "")
    monkeypatch.setitem(deps.FFMPEG_ZIPS, key, archive.as_uri())
    home = tmp_path / "data" / "ffmpeg" / key
    monkeypatch.setattr(deps, "bundled_dir", lambda: home)
    return home


def test_fetch_unpacks_once(bundle, monkeypatch):
    assert not deps.bundled_ffmpeg_ready()
    assert deps.bundled_ffmpeg() is None
    tool = deps.fetch_bundled_ffmpeg()
    assert tool == deps.Tool(str(bundle), bundled=True)
    assert deps.bundled_ffmpeg_ready()
    assert deps.bundled_ffmpeg() == tool
    if sys.platform != "win32":
        assert (bundle / "ffmpeg").stat().st_mode & 0o111
    # Nothing left behind but the build itself.
    assert sorted(p.name for p in bundle.parent.iterdir()) == [bundle.name]

    def no_download(*args):
        raise AssertionError("downloaded twice")

    monkeypatch.setattr(deps, "_download", no_download)
    assert deps.fetch_bundled_ffmpeg() == tool


def test_fetch_replaces_a_broken_attempt(bundle):
    bundle.mkdir(parents=True)
    (bundle / "ffmpeg").write_text("half a file")  # no crumb: an interrupted install
    deps.fetch_bundled_ffmpeg()
    assert (bundle / deps.CRUMB).exists()
    assert "fake" in (bundle / "ffprobe").read_text()


def test_fetch_rejects_a_bad_download(bundle, tmp_path, monkeypatch):
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(broken, "w") as zf:
        zf.writestr(f"{deps.platform_key()}/ffmpeg", "x")  # no ffprobe
    monkeypatch.setitem(deps.FFMPEG_ZIPS, deps.platform_key(), broken.as_uri())
    with pytest.raises(OSError, match="ffprobe"):
        deps.fetch_bundled_ffmpeg()
    assert not deps.bundled_ffmpeg_ready()
    assert not list(bundle.parent.glob(".download-*"))


def test_unknown_platform(bundle, monkeypatch):
    monkeypatch.setattr(deps, "platform_key", lambda: "plan9")
    with pytest.raises(OSError, match="Install ffmpeg yourself"):
        deps.fetch_bundled_ffmpeg()


def test_bundled_build_lives_outside_the_app_environment():
    # So a self-update, which rebuilds the environment, doesn't download it again.
    assert sys.prefix not in str(deps.bundled_dir())


def test_startup_does_not_load_yt_dlp_or_pillow():
    code = (
        "import sys, carrot_leaf.app\n"
        "from carrot_leaf.engine import update\n"
        "update.installed_version()\n"
        "print(sorted(m for m in ('yt_dlp', 'PIL', 'pillow_heif', 'pillow_jxl', "
        "'importlib.metadata') if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_installed_version_matches_yt_dlp():
    from yt_dlp.version import __version__

    from carrot_leaf.engine import update

    assert update.installed_version() == __version__
