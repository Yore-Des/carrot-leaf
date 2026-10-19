"""The Convert files tab, driven with Textual's Pilot. ffprobe/ffmpeg are replaced with fakes."""

import threading
from pathlib import Path

import pytest
from test_ui import URL, fake_info, settle
from textual import events

from carrot_leaf.app import CarrotLeafApp
from carrot_leaf.engine import deps, local, runner
from carrot_leaf.engine.local import AUDIO, IMAGE, VIDEO, FileInfo, Stream
from carrot_leaf.screens import main as main_screen
from carrot_leaf.screens.convert import ConvertScreen
from carrot_leaf.screens.files import FilePickerScreen, FilesPane, describe


def fake_file(path: Path) -> FileInfo:
    ext = path.suffix.lower()
    if ext in (".png", ".jpg"):
        return FileInfo(path, IMAGE, "png_pipe", 2_000_000, None, Stream("png", 4032, 3024), None)
    if ext in (".mp3", ".m4a"):
        return FileInfo(path, AUDIO, "mp3", 3_000_000, 48.0, None, Stream("mp3", channels=2))
    return FileInfo(
        path,
        VIDEO,
        "mov",
        412_000_000,
        134.0,
        Stream("hevc", 1920, 1080, 30.0),
        Stream("aac", channels=2),
    )


@pytest.fixture
def files_fakes(monkeypatch, isolated_config):
    calls = {"probe": [], "convert": [], "download": []}
    gate = {"convert": None, "download": None}  # threading.Events that hold a job open

    def fake_probe_file(path, ffmpeg_dir=None):
        calls["probe"].append(path)
        if path.suffix == ".txt":
            raise local.LocalFileError("x: Invalid data found when processing input")
        return fake_file(path)

    def fake_run_convert(info, choice, on_progress, cancel_event, ffmpeg_dir=None):
        calls["convert"].append((info.path.name, choice))
        on_progress(runner.Progress(runner.CONVERTING, "Converting", 0.4, eta=3))
        if gate["convert"] is not None:
            while not gate["convert"].wait(0.05):
                if cancel_event.is_set():
                    raise local.ConvertCancelled("Cancelled")
        out = info.path.with_name(local.output_name(info, choice))
        out.write_text("x")
        return str(out)

    def fake_run_job(job, download_dir, on_progress, cookies_browser=""):
        calls["download"].append(job.choice)
        on_progress(runner.Progress(runner.DOWNLOADING, "Downloading", 0.5))
        if gate["download"] is not None:
            gate["download"].wait(5)
        path = Path(download_dir) / "video.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        return str(path)

    monkeypatch.setattr(local, "probe_file", fake_probe_file)
    monkeypatch.setattr(local, "run_convert", fake_run_convert)
    monkeypatch.setattr(deps, "ffmpeg_candidates", lambda: [deps.Tool("/ff", False)])
    monkeypatch.setattr(deps, "ffmpeg_has_encoder", lambda d, e: e not in ("libwebp", "exr"))
    monkeypatch.setattr(main_screen, "probe", lambda url, cookies_browser="": fake_info(url))
    monkeypatch.setattr(main_screen.runner, "run_job", fake_run_job)
    calls["gate"] = gate
    return calls


def make(folder: Path, *names: str) -> list[Path]:
    out = []
    for name in names:
        path = folder / name
        path.write_text("x")
        out.append(path)
    return out


def escaped(paths) -> str:
    return " ".join(str(p).replace(" ", "\\ ") for p in paths)


async def test_drop_switches_tab_and_converts(files_fakes, tmp_path):
    (clip,) = make(tmp_path, "my holiday.mov")
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 44)) as pilot:
        screen = app.screen
        assert screen.tab == "download"
        await pilot.press("tab")  # out of the URL bar, like a user dropping onto the window
        screen.post_message(events.Paste(escaped([clip])))
        pane = screen.query_one(FilesPane)
        assert await settle(pilot, lambda: len(pane.files) == 1)
        assert screen.tab == "files"
        assert "convert" in str(screen.query_one("#keys").render())
        assert "browse" in str(screen.query_one("#keys").render())

        await pilot.press("escape", "tab")  # leave the file bar so c is a shortcut
        await pilot.press("c")
        assert await settle(pilot, lambda: isinstance(app.screen, ConvertScreen))
        dialog = app.screen
        tabs = [p.id for p in dialog.query("TabPane")]
        assert tabs == ["video", "audio", "frames"]
        assert "convert" in str(dialog.query_one("#go").label)
        quality = dialog.query_one("#video-quality")
        assert str(quality.get_option_at_index(0).prompt) == "Same as original"
        summary = str(dialog.query_one("#summary").render())
        assert "Fast copy" in summary  # HEVC copies into MP4
        await pilot.click("#go")
        assert await settle(pilot, lambda: pane.jobs and pane.jobs[0].state == runner.DONE)
        assert files_fakes["convert"][0] == ("my holiday.mov", pane.jobs[0].choice)
        assert Path(pane.last_file).name == "my holiday.mp4"
        assert screen.jobs == []  # nothing went to the download queue


async def test_mixed_batch_converts_only_matching_files(files_fakes, tmp_path):
    paths = make(tmp_path, "clip.mov", "photo.png", "song.mp3")
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 44)) as pilot:
        screen = app.screen
        screen.add_files(paths)
        pane = screen.query_one(FilesPane)
        assert await settle(pilot, lambda: len(pane.files) == 3)
        assert pane.query_one("#files-card").border_title == "3 files"
        await pilot.pause()
        button = pane.query_one("#convert-files")
        assert str(button.label) == "[c] convert all"
        assert button.size.width >= len("[c] convert all")  # re-measured after the change
        pane.convert()
        assert await settle(pilot, lambda: isinstance(app.screen, ConvertScreen))
        dialog = app.screen
        assert [p.id for p in dialog.query("TabPane")] == ["video", "audio", "image", "frames"]
        dialog.query_one("#kind").active = "image"
        await pilot.pause()
        options = {o.id: o for o in dialog.query_one("#image-format").options}
        assert options["exr"].disabled  # this ffmpeg has no EXR encoder: shown, greyed out
        assert "needs exr" in str(options["exr"].prompt)
        assert not options["webp"].disabled  # no libwebp, but Pillow writes WebP
        assert not options["jxl"].disabled and not options["heic"].disabled
        assert not options["jpg"].disabled
        formats = list(options)
        dialog.query_one("#image-format").highlighted = formats.index("jpg")
        await pilot.pause()
        assert "Applies to 1 of 3 files" in str(dialog.query_one("#summary").render())
        await pilot.click("#go")
        assert await settle(pilot, lambda: pane.jobs and pane.jobs[-1].state == runner.DONE)
        assert [name for name, _ in files_fakes["convert"]] == ["photo.png"]
        assert files_fakes["convert"][0][1].fmt == "jpg"

        # Audio applies to the clip and the song.
        pane.convert()
        assert await settle(pilot, lambda: isinstance(app.screen, ConvertScreen))
        assert app.screen.query_one("#kind").active == "image"  # remembers the last tab
        app.screen.query_one("#kind").active = "audio"
        await pilot.pause()
        await pilot.click("#go")
        assert await settle(
            pilot, lambda: len(pane.jobs) == 3 and all(j.state == runner.DONE for j in pane.jobs)
        )
        assert sorted(n for n, _ in files_fakes["convert"][1:]) == ["clip.mov", "song.mp3"]


async def test_unreadable_file_is_skipped(files_fakes, tmp_path):
    good, bad = make(tmp_path, "a.mov", "notes.txt")
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 44)) as pilot:
        screen = app.screen
        screen.add_files([good, bad])
        pane = screen.query_one(FilesPane)
        assert await settle(pilot, lambda: pane.reading == 0)
        assert [f.path for f in pane.files] == [good]


async def test_remove_and_switch_tabs(files_fakes, tmp_path):
    paths = make(tmp_path, "a.mov", "b.png")
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 44)) as pilot:
        screen = app.screen
        screen.add_files(paths)
        pane = screen.query_one(FilesPane)
        assert await settle(pilot, lambda: len(pane.files) == 2)
        pane.query_one("#files").focus()
        await pilot.press("r")
        assert [f.path.name for f in pane.files] == ["b.png"]
        await pilot.press("ctrl+t")
        assert screen.tab == "download"
        assert app.focused.id == "url"
        await pilot.press("ctrl+t")  # works while typing in the URL bar too
        assert screen.tab == "files"
        assert app.focused.id == "file-input"


async def test_download_and_conversion_run_together(files_fakes, tmp_path):
    gate = files_fakes["gate"]
    gate["convert"], gate["download"] = threading.Event(), threading.Event()
    (clip,) = make(tmp_path, "a.mov")
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 44)) as pilot:
        screen = app.screen
        screen.load(URL)
        assert await settle(pilot, lambda: screen.info is not None)
        screen.enqueue(screen.info, main_screen.DOWNLOAD)
        screen.add_files([clip])
        pane = screen.query_one(FilesPane)
        assert await settle(pilot, lambda: pane.files)
        pane.enqueue(pane.files[0], local.Choice("video", "mkv"))
        assert await settle(
            pilot,
            lambda: (
                screen.jobs[0].state == runner.DOWNLOADING
                and pane.jobs[0].state == runner.CONVERTING
            ),
        )
        assert "1 converting" in pane.query_one("#conversions").border_title

        # x on the files tab cancels the conversion, not the download.
        await pilot.press("escape", "tab")
        await pilot.press("x")
        assert await settle(pilot, lambda: pane.jobs[0].state == runner.CANCELLED)
        assert screen.jobs[0].state == runner.DOWNLOADING
        gate["download"].set()
        assert await settle(pilot, lambda: screen.jobs[0].state == runner.DONE)


async def test_file_argument_opens_files_tab(files_fakes, tmp_path):
    (clip,) = make(tmp_path, "a b.mov")
    app = CarrotLeafApp(initial_url=str(clip))
    async with app.run_test(size=(110, 44)) as pilot:
        screen = app.screen
        pane = screen.query_one(FilesPane)
        assert await settle(pilot, lambda: len(pane.files) == 1)
        assert screen.tab == "files"
        assert screen.query_one("#url").value == ""


async def test_typed_path_in_url_bar_moves_to_files(files_fakes, tmp_path):
    (clip,) = make(tmp_path, "a.mov")
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 44)) as pilot:
        screen = app.screen
        screen.query_one("#url").value = str(clip)
        pane = screen.query_one(FilesPane)
        assert await settle(pilot, lambda: len(pane.files) == 1)
        assert screen.tab == "files"
        assert files_fakes["download"] == []


async def test_browse_picks_a_file(files_fakes, tmp_path, monkeypatch):
    make(tmp_path, "a.mov")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 44)) as pilot:
        screen = app.screen
        await pilot.press("tab")  # out of the URL bar
        await pilot.press("f")  # opens Convert files and the picker
        assert await settle(pilot, lambda: isinstance(app.screen, FilePickerScreen))
        assert screen.tab == "files"
        tree = app.screen.query_one("#tree")
        assert await settle(pilot, lambda: len(tree.root.children) == 1)
        await pilot.press("down", "enter")
        pane = screen.query_one(FilesPane)
        assert await settle(pilot, lambda: len(pane.files) == 1)
        assert not isinstance(app.screen, FilePickerScreen)


def test_describe():
    assert describe(fake_file(Path("x.mov"))) == "video · 1920×1080 · HEVC · 2:14 · 392.9 MB"
    assert describe(fake_file(Path("x.png"))) == "image · 4032×3024 · 1.9 MB"
    assert describe(fake_file(Path("x.mp3"))) == "audio · MP3 · 0:48 · 2.9 MB"


def test_each_format_uses_the_ffmpeg_that_can_write_it(monkeypatch):
    system, bundled = deps.Tool("/sys", False), deps.Tool("/bundled", True)
    monkeypatch.setattr(deps, "ffmpeg_candidates", lambda: [system, bundled])
    has = {"/sys": {"libx264", "aac", "png"}, "/bundled": {"libx264", "aac", "libtheora", "vorbis"}}
    monkeypatch.setattr(deps, "ffmpeg_has_encoder", lambda d, e: e in has[d])
    pane = FilesPane()
    assert pane.ffmpeg_for("video", "mp4") == "/sys"  # the system's wins when it can
    assert pane.ffmpeg_for("video", "ogv") == "/bundled"  # only the bundled build has Theora
    assert pane.has_encoder("libtheora")
    assert not pane.has_encoder("libjxl")


def test_setup_command_downloads_and_reports(monkeypatch, capsys):
    from carrot_leaf import cli

    fetched = []
    monkeypatch.setattr(deps, "bundled_ffmpeg_ready", lambda: bool(fetched))
    monkeypatch.setattr(
        deps,
        "fetch_bundled_ffmpeg",
        lambda show_progress=False: fetched.append(show_progress) or deps.Tool("/b", True),
    )
    monkeypatch.setattr(deps, "system_ffmpeg", lambda: None)
    monkeypatch.setattr(deps, "ffmpeg_candidates", lambda: [deps.Tool("/b", True)])
    monkeypatch.setattr(deps, "ffmpeg_has_encoder", lambda d, e: e != "libtheora")
    assert cli.main(["setup"]) == 0
    assert fetched == [True]  # progress shown in the terminal
    out = capsys.readouterr().out
    assert "Getting ffmpeg" in out and "✓ ffmpeg is in /b" in out
    assert "not available: OGV" in out


def test_setup_command_survives_being_offline(monkeypatch, capsys):
    from carrot_leaf import cli

    def offline(show_progress=False):
        raise OSError("no network")

    monkeypatch.setattr(deps, "bundled_ffmpeg_ready", lambda: False)
    monkeypatch.setattr(deps, "fetch_bundled_ffmpeg", offline)
    assert cli.main(["setup"]) == 1
    assert "try again the first time" in capsys.readouterr().out
