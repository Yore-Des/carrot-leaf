"""Playlists, the parallel queue, history, clipboard links, notifications, the Convert dialog's
extras and `carrot get` / `carrot convert`. Network, ffmpeg and the desktop are faked."""

import threading
from pathlib import Path

import pytest
from test_ui import URL, fake_info, settle

from carrot_leaf import batch, history
from carrot_leaf.app import CarrotLeafApp
from carrot_leaf.engine import desktop, runner
from carrot_leaf.engine.probe import Entry, PlaylistInfo
from carrot_leaf.screens import main as main_screen
from carrot_leaf.screens.convert import ConvertScreen
from carrot_leaf.screens.history import HistoryScreen
from carrot_leaf.screens.playlist import PlaylistScreen

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL1"


def fake_playlist(n=5):
    entries = [Entry(f"https://youtu.be/v{i}", f"v{i}", f"Part {i + 1}", 60) for i in range(n)]
    return PlaylistInfo(PLAYLIST_URL, "PL1", "My/Mix", "Me", entries, None, "YoutubeTab")


@pytest.fixture
def fakes(monkeypatch, isolated_config):
    calls = {"run": [], "notify": [], "gate": None, "running": 0, "peak": 0}
    lock = threading.Lock()

    def fake_probe(url, cookies_browser=""):
        return fake_playlist() if "playlist" in url else fake_info(url)

    def fake_run_job(job, download_dir, on_progress, cookies_browser=""):
        with lock:
            calls["run"].append(job)
            calls["running"] += 1
            calls["peak"] = max(calls["peak"], calls["running"])
        on_progress(runner.Progress(runner.DOWNLOADING, "Downloading", 0.5))
        if calls["gate"] is not None:
            calls["gate"].wait(5)
        with lock:
            calls["running"] -= 1
        path = Path(download_dir) / f"{job.title}.{job.choice.fmt}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        return str(path)

    monkeypatch.setattr(main_screen, "probe", fake_probe)
    monkeypatch.setattr(main_screen.runner, "run_job", fake_run_job)
    monkeypatch.setattr(desktop, "notify", lambda t, m: calls["notify"].append((t, m)))
    return calls


async def test_playlist_downloads_every_video_into_its_folder(fakes):
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 46)) as pilot:
        screen = app.screen
        screen.load(PLAYLIST_URL)
        assert await settle(pilot, lambda: screen.playlist is not None)
        assert screen.query_one("#card").border_title == "playlist"
        assert "download all" in str(screen.query_one("#download").label)
        await pilot.press("d")
        assert await settle(
            pilot,
            lambda: len(screen.jobs) == 5 and all(j.state == runner.DONE for j in screen.jobs),
        )
        job = fakes["run"][0]
        assert job.template.startswith("{playlist}/")
        assert job.fields == {"playlist": "My_Mix", "index": "01"}


async def test_pick_videos_then_download_some(fakes):
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 46)) as pilot:
        screen = app.screen
        screen.load(PLAYLIST_URL)
        assert await settle(pilot, lambda: screen.playlist is not None)
        screen.query_one("#url").blur()
        await pilot.press("e")
        assert await settle(pilot, lambda: isinstance(app.screen, PlaylistScreen))
        app.screen.query_one("#latest").value = "2"
        await pilot.pause()
        assert app.screen.entries.selected == [0, 1]
        await pilot.press("n", "space", "down", "space")  # none, then tick the first two
        await pilot.press("enter")
        assert await settle(pilot, lambda: not isinstance(app.screen, PlaylistScreen))
        assert screen.picked == {0, 1}
        assert "download 2" in str(screen.query_one("#download").label)
        await pilot.press("d")
        assert await settle(pilot, lambda: len(fakes["run"]) == 2)
        assert [j.title for j in fakes["run"]] == ["Part 1", "Part 2"]


async def test_parallel_downloads_and_pause(fakes):
    fakes["gate"] = threading.Event()
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 46)) as pilot:
        from dataclasses import replace

        app.settings = replace(app.settings, parallel_downloads=2)
        screen = app.screen
        screen.load(URL)
        assert await settle(pilot, lambda: screen.info is not None)
        screen.query_one("#url").blur()
        await pilot.press("p")  # pause before anything starts
        assert screen.paused
        await pilot.press("d", "d", "d")
        await pilot.pause()
        assert all(j.state == runner.QUEUED for j in screen.jobs)
        await pilot.press("p")
        assert await settle(pilot, lambda: len(screen.active) == 2)
        assert screen.jobs[2].state == runner.QUEUED
        fakes["gate"].set()
        assert await settle(pilot, lambda: all(j.state == runner.DONE for j in screen.jobs))
        assert fakes["peak"] == 2


async def test_history_records_and_redownloads(fakes):
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 46)) as pilot:
        screen = app.screen
        screen.load(URL)
        assert await settle(pilot, lambda: screen.info is not None)
        await pilot.press("d")
        assert await settle(pilot, lambda: screen.jobs[0].state == runner.DONE)
        assert [e.source for e in history.load()] == [URL]
        screen.query_one("#url").value = ""
        screen.query_one("#url").blur()
        await pilot.press("h")
        assert await settle(pilot, lambda: isinstance(app.screen, HistoryScreen))
        await pilot.press("r")
        assert await settle(pilot, lambda: not isinstance(app.screen, HistoryScreen))
        assert screen.query_one("#url").value == URL


async def test_clipboard_link_is_offered_once(fakes, monkeypatch):
    monkeypatch.setattr(desktop, "read_clipboard", lambda: "https://vimeo.com/76979871")
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 46)) as pilot:
        screen = app.screen
        assert await settle(pilot, lambda: screen.clipboard_url is not None)
        assert "load copied link" in str(screen.query_one("#keys").render())
        screen.query_one("#url").blur()
        await pilot.press("v")
        assert await settle(pilot, lambda: screen.info is not None)
        assert screen.info.url == "https://vimeo.com/76979871"
        screen.check_clipboard()  # same link again: not offered twice
        await pilot.pause()
        assert screen.clipboard_url is None


async def test_notifies_when_queue_finishes_in_background(fakes):
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 46)) as pilot:
        screen = app.screen
        screen.app_focused = False
        screen.load(URL)
        assert await settle(pilot, lambda: screen.info is not None)
        await pilot.press("d")
        assert await settle(pilot, lambda: fakes["notify"])
        assert fakes["notify"] == [("Downloaded", "A test video")]


async def test_no_notification_while_watching(fakes):
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 46)) as pilot:
        screen = app.screen
        screen.app_focused = True
        screen.load(URL)
        assert await settle(pilot, lambda: screen.info is not None)
        await pilot.press("d")
        assert await settle(pilot, lambda: screen.jobs[0].state == runner.DONE)
        await pilot.pause()
        assert fakes["notify"] == []


async def test_convert_dialog_extras(fakes):
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 50)) as pilot:
        screen = app.screen
        screen.load(URL)
        assert await settle(pilot, lambda: screen.info is not None)
        await pilot.press("c")
        assert await settle(pilot, lambda: isinstance(app.screen, ConvertScreen))
        dialog = app.screen
        dialog.query_one("#kind").active = "audio"
        await pilot.pause()
        assert dialog.query_one("#volume-row").display
        dialog.query_one("#clip-start").value = "0:10"
        dialog.query_one("#clip-end").value = "0:05"
        await pilot.pause()
        assert "after the start" in str(dialog.query_one("#summary").render())
        await pilot.click("#go")
        assert isinstance(app.screen, ConvertScreen)  # refused
        dialog.query_one("#clip-end").value = "1:00"
        dialog.query_one("#normalize").value = True
        dialog.query_one("#subs").value = "srt"
        await pilot.pause(0.4)  # a button ignores clicks while it's still lit from the last one
        await pilot.click("#go")
        assert await settle(pilot, lambda: screen.jobs and screen.jobs[0].state == runner.DONE)
        e = fakes["run"][0].choice.extras
        assert (e.start, e.end, e.normalize, e.subtitles) == (10, 60, True, "srt")
    from carrot_leaf import config

    saved = config.load()
    assert saved.last_normalize and saved.last_subtitles == "srt"


def test_cli_get_downloads_a_playlist(monkeypatch, tmp_path, capsys):
    from carrot_leaf.engine import probe as probe_mod

    got = []

    def fake_run_job(job, download_dir, on_progress, cookies_browser=""):
        got.append(job)
        on_progress(runner.Progress(runner.DOWNLOADING, "Downloading", 0.5))
        path = Path(download_dir) / f"{job.title}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        return str(path)

    monkeypatch.setattr(probe_mod, "probe", lambda url, cookies_browser="": fake_playlist())
    monkeypatch.setattr(runner, "run_job", fake_run_job)
    code = batch.main(
        ["get", PLAYLIST_URL, "-f", "mp3", "--latest", "2", "-o", str(tmp_path), "--normalize"]
    )
    assert code == 0
    out = capsys.readouterr().out.split("\n")
    assert out[:2] == [str(tmp_path / "Part 1.mp3"), str(tmp_path / "Part 2.mp3")]
    assert got[0].choice.kind == "audio" and got[0].choice.extras.normalize
    assert got[1].fields["index"] == "02"


def test_cli_rejects_a_bad_clip(capsys):
    assert batch.main(["get", URL, "--start", "1:00", "--end", "0:30"]) == 2
    assert "after --start" in capsys.readouterr().err


def test_cli_convert(monkeypatch, tmp_path, capsys):
    from carrot_leaf.engine import deps, local
    from carrot_leaf.engine.local import IMAGE, FileInfo, Stream

    src = tmp_path / "a.png"
    src.write_text("x")
    seen = []

    def fake_convert(info, choice, on_progress, cancel_event, ffmpeg_dir=None):
        seen.append(choice)
        out = tmp_path / "a.jpg"
        out.write_text("y")
        return str(out)

    monkeypatch.setattr(deps, "ffmpeg_candidates", lambda: [deps.Tool("/ff", False)])
    monkeypatch.setattr(deps, "ffmpeg_has_encoder", lambda d, e: True)
    monkeypatch.setattr(
        local,
        "probe_file",
        lambda p, d=None: FileInfo(p, IMAGE, "png_pipe", 1, None, Stream("png", 100, 50), None),
    )
    monkeypatch.setattr(local, "run_convert", fake_convert)
    assert batch.main(["convert", str(src), "-f", "jpg", "--max-kb", "50", "--strip-metadata"]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "a.jpg")
    assert seen[0].kind == "image" and seen[0].extras.max_kb == 50
    assert seen[0].extras.strip_metadata
    assert batch.main(["convert", str(src), "-f", "mp3"]) == 1  # a picture has no sound


async def test_options_fold_shows_what_is_on(fakes):
    from textual.widgets import Collapsible

    app = CarrotLeafApp()
    async with app.run_test(size=(110, 50)) as pilot:
        screen = app.screen
        screen.load(URL)
        assert await settle(pilot, lambda: screen.info is not None)
        await pilot.press("c")
        assert await settle(pilot, lambda: isinstance(app.screen, ConvertScreen))
        dialog = app.screen
        fold = dialog.query_one("#extras", Collapsible)
        assert fold.collapsed
        assert "subtitles" in str(fold.title)  # nothing on yet: lists what's available
        dialog.query_one("#subs").value = "embed"  # works while folded
        await pilot.pause()
        assert "subs" in str(fold.title)
        await pilot.press("o")
        assert not fold.collapsed
        assert dialog.choice().extras.subtitles == "embed"
        assert dialog.query_one("#sub-langs").disabled is False
        dialog.query_one("#subs").value = ""
        await pilot.pause()
        assert dialog.query_one("#sub-langs").disabled
