from carrot_leaf.engine.options import (
    DOWNLOAD,
    Choice,
    audio_qualities,
    build_options,
    video_qualities,
)


def keys(opts):
    return [pp["key"] for pp in opts["postprocessors"]]


def test_download_is_best_mp4_with_metadata_and_cover():
    opts = build_options(DOWNLOAD, "/tmp/out")
    assert opts["format"] == "bv*+ba/b"
    # H.264 first: merges into MP4 without re-encoding
    assert opts["format_sort"][:2] == ["vcodec:h264", "acodec:aac"]
    assert opts["merge_output_format"] == "mp4"
    assert opts["noplaylist"] is True
    assert opts["writethumbnail"] is True
    assert keys(opts) == [
        "FFmpegThumbnailsConvertor",
        "FFmpegVideoRemuxer",
        "FFmpegMetadata",
        "EmbedThumbnail",
    ]
    assert opts["outtmpl"]["default"].startswith("/tmp/out")
    assert "[%(id)s]" in opts["outtmpl"]["default"]


def test_video_height_cap_uses_res_sort():
    opts = build_options(Choice("video", "mkv", "720"), "/x")
    assert opts["format_sort"][0] == "res:720"


def test_mp4_at_a_set_height_copies_streams_instead_of_re_encoding():
    opts = build_options(Choice("video", "mp4", "2160"), "/x")
    assert opts["format_sort"][0] == "res:2160"
    assert opts["merge_output_format"] == "mp4"
    assert "FFmpegVideoRemuxer" in keys(opts)
    assert "FFmpegVideoConvertor" not in keys(opts)


def test_webm_has_no_cover_art():
    opts = build_options(Choice("video", "webm", "best"), "/x")
    assert "EmbedThumbnail" not in keys(opts)
    assert "writethumbnail" not in opts


def test_gif_has_palette_and_no_metadata():
    opts = build_options(Choice("video", "gif", "360"), "/x")
    assert opts["format"] == "bv*/b"
    assert keys(opts) == ["FFmpegVideoConvertor"]
    vf = opts["postprocessor_args"]["videoconvertor"]
    assert "palettegen" in vf[1] and "360" in vf[1]


def test_audio_mp3_bitrate():
    opts = build_options(Choice("audio", "mp3", "192"), "/x")
    extract = next(p for p in opts["postprocessors"] if p["key"] == "FFmpegExtractAudio")
    assert extract["preferredcodec"] == "mp3"
    assert extract["preferredquality"] == "192"
    assert opts["format"] == "ba/b"
    assert "EmbedThumbnail" in keys(opts)


def test_audio_best_and_ogg_codec():
    opts = build_options(Choice("audio", "ogg", "best"), "/x")
    extract = next(p for p in opts["postprocessors"] if p["key"] == "FFmpegExtractAudio")
    assert extract["preferredcodec"] == "vorbis"
    assert extract["preferredquality"] == "0"


def test_wav_skips_cover_art():
    assert "EmbedThumbnail" not in keys(build_options(Choice("audio", "wav"), "/x"))


def test_cookies_and_tools_passed_through():
    opts = build_options(
        DOWNLOAD, "/x", ffmpeg_location="/ff", js_runtimes={"deno": {}}, cookies_browser="safari"
    )
    assert opts["ffmpeg_location"] == "/ff"
    assert opts["js_runtimes"] == {"deno": {}}
    assert opts["cookiesfrombrowser"] == ("safari",)


def test_qualities_only_what_video_has():
    assert video_qualities("mp4", 720) == ["best", "720", "480", "360"]
    assert video_qualities("mp4", None)[:2] == ["best", "2160"]
    assert video_qualities("gif", 1080) == ["480", "360", "240"]
    assert video_qualities("gif", 144) == ["240"]


def test_lossless_audio_has_only_best():
    assert audio_qualities("flac") == ["best"]
    assert audio_qualities("mp3") == ["best", "320", "192", "128"]


def test_choice_label():
    assert Choice("video", "mp4", "1080").label == "MP4 · 1080p"
    assert Choice("audio", "mp3", "320").label == "MP3 · 320k"
    assert DOWNLOAD.label == "MP4 · best"


def test_native_vorbis_fallback():
    opts = build_options(Choice("audio", "ogg"), "/x", native_vorbis=True)
    assert opts["postprocessor_args"]["extractaudio"][:2] == ["-c:a", "vorbis"]
    assert "postprocessor_args" not in build_options(
        Choice("audio", "mp3"), "/x", native_vorbis=True
    )
