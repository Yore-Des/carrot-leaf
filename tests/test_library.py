"""Presets, following, transcripts and the clip picker: stores, sync logic, parsing and the
screens, with the network and ffmpeg faked."""

import json
from pathlib import Path

from test_ui import URL, fake_info, settle

from carrot_leaf import batch, following, presets, transcripts
from carrot_leaf.app import CarrotLeafApp
from carrot_leaf.engine import desktop, frames, runner
from carrot_leaf.engine.frames import FrameSource
from carrot_leaf.engine.local import VIDEO, FileInfo, Stream, output_name
from carrot_leaf.engine.options import Choice, Extras
from carrot_leaf.engine.probe import Entry, PlaylistInfo
from carrot_leaf.screens import main as main_screen
from carrot_leaf.screens.clip import ClipScreen
from carrot_leaf.screens.convert import ConvertScreen
from carrot_leaf.screens.following import FollowingScreen
from carrot_leaf.screens.search import SearchScreen


def feed(name="Blender", ids=("a", "b", "c")):
    entries = [Entry(f"https://youtu.be/{i}", i, f"Video {i}", 60) for i in ids]
    return PlaylistInfo(
        f"https://www.youtube.com/@{name}/videos", name, name, name, entries, None, "YoutubeTab"
    )


# ---------- presets ----------


def test_preset_roundtrip_drops_the_clip(tmp_path):
    path = tmp_path / "p.json"
    choice = Choice("audio", "mp3", "128", Extras(start=5, end=9, normalize=True))
    presets.upsert(presets.from_choice("Podcast", choice, "youtube"), path)
    (p,) = presets.load(path)
    assert p.extras == {"normalize": True}
    assert p.choice() == Choice("audio", "mp3", "128", Extras(normalize=True))
    assert p.matches(choice)  # the clip doesn't count


def test_one_default_per_site_and_same_name_replaces(tmp_path):
    path = tmp_path / "p.json"
    presets.upsert(presets.from_choice("A", Choice("video", "mp4"), "youtube"), path)
    presets.upsert(presets.from_choice("B", Choice("audio", "mp3"), "youtube"), path)
    presets.upsert(presets.from_choice("b", Choice("audio", "flac"), "youtube"), path)
    loaded = presets.load(path)
    assert [(p.name, p.site, p.fmt) for p in loaded] == [("A", "", "mp4"), ("b", "youtube", "flac")]
    assert presets.default_for("YoutubeTab", loaded).name == "b"


def test_site_key():
    assert presets.site_key("YoutubeTab") == presets.site_key("Youtube") == "youtube"
    assert presets.site_key("SoundcloudPlaylist") == "soundcloud"
    assert presets.site_key("") == ""


# ---------- following ----------


def test_follow_then_sync_finds_only_new(tmp_path):
    path = tmp_path / "f.json"
    item = following.follow(feed(), Choice("audio", "mp3"), path)
    assert item.seen == ["a", "b", "c"] and not item.new
    assert following.apply_sync(item, feed(ids=("e", "d", "a", "b"))) == 2
    assert [e.id for e in item.new_entries] == ["d", "e"]  # oldest first
    assert following.apply_sync(item, feed(ids=("e", "d", "a"))) == 0  # no double counting
    following.update(item, path)
    assert following.total_new(following.load(path)) == 2
    got = following.mark_fetched(item)
    assert [e.id for e in got] == ["d", "e"] and not item.new and "e" in item.seen


def test_sync_error_is_kept(tmp_path):
    item = following.follow(feed(), Choice("video", "mp4"), tmp_path / "f.json")
    following.apply_sync(item, None, "Couldn't reach the site.")
    assert item.error and item.checked


# ---------- transcripts ----------


def test_parse_json3():
    data = json.dumps(
        {
            "events": [
                {"tStartMs": 1000, "segs": [{"utf8": "hello"}, {"utf8": " world"}]},
                {"tStartMs": 2000, "segs": [{"utf8": "\n"}]},
                {"tStartMs": 3000, "segs": [{"utf8": "again"}]},
            ]
        }
    )
    cues = transcripts.parse_json3(data)
    assert [(c.start, c.text) for c in cues] == [(1.0, "hello world again")]


def test_parse_vtt_drops_rolling_repeats():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:02.000
<c>one</c> two

00:00:02.000 --> 00:00:03.000
one two
three four
"""
    cues = transcripts.parse_vtt(vtt)
    assert cues[0].start == 1.0 and cues[0].text == "one two three four"


def test_pick_track_prefers_creator_then_original_language():
    info = {
        "language": "de",
        "subtitles": {"en": [{"ext": "vtt", "url": "m-en"}]},
        "automatic_captions": {
            "de-orig": [{"ext": "json3", "url": "a-de"}],
            "en": [{"ext": "json3", "url": "a-en"}],
        },
    }
    assert transcripts.pick_track(info) == ("de-orig", "json3", "a-de")
    info["subtitles"]["de"] = [{"ext": "srt", "url": "m-de"}]
    assert transcripts.pick_track(info) == ("de", "srt", "m-de")
    assert transcripts.pick_track({}) is None


def test_search_words_any_order_and_phrases():
    t = transcripts.Transcript(
        "v",
        "Talk",
        "/x.mp4",
        "u",
        "en",
        [transcripts.Cue(1, "descent by gradient"), transcripts.Cue(9, "gradient descent works")],
    )
    assert [h.cue.start for h in transcripts.search("gradient descent", [t])] == [1, 9]
    assert [h.cue.start for h in transcripts.search('"gradient descent"', [t])] == [9]
    assert transcripts.search("   ", [t]) == []


def test_save_and_load(isolated_config):
    transcripts.save(
        transcripts.Transcript("a/b?", "T", "/f", "u", "en", [transcripts.Cue(1, "x")])
    )
    (t,) = transcripts.load_all()
    assert t.title == "T" and t.cues[0].text == "x"


# ---------- frames and clip names ----------


def test_storyboard_cells():
    from PIL import Image

    fmt = {
        "rows": 2,
        "columns": 2,
        "fps": 0.5,
        "width": 10,
        "height": 5,
        "fragments": [{"url": "s0"}, {"url": "s1"}],
        "format_note": "storyboard",
    }
    sb = frames.StoryboardFrames(fmt, 16.0)
    sheet = Image.new("RGB", (20, 10))
    for i, color in enumerate(["red", "green", "blue", "white"]):
        sheet.paste(color, ((i % 2) * 10, (i // 2) * 5, (i % 2) * 10 + 10, (i // 2) * 5 + 5))
    sb._cache = {0: sheet, 1: sheet}
    assert sb.step == 2.0
    assert sb.frame(0).getpixel((1, 1)) == (255, 0, 0)
    assert sb.frame(6).getpixel((1, 1)) == (255, 255, 255)  # 4th thumbnail of sheet 0
    assert sb.frame(9).getpixel((1, 1)) == (255, 0, 0)  # first of sheet 1
    assert frames.best_storyboard([fmt, {**fmt, "width": 40}])["width"] == 40


def test_clip_output_name():
    info = FileInfo(Path("/v/talk.mp4"), VIDEO, "mov", 1, 600.0, Stream("h264", 64, 36), None)
    assert output_name(info, Choice("video", "mp4", extras=Extras(start=90, end=120))) == (
        "talk 1m30s-2m00s.mp4"
    )


# ---------- screens ----------


class FakeFrames(FrameSource):
    duration, step = 100.0, 2.0

    def frame(self, t):
        from PIL import Image

        return Image.new("RGB", (16, 9), "teal")


async def test_clip_picker_marks_and_returns(isolated_config):
    app = CarrotLeafApp()
    results = []
    async with app.run_test(size=(110, 46)) as pilot:
        app.push_screen(ClipScreen(FakeFrames(), "Talk", None, None), results.append)
        await pilot.pause()
        await pilot.press("right", "right", "right", "right", "right")  # 10s
        await pilot.press("left_square_bracket")
        await pilot.press("shift+right", "shift+right")  # 30s
        await pilot.press("right_square_bracket", "enter")
        assert await settle(pilot, lambda: results)
    assert results == [(10.0, 30.0)]


async def test_presets_fill_the_dialog_and_d_uses_site_default(monkeypatch, isolated_config):
    calls = []

    def fake_run_job(job, download_dir, on_progress, cookies_browser=""):
        calls.append(job)
        out = Path(download_dir) / "x.mp3"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("x")
        return str(out)

    monkeypatch.setattr(
        main_screen, "probe", lambda url, cookies_browser="", limit=0: fake_info(url)
    )
    monkeypatch.setattr(main_screen.runner, "run_job", fake_run_job)
    presets.upsert(presets.from_choice("Clip", Choice("video", "mkv", "720")))
    presets.upsert(
        presets.from_choice(
            "Podcast", Choice("audio", "mp3", "128", Extras(normalize=True)), "youtube"
        )
    )
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 50)) as pilot:
        screen = app.screen
        screen.load(URL)
        assert await settle(pilot, lambda: screen.info is not None)
        assert "Podcast preset" in str(screen.query_one("#action-hint").render())
        await pilot.press("c")
        assert await settle(pilot, lambda: isinstance(app.screen, ConvertScreen))
        dialog = app.screen
        dialog.query_one("#audio-format").focus()
        await pilot.press("2")
        await pilot.pause()
        c = dialog.choice()
        assert (c.kind, c.fmt, c.quality, c.extras.normalize) == ("audio", "mp3", "128", True)
        assert dialog.query_one("#preset-2").has_class("-active")
        await pilot.press("1")
        await pilot.pause()
        assert (dialog.choice().fmt, dialog.choice().quality) == ("mkv", "720")
        await pilot.press("escape")
        await pilot.pause()
        screen.query_one("#url").blur()
        await pilot.press("d")
        assert await settle(pilot, lambda: calls)
        assert calls[0].choice.fmt == "mp3" and calls[0].choice.extras.normalize


async def test_follow_sync_and_get_new(monkeypatch, isolated_config):
    current = {"ids": ("a", "b", "c")}
    calls = []

    def fake_run_job(job, download_dir, on_progress, cookies_browser=""):
        calls.append(job)
        out = Path(download_dir) / f"{job.title}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("x")
        return str(out)

    info = fake_info(URL)
    info.raw = {"channel_url": "https://www.youtube.com/@Blender"}
    monkeypatch.setattr(
        main_screen,
        "probe",
        lambda url, cookies_browser="", limit=0: info if url == URL else feed(ids=current["ids"]),
    )
    monkeypatch.setattr(main_screen.runner, "run_job", fake_run_job)
    app = CarrotLeafApp()
    async with app.run_test(size=(110, 50)) as pilot:
        screen = app.screen
        screen.load(URL)
        assert await settle(pilot, lambda: screen.info is not None)
        assert "follow channel" in str(screen.query_one("#follow").label)
        screen.query_one("#url").blur()
        await pilot.press("a")
        assert await settle(pilot, lambda: isinstance(app.screen, ConvertScreen))
        await pilot.click("#go")
        assert await settle(pilot, lambda: following.load())
        assert following.load()[0].seen == ["a", "b", "c"]
        current["ids"] = ("d", "a", "b", "c")
        screen.sync_following()
        assert await settle(pilot, lambda: screen.new_videos == 1)
        assert "1 new · press w" in str(screen.query_one("#status-line").render())
        await pilot.press("w")
        assert await settle(pilot, lambda: isinstance(app.screen, FollowingScreen))
        await pilot.press("enter")
        assert await settle(pilot, lambda: calls)
        assert calls[0].url == "https://youtu.be/d" and calls[0].fields["playlist"] == "Blender"
        assert screen.new_videos == 0 and following.load()[0].new == []


async def test_search_screen_plays_and_clips(monkeypatch, isolated_config, tmp_path):
    video = tmp_path / "talk.mp4"
    video.write_text("x")
    transcripts.save(
        transcripts.Transcript(
            "v",
            "A talk",
            str(video),
            "u",
            "en",
            [transcripts.Cue(100, "intro"), transcripts.Cue(200, "gradient descent here")],
        )
    )
    played = []
    monkeypatch.setattr(desktop, "play_at", lambda p, t: played.append((p, t)) or True)
    clipped = []
    app = CarrotLeafApp()
    async with app.run_test(size=(120, 50)) as pilot:
        screen = app.screen
        monkeypatch.setattr(screen.files, "clip_file", lambda p, a, b: clipped.append((p, a, b)))
        screen.query_one("#url").blur()
        await pilot.press("slash")
        assert await settle(pilot, lambda: isinstance(app.screen, SearchScreen))
        search = app.screen
        search.query_one("#search-input").value = "descent"
        assert await settle(pilot, lambda: len(search.hits) == 1)
        assert "gradient descent here" in str(search.query_one("#search-context").render())
        search.query_one("#search-hits").focus()
        await pilot.press("enter")
        assert played == [(str(video), 200)]
        await pilot.press("c")
        assert await settle(pilot, lambda: clipped)
    assert clipped == [(video, 185.0, 215.0)]


def test_runner_saves_a_transcript(monkeypatch, isolated_config, tmp_path):
    """run_job keeps the captions it fetched with the final path of the download."""

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            work = Path(self.opts["outtmpl"]["default"]).parent
            out = work / "Talk [v].mp4"
            out.write_text("x")
            return {"id": "v", "title": "Talk", "webpage_url": url, "filepath": str(out)}

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr(transcripts, "fetch", lambda ydl, info: ("en", [transcripts.Cue(1, "hi")]))
    monkeypatch.setattr(runner.deps, "find_ffmpeg", lambda fetch=False: None)
    job = runner.Job(url="https://y/v", title="Talk", choice=Choice("video", "mp4"))
    job.keep_transcript = True
    path = runner.run_job(job, tmp_path / "out", lambda p: None)
    (t,) = transcripts.load_all()
    assert t.path == path and t.cues[0].text == "hi"


def test_cli_sync(monkeypatch, isolated_config, tmp_path, capsys):
    from carrot_leaf.engine import probe as probe_mod

    following.follow(feed(), Choice("audio", "mp3"))
    monkeypatch.setattr(probe_mod, "probe", lambda url, c="", limit=0: feed(ids=("z", "a")))
    got = []

    def fake_run_job(job, download_dir, on_progress, cookies_browser=""):
        got.append(job)
        return str(tmp_path / "z.mp3")

    monkeypatch.setattr(runner, "run_job", fake_run_job)
    assert batch.main(["sync", "--quiet"]) == 0
    assert [j.url for j in got] == ["https://youtu.be/z"]
    assert capsys.readouterr().out.strip() == str(tmp_path / "z.mp3")
    item = following.load()[0]
    assert item.new == [] and "z" in item.seen
