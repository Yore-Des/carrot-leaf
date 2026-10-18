"""The main screen: logo, URL bar, video card and download list."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal
from textual.events import Paste, Resize
from textual.screen import Screen
from textual.widgets import Button, Input, Static

from .. import config, logo
from ..engine import errors, runner, update
from ..engine.options import DOWNLOAD, Choice
from ..engine.probe import VideoInfo, fetch_thumbnail, probe
from ..engine.runner import Job, Progress
from ..widgets.queue import DownloadQueue, JobRow, short_path
from ..widgets.video_card import VideoCard
from .convert import ConvertScreen
from .settings import CookiesScreen, SettingsScreen
from .sites import SitesScreen

URL_RE = re.compile(r"^\s*(https?://)?([\w-]+\.)+[a-z]{2,}(/\S*)?\s*$", re.I)
PROGRESS_INTERVAL = 0.15  # seconds between progress repaints per job


# Always shown, unlike Textual's Footer, which hides letter keys while the URL bar has focus.
# Each label is clickable. "{url}" is esc (back to the URL bar) or, while typing in it, tab.
KEY_LIST = (
    ("d", "download", "screen.download"),
    ("c", "convert", "screen.convert"),
    ("x", "cancel", "screen.cancel_download"),
    ("l", "sites", "screen.sites"),
    ("s", "settings", "screen.settings"),
    ("o", "open", "screen.reveal"),
    ("u", "update", "screen.update"),
    ("{url}", "", ""),
    ("t", "themes", "app.change_theme"),
    ("q", "quit", "app.quit"),
)
URL_KEY = ("esc", "url bar", "screen.focus_url")
TAB_KEY = ("tab", "use keys", "screen.leave_url")


def key_bar(typing: bool) -> str:
    keys = [
        (TAB_KEY if typing else URL_KEY) if k == "{url}" else (k, label, action)
        for k, label, action in KEY_LIST
    ]
    return "  ".join(f"[b $primary]{k}[/] [@click={action}]{label}[/]" for k, label, action in keys)


def rich_color(value: str | None, fallback: str) -> str:
    """A theme colour as a Rich colour name. ANSI themes use names like `ansi_blue` Rich can't read."""
    try:
        return Color.parse(value).rich_color.name
    except Exception:
        return fallback


def looks_like_url(text: str) -> bool:
    return bool(URL_RE.match(text)) and "." in text


class MainScreen(Screen):
    BINDINGS = [
        Binding("d", "download", "Download"),
        Binding("c", "convert", "Convert"),
        Binding("l", "sites", "Sites"),
        Binding("s", "settings", "Settings"),
        Binding("t", "app.change_theme", "Themes"),
        Binding("x", "cancel_download", "Cancel download"),
        Binding("o", "reveal", "Show file"),
        Binding("u", "update", "Update yt-dlp"),
        Binding("escape", "focus_url", "URL bar"),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self, initial_url: str = "") -> None:
        super().__init__()
        self.initial_url = initial_url
        self.info: VideoInfo | None = None
        self.loading_url: str | None = None
        self.last_url: str | None = None
        self.probe_token = 0
        self.jobs: list[Job] = []
        self.running: Job | None = None
        self.last_file: str | None = None
        self.update_version: str | None = None
        self._debounce = None

    @property
    def settings(self) -> config.Settings:
        return self.app.settings

    def compose(self) -> ComposeResult:
        yield Static(id="logo")
        yield Static(id="status-line")
        with Horizontal(id="url-row"):
            yield Static("›", id="prompt")
            yield Input(
                placeholder="Paste a video link…  (YouTube, Vimeo, SoundCloud, X, …)", id="url"
            )
        yield VideoCard(id="card")
        yield DownloadQueue(id="queue")
        yield Static(id="keys")

    def on_mount(self) -> None:
        self.query_one("#url-row").border_title = "url"
        self._render_logo(self.size.width)
        self._render_status()
        self._render_keys()
        self.app.theme_changed_signal.subscribe(self, self._theme_changed)
        url_input = self.query_one("#url", Input)
        url_input.focus()
        if self.initial_url:
            url_input.value = self.initial_url
            self.load(self.initial_url)
        self.check_for_update()

    # ---------- layout bits ----------

    def on_resize(self, event: Resize) -> None:
        self._render_logo(event.size.width)
        self.set_class(event.size.height < 30, "-short")

    def _render_logo(self, width: int) -> None:
        colors = self.app.theme_variables
        steps = ("primary-lighten-1", "primary", "primary", "primary-darken-1", "primary-darken-2")
        shadow = "primary-darken-3" if self.app.current_theme.dark else "primary-lighten-2"
        self.query_one("#logo", Static).update(
            logo.render(
                width - 4,
                gradient=[rich_color(colors.get(step), "#ff8700") for step in steps],
                shadow=rich_color(colors.get(shadow), "#af5f00"),
            )
        )

    def on_descendant_focus(self) -> None:
        self._render_keys()

    def on_descendant_blur(self) -> None:
        self._render_keys()

    def _render_keys(self) -> None:
        # Letters type into the URL bar while it has focus, so say how to reach the shortcuts.
        self.query_one("#keys", Static).update(key_bar(isinstance(self.focused, Input)))

    def _render_status(self) -> None:
        # Short, important bits first; the folder path goes last so it is what gets cut off.
        bits = []
        if self.app.pending_app_update:
            bits.append(
                f"[b $primary]carrot-leaf {self.app.pending_app_update} installs when you quit[/]"
            )
        if self.update_version:
            bits.append(f"[b $warning]yt-dlp {self.update_version} available · press u[/]")
        else:
            bits.append(f"[dim]yt-dlp[/dim] {update.installed_version()}")
        if self.settings.cookies_browser:
            bits.append(f"[dim]cookies[/dim] {self.settings.cookies_browser}")
        bits.append(f"[dim]saving to[/dim] {escape(short_path(str(self.settings.download_path)))}")
        self.query_one("#status-line", Static).update("  [dim]·[/dim]  ".join(bits))

    def _theme_changed(self, _theme) -> None:
        self._render_logo(self.size.width)

    # ---------- URL handling ----------

    @on(Input.Changed, "#url")
    def _url_changed(self, event: Input.Changed) -> None:
        if self._debounce:
            self._debounce.stop()
        value = event.value.strip()
        if looks_like_url(value) and value != self.last_url:
            # Paste arrives as one change; a short wait avoids firing while someone types.
            self._debounce = self.set_timer(0.35, lambda: self.load(value))

    def on_paste(self, event: Paste) -> None:
        # Pasting a link works wherever focus is, not only in the URL bar.
        url_input = self.query_one("#url", Input)
        if self.focused is url_input:
            return
        text = event.text.strip()
        if looks_like_url(text):
            event.stop()
            url_input.value = text  # fires Input.Changed, which loads it

    @on(Input.Submitted, "#url")
    def _url_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return
        if self._debounce:
            self._debounce.stop()
        if value == self.last_url and self.info:
            self.query_one("#download", Button).focus()
            return
        self.load(value)

    def load(self, raw: str) -> None:
        raw = raw.strip()
        self.last_url = raw
        url = raw if "://" in raw else "https://" + raw
        self.loading_url = url
        self.info = None
        self.probe_token += 1
        self.query_one(VideoCard).show_loading(url)
        self._probe(url, self.probe_token)

    @work(thread=True, group="probe")
    def _probe(self, url: str, token: int) -> None:
        try:
            info = probe(url, cookies_browser=self.settings.cookies_browser)
        except Exception as exc:  # yt-dlp raises many types; all become a friendly card
            self.app.call_from_thread(self._probe_failed, token, exc)
            return
        self.app.call_from_thread(self._probe_done, token, info)
        image = fetch_thumbnail(info.thumbnail) if info.thumbnail else None
        if image is not None:
            self.app.call_from_thread(self._thumb_done, token, image)

    def _probe_done(self, token: int, info: VideoInfo) -> None:
        if token != self.probe_token:
            return
        self.info = info
        self.query_one(VideoCard).show_info(info)
        self.query_one("#download", Button).focus()

    def _thumb_done(self, token: int, image) -> None:
        if token == self.probe_token:
            self.query_one(VideoCard).set_thumbnail(image)

    def _probe_failed(self, token: int, exc: Exception) -> None:
        if token != self.probe_token:
            return
        self.query_one(VideoCard).show_error(errors.explain(exc))
        self.query_one("#url", Input).focus()

    # ---------- actions ----------

    def action_focus_url(self) -> None:
        url = self.query_one("#url", Input)
        url.focus()
        url.action_select_all()

    def action_download(self) -> None:
        if self.info:
            self.enqueue(self.info, DOWNLOAD)
        else:
            self.app.bell()

    def action_convert(self) -> None:
        if not self.info:
            self.app.bell()
            return
        info = self.info

        def chosen(choice: Choice | None) -> None:
            if choice is None:
                return
            self._remember(choice)
            self.enqueue(info, choice)

        self.app.push_screen(ConvertScreen(info, self.settings), chosen)

    def action_settings(self) -> None:
        def saved(new: config.Settings | None) -> None:
            if new is None:
                return
            self.app.save_settings(new)
            self._render_status()
            self.notify(f"Saving to {short_path(new.download_dir)}", title="Settings saved")

        self.app.push_screen(SettingsScreen(self.settings), saved)

    def action_leave_url(self) -> None:
        self.focus_next()

    def action_sites(self) -> None:
        self.app.push_screen(SitesScreen())

    def action_reveal(self) -> None:
        if not self.last_file or not Path(self.last_file).exists():
            self.notify("Nothing downloaded yet.", severity="warning")
            return
        reveal(self.last_file)

    def action_update(self) -> None:
        self.notify("Updating yt-dlp…", title="Update")
        self._run_update()

    @work(thread=True, exclusive=True, group="update")
    def _run_update(self) -> None:
        ok, output = update.upgrade()
        self.app.call_from_thread(self._update_finished, ok, output)

    def _update_finished(self, ok: bool, output: str) -> None:
        if ok:
            self.update_version = None
            self._render_status()
            self.notify("Restart Carrot Leaf to use the new version.", title="yt-dlp updated")
        else:
            last = output.strip().splitlines()[-1] if output.strip() else "unknown error"
            self.notify(f"Update failed: {last}", severity="error", timeout=10)

    @work(thread=True, group="update-check")
    def check_for_update(self) -> None:
        if os.environ.get("CARROT_NO_UPDATE_CHECK"):
            return
        newer = update.update_available()
        if newer:
            self.app.call_from_thread(self._update_found, newer)
        app_newer = update.app_update_available()
        if app_newer:
            self.app.call_from_thread(self._app_update_found, app_newer)

    def _update_found(self, version: str) -> None:
        self.update_version = version
        self._render_status()

    def _app_update_found(self, version: str) -> None:
        self.app.pending_app_update = version
        self._render_status()

    def _remember(self, choice: Choice) -> None:
        s = replace(self.settings, last_kind=choice.kind)
        if choice.kind == "video":
            s = replace(s, last_video_format=choice.fmt, last_video_quality=choice.quality)
        else:
            s = replace(s, last_audio_format=choice.fmt, last_audio_quality=choice.quality)
        self.app.save_settings(s)

    # ---------- card buttons ----------

    @on(Button.Pressed, "#download")
    def _download_pressed(self) -> None:
        self.action_download()

    @on(Button.Pressed, "#convert")
    def _convert_pressed(self) -> None:
        self.action_convert()

    @on(Button.Pressed, "#update-ytdlp")
    def _update_pressed(self) -> None:
        self.action_update()

    @on(Button.Pressed, "#retry")
    def _retry_pressed(self) -> None:
        if self.loading_url:
            self.load(self.loading_url)

    @on(Button.Pressed, "#use-cookies")
    def _cookies_pressed(self) -> None:
        def picked(browser: str | None) -> None:
            if not browser:
                return
            self.app.save_settings(replace(self.settings, cookies_browser=browser))
            self._render_status()
            if self.loading_url:
                self.load(self.loading_url)

        self.app.push_screen(CookiesScreen(), picked)

    # ---------- queue ----------

    def enqueue(self, info: VideoInfo, choice: Choice) -> None:
        job = Job(url=info.url, title=info.title, choice=choice)
        self.jobs.append(job)
        self.query_one(DownloadQueue).add(job)
        self._pump()

    def action_cancel_download(self) -> None:
        """Cancel the running download, or the next waiting one if nothing is running."""
        waiting = [j for j in self.jobs if j.state == runner.QUEUED]
        target = self.running or (waiting[0] if waiting else None)
        if target is None:
            self.app.bell()
            return
        self.cancel(target)

    @on(JobRow.CancelRequested)
    def _cancel_requested(self, event: JobRow.CancelRequested) -> None:
        self.cancel(event.job)

    def cancel(self, job: Job) -> None:
        if job.state in runner.FINISHED or job.cancelled:
            return
        job.cancel()
        if job.state == runner.QUEUED:
            # Never started: drop it now. A running job stops at its next progress update.
            job.state = runner.CANCELLED
        self.query_one(DownloadQueue).refresh_job(job)
        self._pump()

    def _pump(self) -> None:
        queue = self.query_one(DownloadQueue)
        waiting = [j for j in self.jobs if j.state == runner.QUEUED]
        if self.running is None and waiting:
            self.running = waiting.pop(0)
            self.running.state = runner.PREPARING
            self._run_job(self.running, self.settings.download_path, self.settings.cookies_browser)
        queue.update_title(1 if self.running else 0, len(waiting))

    @work(thread=True, group="jobs")
    def _run_job(self, job: Job, download_dir: Path, cookies_browser: str) -> None:
        last = {"t": 0.0, "state": None}

        def on_progress(p: Progress) -> None:
            # Throttle repaints, but never drop a new step or a finished one: a part that
            # downloads in under PROGRESS_INTERVAL would otherwise sit at 0% on screen.
            now = time.monotonic()
            step = (p.state, p.status)
            finished = p.fraction is not None and p.fraction >= 1
            if step == last["state"] and not finished and now - last["t"] < PROGRESS_INTERVAL:
                return
            last.update(t=now, state=step)
            self.app.call_from_thread(self._job_progress, job, p)

        try:
            path = runner.run_job(job, download_dir, on_progress, cookies_browser=cookies_browser)
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


def reveal(path: str) -> None:
    """Show a file in Finder / Explorer / the default file manager."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", path])
        else:
            subprocess.Popen(["xdg-open", str(Path(path).parent)])
    except OSError:
        pass
