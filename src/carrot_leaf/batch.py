"""`carrot get` and `carrot convert`: the same downloads and conversions, without the app.

Progress goes to stderr and each saved path to stdout, one per line, so scripts can use
the result: `mpv "$(carrot get URL)"`. Exit status is 0 when everything worked, 1 when
something failed, 2 for a bad command line.
"""

from __future__ import annotations

import argparse
import sys
import threading
from dataclasses import replace
from pathlib import Path

from . import config, history
from .engine import errors, runner
from .engine.options import (
    AUDIO_FORMATS,
    VIDEO_FORMATS,
    Choice,
    Extras,
    parse_time,
    safe_name,
    template_error,
)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="carrot",
        description="Download or convert without opening the app.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  carrot get https://youtu.be/aqz-KE-bpKQ\n"
            "  carrot get URL --format mp3 --quality 320 --normalize\n"
            "  carrot get URL --start 1:30 --end 2:00 --format gif\n"
            "  carrot get PLAYLIST_URL --format m4a --latest 10\n"
            "  carrot convert clip.mov --format mp4\n"
            "  carrot convert photo.heic --format jpg --max-kb 500 --strip-metadata"
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    get = sub.add_parser("get", help="download a video, or every video in a playlist")
    get.add_argument("url", nargs="+", help="one or more links")
    get.add_argument(
        "-f",
        "--format",
        default="mp4",
        choices=[*VIDEO_FORMATS, *AUDIO_FORMATS],
        help="file type (default mp4)",
    )
    get.add_argument(
        "-q", "--quality", default="best", help="best, a height like 720, or a bitrate like 192"
    )
    get.add_argument("-o", "--out", help="folder to save into (default: your Carrot folder)")
    get.add_argument("--name", help="filename template, like '{channel}/{title}'")
    get.add_argument("--subs", choices=["embed", "srt"], help="subtitles inside, or as .srt")
    get.add_argument("--sub-langs", default="en", help="subtitle languages, e.g. en,de")
    get.add_argument("--sponsorblock", choices=["remove", "mark"], help="YouTube sponsor segments")
    get.add_argument("--split-chapters", action="store_true", help="one file per chapter")
    get.add_argument("--latest", type=int, help="only the first N videos of a playlist")
    get.add_argument("--cookies", metavar="BROWSER", help="use this browser's cookies")
    _shared(get)

    conv = sub.add_parser("convert", help="convert files on your computer")
    conv.add_argument("files", nargs="+", type=Path, help="files to convert")
    conv.add_argument("-f", "--format", required=True, help="target format, e.g. mp4, mp3, webp")
    conv.add_argument("-q", "--quality", default="best", help="best, a height, bitrate or size")
    conv.add_argument(
        "--frames", action="store_true", help="save a video's frames as pictures in --format"
    )
    conv.add_argument("--strip-metadata", action="store_true", help="images: drop EXIF and GPS")
    conv.add_argument("--max-kb", type=int, help="images: keep each file under this size")
    _shared(conv)
    return p


def _shared(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start", help="clip from here (1:30, 90 or 1m30s)")
    p.add_argument("--end", help="clip to here")
    p.add_argument("--normalize", action="store_true", help="audio: even out the volume")
    p.add_argument("--quiet", action="store_true", help="no progress, just the saved paths")


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    try:
        start, end = parse_time(args.start or ""), parse_time(args.end or "")
    except ValueError as exc:
        return _usage(str(exc))
    if start is not None and end is not None and end <= start:
        return _usage("--end has to come after --start.")
    extras = Extras(start=start or None, end=end, normalize=args.normalize)
    if args.command == "get":
        return _get(args, extras)
    return _convert(args, extras)


def _usage(message: str) -> int:
    print(f"carrot: {message}", file=sys.stderr)
    return 2


class Printer:
    """One progress line on stderr, redrawn in place."""

    def __init__(self, quiet: bool, label: str) -> None:
        self.quiet, self.label, self.width = quiet or not sys.stderr.isatty(), label, 0

    def __call__(self, p: runner.Progress) -> None:
        if self.quiet:
            return
        from .widgets.queue import human_eta, human_size

        bits = [p.status]
        if p.fraction is not None:
            bits.append(f"{p.fraction * 100:3.0f}%")
        if p.speed:
            bits.append(f"{human_size(p.speed)}/s")
        if p.eta is not None:
            bits.append(f"{human_eta(p.eta)} left")
        line = f"{self.label[:40]}  " + " · ".join(bits)
        sys.stderr.write("\r" + line.ljust(self.width))
        self.width = len(line)
        sys.stderr.flush()

    def done(self, message: str) -> None:
        if not self.quiet:
            sys.stderr.write("\r" + message.ljust(self.width) + "\n")
            sys.stderr.flush()


def _fail(printer: Printer, title: str, exc: BaseException) -> None:
    friendly = errors.explain(exc)
    hint = f" {friendly.hint}" if friendly.hint else ""
    printer.quiet = False  # failures always show
    printer.done(f"✗ {title}: {friendly.message}{hint}")


def _get(args, extras: Extras) -> int:
    from .engine.probe import PlaylistInfo, probe

    settings = config.load()
    fmt = args.format
    kind = "audio" if fmt in AUDIO_FORMATS else "video"
    template = args.name or settings.filename_template
    if problem := template_error(template):
        return _usage(f"--name: {problem}")
    extras = replace(
        extras,
        subtitles=args.subs or "",
        sub_langs=args.sub_langs,
        sponsorblock=args.sponsorblock or "",
        split_chapters=args.split_chapters,
        normalize=args.normalize and kind == "audio",
    )
    choice = Choice(kind, fmt, args.quality if args.quality else "best", extras)
    out = Path(args.out).expanduser() if args.out else settings.download_path
    cookies = args.cookies if args.cookies is not None else settings.cookies_browser

    jobs: list[runner.Job] = []
    failed = 0
    for url in args.url:
        url = url if "://" in url else "https://" + url
        try:
            info = probe(url, cookies_browser=cookies)
        except Exception as exc:
            _fail(Printer(args.quiet, url), url, exc)
            failed += 1
            continue
        if isinstance(info, PlaylistInfo):
            entries = info.entries[: args.latest] if args.latest else info.entries
            width = max(2, len(str(len(info.entries))))
            pl_template = template if "{playlist}" in template else "{playlist}/" + template
            for i, entry in enumerate(entries):
                job = runner.Job(
                    url=entry.url, title=entry.title, choice=choice, template=pl_template
                )
                job.fields = {"playlist": safe_name(info.title), "index": f"{i + 1:0{width}}"}
                jobs.append(job)
        else:
            jobs.append(
                runner.Job(url=info.url, title=info.title, choice=choice, template=template)
            )

    for n, job in enumerate(jobs, 1):
        label = f"[{n}/{len(jobs)}] {job.title}" if len(jobs) > 1 else job.title
        printer = Printer(args.quiet, label)
        try:
            path = runner.run_job(job, out, printer, cookies_browser=cookies)
        except KeyboardInterrupt:
            printer.done("Cancelled.")
            return 130
        except Exception as exc:
            _fail(printer, job.title, exc)
            failed += 1
            continue
        printer.done(f"✓ {job.title}")
        print(path, flush=True)
        history.add(history.Entry(title=job.title, path=path, label=choice.label, source=job.url))
    return 1 if failed or not jobs else 0


def _convert(args, extras: Extras) -> int:
    from .engine import deps, local

    fmt = args.format.lower().lstrip(".")
    extras = replace(extras, strip_metadata=args.strip_metadata, max_kb=args.max_kb)
    try:
        tools = deps.ffmpeg_candidates() or [deps.find_ffmpeg(fetch=True)]
    except Exception as exc:
        return _usage(f"Couldn't get ffmpeg: {exc}")
    dirs = [t.path for t in tools if t]
    failed = 0
    for path in args.files:
        printer = Printer(args.quiet, path.name)
        try:
            info = local.probe_file(path.expanduser(), dirs[0] if dirs else None)
        except Exception as exc:
            _fail(printer, path.name, exc)
            failed += 1
            continue
        kind = _kind_for(info, fmt, args.frames)
        if kind is None:
            printer.quiet = False
            printer.done(f"✗ {path.name}: can't make {fmt.upper()} from this file.")
            failed += 1
            continue
        quality = args.quality
        if quality not in local.qualities_for(info, kind, fmt):
            quality = local.qualities_for(info, kind, fmt)[0]
        choice = Choice(kind, fmt, quality, extras)
        ffmpeg_dir = next(
            (
                d
                for d in dirs
                if not local.missing_encoder(
                    kind, fmt, lambda e, d=d: deps.ffmpeg_has_encoder(d, e)
                )
            ),
            dirs[0] if dirs else None,
        )
        try:
            # Ctrl+C stops it instead; ffmpeg is killed on the way out.
            out = local.run_convert(info, choice, printer, threading.Event(), ffmpeg_dir)
        except KeyboardInterrupt:
            printer.done("Cancelled.")
            return 130
        except Exception as exc:
            _fail(printer, path.name, exc)
            failed += 1
            continue
        printer.done(f"✓ {path.name} → {Path(out).name}")
        print(out, flush=True)
        history.add(
            history.Entry(
                title=path.name, path=out, label=choice.label, source=str(path), kind="convert"
            )
        )
    return 1 if failed else 0


def _kind_for(info, fmt: str, frames: bool) -> str | None:
    from .engine import local

    kinds = local.kinds_for(info)
    if frames:
        return "frames" if "frames" in kinds and fmt in local.formats_for("frames") else None
    # A video file asked for a picture format means its frames only via --frames; an
    # animated GIF to webp means animated WebP, so video formats win where both fit.
    for kind in ("video", "audio", "image"):
        if kind in kinds and fmt in local.formats_for(kind):
            return kind
    return None
