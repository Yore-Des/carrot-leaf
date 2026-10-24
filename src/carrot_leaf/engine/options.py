"""Turn a format/quality choice into yt-dlp options. Pure: no I/O."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

VIDEO_FORMATS = ("mp4", "mkv", "webm", "mov", "gif")
AUDIO_FORMATS = ("mp3", "m4a", "opus", "flac", "wav", "ogg")

VIDEO_HEIGHTS = (2160, 1440, 1080, 720, 480, 360)
GIF_HEIGHTS = (480, 360, 240)
AUDIO_BITRATES = ("320", "192", "128")
# Image sizes: "best" keeps the original, "50" halves it, others cap the long side in pixels.
IMAGE_SIZES = ("3840", "1920", "1280")
# Frames per second to pull out of a video; "best" keeps every frame.
FRAME_RATES = ("best", "10", "1", "0.1")
FRAME_RATE_LABELS = {"best": "every frame", "10": "10/s", "1": "1/s", "0.1": "1 every 10s"}
LOSSLESS_AUDIO = ("flac", "wav")

# Containers that can carry embedded cover art.
THUMBNAIL_EXTS = {"mp4", "mov", "mkv", "m4a", "mp3", "flac", "opus", "ogg"}

# yt-dlp names the Vorbis codec, not the ogg container.
AUDIO_CODECS = {"ogg": "vorbis"}

# Prefer codecs each container plays well with; resolution still wins first.
VIDEO_SORT = {
    "mp4": ["vcodec:h264", "acodec:aac"],
    "mov": ["vcodec:h264", "acodec:aac"],
    "webm": ["vcodec:vp9", "acodec:opus"],
    "mkv": [],
}

# Containers whose "best" prefers H.264 so nothing has to be re-encoded to play everywhere.
H264_FIRST = ("mp4", "mov")

# Filenames: people write `{title} [{id}]`; yt-dlp wants `%(title)s [%(id)s]`.
DEFAULT_TEMPLATE = "{title} [{id}]"
TEMPLATE_FIELDS = {
    "title": "%(title).180B",
    "id": "%(id)s",
    "channel": "%(channel,uploader|Unknown)s",
    "uploader": "%(uploader,channel|Unknown)s",
    "date": "%(upload_date>%Y-%m-%d|)s",
    "year": "%(upload_date>%Y|)s",
    "site": "%(extractor_key)s",
    "height": "%(height|)sp",
    # Filled in by Carrot for playlist downloads, which fetch each video on its own.
    "playlist": "",
    "index": "",
}
TEMPLATE_SAMPLE = {
    "title": "Big Buck Bunny",
    "id": "aqz-KE-bpKQ",
    "channel": "Blender",
    "uploader": "Blender",
    "date": "2014-11-10",
    "year": "2014",
    "site": "Youtube",
    "height": "1080p",
    "playlist": "Open Movies",
    "index": "03",
}
_FIELD = re.compile(r"\{(\w+)\}")
OUTPUT_TEMPLATE = "%(title).180B [%(id)s].%(ext)s"

# SponsorBlock: what "remove" cuts out; "mark" turns every kind into a chapter instead.
SPONSOR_REMOVE = ("sponsor", "selfpromo", "interaction")
SPONSOR_MARK = ("sponsor", "selfpromo", "interaction", "intro", "outro", "preview", "filler")
# Containers yt-dlp can put subtitles inside; anything else gets a .srt next to it.
SUBTITLE_EMBED_EXTS = ("mp4", "mkv", "webm")
# Even out loudness to the -16 LUFS podcasts and streaming services use.
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"


@dataclass(frozen=True)
class Extras:
    """Everything past format and quality. The defaults change nothing."""

    start: float | None = None  # clip, in seconds
    end: float | None = None
    subtitles: str = ""  # "", "embed" or "srt"
    sub_langs: str = "en"  # yt-dlp language list, comma separated: "en,de" or "en.*"
    sponsorblock: str = ""  # "", "remove" or "mark"
    split_chapters: bool = False
    normalize: bool = False  # even out the volume (audio)
    strip_metadata: bool = False  # images: drop EXIF, GPS and camera details
    max_kb: int | None = None  # images: lower the quality until the file fits

    @property
    def clipped(self) -> bool:
        return self.start is not None or self.end is not None

    @property
    def label(self) -> str:
        bits = []
        if self.clipped:
            bits.append(
                f"{time_text(self.start or 0)}–{time_text(self.end) if self.end else 'end'}"
            )
        if self.subtitles:
            bits.append("subs" if self.subtitles == "embed" else "+srt")
        if self.sponsorblock == "remove":
            bits.append("no sponsors")
        elif self.sponsorblock == "mark":
            bits.append("sponsor chapters")
        if self.split_chapters:
            bits.append("by chapter")
        if self.normalize:
            bits.append("even volume")
        if self.strip_metadata:
            bits.append("no metadata")
        if self.max_kb:
            bits.append(f"≤{self.max_kb} KB")
        return " · ".join(bits)


NO_EXTRAS = Extras()


@dataclass(frozen=True)
class Choice:
    kind: str  # "video", "audio", or for local files also "image" and "frames"
    fmt: str
    # "best", a height like "1080", a bitrate like "320", an image size like "1920" or "50"
    # (percent), or a frame rate like "1".
    quality: str = "best"
    extras: Extras = field(default=NO_EXTRAS)

    @property
    def label(self) -> str:
        extra = self.extras.label
        return f"{self.base_label} · {extra}" if extra else self.base_label

    @property
    def base_label(self) -> str:
        if self.kind == "frames":
            return (
                f"{self.fmt.upper()} frames · {FRAME_RATE_LABELS.get(self.quality, self.quality)}"
            )
        if self.kind == "image":
            if self.quality == "best":
                q = "original"
            elif self.quality == "50":
                q = "50%"
            else:
                q = f"{self.quality}px"
        elif self.quality == "best":
            q = "best"
        elif self.kind == "video":
            q = f"{self.quality}p"
        else:
            q = f"{self.quality}k"
        return f"{self.fmt.upper()} · {q}"


DOWNLOAD = Choice("video", "mp4", "best")


def parse_time(text: str) -> float | None:
    """`90`, `1:30`, `1:02:03`, `1m30s` or `1:30.5` as seconds; None when blank.

    Raises ValueError for anything else.
    """
    text = text.strip().lower()
    if not text:
        return None
    if m := re.fullmatch(r"(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?", text):
        if any(m.groups()):
            h, mins, secs = (float(g) if g else 0.0 for g in m.groups())
            return h * 3600 + mins * 60 + secs
    parts = text.split(":")
    if len(parts) > 3 or not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        raise ValueError(f"Can't read {text!r} as a time. Try 1:30 or 90.")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def time_text(seconds: float | None) -> str:
    if seconds is None or math.isinf(seconds):
        return "end"
    whole = int(seconds)
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    text = f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"
    if frac := round(seconds - whole, 1):
        text += f"{frac:.1f}"[1:]
    return text


def check_clip(start: float | None, end: float | None, duration: float | None) -> str:
    """Why this clip can't work, or "" when it can."""
    if start is not None and end is not None and end <= start:
        return "The end has to come after the start."
    if duration and start is not None and start >= duration:
        return f"The video is only {time_text(duration)} long."
    return ""


def template_error(template: str) -> str:
    """What's wrong with a filename template, or "" when it's fine."""
    if not template.strip():
        return "The name can't be empty."
    unknown = [f for f in _FIELD.findall(template) if f not in TEMPLATE_FIELDS]
    if unknown:
        return f"Unknown field {{{unknown[0]}}}. Use " + ", ".join(
            f"{{{f}}}" for f in TEMPLATE_FIELDS
        )
    if template.strip().startswith(("/", "\\")) or ".." in template.replace("\\", "/").split("/"):
        return "Keep the name inside the download folder."
    return ""


def safe_name(name: str) -> str:
    """A folder name that works on every system."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned[:120] or "Playlist"


def template_preview(template: str, ext: str = "mp4") -> str:
    name = _FIELD.sub(lambda m: TEMPLATE_SAMPLE.get(m.group(1), m.group(0)), template)
    return f"{name.strip()}.{ext}"


def to_outtmpl(template: str, fields: dict[str, str] | None = None) -> str:
    """A yt-dlp output template from `{field}` text. `fields` fills in literal values
    (playlist name and position) that yt-dlp doesn't know when it fetches one video."""
    fields = fields or {}
    out = []
    pos = 0
    for m in _FIELD.finditer(template):
        out.append(template[pos : m.start()].replace("%", "%%"))
        name = m.group(1)
        if name in fields:
            out.append(fields[name].replace("%", "%%"))
        else:
            out.append(TEMPLATE_FIELDS.get(name, ""))
        pos = m.end()
    out.append(template[pos:].replace("%", "%%"))
    text = "".join(out).strip().replace("\\", "/")
    # Empty fields leave stray separators: "/ - Title" or "a//b".
    parts = [p.strip(" -_.") or "_" for p in text.split("/")]
    return "/".join(parts) + ".%(ext)s"


def video_qualities(fmt: str, max_height: int | None) -> list[str]:
    """Quality options for a video format, limited to what the video has."""
    heights = GIF_HEIGHTS if fmt == "gif" else VIDEO_HEIGHTS
    if max_height:
        heights = tuple(h for h in heights if h <= max_height)
    if fmt == "gif":
        return [str(h) for h in heights] or ["240"]
    return ["best", *(str(h) for h in heights)]


def audio_qualities(fmt: str) -> list[str]:
    return ["best"] if fmt in LOSSLESS_AUDIO else ["best", *AUDIO_BITRATES]


def image_sizes(long_side: int | None) -> list[str]:
    """Size options for an image: original, half, and caps smaller than the image."""
    caps = [s for s in IMAGE_SIZES if not long_side or int(s) < long_side]
    return ["best", "50", *caps]


def gif_filter(height: str) -> str:
    """A palette pass makes GIFs look far better than ffmpeg's default dithering."""
    return (
        f"fps=10,scale=-2:{height}:flags=lanczos,split[a][b];"
        "[a]palettegen=stats_mode=diff[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )


def build_options(
    choice: Choice,
    download_dir: str,
    *,
    ffmpeg_location: str | None = None,
    js_runtimes: dict | None = None,
    cookies_browser: str = "",
    native_vorbis: bool = False,
    template: str = DEFAULT_TEMPLATE,
    fields: dict[str, str] | None = None,
) -> dict:
    outtmpl = to_outtmpl(template, fields) if template != DEFAULT_TEMPLATE else OUTPUT_TEMPLATE
    opts: dict = {
        "outtmpl": {"default": os.path.join(download_dir, outtmpl)},
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "windowsfilenames": True,
        "overwrites": False,
        "postprocessors": [],
    }
    if ffmpeg_location:
        opts["ffmpeg_location"] = ffmpeg_location
    if js_runtimes:
        opts["js_runtimes"] = js_runtimes
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)

    pps = opts["postprocessors"]
    extras = choice.extras
    # Audio is cut after the download (runner.py): yt-dlp's cut ignores the start there.
    if extras.clipped and choice.kind != "audio":
        from yt_dlp.utils import download_range_func  # only when a clip is asked for

        opts["download_ranges"] = download_range_func(
            None, [(extras.start or 0, extras.end if extras.end is not None else math.inf)]
        )
    if extras.sponsorblock:
        cats = SPONSOR_REMOVE if extras.sponsorblock == "remove" else SPONSOR_MARK
        pps.append({"key": "SponsorBlock", "categories": list(cats), "when": "after_filter"})
    if choice.kind == "audio":
        _audio(choice, opts, pps)
        if choice.fmt == "ogg" and native_vorbis:
            # Some ffmpeg builds (e.g. Homebrew's) ship without libvorbis; use the built-in encoder.
            opts["postprocessor_args"] = {"extractaudio": ["-c:a", "vorbis", "-strict", "-2"]}
        final_ext = choice.fmt
    elif choice.fmt == "gif":
        _gif(choice, opts, pps)
        final_ext = "gif"
    else:
        _video(choice, opts, pps)
        final_ext = choice.fmt

    _subtitles(extras, final_ext, opts, pps)
    if extras.sponsorblock and final_ext != "gif":
        # Must run before FFmpegMetadata, which writes the chapters it leaves.
        pps.append(
            {
                "key": "ModifyChapters",
                "remove_sponsor_segments": list(SPONSOR_REMOVE)
                if extras.sponsorblock == "remove"
                else [],
                "sponsorblock_chapter_title": "[SponsorBlock]: %(category_names)l",
            }
        )
    if final_ext != "gif":
        pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True})
    if final_ext in THUMBNAIL_EXTS:
        opts["writethumbnail"] = True
        # mp4/m4a/mp3 can't hold webp art, so normalise thumbnails to jpg first.
        pps.insert(0, {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
        pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
    if extras.split_chapters and final_ext != "gif":
        # One file per chapter, in a folder named after the video. runner.py keeps the
        # folder and drops the whole file when there were chapters to split.
        stem = outtmpl.rsplit(".%(ext)s", 1)[0]
        opts["outtmpl"]["chapter"] = os.path.join(
            download_dir, stem, "%(section_number)02d - %(section_title).150B.%(ext)s"
        )
        pps.append({"key": "FFmpegSplitChapters", "force_keyframes": False})
    return opts


def _subtitles(extras: Extras, final_ext: str, opts: dict, pps: list) -> None:
    if not extras.subtitles or final_ext == "gif":
        return
    opts["writesubtitles"] = True
    opts["writeautomaticsub"] = True  # most videos only have YouTube's automatic captions
    opts["subtitleslangs"] = [lang.strip() for lang in extras.sub_langs.split(",") if lang.strip()]
    if extras.subtitles == "embed" and final_ext in SUBTITLE_EMBED_EXTS:
        pps.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})
    else:
        opts["subtitlesformat"] = "srt/vtt/best"
        pps.insert(0, {"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"})


def _video(choice: Choice, opts: dict, pps: list) -> None:
    res = "res" if choice.quality == "best" else f"res:{choice.quality}"
    opts["format"] = "bv*+ba/b"
    if choice.fmt in H264_FIRST and choice.quality == "best":
        # "Best" MP4/MOV means the best copy that plays everywhere: H.264 + AAC merge straight
        # in, no re-encode. YouTube only has H.264 up to 1080p; above that it's VP9/AV1, and
        # turning those into H.264 re-encodes the whole video (minutes for 4K).
        opts["format_sort"] = ["vcodec:h264", "acodec:aac", res]
    else:
        opts["format_sort"] = [res, *VIDEO_SORT[choice.fmt]]
    if choice.fmt == "mp4":
        # MP4 can hold VP9/AV1/Opus too, so merge and remux by copying streams, never
        # re-encoding. A 1440p/2160p pick may then not open in older players.
        opts["merge_output_format"] = "mp4"
        pps.append({"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"})
    elif choice.fmt == "mov" and choice.quality == "best":
        # H.264 + AAC copy into MOV as-is.
        opts["merge_output_format"] = "mov/mkv"
        pps.append({"key": "FFmpegVideoRemuxer", "preferedformat": "mov"})
    else:
        # Fall back to mkv if the streams can't go into the target container, then convert;
        # FFmpegVideoConvertor skips when the file already has the right ext.
        opts["merge_output_format"] = f"{choice.fmt}/mkv"
        pps.append({"key": "FFmpegVideoConvertor", "preferedformat": choice.fmt})


def _gif(choice: Choice, opts: dict, pps: list) -> None:
    height = choice.quality if choice.quality != "best" else str(GIF_HEIGHTS[0])
    opts["format"] = "bv*/b"
    opts["format_sort"] = [f"res:{height}"]
    pps.append({"key": "FFmpegVideoConvertor", "preferedformat": "gif"})
    opts["postprocessor_args"] = {
        "videoconvertor": [
            "-vf",
            gif_filter(height),
            "-loop",
            "0",
            "-an",
        ]
    }


def _audio(choice: Choice, opts: dict, pps: list) -> None:
    opts["format"] = "ba/b"
    quality = "0" if choice.quality == "best" else choice.quality
    pps.append(
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": AUDIO_CODECS.get(choice.fmt, choice.fmt),
            "preferredquality": quality,
        }
    )
