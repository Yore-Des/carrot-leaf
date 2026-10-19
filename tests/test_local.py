"""The local file converter: path parsing, ffprobe parsing, ffmpeg argument planning.

All offline. `test_real_ffmpeg_*` run ffmpeg when it is on PATH and skip otherwise.
"""

import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from carrot_leaf.engine import errors, local
from carrot_leaf.engine.local import (
    ANIMATED,
    AUDIO,
    IMAGE,
    VIDEO,
    FileInfo,
    Stream,
    build_ffmpeg_args,
    formats_for,
    is_fast_copy,
    kinds_for,
    missing_encoder,
    parse_paths,
    parse_probe,
    progress_from,
    qualities_for,
)
from carrot_leaf.engine.options import Choice


def touch(folder: Path, name: str) -> Path:
    path = folder / name
    path.write_bytes(b"x")
    return path


def video(codec="h264", audio="aac", width=1920, height=1080, path="/in/holiday.mov"):
    return FileInfo(
        path=Path(path),
        media=VIDEO,
        container="mov",
        size=1000,
        duration=10.0,
        video=Stream(codec, width, height, 30.0),
        audio=Stream(audio, channels=2) if audio else None,
    )


def audio_file(codec="mp3", path="/in/song.mp3"):
    return FileInfo(Path(path), AUDIO, "mp3", 1000, 60.0, None, Stream(codec, channels=2))


def image(width=4032, height=3024, path="/in/photo.png"):
    return FileInfo(Path(path), IMAGE, "png_pipe", 1000, None, Stream("png", width, height), None)


def after(args, flag):
    return args[args.index(flag) + 1]


# ---------- parse_paths ----------


def test_plain_path_with_spaces(tmp_path):
    f = touch(tmp_path, "My Holiday.mov")
    assert parse_paths(str(f)) == [f]


def test_escaped_and_quoted_paths(tmp_path):
    f = touch(tmp_path, "My Holiday.mov")
    escaped = str(f).replace(" ", "\\ ")
    assert parse_paths(escaped) == [f]
    assert parse_paths(f"'{f}'") == [f]
    assert parse_paths(f'"{f}"  ') == [f]


def test_file_url(tmp_path):
    f = touch(tmp_path, "a b.mp4")
    assert parse_paths(f.as_uri()) == [f]


def test_several_dropped_files(tmp_path):
    a, b = touch(tmp_path, "a.mov"), touch(tmp_path, "b c.png")
    text = f"{a} {str(b).replace(' ', chr(92) + ' ')} {a}"
    assert parse_paths(text) == [a, b]  # duplicates dropped, order kept


def test_windows_quoted_paths(tmp_path):
    a, b = touch(tmp_path, "one two.mov"), touch(tmp_path, "three.mp3")
    assert parse_paths(f'"{a}" "{b}"', windows=True) == [a, b]


def test_not_files():
    assert parse_paths("") == []
    assert parse_paths("https://youtube.com/watch?v=x") == []
    assert parse_paths("clip.mov") == []  # looks like a domain, isn't a file here
    assert parse_paths("/no/such/file.mov") == []


def test_one_bad_path_rejects_the_lot(tmp_path):
    a = touch(tmp_path, "a.mov")
    assert parse_paths(f"{a} /no/such/file.mov") == []


def test_folder_is_not_a_file(tmp_path):
    assert parse_paths(str(tmp_path)) == []


# ---------- parse_probe ----------


def probe_json(format_name, *streams, duration=None, size="2048"):
    fmt = {"format_name": format_name, "size": size}
    if duration is not None:
        fmt["duration"] = str(duration)
    return {"format": fmt, "streams": list(streams)}


V = {"codec_type": "video", "codec_name": "hevc", "width": 1080, "height": 1920,
     "avg_frame_rate": "30000/1001"}  # fmt: skip
A = {"codec_type": "audio", "codec_name": "aac", "channels": 2}


def test_probe_video():
    info = parse_probe(probe_json("mov,mp4,m4a,3gp,3g2,mj2", V, A, duration=134.5), Path("x.mov"))
    assert info.media == VIDEO
    assert info.container == "mov"
    assert info.video.codec == "hevc"
    assert info.video.fps == pytest.approx(29.97, abs=0.01)
    assert info.audio.channels == 2
    assert info.duration == 134.5
    assert info.size == 2048
    assert info.max_height == 1080  # vertical video: short side
    assert info.title == "x.mov"


def test_probe_audio_with_cover_art_is_audio():
    cover = {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}}
    info = parse_probe(probe_json("mp3", A, cover, duration=200), Path("s.mp3"))
    assert info.media == AUDIO
    assert info.video is None


def test_probe_images():
    png = {"codec_type": "video", "codec_name": "png", "width": 800, "height": 600}
    info = parse_probe(probe_json("png_pipe", png), Path("p.png"))
    assert info.media == IMAGE
    assert info.duration is None
    assert info.long_side == 800
    jpg = {**png, "codec_name": "mjpeg"}
    assert parse_probe(probe_json("image2", jpg), Path("p.jpg")).media == IMAGE


def test_probe_gif():
    gif = {"codec_type": "video", "codec_name": "gif", "width": 320, "height": 240}
    assert parse_probe(probe_json("gif", gif, duration=2.5), Path("a.gif")).media == ANIMATED
    assert parse_probe(probe_json("gif", gif), Path("s.gif")).media == IMAGE


def test_probe_nothing_usable():
    with pytest.raises(local.LocalFileError):
        parse_probe(probe_json("tty"), Path("x.txt"))


def test_unreadable_file_explained():
    friendly = errors.explain("x.txt: Invalid data found when processing input")
    assert friendly.message == "Carrot Leaf can't read this file."


# ---------- what the dialog offers ----------


def test_kinds_by_media():
    assert kinds_for(video()) == ["video", "audio", "frames"]
    assert kinds_for(video(audio=None)) == ["video", "frames"]
    assert kinds_for(audio_file()) == ["audio"]
    assert kinds_for(image()) == ["image"]
    gif = FileInfo(Path("a.gif"), ANIMATED, "gif", 1, 2.0, Stream("gif", 320, 240), None)
    assert kinds_for(gif) == ["video", "image"]


def test_missing_encoders_are_named():
    only = {"mjpeg", "png", "libsvtav1", "aac", "vorbis", "libx264"}

    def has(name):
        return name in only

    assert missing_encoder("image", "jxl", has) == ""  # Pillow, via pillow-jxl-plugin
    assert missing_encoder("image", "heic", has) == ""  # Pillow, via pillow-heif
    assert missing_encoder("image", "webp", has) == ""  # no libwebp, but Pillow writes WebP
    assert missing_encoder("frames", "webp", has) == "libwebp"  # frames are ffmpeg only
    assert missing_encoder("image", "exr", has) == "exr"
    assert missing_encoder("image", "avif", has) == ""
    assert missing_encoder("audio", "ogg", has) == ""  # built-in Vorbis fallback
    assert missing_encoder("audio", "mp3", has) == "libmp3lame"
    assert missing_encoder("video", "ogv", has) == "libtheora"
    assert missing_encoder("video", "mp4", has) == ""
    assert missing_encoder("video", "webm", has) == "libvpx-vp9"


def test_every_format_has_a_note_and_builds():
    for kind, table in (
        ("video", local.VIDEO_TARGETS),
        ("audio", local.AUDIO_TARGETS),
        ("image", local.IMAGE_TARGETS),
    ):
        assert formats_for(kind) == tuple(table)
        for fmt in table:
            assert local.format_note(kind, fmt)
            if fmt in local.PILLOW_ONLY:
                continue
            src = image() if kind == "image" else video()
            args = build_ffmpeg_args(src, Choice(kind, fmt), Path(f"/o/x.{fmt}"))
            assert args[-1] == f"/o/x.{fmt}"
    assert set(formats_for("frames")) == set(formats_for("image")) - {"jxl", "heic"}


def test_many_formats_on_offer():
    assert {"jxl", "avif", "webp", "heic"} <= set(formats_for("image"))
    assert len(formats_for("image")) >= 15
    assert {"alac", "aiff", "wma", "ac3", "wv", "caf"} <= set(formats_for("audio"))
    assert {"avi", "ts", "wmv", "mpg", "apng", "ogv"} <= set(formats_for("video"))


def test_qualities():
    assert qualities_for(video(height=720, width=1280), "video", "mp4") == [
        "best",
        "720",
        "480",
        "360",
    ]
    assert qualities_for(image(width=1600, height=1200), "image", "jpg") == ["best", "50", "1280"]
    assert qualities_for(video(), "frames", "png")[0] == "best"


def test_new_choice_labels():
    assert Choice("image", "jpg", "1920").label == "JPG · 1920px"
    assert Choice("image", "png", "best").label == "PNG · original"
    assert Choice("image", "webp", "50").label == "WEBP · 50%"
    assert Choice("frames", "png", "1").label == "PNG frames · 1/s"


# ---------- build_ffmpeg_args: video ----------


def test_h264_mov_to_mp4_copies():
    info = video("h264", "aac")
    choice = Choice("video", "mp4")
    args = build_ffmpeg_args(info, choice, Path("/out/holiday.mp4"))
    assert args[:5] == ["-hide_banner", "-nostdin", "-y", "-i", "/in/holiday.mov"]
    assert after(args, "-c:v") == "copy"
    assert after(args, "-c:a") == "copy"
    assert "+faststart" in args
    assert args[-1] == "/out/holiday.mp4"
    assert is_fast_copy(info, choice)


def test_hevc_into_mp4_is_tagged_for_quicktime():
    args = build_ffmpeg_args(video("hevc"), Choice("video", "mp4"), Path("o.mp4"))
    assert after(args, "-c:v") == "copy"
    assert after(args, "-tag:v") == "hvc1"


def test_vp9_to_mp4_re_encodes_to_h264():
    info = video("vp9", "opus")
    choice = Choice("video", "mp4")
    args = build_ffmpeg_args(info, choice, Path("o.mp4"))
    assert after(args, "-c:v") == "libx264"
    assert after(args, "-c:a") == "aac"
    assert not is_fast_copy(info, choice)


def test_anything_copies_into_mkv():
    args = build_ffmpeg_args(video("vp9", "opus"), Choice("video", "mkv"), Path("o.mkv"))
    assert after(args, "-c:v") == "copy" and after(args, "-c:a") == "copy"
    assert "+faststart" not in args


def test_webm_needs_vp9_and_opus():
    args = build_ffmpeg_args(video("h264", "aac"), Choice("video", "webm"), Path("o.webm"))
    assert after(args, "-c:v") == "libvpx-vp9"
    assert after(args, "-c:a") == "libopus"


def test_smaller_height_scales_and_re_encodes():
    info = video("h264")
    choice = Choice("video", "mp4", "720")
    args = build_ffmpeg_args(info, choice, Path("o.mp4"))
    assert "720" in after(args, "-vf")
    assert after(args, "-c:v") == "libx264"
    assert after(args, "-c:a") == "copy"  # audio can still be copied
    assert not is_fast_copy(info, choice)


def test_height_at_or_above_source_does_not_scale():
    args = build_ffmpeg_args(
        video(height=720, width=1280), Choice("video", "mp4", "720"), Path("o")
    )
    assert "-vf" not in args
    assert after(args, "-c:v") == "copy"


def test_silent_video_has_no_audio_codec():
    info = video(audio=None)
    args = build_ffmpeg_args(info, Choice("video", "mp4"), Path("o.mp4"))
    assert "-c:a" not in args
    assert is_fast_copy(info, Choice("video", "mp4"))


def test_gif_uses_palette_and_drops_audio():
    args = build_ffmpeg_args(video(), Choice("video", "gif", "360"), Path("o.gif"))
    assert "palettegen" in after(args, "-vf") and "360" in after(args, "-vf")
    assert "-an" in args and after(args, "-loop") == "0"


# ---------- build_ffmpeg_args: audio ----------


def test_same_codec_audio_copies():
    info = audio_file("mp3")
    choice = Choice("audio", "mp3")
    args = build_ffmpeg_args(info, choice, Path("o.mp3"))
    assert after(args, "-c:a") == "copy"
    assert "-vn" in args
    assert is_fast_copy(info, choice)


def test_set_bitrate_re_encodes_even_same_codec():
    args = build_ffmpeg_args(audio_file("mp3"), Choice("audio", "mp3", "128"), Path("o.mp3"))
    assert after(args, "-c:a") == "libmp3lame"
    assert after(args, "-b:a") == "128k"


def test_mp3_best_uses_vbr_and_m4a_a_default_bitrate():
    args = build_ffmpeg_args(audio_file("flac"), Choice("audio", "mp3"), Path("o.mp3"))
    assert after(args, "-q:a") == "0"
    args = build_ffmpeg_args(audio_file("flac"), Choice("audio", "m4a"), Path("o.m4a"))
    assert after(args, "-c:a") == "aac" and after(args, "-b:a") == "256k"


def test_lossless_targets_have_no_bitrate():
    for fmt, codec in (("flac", "flac"), ("wav", "pcm_s16le")):
        args = build_ffmpeg_args(audio_file("aac"), Choice("audio", fmt), Path(f"o.{fmt}"))
        assert after(args, "-c:a") == codec
        assert "-b:a" not in args


def test_audio_from_video():
    args = build_ffmpeg_args(video(audio="aac"), Choice("audio", "m4a"), Path("o.m4a"))
    assert after(args, "-c:a") == "copy"
    assert "-vn" in args


def test_ogg_falls_back_to_native_vorbis():
    args = build_ffmpeg_args(
        audio_file("aac"), Choice("audio", "ogg"), Path("o.ogg"), has_encoder=lambda e: False
    )
    assert after(args, "-c:a") == "vorbis"
    assert after(args, "-strict") == "-2"
    with_lib = build_ffmpeg_args(audio_file("aac"), Choice("audio", "ogg"), Path("o.ogg"))
    assert after(with_lib, "-c:a") == "libvorbis"


# ---------- build_ffmpeg_args: images and frames ----------


def test_image_original_size():
    args = build_ffmpeg_args(image(), Choice("image", "jpg"), Path("o.jpg"))
    assert after(args, "-frames:v") == "1"
    assert "-vf" not in args
    assert after(args, "-q:v") == "2"


def test_image_caps_and_half():
    args = build_ffmpeg_args(image(), Choice("image", "png", "1920"), Path("o.png"))
    assert "min(iw,1920)" in after(args, "-vf")
    args = build_ffmpeg_args(image(), Choice("image", "webp", "50"), Path("o.webp"))
    assert after(args, "-vf") == "scale=iw/2:-1"
    assert after(args, "-c:v") == "libwebp"


def test_frames_go_into_a_folder():
    args = build_ffmpeg_args(video(), Choice("frames", "png", "1"), Path("/out/holiday frames"))
    assert after(args, "-vf") == "fps=1"
    assert args[-1] == "/out/holiday frames/holiday %04d.png"
    every = build_ffmpeg_args(video(), Choice("frames", "jpg"), Path("/out/f"))
    assert "-vf" not in every and after(every, "-q:v") == "2"


def test_output_names():
    assert local.output_name(video(), Choice("video", "mp4")) == "holiday.mp4"
    assert local.output_name(video(), Choice("frames", "png")) == "holiday frames"


def test_avif_uses_whichever_av1_encoder_exists():
    args = build_ffmpeg_args(
        image(), Choice("image", "avif"), Path("o.avif"), has_encoder=lambda e: e == "libsvtav1"
    )
    assert after(args, "-c:v") == "libsvtav1"
    args = build_ffmpeg_args(image(), Choice("image", "avif"), Path("o.avif"))
    assert after(args, "-c:v") == "libaom-av1"


def test_jxl_and_still_gif():
    with pytest.raises(local.LocalFileError):  # Pillow's job, not ffmpeg's
        build_ffmpeg_args(image(), Choice("image", "jxl"), Path("o"))
    args = build_ffmpeg_args(image(), Choice("image", "gif", "1920"), Path("o.gif"))
    assert "min(iw,1920)" in after(args, "-vf") and "palettegen" in after(args, "-vf")


def test_more_audio_formats():
    alac = Choice("audio", "alac")
    assert local.output_name(audio_file("flac"), alac) == "song.m4a"
    args = build_ffmpeg_args(audio_file("flac"), alac, Path("o.m4a"))
    assert after(args, "-c:a") == "alac" and "-b:a" not in args
    assert qualities_for(audio_file(), "audio", "alac") == ["best"]
    wma = build_ffmpeg_args(audio_file(), Choice("audio", "wma", "128"), Path("o.wma"))
    assert after(wma, "-c:a") == "wmav2" and after(wma, "-b:a") == "128k"
    ac3 = build_ffmpeg_args(audio_file(), Choice("audio", "ac3"), Path("o.ac3"))
    assert after(ac3, "-b:a") == "448k"
    # Matroska audio takes any codec as it is.
    assert (
        after(build_ffmpeg_args(audio_file("mp3"), Choice("audio", "mka"), Path("o")), "-c:a")
        == "copy"
    )


def test_more_video_formats():
    avi = build_ffmpeg_args(video("hevc", "aac"), Choice("video", "avi"), Path("o.avi"))
    assert after(avi, "-c:v") == "mpeg4" and after(avi, "-c:a") == "libmp3lame"
    wmv = build_ffmpeg_args(video(), Choice("video", "wmv"), Path("o.wmv"))
    assert after(wmv, "-c:v") == "wmv2" and after(wmv, "-c:a") == "wmav2"
    ts = build_ffmpeg_args(video("h264", "aac"), Choice("video", "ts"), Path("o.ts"))
    assert after(ts, "-c:v") == "copy" and after(ts, "-c:a") == "copy"
    m4v = build_ffmpeg_args(video("hevc"), Choice("video", "m4v"), Path("o.m4v"))
    assert after(m4v, "-tag:v") == "hvc1"


def test_animated_formats():
    apng = Choice("video", "apng", "360")
    assert local.output_name(video(), apng) == "holiday.png"
    args = build_ffmpeg_args(video(), apng, Path("o.png"))
    assert "scale=-2:360" in after(args, "-vf") and after(args, "-f") == "apng"
    assert "-an" in args
    assert qualities_for(video(), "video", "webp") == ["480", "360", "240"]
    assert not is_fast_copy(video(), Choice("video", "webp", "480"))


# ---------- progress ----------


def test_progress_fraction_and_eta():
    p = progress_from({"out_time_us": "5000000", "speed": "2.0x", "progress": "continue"}, 10, "C")
    assert p.fraction == 0.5
    assert p.eta == 2.5


def test_progress_without_duration_is_busy():
    assert progress_from({"out_time_us": "1", "progress": "continue"}, None, "C").fraction is None
    assert progress_from({"out_time_us": "N/A", "progress": "continue"}, 10, "C").fraction is None


def test_progress_end():
    assert progress_from({"progress": "end"}, None, "C").fraction == 1.0


# ---------- real ffmpeg ----------

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and shutil.which("ffprobe")), reason="no ffmpeg")


def make_clip(folder: Path) -> Path:
    clip = folder / "clip.mov"
    subprocess.run(
        [FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2",
         "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(clip)],
        check=True,
    )  # fmt: skip
    return clip


@needs_ffmpeg
def test_real_ffmpeg_copy_and_convert(tmp_path):
    clip = make_clip(tmp_path)
    info = local.probe_file(clip)
    assert info.media == VIDEO and info.video.codec == "h264" and info.audio.codec == "aac"

    seen = []
    out = local.run_convert(info, Choice("video", "mp4"), seen.append, threading.Event())
    assert Path(out) == tmp_path / "clip.mp4"
    assert local.probe_file(Path(out)).video.codec == "h264"
    assert seen and seen[-1].fraction == 1.0

    # Same name again gets a number, never overwrites.
    again = local.run_convert(info, Choice("video", "mp4"), lambda p: None, threading.Event())
    assert Path(again).name == "clip (2).mp4"

    mp3 = local.run_convert(info, Choice("audio", "mp3", "128"), lambda p: None, threading.Event())
    assert local.probe_file(Path(mp3)).media == AUDIO

    frames = local.run_convert(
        info, Choice("frames", "png", "1"), lambda p: None, threading.Event()
    )
    assert 2 <= len(list(Path(frames).glob("clip *.png"))) <= 3

    png = next(Path(frames).glob("clip *.png"))
    still = local.probe_file(png)
    assert still.media == IMAGE
    jpg = local.run_convert(still, Choice("image", "jpg", "50"), lambda p: None, threading.Event())
    assert local.probe_file(Path(jpg)).video.width == 160

    # Nothing left behind but results.
    assert not list(tmp_path.glob(".carrot-leaf-*"))


@needs_ffmpeg
def test_real_ffmpeg_cancel(tmp_path):
    clip = make_clip(tmp_path)
    info = local.probe_file(clip)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(local.ConvertCancelled):
        local.run_convert(info, Choice("video", "webm"), lambda p: None, cancel)
    assert not (tmp_path / "clip.webm").exists()
    assert not list(tmp_path.glob(".carrot-leaf-*"))


@needs_ffmpeg
def test_real_ffprobe_rejects_text(tmp_path):
    with pytest.raises(local.LocalFileError):
        local.probe_file(touch(tmp_path, "notes.txt"))


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("no ffmpeg")
    folder = tmp_path_factory.mktemp("samples")
    clip = make_clip(folder)
    still = folder / "still.png"
    subprocess.run(
        [FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240", "-frames:v", "1",
         str(still)],
        check=True,
    )  # fmt: skip
    return local.probe_file(clip), local.probe_file(still)


EVERY_FORMAT = [
    *(("video", f) for f in local.VIDEO_TARGETS),
    *(("audio", f) for f in local.AUDIO_TARGETS),
    *(("image", f) for f in local.IMAGE_TARGETS),
]


@needs_ffmpeg
@pytest.mark.parametrize("kind, fmt", EVERY_FORMAT, ids=[f"{k}-{f}" for k, f in EVERY_FORMAT])
def test_real_ffmpeg_every_format(samples, kind, fmt):
    """Convert into every format with whichever ffmpeg here can (system, then bundled),
    the way the Convert files tab picks one."""
    from carrot_leaf.engine import deps

    clip, still = samples
    dirs = [t.path for t in deps.ffmpeg_candidates()]
    usable = [
        d
        for d in dirs
        if not local.missing_encoder(kind, fmt, lambda e, d=d: deps.ffmpeg_has_encoder(d, e))
    ]
    if not usable:
        pytest.skip(f"no ffmpeg here can write {fmt}")
    source = still if kind == "image" else clip
    out = local.run_convert(source, Choice(kind, fmt), lambda p: None, threading.Event(), usable[0])
    try:
        result = local.probe_file(Path(out), usable[0])
        if kind == "audio":
            assert result.audio is not None
        else:
            assert result.video is not None
            if kind == "video" and local.VIDEO_TARGETS[fmt].audio:
                assert result.audio is not None
    finally:
        Path(out).unlink()


@needs_ffmpeg
@pytest.mark.parametrize("source_fmt", ["heic", "jxl"])
@pytest.mark.parametrize("target_fmt", ["png", "jpg", "exr"])
def test_real_pillow_formats_both_ways(samples, tmp_path, source_fmt, target_fmt):
    """HEIC and JPEG XL in and out, including Pillow -> PNG -> ffmpeg for EXR."""
    _, still = samples
    ff = str(Path(FFMPEG).parent)
    work = tmp_path / "w"
    work.mkdir()
    src = work / "still.png"
    src.write_bytes(still.path.read_bytes())
    made = local.run_convert(
        local.probe_file(src, ff),
        Choice("image", source_fmt),
        lambda p: None,
        threading.Event(),
        ff,
    )
    info = local.probe_file(Path(made), ff)
    assert info.reader == "pillow" and info.media == IMAGE
    assert (info.video.width, info.video.height) == (320, 240)
    out = local.run_convert(
        info, Choice("image", target_fmt), lambda p: None, threading.Event(), ff
    )
    result = local.probe_file(Path(out), ff)
    assert (result.video.width, result.video.height) == (320, 240)


def test_pillow_flattens_transparency_for_jpg(tmp_path):
    from PIL import Image

    src = tmp_path / "clear.png"
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(src)
    out = tmp_path / "clear.jpg"
    local.save_with_pillow(src, Choice("image", "jpg"), out)
    with Image.open(out) as im:
        assert im.mode == "RGB"
        assert im.getpixel((5, 5)) == (255, 255, 255)  # white, not black


def test_pillow_resizes_long_side(tmp_path):
    from PIL import Image

    src = tmp_path / "tall.png"
    Image.new("RGB", (1000, 3000)).save(src)
    out = tmp_path / "tall.webp"
    local.save_with_pillow(src, Choice("image", "webp", "1280"), out)
    with Image.open(out) as im:
        assert im.size == (427, 1280)


def test_probe_reads_heic_as_a_photo_not_a_video(tmp_path):
    from PIL import Image

    png = tmp_path / "p.png"
    Image.new("RGB", (64, 48), "teal").save(png)
    heic = tmp_path / "p.heic"
    local.save_with_pillow(png, Choice("image", "heic"), heic)
    info = local.probe_file(heic)
    assert info.media == IMAGE and info.reader == "pillow" and info.container == "heif"
    assert local.kinds_for(info) == ["image"]
