"""Clips, subtitles, SponsorBlock, chapters, volume, filename templates, playlists and history:
the pure parts, without the network or a real ffmpeg."""

import math
import os
from pathlib import Path

import pytest

from carrot_leaf import config, history
from carrot_leaf.engine import probe, runner
from carrot_leaf.engine.local import (
    AUDIO,
    IMAGE,
    VIDEO,
    FileInfo,
    Stream,
    build_ffmpeg_args,
    clip_duration,
    is_fast_copy,
)
from carrot_leaf.engine.options import (
    Choice,
    Extras,
    build_options,
    check_clip,
    parse_time,
    safe_name,
    template_error,
    template_preview,
    time_text,
    to_outtmpl,
)


def keys(opts):
    return [pp["key"] for pp in opts["postprocessors"]]


# ---------- times ----------


@pytest.mark.parametrize(
    "text,seconds",
    [("", None), ("90", 90), ("1:30", 90), ("1:02:03", 3723), ("1m30s", 90), ("2h", 7200)],
)
def test_parse_time(text, seconds):
    assert parse_time(text) == seconds


def test_parse_time_with_fraction():
    assert parse_time("1:30.5") == 90.5


@pytest.mark.parametrize("text", ["soon", "1:2:3:4", "-5", "1:xx"])
def test_parse_time_rejects(text):
    with pytest.raises(ValueError):
        parse_time(text)


def test_time_text():
    assert time_text(90) == "1:30"
    assert time_text(3723) == "1:02:03"
    assert time_text(90.5) == "1:30.5"
    assert time_text(None) == time_text(math.inf) == "end"


def test_check_clip():
    assert check_clip(10, 5, None)
    assert check_clip(300, None, 200)
    assert check_clip(None, 30, 200) == ""


def test_extras_label():
    e = Extras(start=30, end=75, subtitles="embed", sponsorblock="remove", normalize=True)
    assert Choice("audio", "mp3", "best", e).label == (
        "MP3 · best · 0:30–1:15 · subs · no sponsors · even volume"
    )
    assert Choice("video", "mp4").label == "MP4 · best"


# ---------- yt-dlp options ----------


def test_video_clip_uses_download_ranges():
    opts = build_options(Choice("video", "mp4", extras=Extras(start=5, end=10)), "/x")
    ranges = list(opts["download_ranges"]({}, None))
    assert ranges[0]["start_time"] == 5 and ranges[0]["end_time"] == 10


def test_audio_clip_is_cut_after_download():
    opts = build_options(Choice("audio", "mp3", extras=Extras(start=5)), "/x")
    assert "download_ranges" not in opts


def test_subtitles_embed_and_fallback_to_srt():
    opts = build_options(Choice("video", "mp4", extras=Extras(subtitles="embed")), "/x")
    assert opts["writesubtitles"] and opts["writeautomaticsub"]
    assert opts["subtitleslangs"] == ["en"]
    assert keys(opts).index("FFmpegEmbedSubtitle") < keys(opts).index("FFmpegMetadata")
    # MOV can't hold them, so they come as a file
    opts = build_options(Choice("video", "mov", extras=Extras(subtitles="embed")), "/x")
    assert "FFmpegEmbedSubtitle" not in keys(opts)
    assert "FFmpegSubtitlesConvertor" in keys(opts)


def test_subtitle_languages():
    e = Extras(subtitles="srt", sub_langs="en, de")
    assert build_options(Choice("audio", "mp3", extras=e), "/x")["subtitleslangs"] == ["en", "de"]


def test_sponsorblock_remove_and_mark():
    opts = build_options(Choice("video", "mp4", extras=Extras(sponsorblock="remove")), "/x")
    k = keys(opts)
    assert k.index("SponsorBlock") < k.index("ModifyChapters") < k.index("FFmpegMetadata")
    modify = opts["postprocessors"][k.index("ModifyChapters")]
    assert "sponsor" in modify["remove_sponsor_segments"]
    opts = build_options(Choice("video", "mp4", extras=Extras(sponsorblock="mark")), "/x")
    modify = next(p for p in opts["postprocessors"] if p["key"] == "ModifyChapters")
    assert modify["remove_sponsor_segments"] == []


def test_split_chapters_go_in_a_folder():
    opts = build_options(Choice("audio", "m4a", extras=Extras(split_chapters=True)), "/x")
    assert keys(opts)[-1] == "FFmpegSplitChapters"
    folder = os.path.join("/x", "%(title).180B [%(id)s]")
    assert opts["outtmpl"]["chapter"].startswith(folder + os.sep)


# ---------- filename templates ----------


def test_default_template_is_unchanged():
    opts = build_options(Choice("video", "mp4"), "/x")
    assert opts["outtmpl"]["default"] == os.path.join("/x", "%(title).180B [%(id)s].%(ext)s")


def test_template_with_folders_and_literal_fields():
    out = to_outtmpl("{playlist}/{index} - {title}", {"playlist": "My 100% list", "index": "03"})
    assert out == "My 100%% list/03 - %(title).180B.%(ext)s"


def test_template_errors_and_preview():
    assert template_error("{title} [{id}]") == ""
    assert "Unknown field" in template_error("{nope}")
    assert template_error("")
    assert template_error("../{title}")
    assert template_preview("{channel}/{title}") == "Blender/Big Buck Bunny.mp4"


def test_safe_name():
    assert safe_name('AC/DC: "Live"?') == "AC_DC_ _Live__"
    assert safe_name("...") == "Playlist"


# ---------- local files ----------

CLIP = FileInfo(Path("a.mov"), VIDEO, "mov", 1, 100.0, Stream("h264", 1920, 1080), Stream("aac"))
SONG = FileInfo(Path("a.mp3"), AUDIO, "mp3", 1, 200.0, None, Stream("mp3", channels=2))
PHOTO = FileInfo(Path("a.jpg"), IMAGE, "jpeg_pipe", 1, None, Stream("mjpeg", 400, 300), None)


def test_local_clip_seeks_before_input():
    choice = Choice("video", "mp4", extras=Extras(start=10, end=20.5))
    args = build_ffmpeg_args(CLIP, choice, Path("out.mp4"))
    assert args[args.index("-i") - 4 : args.index("-i")] == ["-ss", "10", "-to", "20.5"]
    assert is_fast_copy(CLIP, choice)  # cutting still copies
    assert clip_duration(CLIP, choice) == 10.5
    assert clip_duration(CLIP, Choice("video", "mp4", extras=Extras(start=90))) == 10


def test_images_ignore_clips():
    args = build_ffmpeg_args(PHOTO, Choice("image", "png", extras=Extras(start=3)), Path("o.png"))
    assert "-ss" not in args


def test_normalize_re_encodes_audio():
    choice = Choice("audio", "mp3", extras=Extras(normalize=True))
    assert not is_fast_copy(SONG, choice)
    args = build_ffmpeg_args(SONG, choice, Path("o.mp3"))
    assert "loudnorm" in args[args.index("-af") + 1]
    assert "-ar" in args and "copy" not in args


def test_strip_metadata_for_ffmpeg_images():
    args = build_ffmpeg_args(
        PHOTO, Choice("image", "png", extras=Extras(strip_metadata=True)), Path("o.png")
    )
    assert args[args.index("-map_metadata") + 1] == "-1"


def test_save_under_a_size(tmp_path):
    from PIL import Image

    from carrot_leaf.engine import local

    src = tmp_path / "noise.png"
    Image.effect_noise((800, 600), 90).convert("RGB").save(src)
    out = tmp_path / "small.jpg"
    local.save_with_pillow(src, Choice("image", "jpg", extras=Extras(max_kb=40)), out)
    assert out.stat().st_size <= 40 * 1024


def test_strip_metadata_with_pillow(tmp_path):
    from PIL import Image

    from carrot_leaf.engine import local

    src = tmp_path / "cam.jpg"
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    Image.new("RGB", (40, 30), "red").save(src, exif=exif.tobytes())
    kept, stripped = tmp_path / "kept.webp", tmp_path / "stripped.webp"
    local.save_with_pillow(src, Choice("image", "webp"), kept)
    local.save_with_pillow(
        src, Choice("image", "webp", extras=Extras(strip_metadata=True)), stripped
    )
    assert dict(Image.open(kept).getexif()).get(0x010F) == "TestCam"
    assert not dict(Image.open(stripped).getexif())


# ---------- moving results out ----------


def test_collect_keeps_subfolders_and_sidecars(tmp_path):
    work, dest = tmp_path / "work", tmp_path / "dest"
    (work / "Show").mkdir(parents=True)
    main = work / "Show" / "Ep 1.mp4"
    main.write_text("v")
    (work / "Show" / "Ep 1.en.srt").write_text("s")
    (work / "Show" / "Ep 1.webp").write_text("thumb")  # leftover, not a result
    (dest / "Show").mkdir(parents=True)
    (dest / "Show" / "Ep 1.mp4").write_text("old")
    moved = runner.collect(work, dest, main)
    assert moved == dest / "Show" / "Ep 1 (2).mp4"
    assert (dest / "Show" / "Ep 1.en.srt").exists()
    assert not (dest / "Show" / "Ep 1.webp").exists()
    assert (dest / "Show" / "Ep 1.mp4").read_text() == "old"


def test_collect_moves_a_chapter_folder(tmp_path):
    work, dest = tmp_path / "work", tmp_path / "dest"
    tracks = work / "Album"
    tracks.mkdir(parents=True)
    (tracks / "01 - Intro.m4a").write_text("a")
    dest.mkdir()
    assert runner.collect(work, dest, tracks) == dest / "Album"
    assert (dest / "Album" / "01 - Intro.m4a").exists()


# ---------- playlists ----------


def test_playlist_info_skips_private_and_builds_links():
    info = {
        "_type": "playlist",
        "title": "Mix",
        "id": "PL1",
        "uploader": "Someone",
        "playlist_count": 900,
        "extractor_key": "YoutubeTab",
        "entries": [
            {"url": "abc", "id": "abc", "title": "One", "duration": 61, "ie_key": "Youtube"},
            None,
            {"url": "https://youtu.be/x", "id": "x", "title": "[Private video]"},
            {"url": "https://vimeo.com/2", "id": "2", "title": "Two"},
        ],
    }
    pl = probe.playlist_info(info, "https://x")
    assert [e.title for e in pl.entries] == ["One", "Two"]
    assert pl.entries[0].url == "https://www.youtube.com/watch?v=abc"
    assert pl.truncated and pl.duration == 61


def test_channel_tabs_pick_videos():
    info = {
        "title": "Blender",
        "entries": [
            {"_type": "playlist", "title": "Blender - Live", "entries": []},
            {"_type": "playlist", "title": "Blender - Videos", "entries": [{"url": "v"}]},
        ],
    }
    tab = probe.videos_tab(info)
    assert tab["entries"] == [{"url": "v"}] and tab["title"] == "Blender"


def test_channel_home_goes_to_videos():
    assert probe.channel_home_to_videos("https://www.youtube.com/@x") == (
        "https://www.youtube.com/@x/videos"
    )
    assert probe.channel_home_to_videos("https://youtube.com/@x/shorts").endswith("/shorts")
    assert probe.channel_home_to_videos("https://youtu.be/abc") == "https://youtu.be/abc"


# ---------- settings and history ----------


def test_typed_settings_roundtrip(tmp_path):
    path = tmp_path / "c.toml"
    s = config.Settings(download_dir=str(tmp_path), parallel_downloads=3, watch_clipboard=False)
    config.save(s, path)
    assert config.load(path) == s


def test_wrong_types_are_ignored(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('parallel_downloads = "lots"\nnotify_when_done = 1\nwatch_clipboard = false\n')
    loaded = config.load(path)
    assert loaded.parallel_downloads == 1 and loaded.notify_when_done is True
    assert loaded.watch_clipboard is False


def test_history_roundtrip(tmp_path):
    path = tmp_path / "h.jsonl"
    history.add(history.Entry("A", "/a.mp4", "MP4 · best", "https://x/a"), path)
    history.add(history.Entry("B", "/b.png", "PNG", "/src/b.jpg", kind="convert"), path)
    path.write_text(path.read_text() + "not json\n")
    entries = history.load(path)
    assert [e.title for e in entries] == ["A", "B"]
    assert entries[0].is_link and not entries[1].is_link
    history.remove(entries[0], path)
    assert [e.title for e in history.load(path)] == ["B"]
