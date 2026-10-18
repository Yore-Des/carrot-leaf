"""The card that shows the looked-up video and its Download / Convert buttons."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, ContentSwitcher, LoadingIndicator, Static

from ..engine import errors
from ..engine.probe import VideoInfo
from .thumbnail import Thumbnail

EMPTY_HINT = (
    "[b]Paste a link above[/b] to get started.\n\n"
    "[dim]YouTube and 1,000+ other sites work. Enter loads a link you typed.[/dim]"
)
LEADER = 10  # metadata values line up this many columns after the key


class VideoCard(Vertical):
    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="card-empty", id="card-switcher"):
            yield Static(EMPTY_HINT, id="card-empty")
            with Horizontal(id="card-loading"):
                yield LoadingIndicator()
                yield Static("Looking up video…", id="loading-text")
            with Vertical(id="card-error"):
                yield Static(id="error-message")
                yield Static(id="error-hint")
                with Horizontal(id="error-actions"):
                    yield Button("Use browser cookies", id="use-cookies", variant="primary")
                    yield Button("Update yt-dlp", id="update-ytdlp", variant="primary")
                    yield Button("Try again", id="retry")
            with Horizontal(id="card-info"):
                yield Thumbnail(id="thumb")
                with Vertical(id="details"):
                    yield Static(id="video-title")
                    yield Static(id="video-meta")
                    with Horizontal(id="actions"):
                        yield Button("\\[d] download", id="download")
                        yield Button("\\[c] convert", id="convert")
                        yield Static("best mp4 · pick a format", id="action-hint")

    def on_mount(self) -> None:
        self.border_title = "video"

    def _show(self, name: str) -> None:
        self.query_one(ContentSwitcher).current = name

    def show_empty(self) -> None:
        self._show("card-empty")

    def show_loading(self, url: str) -> None:
        self.query_one("#loading-text", Static).update(f"Looking up [b]{_escape(url)}[/b]…")
        self._show("card-loading")

    def show_error(self, err: errors.FriendlyError) -> None:
        self.query_one("#error-message", Static).update(f"[b $error]✗ {_escape(err.message)}[/]")
        self.query_one("#error-hint", Static).update(_escape(err.hint))
        self.query_one("#use-cookies").display = err.kind == errors.LOGIN
        self.query_one("#update-ytdlp").display = err.kind == errors.OUTDATED
        self.query_one("#retry").display = err.kind in (
            errors.NETWORK,
            errors.OUTDATED,
            errors.GENERIC,
            errors.LOGIN,
        )
        self._show("card-error")

    def show_info(self, info: VideoInfo) -> None:
        self.query_one("#thumb", Thumbnail).image = None
        self.query_one("#video-title", Static).update(f"[b]{_escape(info.title)}[/b]")
        rows = [
            ("channel", info.channel),
            ("length", info.duration_text),
            ("uploaded", info.date_text),
        ]
        if info.max_height:
            rows.append(("quality", f"up to {info.max_height}p"))
        if info.extractor:
            rows.append(("site", info.extractor))
        self.query_one("#video-meta", Static).update(
            "\n".join(
                f"[dim]{key}[/dim] [$foreground-disabled]{'·' * (LEADER - len(key))}[/] {_escape(value)}"
                for key, value in rows
            )
        )
        self._show("card-info")

    def set_thumbnail(self, image) -> None:
        self.query_one("#thumb", Thumbnail).image = image


def _escape(text: str) -> str:
    from rich.markup import escape

    return escape(text or "")
