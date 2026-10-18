"""Drive the app with Textual's Pilot. Network calls are replaced with fakes."""

import asyncio
from pathlib import Path

import pytest
from textual import events

from carrot_leaf import config
from carrot_leaf.app import CarrotLeafApp
from carrot_leaf.engine import runner
from carrot_leaf.engine.probe import VideoInfo
from carrot_leaf.screens import main as main_screen
from carrot_leaf.screens.convert import ConvertScreen
from carrot_leaf.screens.settings import SettingsScreen

URL = "https://www.youtube.com/watch?v=abc123"


def fake_info(url=URL):
    return VideoInfo(
        url=url,
        id="abc123",
        title="A test video",
        channel="Tester",
        duration=95,
        upload_date="20240102",
        thumbnail=None,
        extractor="Youtube",
        max_height=1080,
    )


@pytest.fixture
def fakes(monkeypatch, isolated_config):
    calls = {"probe": [], "run": []}

    def fake_probe(url, cookies_browser=""):
        calls["probe"].append((url, cookies_browser))
        if "login" in url:
            raise Exception("ERROR: [youtube] x: Sign in to confirm your age")
        return fake_info(url)

    def fake_run_job(job, download_dir, on_progress, cookies_browser=""):
        calls["run"].append(job.choice)
        on_progress(runner.Progress(runner.DOWNLOADING, "Downloading", 0.5, 1_000_000, 3))
        path = Path(download_dir) / f"{job.title}.{job.choice.fmt}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        return str(path)

    monkeypatch.setattr(main_screen, "probe", fake_probe)
    monkeypatch.setattr(main_screen.runner, "run_job", fake_run_job)
    return calls


async def settle(pilot, pred, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        await pilot.pause()
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_paste_then_download(fakes):
    app = CarrotLeafApp()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = app.screen
        screen.query_one("#url").post_message(events.Paste(URL))
        assert await settle(pilot, lambda: screen.info is not None)
        assert app.focused.id == "download"
        await pilot.press("d")
        assert await settle(pilot, lambda: screen.jobs and screen.jobs[0].state == runner.DONE)
        assert fakes["run"][0].fmt == "mp4"
        assert Path(screen.last_file).exists()


async def test_convert_remembers_choice(fakes):
    app = CarrotLeafApp()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = app.screen
        screen.load(URL)
        assert await settle(pilot, lambda: screen.info is not None)
        await pilot.press("c")
        assert await settle(pilot, lambda: isinstance(app.screen, ConvertScreen))
        dialog = app.screen
        dialog.query_one("#kind").active = "audio"
        await pilot.pause()
        dialog.query_one("#audio-format").highlighted = 3  # flac
        await pilot.pause()
        assert [o.id for o in dialog.query_one("#audio-quality").options] == ["best"]
        await pilot.click("#go")
        assert await settle(pilot, lambda: screen.jobs and screen.jobs[0].state == runner.DONE)
        assert fakes["run"][0].fmt == "flac"
    saved = config.load()
    assert (saved.last_kind, saved.last_audio_format) == ("audio", "flac")


async def test_queue_runs_one_at_a_time(fakes):
    app = CarrotLeafApp()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = app.screen
        screen.load(URL)
        assert await settle(pilot, lambda: screen.info is not None)
        await pilot.press("d", "d", "d")
        assert await settle(
            pilot,
            lambda: len(screen.jobs) == 3 and all(j.state == runner.DONE for j in screen.jobs),
        )
        assert len(fakes["run"]) == 3


async def test_login_error_offers_cookies(fakes):
    app = CarrotLeafApp()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = app.screen
        screen.load("https://www.youtube.com/watch?v=login")
        assert await settle(
            pilot, lambda: screen.query_one("#card-switcher").current == "card-error"
        )
        assert screen.query_one("#use-cookies").display
        await pilot.click("#use-cookies")
        await pilot.pause()
        await pilot.press("down", "enter")  # Firefox
        assert await settle(pilot, lambda: len(fakes["probe"]) == 2)
        assert fakes["probe"][-1][1] == "firefox"
    assert config.load().cookies_browser == "firefox"


async def test_settings_changes_folder(fakes, tmp_path):
    target = tmp_path / "elsewhere"
    app = CarrotLeafApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.press("escape")  # leave the URL bar so letter keys are shortcuts
        app.screen.query_one("#url").blur()
        await pilot.press("s")
        assert await settle(pilot, lambda: isinstance(app.screen, SettingsScreen))
        app.screen.query_one("#folder").value = str(target)
        await pilot.click("#save")
        await pilot.pause()
    assert config.load().download_dir == str(target)
    assert target.is_dir()


@pytest.mark.parametrize("width", [60, 80, 120])
async def test_logo_fits(fakes, width):
    app = CarrotLeafApp()
    async with app.run_test(size=(width, 40)) as pilot:
        await pilot.pause()
        logo = app.screen.query_one("#logo")
        assert logo.size.width <= width


def all_theme_names():
    from carrot_leaf.theme import THEMES

    return sorted(set(CarrotLeafApp().available_themes) | {t.name for t in THEMES})


@pytest.mark.parametrize("theme", all_theme_names())
async def test_every_theme_renders(fakes, theme, tmp_path):
    """Each theme in the command palette survives every screen and job state."""
    from PIL import Image

    from carrot_leaf.engine.options import DOWNLOAD
    from carrot_leaf.engine.runner import Job

    app = CarrotLeafApp()
    async with app.run_test(size=(120, 50)) as pilot:
        app.theme = theme
        screen = app.screen
        screen.load(URL)
        assert await settle(pilot, lambda: screen.info is not None)
        screen.query_one(main_screen.VideoCard).set_thumbnail(Image.new("RGB", (64, 36), "teal"))
        queue = screen.query_one(main_screen.DownloadQueue)
        for state in (runner.QUEUED, runner.DOWNLOADING, runner.DONE, runner.FAILED):
            job = Job(url=URL, title=state, choice=DOWNLOAD)
            job.state, job.fraction, job.status = state, 0.5, "Downloading"
            job.error = Exception("boom")
            queue.add(job)
        await pilot.pause()
        for screen_cls in (ConvertScreen, SettingsScreen):
            app.push_screen(
                screen_cls(screen.info, app.settings)
                if screen_cls is ConvertScreen
                else screen_cls(app.settings)
            )
            await pilot.pause()
            app.save_screenshot(path=str(tmp_path))  # forces a full render of the current screen
            app.pop_screen()
            await pilot.pause()
        screen.load("https://www.youtube.com/watch?v=login")
        assert await settle(
            pilot, lambda: screen.query_one("#card-switcher").current == "card-error"
        )
        await pilot.resize_terminal(60, 24)
        await pilot.pause()
        app.save_screenshot(path=str(tmp_path))
