"""The download list: one row per job with a progress bar."""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import ProgressBar, Static

from ..engine import errors, runner
from ..engine.runner import Job


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


def short_path(path: str) -> str:
    from pathlib import Path

    p, home = Path(path), Path.home()
    try:
        return "~/" + str(p.relative_to(home)).replace("\\", "/")
    except ValueError:
        return str(p)


class JobRow(Vertical):
    DEFAULT_CSS = "JobRow { height: auto; }"

    def __init__(self, job: Job) -> None:
        super().__init__(id=f"job-{job.id}")
        self.job = job

    def compose(self) -> ComposeResult:
        with Horizontal(classes="job-head"):
            yield Static(classes="job-title")
            yield Static(classes="job-kind")
        with Horizontal(classes="job-body"):
            yield ProgressBar(total=100, show_eta=False, show_percentage=False)
            yield Static(classes="job-status")

    def on_mount(self) -> None:
        self.refresh_job()

    def refresh_job(self) -> None:
        job = self.job
        self.query_one(".job-title", Static).update(escape(job.title))
        self.query_one(".job-kind", Static).update(escape(job.choice.label))
        bar = self.query_one(ProgressBar)
        status = self.query_one(".job-status", Static)
        self.set_class(job.state == runner.DONE, "-done")
        self.set_class(job.state == runner.FAILED, "-failed")

        if job.state == runner.DONE:
            bar.update(total=100, progress=100)
            status.update(f"[b $success]✓ Saved[/] → {escape(short_path(job.filepath or ''))}")
        elif job.state == runner.FAILED:
            bar.update(total=100, progress=0)
            friendly = errors.explain(job.error or "")
            hint = f" [dim]{escape(friendly.hint)}[/dim]" if friendly.hint else ""
            if friendly.kind == errors.LOGIN:
                hint = " [dim]Press s to choose a browser for cookies, then try again.[/dim]"
            status.update(f"[b $error]✗ {escape(friendly.message)}[/]{hint}")
        elif job.state == runner.QUEUED:
            bar.update(total=100, progress=0)
            status.update("[dim]Waiting in line[/dim]")
        elif job.fraction is None:
            bar.update(total=None)
            status.update(escape(job.status) + "…")
        else:
            bar.update(total=100, progress=job.fraction * 100)
            bits = [job.status, f"{job.fraction * 100:.0f}%"]
            if job.speed:
                bits.append(f"{human_size(job.speed)}/s")
            if job.eta is not None:
                bits.append(f"{human_eta(job.eta)} left")
            status.update(escape(" · ".join(bits)))


class DownloadQueue(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static("[dim]Nothing yet. Downloads show up here.[/dim]", id="queue-empty")

    def on_mount(self) -> None:
        self.border_title = "Downloads"

    def add(self, job: Job) -> None:
        self.query_one("#queue-empty").display = False
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
            bits.append("1 downloading")
        if queued:
            bits.append(f"{queued} waiting")
        self.border_title = "Downloads" + (f" · {', '.join(bits)}" if bits else "")
