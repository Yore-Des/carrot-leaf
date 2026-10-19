"""Convert files already on disk with ffmpeg. No yt-dlp involved.

`parse_paths` turns pasted or dropped text into files, `probe_file` reads one with ffprobe,
`build_ffmpeg_args` plans the conversion (pure, so it is easy to test), and `run_convert`
runs it with progress and cancel.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from urllib.parse import unquote, urlparse

from .options import (
    AUDIO_BITRATES,
    FRAME_RATES,
    Choice,
    gif_filter,
    image_sizes,
    video_qualities,
)
from .runner import CONVERTING, Progress, unique_path

VIDEO, AUDIO, IMAGE, ANIMATED = "video", "audio", "image", "animated"

# ffprobe format names for single pictures (jpeg_pipe, png_pipe, webp_pipe, ...).
_IMAGE_FORMAT_NAMES = ("image2",)

HasEncoder = Callable[[str], bool]


def _any_encoder(_name: str) -> bool:
    return True


def _codecs(*names: str) -> frozenset[str]:
    return frozenset(names)


# ---------- what each format is ----------
#
# One table per media type. Each entry says what the format is for (the note shown in the
# Convert dialog), how to encode into it, and which codecs it takes as they are, so a
# conversion copies streams whenever it can instead of re-encoding.


@dataclass(frozen=True)
class AudioTarget:
    note: str
    encode: tuple[str, ...]  # -c:a and friends when the audio can't be copied
    copy: frozenset[str] | None = frozenset()  # codecs taken as-is at "best"; None: any
    lossless: bool = False
    bitrate: str = "192"  # kbps at "best" for lossy formats without `best` args
    best: tuple[str, ...] = ()  # args at "best" instead of a bitrate (e.g. VBR)
    fallback: tuple[str, ...] = ()  # built-in encoder for ffmpeg builds missing `encode`'s
    ext: str = ""


AUDIO_TARGETS: dict[str, AudioTarget] = {
    "mp3": AudioTarget(
        "plays everywhere", ("-c:a", "libmp3lame"), _codecs("mp3"), best=("-q:a", "0")
    ),
    "m4a": AudioTarget("AAC, Apple devices", ("-c:a", "aac"), _codecs("aac"), bitrate="256"),
    "aac": AudioTarget("raw AAC stream", ("-c:a", "aac"), _codecs("aac"), bitrate="256"),
    "opus": AudioTarget(
        "small and sharp",
        ("-c:a", "libopus"),
        _codecs("opus"),
        bitrate="160",
        fallback=("-c:a", "opus", "-strict", "-2"),
    ),
    # Homebrew's ffmpeg ships without libvorbis; the built-in encoder is stereo-only.
    "ogg": AudioTarget(
        "Vorbis",
        ("-c:a", "libvorbis"),
        _codecs("vorbis"),
        fallback=("-c:a", "vorbis", "-strict", "-2", "-ac", "2"),
    ),
    "flac": AudioTarget("lossless", ("-c:a", "flac"), _codecs("flac"), lossless=True),
    "alac": AudioTarget(
        "Apple lossless", ("-c:a", "alac"), _codecs("alac"), lossless=True, ext="m4a"
    ),
    "wav": AudioTarget("uncompressed", ("-c:a", "pcm_s16le"), _codecs("pcm_s16le"), lossless=True),
    "aiff": AudioTarget(
        "uncompressed, Apple", ("-c:a", "pcm_s16be"), _codecs("pcm_s16be"), lossless=True
    ),
    "wma": AudioTarget("Windows Media", ("-c:a", "wmav2"), _codecs("wmav2")),
    "ac3": AudioTarget("Dolby Digital", ("-c:a", "ac3"), _codecs("ac3"), bitrate="448"),
    "eac3": AudioTarget("Dolby Digital Plus", ("-c:a", "eac3"), _codecs("eac3"), bitrate="448"),
    "mp2": AudioTarget("MPEG layer 2, broadcast", ("-c:a", "mp2"), _codecs("mp2"), bitrate="256"),
    "wv": AudioTarget("WavPack, lossless", ("-c:a", "wavpack"), _codecs("wavpack"), lossless=True),
    "tta": AudioTarget("True Audio, lossless", ("-c:a", "tta"), _codecs("tta"), lossless=True),
    "caf": AudioTarget("Core Audio, lossless", ("-c:a", "alac"), _codecs("alac"), lossless=True),
    "au": AudioTarget(
        "Sun / NeXT audio", ("-c:a", "pcm_s16be"), _codecs("pcm_s16be"), lossless=True
    ),
    "mka": AudioTarget("Matroska, keeps any audio", ("-c:a", "flac"), None, lossless=True),
}

X264 = ("-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p")
FASTSTART = ("-movflags", "+faststart")


@dataclass(frozen=True)
class VideoTarget:
    note: str
    encode: tuple[str, ...]  # -c:v and friends when the video can't be copied
    audio: str | None  # AUDIO_TARGETS key used to encode the sound; None drops it
    # Codecs the container takes as they are. MP4/MOV keep to H.264/HEVC so the result opens
    # in QuickTime, phones and TVs. None means anything.
    copy_video: frozenset[str] | None = frozenset()
    copy_audio: frozenset[str] | None = frozenset()
    extra: tuple[str, ...] = ()
    ext: str = ""
    animated: bool = False  # GIF-like: no sound, a capped frame rate, small sizes


VIDEO_TARGETS: dict[str, VideoTarget] = {
    "mp4": VideoTarget(
        "plays everywhere",
        X264,
        "m4a",
        _codecs("h264", "hevc"),
        _codecs("aac", "mp3", "alac"),
        FASTSTART,
    ),
    "mkv": VideoTarget("keeps every stream", X264, "m4a", None, None),
    "webm": VideoTarget(
        "open web format",
        ("-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1"),
        "opus",
        _codecs("vp8", "vp9", "av1"),
        _codecs("opus", "vorbis"),
    ),
    "mov": VideoTarget(
        "Apple / editing apps",
        X264,
        "m4a",
        _codecs("h264", "hevc", "prores"),
        _codecs("aac", "alac", "pcm_s16le", "pcm_s24le"),
        FASTSTART,
    ),
    "m4v": VideoTarget(
        "iTunes / Apple TV",
        X264,
        "m4a",
        _codecs("h264", "hevc"),
        _codecs("aac", "ac3", "alac"),
        FASTSTART,
    ),
    "avi": VideoTarget(
        "older players",
        ("-c:v", "mpeg4", "-q:v", "2", "-tag:v", "XVID", "-pix_fmt", "yuv420p"),
        "mp3",
        _codecs("mpeg4", "h264", "mjpeg"),
        _codecs("mp3", "ac3", "pcm_s16le"),
    ),
    "ts": VideoTarget(
        "broadcast / streaming",
        X264,
        "m4a",
        _codecs("h264", "hevc", "mpeg2video"),
        _codecs("aac", "mp3", "ac3", "mp2"),
    ),
    "flv": VideoTarget("Flash video", X264, "m4a", _codecs("h264"), _codecs("aac", "mp3")),
    "wmv": VideoTarget(
        "Windows Media",
        ("-c:v", "wmv2", "-q:v", "2"),
        "wma",
        _codecs("wmv1", "wmv2"),
        _codecs("wmav1", "wmav2"),
    ),
    "mpg": VideoTarget(
        "MPEG-2, DVD players",
        ("-c:v", "mpeg2video", "-q:v", "2", "-pix_fmt", "yuv420p"),
        "mp2",
        _codecs("mpeg1video", "mpeg2video"),
        _codecs("mp2"),
    ),
    "3gp": VideoTarget(
        "old phones",
        ("-c:v", "libx264", "-crf", "23", "-profile:v", "baseline", "-pix_fmt", "yuv420p"),
        "m4a",
        _codecs("h264", "h263"),
        _codecs("aac"),
    ),
    "ogv": VideoTarget(
        "Ogg Theora",
        ("-c:v", "libtheora", "-q:v", "7"),
        "ogg",
        _codecs("theora"),
        _codecs("vorbis", "opus"),
    ),
    "gif": VideoTarget("short clips, no sound", ("-c:v", "gif"), None, animated=True),
    "apng": VideoTarget(
        "animated PNG, no sound",
        ("-c:v", "apng", "-plays", "0", "-f", "apng"),
        None,
        ext="png",
        animated=True,
    ),
    "webp": VideoTarget(
        "animated WebP, no sound",
        ("-c:v", "libwebp_anim", "-loop", "0", "-quality", "80", "-f", "webp"),
        None,
        animated=True,
    ),
}


@dataclass(frozen=True)
class ImageTarget:
    note: str
    encoders: tuple[tuple[str, ...], ...]  # alternatives; the first this ffmpeg has wins
    ext: str = ""


IMAGE_TARGETS: dict[str, ImageTarget] = {
    "jpg": ImageTarget("small, for photos", (("-c:v", "mjpeg", "-q:v", "2"),)),
    "png": ImageTarget("lossless", (("-c:v", "png"),)),
    "webp": ImageTarget("smaller than JPG", (("-c:v", "libwebp", "-quality", "90"),)),
    "avif": ImageTarget(
        "smallest, newer apps",
        (
            ("-c:v", "libaom-av1", "-still-picture", "1", "-crf", "28"),
            ("-c:v", "libsvtav1", "-crf", "30", "-pix_fmt", "yuv420p"),
            ("-c:v", "librav1e", "-qp", "70"),
        ),
    ),
    "jxl": ImageTarget("JPEG XL, newest", ()),  # Pillow writes these two; see PILLOW_ONLY
    "heic": ImageTarget("iPhone photos", ()),
    "jp2": ImageTarget("JPEG 2000, archives", (("-c:v", "libopenjpeg"), ("-c:v", "jpeg2000"))),
    "tiff": ImageTarget("print and editing", (("-c:v", "tiff"),)),
    "bmp": ImageTarget("uncompressed", (("-c:v", "bmp"),)),
    "gif": ImageTarget("256 colours", (("-c:v", "gif"),)),
    "tga": ImageTarget("games and 3D", (("-c:v", "targa"),)),
    "qoi": ImageTarget("fast lossless", (("-c:v", "qoi"),)),
    "exr": ImageTarget("HDR, visual effects", (("-c:v", "exr"),)),
    "hdr": ImageTarget("Radiance HDR", (("-c:v", "hdr"),)),
    "ppm": ImageTarget("plain bitmap", (("-c:v", "ppm"),)),
    "pcx": ImageTarget("retro PC paint", (("-c:v", "pcx"),)),
    "sgi": ImageTarget("Silicon Graphics", (("-c:v", "sgi"),)),
    "dpx": ImageTarget("film scans", (("-c:v", "dpx"),)),
}

# Formats only Pillow (with a plugin) can write: no ffmpeg build Carrot uses has a JPEG XL
# encoder, and ffmpeg can't write HEIF at all. Value: the package that adds the format.
PILLOW_ONLY = {"jxl": "pillow-jxl-plugin", "heic": "pillow-heif"}
PILLOW_PLUGIN_MODULES = {"jxl": "pillow_jxl", "heic": "pillow_heif"}
# Pillow formats that need an optional codec built into Pillow; the rest always ship with it.
PILLOW_FEATURES = {"webp": "webp", "avif": "avif", "jp2": "jpg_2000"}
# Pillow's names for the image formats it can save.
PILLOW_NAMES = {
    "jpg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "avif": "AVIF",
    "jxl": "JXL",
    "heic": "HEIF",
    "jp2": "JPEG2000",
    "tiff": "TIFF",
    "bmp": "BMP",
    "gif": "GIF",
    "tga": "TGA",
    "qoi": "QOI",
    "ppm": "PPM",
    "pcx": "PCX",
    "sgi": "SGI",
}
# Still-image files Pillow reads first. HEIC and AVIF are MP4-style boxes that ffprobe
# reports as a one-frame video, and ffmpeg can't decode JPEG XL without libjxl.
PILLOW_FIRST = {".heic", ".heif", ".hif", ".avif", ".jxl"}

LOCAL_VIDEO_FORMATS = tuple(VIDEO_TARGETS)
LOCAL_AUDIO_FORMATS = tuple(AUDIO_TARGETS)
LOCAL_IMAGE_FORMATS = tuple(IMAGE_TARGETS)


def _encoder(args: tuple[str, ...]) -> str:
    """The encoder named in `-c:v x` / `-c:a x` args."""
    for flag in ("-c:v", "-c:a"):
        if flag in args:
            return args[args.index(flag) + 1]
    return ""


def _pick(options: tuple[tuple[str, ...], ...], has_encoder: HasEncoder) -> tuple[str, ...] | None:
    return next((o for o in options if has_encoder(_encoder(o))), None)


class LocalFileError(Exception):
    """A file ffprobe can't read, or a conversion ffmpeg couldn't finish."""


class ConvertCancelled(Exception):
    pass


# ---------- finding files in pasted text ----------


def parse_paths(text: str, *, windows: bool = sys.platform == "win32") -> list[Path]:
    """Files named by pasted or dropped text, or [] if it isn't only files.

    Terminals paste a dropped file as its path: escaped (`My\\ File.mov`), quoted, or as a
    `file://` URL. Several dropped files arrive as one line separated by spaces.
    """
    text = text.strip()
    if not text:
        return []
    # A single path pasted as-is, spaces and all.
    whole = _to_path(text.strip("'\""), windows)
    if whole is not None and whole.is_file():
        return [whole]
    try:
        parts = shlex.split(text, posix=not windows)
    except ValueError:
        return []
    paths: list[Path] = []
    for part in parts:
        path = _to_path(part.strip('"') if windows else part, windows)
        if path is None or not path.is_file():
            return []  # all or nothing, so stray words never half-load
        if path not in paths:
            paths.append(path)
    return paths


def _to_path(text: str, windows: bool) -> Path | None:
    if not text:
        return None
    if text.lower().startswith("file://"):
        raw = unquote(urlparse(text).path)
        if windows and len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]  # file:///C:/x -> C:/x
        text = raw
    try:
        return Path(text).expanduser()
    except (OSError, ValueError):
        return None


# ---------- reading a file ----------


@dataclass(frozen=True)
class Stream:
    codec: str
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    channels: int | None = None


@dataclass
class FileInfo:
    path: Path
    media: str  # VIDEO, AUDIO, IMAGE or ANIMATED
    container: str
    size: int
    duration: float | None
    video: Stream | None
    audio: Stream | None
    raw: dict = field(repr=False, default_factory=dict)
    reader: str = "ffmpeg"  # or "pillow", for images ffmpeg can't read (or misreads)

    @property
    def title(self) -> str:
        return self.path.name

    @property
    def max_height(self) -> int | None:
        """Short side, so vertical videos get the right quality list (as in probe.py)."""
        v = self.video
        if not v or not v.height:
            return None
        return min(v.width, v.height) if v.width else v.height

    @property
    def long_side(self) -> int | None:
        v = self.video
        if not v:
            return None
        return max(v.width or 0, v.height or 0) or None


def probe_file(path: Path, ffmpeg_dir: str | None = None) -> FileInfo:
    """Read `path` with ffprobe, or Pillow for images ffprobe can't handle.

    Raises LocalFileError if it isn't audio, video or an image.
    """
    if path.suffix.lower() in PILLOW_FIRST and (info := probe_with_pillow(path)):
        return info
    try:
        return _ffprobe(path, ffmpeg_dir)
    except LocalFileError:
        if info := probe_with_pillow(path):
            return info
        raise


def probe_with_pillow(path: Path) -> FileInfo | None:
    try:
        with pillow().open(path) as im:
            fmt = (im.format or path.suffix.lstrip(".")).lower()
            width, height = im.size
    except Exception:
        return None
    return FileInfo(
        path=path,
        media=IMAGE,
        container=fmt,
        size=_file_size(path),
        duration=None,
        video=Stream(fmt, width, height),
        audio=None,
        reader="pillow",
    )


def _ffprobe(path: Path, ffmpeg_dir: str | None) -> FileInfo:
    exe = _exe("ffprobe", ffmpeg_dir)
    cmd = [exe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalFileError(f"Couldn't run ffprobe: {exc}") from exc
    if out.returncode != 0:
        raise LocalFileError(_last_line(out.stderr) or "Can't read this file.")
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise LocalFileError("Can't read this file.") from exc
    return parse_probe(data, path)


def parse_probe(data: dict, path: Path) -> FileInfo:
    """Turn ffprobe's JSON into a FileInfo. Pure, for tests."""
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video_raw = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            and not (s.get("disposition") or {}).get("attached_pic")  # an MP3's cover art
        ),
        None,
    )
    audio_raw = next((s for s in streams if s.get("codec_type") == "audio"), None)
    names = (fmt.get("format_name") or "").split(",")
    duration = _float(fmt.get("duration"))

    if video_raw and any(n.endswith("_pipe") or n in _IMAGE_FORMAT_NAMES for n in names):
        media = IMAGE
    elif video_raw and "gif" in names:
        media = ANIMATED if duration and duration > 0 else IMAGE
    elif video_raw:
        media = VIDEO
    elif audio_raw:
        media = AUDIO
    else:
        raise LocalFileError("Can't read this file. It isn't audio, video or an image.")

    video = None
    if video_raw:
        video = Stream(
            codec=video_raw.get("codec_name") or "",
            width=video_raw.get("width"),
            height=video_raw.get("height"),
            fps=_rate(video_raw.get("avg_frame_rate")),
        )
    audio = None
    if audio_raw:
        audio = Stream(codec=audio_raw.get("codec_name") or "", channels=audio_raw.get("channels"))
    size = int(_float(fmt.get("size")) or 0) or _file_size(path)
    return FileInfo(
        path=path,
        media=media,
        container=names[0] if names else "",
        size=size,
        duration=duration if media in (VIDEO, AUDIO, ANIMATED) else None,
        video=video,
        audio=audio if media != IMAGE else None,
        raw=data,
    )


# ---------- what the Convert dialog can offer ----------


def kinds_for(info: FileInfo) -> list[str]:
    """Convert dialog tabs for this file."""
    if info.media == VIDEO:
        return ["video", "audio", "frames"] if info.audio else ["video", "frames"]
    if info.media == ANIMATED:
        return ["video", "image"]
    if info.media == IMAGE:
        return ["image"]
    return ["audio"]


def formats_for(kind: str) -> tuple[str, ...]:
    if kind == "video":
        return LOCAL_VIDEO_FORMATS
    if kind == "audio":
        return LOCAL_AUDIO_FORMATS
    if kind == "frames":  # ffmpeg writes frames straight out, so no Pillow-only formats
        return tuple(f for f in LOCAL_IMAGE_FORMATS if f not in PILLOW_ONLY)
    return LOCAL_IMAGE_FORMATS


def format_note(kind: str, fmt: str) -> str:
    if kind == "video":
        return VIDEO_TARGETS[fmt].note
    if kind == "audio":
        return AUDIO_TARGETS[fmt].note
    return IMAGE_TARGETS[fmt].note


def missing_encoder(kind: str, fmt: str, has_encoder: HasEncoder = _any_encoder) -> str:
    """The encoder this ffmpeg lacks for `fmt`, or "" when it can write it."""
    if kind == "video":
        target = VIDEO_TARGETS[fmt]
        if not has_encoder(name := _encoder(target.encode)):
            return name
        return missing_encoder("audio", target.audio, has_encoder) if target.audio else ""
    if kind == "audio":
        target = AUDIO_TARGETS[fmt]
        if has_encoder(name := _encoder(target.encode)):
            return ""
        return "" if target.fallback and has_encoder(_encoder(target.fallback)) else name
    if fmt in PILLOW_ONLY:
        return "" if pillow_can_save(fmt) else PILLOW_ONLY[fmt]
    target = IMAGE_TARGETS[fmt]
    if _pick(target.encoders, has_encoder) or (kind == "image" and pillow_can_save(fmt)):
        return ""
    return _encoder(target.encoders[0])


def qualities_for(info: FileInfo, kind: str, fmt: str) -> list[str]:
    if kind == "video":
        # GIF-like formats get small sizes only; "gif" picks that list.
        return video_qualities("gif" if VIDEO_TARGETS[fmt].animated else fmt, info.max_height)
    if kind == "audio":
        return ["best"] if AUDIO_TARGETS[fmt].lossless else ["best", *AUDIO_BITRATES]
    if kind == "frames":
        return list(FRAME_RATES)
    return image_sizes(info.long_side)


def is_fast_copy(info: FileInfo, choice: Choice) -> bool:
    """True when the conversion only repackages streams: seconds, and no quality lost."""
    if choice.kind == "video":
        copy_v, copy_a = _video_copy(info, choice)
        return copy_v and (copy_a or info.audio is None)
    if choice.kind == "audio":
        return _audio_copy(info, choice)
    return False


def _scale_height(info: FileInfo, choice: Choice) -> int | None:
    """Target short side, or None when the video keeps its size."""
    if choice.quality == "best" or not choice.quality.isdigit():
        return None
    h = int(choice.quality)
    return h if not info.max_height or h < info.max_height else None


def _allows(codecs: frozenset[str] | None, codec: str) -> bool:
    return codecs is None or codec in codecs


def _video_copy(info: FileInfo, choice: Choice) -> tuple[bool, bool]:
    target = VIDEO_TARGETS[choice.fmt]
    if target.animated:
        return False, False
    v, a = info.video, info.audio
    copy_v = (
        v is not None
        and info.media == VIDEO
        and _scale_height(info, choice) is None
        and _allows(target.copy_video, v.codec)
    )
    copy_a = a is not None and _allows(target.copy_audio, a.codec)
    return copy_v, copy_a


def _audio_copy(info: FileInfo, choice: Choice) -> bool:
    a = info.audio
    target = AUDIO_TARGETS[choice.fmt]
    return bool(a) and choice.quality == "best" and _allows(target.copy, a.codec)


# ---------- planning the ffmpeg command ----------


def extension(kind: str, fmt: str) -> str:
    if kind == "video":
        return VIDEO_TARGETS[fmt].ext or fmt
    if kind == "audio":
        return AUDIO_TARGETS[fmt].ext or fmt
    return IMAGE_TARGETS[fmt].ext or fmt


def output_name(info: FileInfo, choice: Choice) -> str:
    """File (or, for frames, folder) name for the result, next to the original."""
    stem = info.path.stem
    if choice.kind == "frames":
        return f"{stem} frames"
    return f"{stem}.{extension(choice.kind, choice.fmt)}"


def build_ffmpeg_args(
    info: FileInfo, choice: Choice, out: Path, has_encoder: HasEncoder = _any_encoder
) -> list[str]:
    """ffmpeg arguments (without the executable) that turn `info` into `out`.

    For frames, `out` is a folder; the frames go inside it as `<stem> 0001.png`...
    """
    args = ["-hide_banner", "-nostdin", "-y", "-i", str(info.path)]
    if choice.kind == "video":
        return [*args, *_video_args(info, choice, has_encoder), str(out)]
    if choice.kind == "audio":
        return [*args, *_audio_args(info, choice, has_encoder), str(out)]
    if choice.kind == "image":
        return [*args, *_image_args(choice, has_encoder), str(out)]
    if choice.kind == "frames":
        name = f"{info.path.stem} %04d.{extension('image', choice.fmt)}"
        return [*args, *_frames_args(choice, has_encoder), str(out / name)]
    raise ValueError(f"Unknown kind: {choice.kind}")


def _video_args(info: FileInfo, choice: Choice, has_encoder: HasEncoder) -> list[str]:
    target = VIDEO_TARGETS[choice.fmt]
    if target.animated:
        height = choice.quality if choice.quality.isdigit() else "480"
        if choice.fmt == "gif":
            return ["-vf", gif_filter(height), "-loop", "0", "-an"]
        return ["-vf", f"fps=15,scale=-2:{height}:flags=lanczos", "-an", *target.encode]
    copy_v, copy_a = _video_copy(info, choice)
    args = ["-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "0"]
    if copy_v:
        args += ["-c:v", "copy"]
        if choice.fmt in ("mp4", "mov", "m4v") and info.video and info.video.codec == "hevc":
            args += ["-tag:v", "hvc1"]  # QuickTime only plays HEVC tagged hvc1
    else:
        if (height := _scale_height(info, choice)) is not None:
            # Scale the short side, so vertical videos come out at the picked quality too.
            args += ["-vf", f"scale='if(gt(iw,ih),-2,{height})':'if(gt(iw,ih),{height},-2)'"]
        args += target.encode
    if info.audio and target.audio:
        if copy_a:
            args += ["-c:a", "copy"]
        else:
            args += _audio_encode(AUDIO_TARGETS[target.audio], "best", has_encoder)
    elif info.audio:
        args += ["-an"]
    return [*args, *target.extra]


def _audio_encode(target: AudioTarget, quality: str, has_encoder: HasEncoder) -> list[str]:
    if target.fallback and not has_encoder(_encoder(target.encode)):
        args = list(target.fallback)
    else:
        args = list(target.encode)
    if target.lossless:
        return args
    if quality != "best":
        return [*args, "-b:a", f"{quality}k"]
    return [*args, *target.best] if target.best else [*args, "-b:a", f"{target.bitrate}k"]


def _audio_args(info: FileInfo, choice: Choice, has_encoder: HasEncoder) -> list[str]:
    args = ["-vn", "-map", "0:a:0", "-map_metadata", "0"]
    if _audio_copy(info, choice):
        return [*args, "-c:a", "copy"]
    return [*args, *_audio_encode(AUDIO_TARGETS[choice.fmt], choice.quality, has_encoder)]


def _image_encode(fmt: str, has_encoder: HasEncoder) -> list[str]:
    if fmt in PILLOW_ONLY:
        raise LocalFileError(f"ffmpeg can't write {fmt.upper()}; Carrot Leaf uses Pillow for it.")
    target = IMAGE_TARGETS[fmt]
    return list(_pick(target.encoders, has_encoder) or target.encoders[0])


def _image_args(choice: Choice, has_encoder: HasEncoder) -> list[str]:
    args = ["-map", "0:v:0", "-frames:v", "1", "-update", "1"]
    filters = []
    if choice.quality == "50":
        filters.append("scale=iw/2:-1")
    elif choice.quality.isdigit():
        cap = choice.quality
        # Cap the long side, never enlarge.
        filters.append(f"scale='if(gte(iw,ih),min(iw,{cap}),-1)':'if(gte(iw,ih),-1,min(ih,{cap}))'")
    if choice.fmt == "gif":
        # Pick the 256 colours from the picture itself rather than a fixed web palette.
        filters.append("split[a][b];[a]palettegen[p];[b][p]paletteuse")
    if filters:
        args += ["-vf", ",".join(filters)]
    return [*args, *_image_encode(choice.fmt, has_encoder)]


def _frames_args(choice: Choice, has_encoder: HasEncoder) -> list[str]:
    args = ["-map", "0:v:0", "-an"]
    if choice.quality != "best":
        args += ["-vf", f"fps={choice.quality}"]
    else:
        args += ["-fps_mode", "passthrough"]
    return [*args, *_image_encode(choice.fmt, has_encoder)]


# ---------- running it ----------


def run_convert(
    info: FileInfo,
    choice: Choice,
    on_progress: Callable[[Progress], None],
    cancel_event: threading.Event,
    ffmpeg_dir: str | None = None,
) -> str:
    """Convert `info` per `choice`. Blocks; returns the path of the new file or folder.

    The result lands next to the original and never replaces anything. Raises
    ConvertCancelled soon after `cancel_event` is set.
    """
    from . import deps

    def has_encoder(name: str) -> bool:
        return deps.ffmpeg_has_encoder(ffmpeg_dir, name) if ffmpeg_dir else True

    folder = info.path.parent
    # Write into a private folder first, so a half-written file never sits next to the
    # original under its final name; move the result out at the end.
    work = Path(tempfile.mkdtemp(prefix=".carrot-leaf-", dir=folder))
    try:
        out = work / output_name(info, choice)
        if choice.kind == "frames":
            out.mkdir()
        if choice.kind == "image":
            _convert_image(
                info, choice, out, work, has_encoder, ffmpeg_dir, on_progress, cancel_event
            )
            target = unique_path(folder / out.name)
            shutil.move(str(out), target)
            return str(target)
        args = build_ffmpeg_args(info, choice, out, has_encoder)
        fast = is_fast_copy(info, choice)
        on_progress(Progress(CONVERTING, "Copying" if fast else "Converting", fraction=None))
        _run_ffmpeg(info, args, ffmpeg_dir, on_progress, cancel_event, fast)
        if choice.kind == "frames" and not any(out.iterdir()):
            raise LocalFileError("ffmpeg finished but wrote no frames.")
        if not out.exists():
            raise LocalFileError("ffmpeg finished but no file was produced.")
        target = unique_path(folder / out.name)
        shutil.move(str(out), target)
        return str(target)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _convert_image(info, choice, out, work, has_encoder, ffmpeg_dir, on_progress, cancel_event):
    """One still image: by Pillow, ffmpeg, or both with a lossless PNG in between."""
    on_progress(Progress(CONVERTING, "Converting", fraction=None))
    ffmpeg_can = choice.fmt not in PILLOW_ONLY and _pick(
        IMAGE_TARGETS[choice.fmt].encoders, has_encoder
    )
    # Cheap checks first, so a plain ffmpeg conversion never loads Pillow.
    by_pillow = choice.fmt in PILLOW_ONLY or (
        (info.reader == "pillow" or not ffmpeg_can) and pillow_can_save(choice.fmt)
    )
    if by_pillow:
        source = info.path
        if info.reader != "pillow" and not _pillow_opens(source):
            # e.g. EXR or DPX into JPEG XL: ffmpeg decodes, Pillow encodes.
            source = work / "decoded.png"
            png = Choice("image", "png")
            args = build_ffmpeg_args(info, png, source, has_encoder)
            _run_ffmpeg(info, args, ffmpeg_dir, lambda p: None, cancel_event, False)
        if cancel_event.is_set():
            raise ConvertCancelled("Cancelled")
        save_with_pillow(source, choice, out)
    else:
        if info.reader == "pillow":
            # e.g. HEIC into EXR: Pillow decodes, ffmpeg encodes.
            decoded = work / "decoded.png"
            save_with_pillow(info.path, Choice("image", "png"), decoded)
            info = replace(info, path=decoded, reader="ffmpeg")
        args = build_ffmpeg_args(info, choice, out, has_encoder)
        _run_ffmpeg(info, args, ffmpeg_dir, lambda p: None, cancel_event, False)
    if not out.exists():
        raise LocalFileError("The conversion finished but no file was produced.")
    on_progress(Progress(CONVERTING, "Converting", fraction=1.0))


# ---------- Pillow, for JPEG XL, HEIC and images ffmpeg can't read ----------

# Encoder settings per Pillow format; anything missing uses Pillow's defaults.
PILLOW_SAVE_ARGS = {
    "JPEG": {"quality": 92, "optimize": True},
    "WEBP": {"quality": 90},
    "AVIF": {"quality": 80},
    "JXL": {"quality": 90},
    "HEIF": {"quality": 85},
}
# Formats that keep EXIF and a colour profile through Pillow.
PILLOW_KEEPS_METADATA = {"JPEG", "WEBP", "PNG", "AVIF", "HEIF", "JXL", "TIFF"}


@cache
def pillow():
    """PIL.Image with the HEIF and JPEG XL plugins registered (when they're installed)."""
    from PIL import Image

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except Exception:
        pass
    try:
        import pillow_jxl  # noqa: F401  (registers JXL on import)
    except Exception:
        pass
    Image.init()
    return Image


def pillow_can_save(fmt: str) -> bool:
    if fmt in PILLOW_ONLY:
        # The plugin being installed is enough to know; importing it (and every Pillow
        # format) just to grey out a list entry would cost ~0.1 s and its native libraries.
        return _installed(PILLOW_PLUGIN_MODULES[fmt])
    if feature := PILLOW_FEATURES.get(fmt):
        # Pillow's feature table answers without loading all ~40 of its format plugins.
        import warnings

        from PIL import features

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # an older Pillow that doesn't know the feature
            return bool(features.check(feature))
    name = PILLOW_NAMES.get(fmt)
    return bool(name) and name in pillow().SAVE


@cache
def _installed(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


def _pillow_opens(path: Path) -> bool:
    try:
        with pillow().open(path):
            return True
    except Exception:
        return False


def save_with_pillow(src: Path, choice: Choice, out: Path) -> None:
    from PIL import ImageOps

    name = PILLOW_NAMES[choice.fmt]
    try:
        with pillow().open(src) as opened:
            im = ImageOps.exif_transpose(opened)  # photos come out the right way up
    except Exception as exc:
        raise LocalFileError(f"Can't read this file: {exc}") from exc
    im = _pillow_resize(im, choice.quality)
    im = _pillow_mode(im, name)
    kwargs = dict(PILLOW_SAVE_ARGS.get(name, {}))
    if name in PILLOW_KEEPS_METADATA:
        for key in ("exif", "icc_profile"):
            if im.info.get(key):
                kwargs[key] = im.info[key]
    try:
        im.save(out, name, **kwargs)
    except (TypeError, ValueError, OSError):
        # Some plugins reject metadata they don't support; the picture matters more.
        out.unlink(missing_ok=True)
        try:
            im.save(out, name, **PILLOW_SAVE_ARGS.get(name, {}))
        except Exception as exc:
            raise LocalFileError(f"Couldn't write {choice.fmt.upper()}: {exc}") from exc


def _pillow_resize(im, quality: str):
    from PIL import Image

    width, height = im.size
    if quality == "50":
        size = (max(1, width // 2), max(1, height // 2))
    elif quality.isdigit() and max(width, height) > int(quality):
        scale = int(quality) / max(width, height)
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
    else:
        return im
    return im.resize(size, Image.Resampling.LANCZOS)


def _pillow_mode(im, name: str):
    """A pixel mode `name` can store, flattening transparency onto white where it can't."""
    has_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
    if name == "JPEG" or (name in ("PPM", "PCX") and has_alpha):
        if has_alpha:
            rgba = im.convert("RGBA")
            flat = pillow().new("RGB", rgba.size, "white")
            flat.paste(rgba, mask=rgba.getchannel("A"))
            return flat
        return im if im.mode in ("RGB", "L") else im.convert("RGB")
    if name == "GIF":
        return im
    if im.mode in ("RGB", "RGBA", "L", "LA"):
        return im
    return im.convert("RGBA" if has_alpha else "RGB")


def _run_ffmpeg(info, args, ffmpeg_dir, on_progress, cancel_event, fast) -> None:
    cmd = [_exe("ffmpeg", ffmpeg_dir), "-progress", "pipe:1", "-nostats", "-loglevel", "error"]
    cmd += args
    label = "Copying" if fast else "Converting"
    # stderr goes to a file: a pipe nobody reads can fill up and stall ffmpeg.
    with tempfile.TemporaryFile(mode="w+") as err:
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL, text=True
            )
        except OSError as exc:
            raise LocalFileError(f"Couldn't run ffmpeg: {exc}") from exc
        watcher = threading.Thread(target=_kill_on_cancel, args=(proc, cancel_event), daemon=True)
        watcher.start()
        try:
            assert proc.stdout is not None
            block: dict[str, str] = {}
            for line in proc.stdout:
                key, _, value = line.strip().partition("=")
                block[key] = value
                if key == "progress":  # ffmpeg ends each progress block with this line
                    if p := progress_from(block, info.duration, label):
                        on_progress(p)
                    block = {}
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if cancel_event.is_set():
            raise ConvertCancelled("Cancelled")
        if proc.returncode != 0:
            err.seek(0)
            raise LocalFileError(_last_line(err.read()) or f"ffmpeg failed ({proc.returncode}).")


def _kill_on_cancel(proc: subprocess.Popen, cancel_event: threading.Event) -> None:
    while proc.poll() is None:
        if cancel_event.wait(0.2):
            proc.terminate()
            return


def progress_from(block: dict[str, str], duration: float | None, label: str) -> Progress | None:
    """A Progress from one `-progress` block of key=value lines. Pure, for tests."""
    if block.get("progress") == "end":
        return Progress(CONVERTING, label, fraction=1.0)
    us = block.get("out_time_us") or block.get("out_time_ms")  # both are microseconds
    try:
        done = int(us) / 1_000_000 if us else None
    except ValueError:
        done = None
    if not duration or done is None or done < 0:
        return Progress(CONVERTING, label, fraction=None)
    fraction = min(done / duration, 1.0)
    eta = None
    speed = (block.get("speed") or "").rstrip("x").strip()
    try:
        rate = float(speed)
    except ValueError:
        rate = 0.0
    if rate > 0:
        eta = max(duration - done, 0) / rate
    return Progress(CONVERTING, label, fraction=fraction, eta=eta)


# ---------- helpers ----------


def _exe(name: str, ffmpeg_dir: str | None) -> str:
    if ffmpeg_dir and (found := shutil.which(name, path=ffmpeg_dir)):
        return found
    return shutil.which(name) or name


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rate(value: str | None) -> float | None:
    """ffprobe frame rates look like `30000/1001`."""
    if not value or value in ("0/0",):
        return None
    num, _, den = value.partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""
