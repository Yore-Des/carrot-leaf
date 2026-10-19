"""The Convert files tab: a batch of files on disk, converted with ffmpeg next to each original."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, OptionList, Static
from textual.widgets.option_list import Option

from ..engine import deps, errors, local, runner
from ..engine.local import ANIMATED, AUDIO, IMAGE, FileInfo
from ..engine.options import Choice
from ..engine.runner import Job, Progress
from ..widgets.queue import DownloadQueue, JobRow, human_size, short_path, throttled
from .convert import ConvertOptions, ConvertScreen, remembered

KIND_ORDER = ("video", "audio", "image", "frames")
EMPTY_HINT = (
    "[b]Drop files above[/b] to convert them.\n\n"
    "[dim]Drag files from Finder or Explorer into this window, or press f to browse. "
    "Video, audio and images all work, and you can add several at once.[/dim]"
)


def duration_text(seconds: float | None) -> str:
    if not seconds:
        return ""
    m, s = divmod(round(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def describe(info: FileInfo) -> str:
    """One line of facts for the file list: `video · 1920×1080 · HEVC · 2:14 · 412 MB`."""
    bits = [{ANIMATED: "animated", AUDIO: "audio", IMAGE: "image"}.get(info.media, "video")]
    v, a = info.video, info.audio
    if v and v.width and v.height and info.media != AUDIO:
        bits.append(f"{v.width}×{v.height}")
    if info.media == local.VIDEO and v:
        bits.append(v.codec.upper())
    elif info.media == AUDIO and a:
        bits.append(a.codec.upper())
    bits += [duration_text(info.duration), human_size(info.size)]
    return " · ".join(b for b in bits if b)


class FilesPane(Vertical):
    """Its own input, file list and conversions queue. Runs one conversion at a time,
    alongside (not instead of) the Download tab's queue."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.files: list[FileInfo] = []
        self.jobs: list[Job] = []
        self.running: Job | None = None
        self.last_file: str | None = None
        self.reading = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="files-row", classes="source-row"):
            yield Static("›", classes="prompt")
            yield Input(placeholder="Drop files here, or press f to browse", id="file-input")
        with Vertical(id="files-card"):
            yield Static(EMPTY_HINT, id="files-empty")
            yield Static(id="files-status")
            yield OptionList(id="files")
            with Horizontal(id="files-actions"):
                yield Button("\\[c] convert all", id="convert-files")
                yield Button("\\[r] remove", id="remove-file")
                yield Static("saves next to each original", id="files-hint")
        yield DownloadQueue(
            title="conversions",
            busy="converting",
            empty="Nothing yet. Conversions show up here.",
            id="conversions",
        )

    def on_mount(self) -> None:
        self.query_one("#files-row").border_title = "files"
        self._render_files()

    def focus_input(self) -> None:
        field = self.query_one("#file-input", Input)
        field.focus()
        field.action_select_all()

    # ---------- adding files ----------

    @on(Input.Changed, "#file-input")
    def _input_changed(self, event: Input.Changed) -> None:
        # A dropped file arrives as a paste of its path; take it as soon as it's a real file.
        if paths := local.parse_paths(event.value):
            event.input.value = ""
            self.add_paths(paths)

    @on(Input.Submitted, "#file-input")
    def _input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            if self.files:
                self.convert()
            return
        paths = local.parse_paths(text)
        if not paths:
            self.notify("Carrot Leaf can't find that file.", severity="warning")
            return
        event.input.value = ""
        self.add_paths(paths)

    def add_paths(self, paths: list[Path]) -> None:
        known = {f.path for f in self.files}
        new = [p for p in paths if p not in known]
        if not new:
            return
        self.reading += len(new)
        self._render_files()
        self._read(new)

    @work(thread=True, group="file-read")
    def _read(self, paths: list[Path]) -> None:
        try:
            if not deps.ffmpeg_candidates():
                self.app.call_from_thread(self._set_status, "Getting ffmpeg (one time, ~50 MB)…")
                deps.fetch_bundled_ffmpeg()
        except Exception as exc:
            self.app.call_from_thread(self._read_failed, None, exc, len(paths))
            return
        probe_dir = next(iter(self.ffmpeg_dirs), None)

        def read(path: Path) -> FileInfo | Exception:
            try:
                return local.probe_file(path, probe_dir)
            except Exception as exc:
                return exc

        # Each read waits on an ffprobe process, so a dropped batch reads a few at a time.
        # map() hands results back in the order the files were dropped.
        with ThreadPoolExecutor(max_workers=min(4, len(paths))) as pool:
            for path, result in zip(paths, pool.map(read, paths), strict=True):
                if isinstance(result, Exception):
                    self.app.call_from_thread(self._read_failed, path, result, 1)
                else:
                    self.app.call_from_thread(self._file_read, result)

    def _file_read(self, info: FileInfo) -> None:
        self.reading -= 1
        if all(f.path != info.path for f in self.files):
            self.files.append(info)
        self._render_files()

    def _read_failed(self, path: Path | None, exc: Exception, count: int) -> None:
        self.reading -= count
        friendly = errors.explain(exc)
        name = f"{path.name}: " if path else ""
        self.notify(f"{name}{friendly.message}", title="Couldn't add file", severity="error")
        self._render_files()

    def _set_status(self, text: str) -> None:
        self.query_one("#files-status", Static).update(f"[dim]{escape(text)}[/dim]")

    def _render_files(self) -> None:
        card = self.query_one("#files-card")
        n = len(self.files)
        card.border_title = f"{n} file{'s' if n != 1 else ''}" if n else "files"
        self.query_one("#files-empty").display = not n and not self.reading
        status = self.query_one("#files-status", Static)
        status.display = bool(self.reading)
        if self.reading:
            self._set_status(f"Reading {self.reading} file{'s' if self.reading != 1 else ''}…")
        files = self.query_one("#files", OptionList)
        files.display = bool(n)
        self.query_one("#files-actions").display = bool(n)
        button = self.query_one("#convert-files", Button)
        label = "\\[c] convert all" if n > 1 else "\\[c] convert"
        if str(button.label) != label.replace("\\", ""):
            button.label = label
            # An auto-width button keeps its old width after a label change; re-measure it.
            button.refresh(layout=True)
            self.query_one("#files-actions").refresh(layout=True)
        width = max((len(f.path.name) for f in self.files), default=0)
        width = min(width, 32)
        keep = files.highlighted
        files.clear_options()
        for f in self.files:
            row = Text.assemble(
                ("▸ ", "bold"),
                (f.path.name[:width].ljust(width), "bold"),
                "  ",
                (describe(f), "dim"),
            )
            files.add_option(Option(row))
        if n:
            files.highlighted = min(keep or 0, n - 1)

    def remove(self) -> None:
        files = self.query_one("#files", OptionList)
        if not self.files or files.highlighted is None:
            self.app.bell()
            return
        self.files.pop(files.highlighted)
        self._render_files()

    def browse(self) -> None:
        def picked(path: Path | None) -> None:
            if path:
                self.add_paths([path])

        self.app.push_screen(FilePickerScreen(), picked)

    @on(Button.Pressed, "#convert-files")
    def _convert_pressed(self) -> None:
        self.convert()

    @on(Button.Pressed, "#remove-file")
    def _remove_pressed(self) -> None:
        self.remove()

    # ---------- converting ----------

    @property
    def ffmpeg_dirs(self) -> list[str]:
        # Looked up each time: the bundled build may finish downloading while Carrot runs.
        return [tool.path for tool in deps.ffmpeg_candidates()]

    def has_encoder(self, name: str) -> bool:
        dirs = self.ffmpeg_dirs
        return not dirs or any(deps.ffmpeg_has_encoder(d, name) for d in dirs)

    def ffmpeg_for(self, kind: str, fmt: str) -> str | None:
        """The ffmpeg that can write `fmt`: the system's if it can, else the bundled one."""
        dirs = self.ffmpeg_dirs
        for d in dirs:
            if not local.missing_encoder(kind, fmt, lambda e, d=d: deps.ffmpeg_has_encoder(d, e)):
                return d
        return dirs[0] if dirs else None

    def convert_options(self) -> ConvertOptions:
        files = list(self.files)
        # Look the ffmpegs up once for the whole dialog, and each format once: the dialog
        # asks about every format at least twice (to draw it, then to pick a starting one).
        dirs = self.ffmpeg_dirs
        seen: dict[tuple[str, str], str] = {}

        def has(encoder: str) -> bool:
            return not dirs or any(deps.ffmpeg_has_encoder(d, encoder) for d in dirs)

        def missing(kind: str, fmt: str) -> str:
            if (kind, fmt) not in seen:
                seen[kind, fmt] = local.missing_encoder(kind, fmt, has)
            return seen[kind, fmt]

        kinds = [k for k in KIND_ORDER if any(k in local.kinds_for(f) for f in files)]

        def applicable(kind: str) -> list[FileInfo]:
            return [f for f in files if kind in local.kinds_for(f)]

        def qualities(kind: str, fmt: str) -> list[str]:
            # Offer what the biggest file has; smaller files simply aren't enlarged.
            biggest = max(applicable(kind), key=lambda f: f.long_side or 0)
            return local.qualities_for(biggest, kind, fmt)

        def note(choice: Choice) -> str:
            targets = applicable(choice.kind)
            bits = []
            if len(targets) < len(files):
                bits.append(f"Applies to {len(targets)} of {len(files)} files.")
            fast = [local.is_fast_copy(f, choice) for f in targets]
            if all(fast):
                bits.append("Fast copy, no quality lost.")
            elif any(fast):
                bits.append("Some files copy fast, others re-encode.")
            else:
                bits.append("Re-encodes, which can take a while.")
            return " ".join(bits)

        folders = {f.path.parent for f in files}
        where = short_path(str(folders.pop())) if len(folders) == 1 else "next to each original"
        title = files[0].path.name if len(files) == 1 else f"{len(files)} files"
        return ConvertOptions(
            title=title,
            kinds=kinds,
            formats={k: local.formats_for(k) for k in kinds},
            note_for=local.format_note,
            missing=missing,
            qualities=qualities,
            where=where,
            action="convert",
            local=True,
            note=note,
        )

    def convert(self) -> None:
        if not self.files:
            self.app.bell()
            return
        files = list(self.files)

        def chosen(choice: Choice | None) -> None:
            if choice is None:
                return
            self.app.save_settings(remembered(self.app.settings, choice))
            for f in files:
                if choice.kind in local.kinds_for(f):
                    self.enqueue(f, choice)

        self.app.push_screen(ConvertScreen(self.convert_options(), self.app.settings), chosen)

    def enqueue(self, info: FileInfo, choice: Choice) -> None:
        job = Job(url=str(info.path), title=info.path.name, choice=choice, file=info)
        self.jobs.append(job)
        self.query_one(DownloadQueue).add(job)
        self._pump()

    def cancel_next(self) -> None:
        """Cancel the running conversion, or the next waiting one if nothing is running."""
        waiting = [j for j in self.jobs if j.state == runner.QUEUED]
        target = self.running or (waiting[0] if waiting else None)
        if target is None:
            self.app.bell()
            return
        self.cancel(target)

    @on(JobRow.CancelRequested)
    def _cancel_requested(self, event: JobRow.CancelRequested) -> None:
        event.stop()  # a conversion, not a download: the screen's handler mustn't see it
        self.cancel(event.job)

    def cancel(self, job: Job) -> None:
        if job.state in runner.FINISHED or job.cancelled:
            return
        job.cancel()
        if job.state == runner.QUEUED:
            job.state = runner.CANCELLED
        self.query_one(DownloadQueue).refresh_job(job)
        self._pump()

    def _pump(self) -> None:
        waiting = [j for j in self.jobs if j.state == runner.QUEUED]
        if self.running is None and waiting:
            self.running = waiting.pop(0)
            self.running.state = runner.PREPARING
            self._run_job(self.running)
        self.query_one(DownloadQueue).update_title(1 if self.running else 0, len(waiting))

    @work(thread=True, group="conversions")
    def _run_job(self, job: Job) -> None:
        on_progress = throttled(lambda p: self.app.call_from_thread(self._job_progress, job, p))
        try:
            path = local.run_convert(
                job.file,
                job.choice,
                on_progress,
                job.cancel_event,
                self.ffmpeg_for(job.choice.kind, job.choice.fmt),
            )
        except Exception as exc:
            self.app.call_from_thread(self._job_finished, job, None, exc)
        else:
            self.app.call_from_thread(self._job_finished, job, path, None)

    def _job_progress(self, job: Job, p: Progress) -> None:
        job.state, job.status, job.fraction, job.speed, job.eta = (
            p.state,
            p.status,
            p.fraction,
            p.speed,
            p.eta,
        )
        self.query_one(DownloadQueue).refresh_job(job)

    def _job_finished(self, job: Job, path: str | None, exc: Exception | None) -> None:
        if exc is None:
            job.state, job.filepath = runner.DONE, path
            self.last_file = path
        elif job.cancelled:
            job.state = runner.CANCELLED
        else:
            job.state, job.error = runner.FAILED, exc
        self.query_one(DownloadQueue).refresh_job(job)
        self.running = None
        self._pump()


class MediaTree(DirectoryTree):
    def filter_paths(self, paths):
        return [p for p in paths if not p.name.startswith(".")]


class FilePickerScreen(ModalScreen[Path | None]):
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, start: Path | None = None) -> None:
        super().__init__()
        self.start = start or Path.home()

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="picker-dialog"):
            yield Static(
                "[b]Pick a file[/b]  [dim]enter opens a folder or picks a file · esc closes[/dim]",
                id="dialog-title",
            )
            yield MediaTree(self.start, id="tree")

    def on_mount(self) -> None:
        self.query_one("#tree").focus()

    @on(DirectoryTree.FileSelected)
    def _picked(self, event: DirectoryTree.FileSelected) -> None:
        self.dismiss(Path(event.path))

    def action_close(self) -> None:
        self.dismiss(None)
