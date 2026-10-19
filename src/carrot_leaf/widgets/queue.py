"""The download list: one row per job with a progress bar."""

from __future__ import annotations

import time
from collections.abc import Callable

from rich.markup import escape
from rich.style import Style
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, Static

from ..engine import errors, runner
from ..engine.runner import Job, Progress

PROGRESS_INTERVAL = 0.15  # seconds between progress repaints per job


def human_size(n: float | None) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n:.0f} B"
        n /= 1024
    return f"{n:.1f} TB"


def human_eta(seconds: float | None) -> str:
    if seconds is None:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def throttled(deliver: Callable[[Progress], None]) -> Callable[[Progress], None]:
    """Wrap a progress callback so repaints come at most every PROGRESS_INTERVAL.

    A new step or a finished one always gets through: a part that completes in under
    PROGRESS_INTERVAL would otherwise sit at 0% on screen.
    """
    last: dict = {"t": 0.0, "step": None}

    def on_progress(p: Progress) -> None:
        now = time.monotonic()
        step = (p.state, p.status)
        finished = p.fraction is not None and p.fraction >= 1
        if step == last["step"] and not finished and now - last["t"] < PROGRESS_INTERVAL:
            return
        last.update(t=now, step=step)
        deliver(p)

    return on_progress


def short_path(path: str) -> str:
    from pathlib import Path

    p, home = Path(path), Path.home()
    try:
        return "~/" + str(p.relative_to(home)).replace("\\", "/")
    except ValueError:
        return str(p)


class BlockBar(Widget):
    """A progress bar drawn with full-height blocks. `fraction` None means "busy", and animates."""

    DEFAULT_CSS = """
    BlockBar {
        width: 30;
        height: 1;
        color: $primary;
    }
    """
    COMPONENT_CLASSES = {"block-bar--track"}

    fraction: reactive[float | None] = reactive(0.0)
    _tick = reactive(0)

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.08, self._advance, pause=True)
        self.watch_fraction(self.fraction)

    def watch_fraction(self, fraction: float | None) -> None:
        timer = getattr(self, "_timer", None)
        if timer is not None:
            timer.pause() if fraction is not None else timer.resume()

    def _advance(self) -> None:
        self._tick += 1

    def render(self) -> Text:
        width = self.size.width
        fill = Style(color=self.rich_style.color)
        track = Style(color=self.get_component_rich_style("block-bar--track").color)
        cells = [False] * width
        if self.fraction is None:
            span = max(4, width // 5)
            start = self._tick % (width + span) - span
            for i in range(max(0, start), min(width, start + span)):
                cells[i] = True
        else:
            for i in range(round(width * min(max(self.fraction, 0.0), 1.0))):
                cells[i] = True
        out = Text(no_wrap=True)
        for filled in cells:
            out.append("█", fill if filled else track)
        return out


class JobRow(Vertical):
    DEFAULT_CSS = "JobRow { height: auto; }"

    class CancelRequested(Message):
        def __init__(self, job: Job) -> None:
            super().__init__()
            self.job = job

    def __init__(self, job: Job) -> None:
        super().__init__(id=f"job-{job.id}")
        self.job = job

    def compose(self) -> ComposeResult:
        with Horizontal(classes="job-head"):
            yield Static(classes="job-mark")
            yield Static(classes="job-title")
            yield Static(classes="job-kind")
            yield Button("cancel", classes="job-cancel")
        with Horizontal(classes="job-body"):
            yield BlockBar()
            yield Static(classes="job-status")

    def on_mount(self) -> None:
        self.refresh_job()

    def refresh_job(self) -> None:
        job = self.job
        self.query_one(".job-title", Static).update(escape(job.title))
        kind = job.choice.label
        if job.file is not None:
            kind = f"{job.file.path.suffix.lstrip('.').upper() or 'FILE'} → {kind}"
        self.query_one(".job-kind", Static).update(escape(kind))
        bar = self.query_one(BlockBar)
        status = self.query_one(".job-status", Static)
        self.set_class(job.state == runner.DONE, "-done")
        self.set_class(job.state == runner.FAILED, "-failed")
        self.set_class(job.state == runner.CANCELLED, "-cancelled")
        self.set_class(job.state not in (*runner.FINISHED, runner.QUEUED), "-running")
        mark = {runner.DONE: "✓", runner.FAILED: "✗", runner.CANCELLED: "–", runner.QUEUED: "·"}
        busy = "⟳" if job.file is not None else "↓"
        self.query_one(".job-mark", Static).update(mark.get(job.state, busy))
        cancel = self.query_one(".job-cancel", Button)
        cancel.visible = job.state not in runner.FINISHED  # keeps its space so columns line up
        cancel.disabled = job.cancelled

        if job.state == runner.DONE:
            bar.fraction = 1.0
            status.update(f"[b $success]✓ Saved[/] → {escape(short_path(job.filepath or ''))}")
        elif job.state == runner.CANCELLED:
            bar.fraction = 0.0
            status.update("[dim]Cancelled[/dim]")
        elif job.cancelled:
            bar.fraction = None
            status.update("[dim]Cancelling…[/dim]")
        elif job.state == runner.FAILED:
            bar.fraction = 0.0
            friendly = errors.explain(job.error or "")
            hint = f" [dim]{escape(friendly.hint)}[/dim]" if friendly.hint else ""
            if friendly.kind == errors.LOGIN:
                hint = " [dim]Press s to choose a browser for cookies, then try again.[/dim]"
            status.update(f"[b $error]✗ {escape(friendly.message)}[/]{hint}")
        elif job.state == runner.QUEUED:
            bar.fraction = 0.0
            status.update("[dim]Waiting in line[/dim]")
        elif job.fraction is None:
            bar.fraction = None
            status.update(escape(job.status) + "…")
        else:
            bar.fraction = job.fraction
            bits = [job.status, f"{job.fraction * 100:.0f}%"]
            if job.speed:
                bits.append(f"{human_size(job.speed)}/s")
            if job.eta is not None:
                bits.append(f"{human_eta(job.eta)} left")
            status.update(escape(" · ".join(bits)))

    @on(Button.Pressed, ".job-cancel")
    def _cancel_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(self.CancelRequested(self.job))


class DownloadQueue(VerticalScroll):
    """The job list. Also used for conversions, with its own words."""

    def __init__(
        self,
        title: str = "downloads",
        busy: str = "downloading",
        empty: str = "Nothing yet. Downloads show up here.",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.queue_title, self.busy, self.empty = title, busy, empty

    def compose(self) -> ComposeResult:
        yield Static(self.empty, classes="queue-empty")

    def on_mount(self) -> None:
        self.border_title = self.queue_title

    def add(self, job: Job) -> None:
        self.query_one(".queue-empty").display = False
        row = JobRow(job)
        self.mount(row, before=0)
        self.scroll_home(animate=False)

    def refresh_job(self, job: Job) -> None:
        try:
            self.query_one(f"#job-{job.id}", JobRow).refresh_job()
        except Exception:
            pass

    def update_title(self, running: int, queued: int) -> None:
        bits = []
        if running:
            bits.append(f"1 {self.busy}")
        if queued:
            bits.append(f"{queued} waiting")
        self.border_title = self.queue_title + (f" · {', '.join(bits)}" if bits else "")
