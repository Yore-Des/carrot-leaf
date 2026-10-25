"""The main screen: logo, URL bar, video card and download list."""

from __future__ import annotations

import os
import re
import time
from dataclasses import replace
from pathlib import Path

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical
from textual.events import AppBlur, AppFocus, Paste, Resize
from textual.screen import Screen
from textual.widgets import Button, ContentSwitcher, Input, Static

from .. import config, following, history, logo, presets
from ..engine import desktop, errors, local, runner, update
from ..engine.desktop import reveal
from ..engine.options import DOWNLOAD, Choice, safe_name
from ..engine.probe import (
    PlaylistInfo,
    VideoInfo,
    channel_home_to_videos,
    fetch_thumbnail,
    probe,
)
from ..engine.runner import Job, Progress
from ..widgets.queue import DownloadQueue, JobRow, short_path, throttled
from ..widgets.video_card import VideoCard
from .convert import ConvertScreen, for_playlist, remembered
from .files import FilesPane
from .following import FollowingScreen, GetNew
from .history import HistoryScreen, Redownload
from .playlist import PlaylistScreen
from .search import ClipRequest, SearchScreen
from .settings import CookiesScreen, SettingsScreen
from .sites import SitesScreen

URL_RE = re.compile(r"^\s*(https?://)?([\w-]+\.)+[a-z]{2,}(/\S*)?\s*$", re.I)
DOWNLOAD_TAB, FILES_TAB = "download", "files"
TABS = ((DOWNLOAD_TAB, "Download"), (FILES_TAB, "Convert files"))


# Always shown, unlike Textual's Footer, which hides letter keys while the URL bar has focus.
# Each label is clickable. "{url}" is esc (back to the URL bar) or, while typing in it, tab.
# "{pick}" and "{paste}" show only when there's a playlist or a copied link to act on.
KEY_LIST = (
    ("{paste}", "", ""),
    ("d", "download", "screen.download"),
    ("c", "convert", "screen.convert"),
    ("{pick}", "", ""),
    ("x", "cancel", "screen.cancel_download"),
    ("p", "pause", "screen.pause"),
    ("h", "history", "screen.history"),
    ("w", "following", "screen.following"),
    ("/", "search", "screen.search"),
    ("l", "sites", "screen.sites"),
    ("s", "settings", "screen.settings"),
    ("o", "open", "screen.reveal"),
    ("u", "update", "screen.update"),
    ("{url}", "", ""),
    ("ctrl+t", "files", "screen.switch_tab"),
    ("t", "themes", "app.change_theme"),
    ("q", "quit", "app.quit"),
)
PICK_KEY = ("e", "pick videos", "screen.pick")
PASTE_KEY = ("v", "load copied link", "screen.paste_clipboard")
# A system notification only for queues that took a while; short ones you watched finish.
NOTIFY_AFTER = 20.0
FILES_KEY_LIST = (
    ("c", "convert", "screen.convert"),
    ("f", "browse", "screen.browse"),
    ("r", "remove", "screen.remove_file"),
    ("x", "cancel", "screen.cancel_download"),
    ("o", "open", "screen.reveal"),
    ("h", "history", "screen.history"),
    ("{url}", "", ""),
    ("ctrl+t", "download", "screen.switch_tab"),
    ("t", "themes", "app.change_theme"),
    ("q", "quit", "app.quit"),
)
URL_KEY = ("esc", "url bar", "screen.focus_url")
FILES_INPUT_KEY = ("esc", "file bar", "screen.focus_url")
TAB_KEY = ("tab", "use keys", "screen.leave_url")


def key_bar(
    typing: bool,
    tab: str = DOWNLOAD_TAB,
    *,
    playlist: bool = False,
    clipboard: bool = False,
    paused: bool = False,
) -> str:
    key_list, input_key = (
        (FILES_KEY_LIST, FILES_INPUT_KEY) if tab == FILES_TAB else (KEY_LIST, URL_KEY)
    )
    keys = []
    for k, label, action in key_list:
        if k == "{url}":
            keys.append(TAB_KEY if typing else input_key)
        elif k == "{pick}":
            if playlist:
                keys.append(PICK_KEY)
        elif k == "{paste}":
            if clipboard:
                keys.append(PASTE_KEY)
        elif k == "p":
            keys.append((k, "resume" if paused else label, action))
        else:
            keys.append((k, label, action))
    return "   ".join(
        f"[b $primary]{k}[/] [@click={action}]{label}[/]" for k, label, action in keys
    )


def tab_bar(active: str) -> str:
    bits = []
    for tab, label in TABS:
        if tab == active:
            bits.append(f"[b $background on $primary] {label} [/]")
        else:
            bits.append(f"[@click=screen.show_tab('{tab}')] {label} [/]")
    return "  ".join(bits)


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
        Binding("f", "browse", "Browse files"),
        Binding("r", "remove_file", "Remove file"),
        Binding("e", "pick", "Pick videos"),
        Binding("p", "pause", "Pause queue"),
        Binding("h", "history", "History"),
        Binding("w", "following", "Following"),
        Binding("a", "follow", "Follow"),
        Binding("slash", "search", "Search what was said"),
        Binding("v", "paste_clipboard", "Load copied link"),
        Binding("ctrl+t", "switch_tab", "Switch tab", priority=True),
        Binding("escape", "focus_url", "URL bar"),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self, initial_url: str = "") -> None:
        super().__init__()
        self.initial_url = initial_url
        self.info: VideoInfo | None = None
        self.playlist: PlaylistInfo | None = None
        self.picked: set[int] = set()  # indexes into playlist.entries
        self.loading_url: str | None = None
        self.last_url: str | None = None
        self.probe_token = 0
        self.jobs: list[Job] = []
        self.active: list[Job] = []  # running now; up to settings.parallel_downloads
        self.paused = False  # no new downloads start; running ones finish
        self.last_file: str | None = None
        # A link found on the clipboard, offered once with `v`.
        self.clipboard_url: str | None = None
        self.seen_clipboard: set[str] = set()
        # True/False once the terminal reports focus; None for terminals that never do.
        self.app_focused: bool | None = None
        self.batch: list[Job] = []  # finished since the queue last went quiet
        self.batch_started: float | None = None
        self.new_videos = 0  # waiting in followed channels and playlists
        self.syncing = False
        self.update_version: str | None = None
        self.getting_ffmpeg = False
        self.tab = DOWNLOAD_TAB
        self._debounce = None

    @property
    def settings(self) -> config.Settings:
        return self.app.settings

    @property
    def running(self) -> Job | None:
        """The oldest running download, if any."""
        return self.active[0] if self.active else None

    def compose(self) -> ComposeResult:
        yield Static(id="logo")
        yield Static(id="status-line")
        yield Static(tab_bar(self.tab), id="tabs")
        with ContentSwitcher(initial="pane-download", id="panes"):
            with Vertical(id="pane-download"):
                with Horizontal(id="url-row", classes="source-row"):
                    yield Static("›", classes="prompt")
                    yield Input(
                        placeholder="Paste a video link…  (YouTube, Vimeo, SoundCloud, X, …)",
                        id="url",
                    )
                yield VideoCard(id="card")
                yield DownloadQueue(id="queue")
            yield FilesPane(id="pane-files")
        yield Static(id="keys")

    def on_mount(self) -> None:
        self.query_one("#url-row").border_title = "url"
        self._render_logo(self.size.width)
        self._render_status()
        self._render_keys()
        self.app.theme_changed_signal.subscribe(self, self._theme_changed)
        url_input = self.query_one("#url", Input)
        url_input.focus()
        if self.initial_url and (paths := local.parse_paths(self.initial_url)):
            self.add_files(paths)  # `carrot clip.mov` opens the Convert files tab
        elif self.initial_url:
            url_input.value = self.initial_url
            self.load(self.initial_url)
        else:
            self.check_clipboard()
        self.check_for_update()
        self.prefetch_ffmpeg()
        self.new_videos = following.total_new()
        self._render_status()
        if (items := following.load()) and following.due(items):
            self.sync_following()

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
        self.query_one("#keys", Static).update(
            key_bar(
                isinstance(self.focused, Input),
                self.tab,
                playlist=self.playlist is not None,
                clipboard=self.clipboard_url is not None,
                paused=self.paused,
            )
        )

    def _render_status(self) -> None:
        # Short, important bits first; the folder path goes last so it is what gets cut off.
        from .. import __version__  # looked up here, not at import, to keep startup fast

        bits = [f"[dim]carrot[/dim] {__version__}"]
        if self.app.pending_app_update:
            bits.append(
                f"[b $primary]carrot-leaf {self.app.pending_app_update} installs when you quit[/]"
            )
        if self.update_version:
            bits.append(f"[b $warning]yt-dlp {self.update_version} available · press u[/]")
        else:
            bits.append(f"[dim]yt-dlp[/dim] {update.installed_version()}")
        if self.getting_ffmpeg:
            bits.append("[dim]getting ffmpeg…[/dim]")
        if self.paused:
            bits.append("[b $warning]queue paused · press p[/]")
        if self.new_videos:
            bits.append(f"[b $primary]{self.new_videos} new · press w[/]")
        if self.settings.cookies_browser:
            bits.append(f"[dim]cookies[/dim] {self.settings.cookies_browser}")
        if self.tab == FILES_TAB:
            bits.append("[dim]saving next to each original[/dim]")
        else:
            where = escape(short_path(str(self.settings.download_path)))
            bits.append(f"[dim]saving to[/dim] {where}")
        self.query_one("#status-line", Static).update("  [dim]·[/dim]  ".join(bits))

    def _theme_changed(self, _theme) -> None:
        self._render_logo(self.size.width)

    # ---------- URL handling ----------

    @on(Input.Changed, "#url")
    def _url_changed(self, event: Input.Changed) -> None:
        if self._debounce:
            self._debounce.stop()
        value = event.value.strip()
        if paths := local.parse_paths(value):
            # A file dropped on the Download tab: it belongs on Convert files.
            event.input.value = ""
            self.add_files(paths)
            return
        if looks_like_url(value) and value != self.last_url:
            # Paste arrives as one change; a short wait avoids firing while someone types.
            self._debounce = self.set_timer(0.35, lambda: self.load(value))

    def on_paste(self, event: Paste) -> None:
        # Pasting a link or dropping files works wherever focus is, not only in an input.
        if isinstance(self.focused, Input):
            return
        text = event.text.strip()
        if paths := local.parse_paths(text):
            event.stop()
            self.add_files(paths)
            return
        url_input = self.query_one("#url", Input)
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
        if paths := local.parse_paths(value):
            event.input.value = ""
            self.add_files(paths)
            return
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
        self.playlist = None
        self.seen_clipboard.add(raw)
        self.seen_clipboard.add(url)
        if self.clipboard_url in (raw, url):
            self.clipboard_url = None
        self._render_keys()
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

    def _probe_done(self, token: int, info: VideoInfo | PlaylistInfo) -> None:
        if token != self.probe_token:
            return
        card = self.query_one(VideoCard)
        if isinstance(info, PlaylistInfo):
            self.playlist = info
            self.picked = set(range(len(info.entries)))
            card.show_playlist(info, len(self.picked))
            card.set_follow(following.find(info.url) is not None)
        else:
            self.info = info
            preset = presets.default_for(info.extractor)
            card.show_info(info, preset.name if preset else "")
            if info.channel_url:
                card.set_follow(False)
        self._render_keys()
        self.query_one("#download", Button).focus()

    def _thumb_done(self, token: int, image) -> None:
        if token == self.probe_token:
            self.query_one(VideoCard).set_thumbnail(image)

    def _probe_failed(self, token: int, exc: Exception) -> None:
        if token != self.probe_token:
            return
        self.query_one(VideoCard).show_error(errors.explain(exc))
        self.query_one("#url", Input).focus()

    # ---------- tabs ----------

    @property
    def files(self) -> FilesPane:
        return self.query_one(FilesPane)

    def action_show_tab(self, tab: str) -> None:
        self.tab = tab
        self.query_one("#panes", ContentSwitcher).current = f"pane-{tab}"
        self.query_one("#tabs", Static).update(tab_bar(tab))
        self._render_status()
        self.action_focus_url()

    def action_switch_tab(self) -> None:
        self.action_show_tab(FILES_TAB if self.tab == DOWNLOAD_TAB else DOWNLOAD_TAB)

    def add_files(self, paths: list[Path]) -> None:
        if self.tab != FILES_TAB:
            self.action_show_tab(FILES_TAB)
        self.files.add_paths(paths)

    # ---------- actions ----------

    def action_focus_url(self) -> None:
        if self.tab == FILES_TAB:
            self.files.focus_input()
            self._render_keys()
            return
        url = self.query_one("#url", Input)
        url.focus()
        url.action_select_all()

    def action_browse(self) -> None:
        if self.tab != FILES_TAB:
            self.action_show_tab(FILES_TAB)
        self.files.browse()

    def action_remove_file(self) -> None:
        if self.tab == FILES_TAB:
            self.files.remove()
        else:
            self.app.bell()

    def action_download(self) -> None:
        if self.tab == FILES_TAB:
            self.app.bell()
        elif self.playlist:
            preset = presets.default_for(self.playlist.extractor)
            self.enqueue_playlist(self.playlist, preset.choice() if preset else DOWNLOAD)
        elif self.info:
            preset = presets.default_for(self.info.extractor)
            self.enqueue(self.info, preset.choice() if preset else DOWNLOAD)
        else:
            self.app.bell()

    def action_pick(self) -> None:
        if self.tab == FILES_TAB or not self.playlist:
            self.app.bell()
            return
        playlist = self.playlist

        def picked(chosen: set[int] | None) -> None:
            if chosen is None or playlist is not self.playlist:
                return
            self.picked = chosen
            self.query_one(VideoCard).update_picked(playlist, len(chosen))

        self.app.push_screen(PlaylistScreen(playlist, self.picked), picked)

    def action_convert(self) -> None:
        if self.tab == FILES_TAB:
            self.files.convert()
            return
        if self.playlist:
            self._convert_playlist(self.playlist)
            return
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

    def _convert_playlist(self, playlist: PlaylistInfo) -> None:
        if not self.picked:
            self.notify("No videos picked. Press e to pick some.", severity="warning")
            return
        where = short_path(str(self.settings.download_path / safe_name(playlist.title)))
        youtube = playlist.extractor.lower().startswith("youtube")
        options = for_playlist(playlist.title, len(self.picked), where, youtube)

        def chosen(choice: Choice | None) -> None:
            if choice is None:
                return
            self._remember(choice)
            self.enqueue_playlist(playlist, choice)

        self.app.push_screen(ConvertScreen(options, self.settings), chosen)

    def action_pause(self) -> None:
        if self.tab == FILES_TAB:
            self.app.bell()
            return
        self.paused = not self.paused
        self._render_status()
        self._render_keys()
        self._pump()
        if self.paused:
            self.notify("Running downloads finish; waiting ones hold until you press p.")

    def action_history(self) -> None:
        def closed(result: Redownload | None) -> None:
            if isinstance(result, Redownload):
                if self.tab != DOWNLOAD_TAB:
                    self.action_show_tab(DOWNLOAD_TAB)
                url = self.query_one("#url", Input)
                url.value = result.url
                self.load(result.url)

        self.app.push_screen(HistoryScreen(), closed)

    # ---------- following ----------

    def action_follow(self) -> None:
        """Follow the loaded playlist or channel, or the channel of the loaded video."""
        if self.tab == FILES_TAB:
            self.app.bell()
            return
        if self.playlist:
            feed_url, title = self.playlist.url, self.playlist.title
            count, extractor = len(self.playlist.entries), self.playlist.extractor
        elif self.info and self.info.channel_url:
            feed_url, title, count = self.info.channel_url, self.info.channel, 0
            extractor = self.info.extractor
        else:
            self.app.bell()
            return
        if following.find(feed_url) or (
            self.playlist is None and following.find(channel_home_to_videos(feed_url))
        ):
            self.action_following()
            return
        youtube = extractor.lower().startswith("youtube")
        where = short_path(str(self.settings.download_path / safe_name(title)))
        options = replace(
            for_playlist(title, max(count, 1), where, youtube),
            title=f"{title} · new videos",
            action="follow",
        )
        playlist = self.playlist

        def chosen(choice: Choice | None) -> None:
            if choice is not None:
                self._remember(choice)
                self._start_following(feed_url, playlist, choice)

        self.app.push_screen(ConvertScreen(options, self.settings), chosen)

    @work(thread=True, group="follow")
    def _start_following(self, url: str, feed: PlaylistInfo | None, choice: Choice) -> None:
        try:
            if feed is None:  # a channel, from one of its videos: read what's there now
                found = probe(url, self.settings.cookies_browser, limit=following.SYNC_DEPTH)
                if not isinstance(found, PlaylistInfo):
                    raise ValueError("That channel has no video list Carrot Leaf can follow.")
                feed = found
            item = following.follow(feed, choice)
        except Exception as exc:
            msg = errors.explain(exc).message
            self.app.call_from_thread(self.notify, msg, title="Couldn't follow", severity="error")
            return
        self.app.call_from_thread(self._now_following, item)

    def _now_following(self, item: following.Followed) -> None:
        self.query_one(VideoCard).set_follow(True)
        self.notify(
            f"New videos will be saved as {item.choice().base_label} in "
            f"{short_path(str(self.settings.download_path / safe_name(item.title)))}. "
            "Press w to see what you follow.",
            title=f"Following {item.title}",
            timeout=8,
        )

    def action_following(self) -> None:
        def closed(result: GetNew | None) -> None:
            if isinstance(result, GetNew):
                self.get_new(result.items)

        self.app.push_screen(FollowingScreen(self.sync_following, self.syncing), closed)

    def get_new(self, items: list[following.Followed]) -> None:
        template = self.settings.filename_template
        if "{playlist}" not in template:
            template = "{playlist}/" + template
        count = 0
        for item in items:
            choice = item.choice()
            for entry in following.mark_fetched(item):
                job = Job(url=entry.url, title=entry.title, choice=choice, template=template)
                job.fields = {"playlist": safe_name(item.title), "index": ""}
                self._add_job(job)
                count += 1
            following.update(item)
        self.new_videos = following.total_new()
        self._render_status()
        if self.tab != DOWNLOAD_TAB:
            self.action_show_tab(DOWNLOAD_TAB)
        self._pump()
        if count:
            self.notify(f"Getting {count} new video{'s' if count != 1 else ''}.")

    @work(thread=True, exclusive=True, group="sync")
    def sync_following(self) -> None:
        """Look for new videos in everything followed. Runs at startup and on `s`."""
        self.app.call_from_thread(self._sync_state, True)
        try:
            for item in following.load():
                try:
                    feed = probe(
                        item.url, self.settings.cookies_browser, limit=following.SYNC_DEPTH
                    )
                    if not isinstance(feed, PlaylistInfo):
                        raise ValueError("No longer a list of videos.")
                    following.apply_sync(item, feed)
                except Exception as exc:
                    following.apply_sync(item, None, errors.explain(exc).message)
                following.update(item)
        finally:
            self.app.call_from_thread(self._sync_state, False)

    def _sync_state(self, busy: bool) -> None:
        before = self.new_videos
        self.syncing = busy
        self.new_videos = following.total_new()
        self._render_status()
        if isinstance(self.app.screen, FollowingScreen):
            self.app.screen.refresh_rows(syncing=busy)
        if not busy and self.new_videos > before and self.app_focused is False:
            desktop.notify("New videos", f"{self.new_videos} new in what you follow.")

    # ---------- search ----------

    def action_search(self) -> None:
        def closed(result: ClipRequest | None) -> None:
            if isinstance(result, ClipRequest):
                self.files.clip_file(Path(result.path), result.start, result.end)
                if self.tab != FILES_TAB:
                    self.action_show_tab(FILES_TAB)

        self.app.push_screen(SearchScreen(), closed)

    # ---------- clipboard ----------

    def on_app_focus(self, _event: AppFocus) -> None:
        self.app_focused = True
        self.check_clipboard()

    def on_app_blur(self, _event: AppBlur) -> None:
        self.app_focused = False

    @work(thread=True, exclusive=True, group="clipboard")
    def check_clipboard(self) -> None:
        if not self.settings.watch_clipboard:
            return
        text = desktop.read_clipboard()
        if text and "\n" not in text and looks_like_url(text):
            self.app.call_from_thread(self._clipboard_found, text)

    def _clipboard_found(self, url: str) -> None:
        if url in self.seen_clipboard or url == self.last_url:
            return
        self.seen_clipboard.add(url)
        self.clipboard_url = url
        self._render_keys()
        self.notify(
            f"{escape(url)}\n[dim]Press v to load it.[/dim]",
            title="Link on your clipboard",
            timeout=8,
        )

    def action_paste_clipboard(self) -> None:
        url = self.clipboard_url
        if not url:
            self.app.bell()
            return
        self.clipboard_url = None
        if self.tab != DOWNLOAD_TAB:
            self.action_show_tab(DOWNLOAD_TAB)
        self.query_one("#url", Input).value = url  # fires Input.Changed, which loads it

    def action_settings(self) -> None:
        def saved(new: config.Settings | None) -> None:
            if new is None:
                return
            self.app.save_settings(new)
            self._render_status()
            self._pump()  # more downloads may run at once now
            self.notify(f"Saving to {short_path(new.download_dir)}", title="Settings saved")

        self.app.push_screen(SettingsScreen(self.settings), saved)

    def action_leave_url(self) -> None:
        self.focus_next()

    def action_sites(self) -> None:
        self.app.push_screen(SitesScreen())

    def action_reveal(self) -> None:
        last = self.files.last_file if self.tab == FILES_TAB else self.last_file
        if not last or not Path(last).exists():
            word = "converted" if self.tab == FILES_TAB else "downloaded"
            self.notify(f"Nothing {word} yet.", severity="warning")
            return
        reveal(last)

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

    @work(thread=True, group="ffmpeg-fetch")
    def prefetch_ffmpeg(self) -> None:
        """Download Carrot's own ffmpeg in the background if the installer didn't, so the first
        conversion doesn't wait for it. It adds formats a system ffmpeg may lack."""
        from ..engine import deps

        if os.environ.get("CARROT_NO_FFMPEG_FETCH") or deps.bundled_ffmpeg_ready():
            return
        self.app.call_from_thread(self._getting_ffmpeg, True)
        try:
            deps.fetch_bundled_ffmpeg()
        except Exception:
            pass  # offline, say; conversions fetch it again when they need it
        finally:
            self.app.call_from_thread(self._getting_ffmpeg, False)

    def _getting_ffmpeg(self, busy: bool) -> None:
        self.getting_ffmpeg = busy
        self._render_status()

    def _update_found(self, version: str) -> None:
        self.update_version = version
        self._render_status()

    def _app_update_found(self, version: str) -> None:
        self.app.pending_app_update = version
        self._render_status()

    def _remember(self, choice: Choice) -> None:
        self.app.save_settings(remembered(self.settings, choice))

    # ---------- card buttons ----------

    @on(Button.Pressed, "#download")
    def _download_pressed(self) -> None:
        self.action_download()

    @on(Button.Pressed, "#convert")
    def _convert_pressed(self) -> None:
        self.action_convert()

    @on(Button.Pressed, "#follow")
    def _follow_pressed(self) -> None:
        self.action_follow()

    @on(Button.Pressed, "#pick")
    def _pick_pressed(self) -> None:
        self.action_pick()

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
        self._add_job(job)
        self._pump()

    def enqueue_playlist(self, playlist: PlaylistInfo, choice: Choice) -> None:
        """One download per picked video, into a folder named after the playlist."""
        picked = sorted(self.picked)
        if not picked:
            self.notify("No videos picked. Press e to pick some.", severity="warning")
            return
        template = self.settings.filename_template
        if "{playlist}" not in template:
            template = "{playlist}/" + template
        width = max(2, len(str(len(playlist.entries))))
        for i in picked:
            entry = playlist.entries[i]
            job = Job(url=entry.url, title=entry.title, choice=choice, template=template)
            job.fields = {"playlist": safe_name(playlist.title), "index": f"{i + 1:0{width}}"}
            self._add_job(job)
        self._pump()

    def _add_job(self, job: Job) -> None:
        if not job.fields:
            job.template = self.settings.filename_template
        job.keep_transcript = self.settings.keep_transcripts
        self.jobs.append(job)
        self.query_one(DownloadQueue).add(job)

    def action_cancel_download(self) -> None:
        """Cancel the running download, or the next waiting one if nothing is running."""
        if self.tab == FILES_TAB:
            self.files.cancel_next()
            return
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
        limit = max(1, min(self.settings.parallel_downloads, 4))
        while not self.paused and waiting and len(self.active) < limit:
            job = waiting.pop(0)
            job.state, job.started = runner.PREPARING, time.monotonic()
            if self.batch_started is None:
                self.batch_started = job.started
            self.active.append(job)
            self._run_job(job, self.settings.download_path, self.settings.cookies_browser)
        queue.update_title(len(self.active), len(waiting), paused=self.paused)
        if not self.active and not waiting and self.batch:
            self._batch_done()

    @work(thread=True, group="jobs")
    def _run_job(self, job: Job, download_dir: Path, cookies_browser: str) -> None:
        on_progress = throttled(lambda p: self.app.call_from_thread(self._job_progress, job, p))

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
            history.add(
                history.Entry(
                    title=job.title, path=path or "", label=job.choice.label, source=job.url
                )
            )
        elif job.cancelled:
            job.state = runner.CANCELLED
        else:
            job.state, job.error = runner.FAILED, exc
        if job.state != runner.CANCELLED:
            self.batch.append(job)
        self.query_one(DownloadQueue).refresh_job(job)
        if job in self.active:
            self.active.remove(job)
        self._pump()

    def _batch_done(self) -> None:
        """The queue went quiet: say so on the desktop, if Carrot isn't in front."""
        batch, started = self.batch, self.batch_started
        self.batch, self.batch_started = [], None
        took = time.monotonic() - started if started else 0
        if not self.settings.notify_when_done or self.app_focused is True:
            return
        if self.app_focused is None and took < NOTIFY_AFTER:
            return  # can't tell where the user is; only bother them for long waits
        title, message = batch_summary(batch)
        desktop.notify(title, message)


def batch_summary(jobs: list[Job]) -> tuple[str, str]:
    done = [j for j in jobs if j.state == runner.DONE]
    failed = len(jobs) - len(done)
    if len(jobs) == 1:
        job = jobs[0]
        return ("Downloaded" if done else "Download failed", job.title)
    title = f"{len(done)} download{'s' if len(done) != 1 else ''} finished"
    message = f"{failed} didn't work." if failed else ", ".join(j.title for j in done[:3])
    if not failed and len(done) > 3:
        message += f" and {len(done) - 3} more"
    return title, message


__all__ = ["MainScreen", "reveal"]
