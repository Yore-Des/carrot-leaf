"""Channels and playlists Carrot keeps up with: what's new in each, and getting it."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from .. import following

HELP = (
    "[b $primary]enter[/] get new  [b $primary]a[/] get all new  [b $primary]s[/] sync now  "
    "[b $primary]del[/] unfollow"
)
EMPTY = (
    "[b]You're not following anything yet.[/b]\n\n"
    "[dim]Load a channel or playlist (or any video from one) and press a on its card. "
    "Carrot then checks for new videos each time it starts, and saves them the way "
    "you chose.[/dim]"
)


@dataclass
class GetNew:
    """Dismissed with this to download the waiting videos of these sources."""

    items: list[following.Followed]


class FollowingScreen(ModalScreen[GetNew | None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("a", "get_all", "Get all new", show=False),
        Binding("s", "sync", "Sync now", show=False),
        Binding("delete,backspace", "unfollow", "Unfollow", show=False),
    ]

    def __init__(self, on_sync=None, syncing: bool = False) -> None:
        super().__init__()
        self.on_sync = on_sync  # MainScreen.sync_following, so the status line hears of it
        self.syncing = syncing
        self.items: list[following.Followed] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="following"):
            yield Static(id="dialog-title")
            yield Static(HELP, id="following-help")
            yield OptionList(id="following")
            yield Static(EMPTY, id="following-empty")

    def on_mount(self) -> None:
        self.refresh_rows()
        self.query_one("#following").focus()

    def refresh_rows(self, syncing: bool | None = None) -> None:
        if syncing is not None:
            self.syncing = syncing
        self.items = following.load()
        n = following.total_new(self.items)
        bits = ["[b]Following[/b]"]
        if self.syncing:
            bits.append("[dim]checking for new videos…[/dim]")
        elif n:
            bits.append(f"[b $primary]{n} new[/]")
        self.query_one("#dialog-title", Static).update("  ".join(bits))
        ol = self.query_one("#following", OptionList)
        keep = ol.highlighted
        ol.clear_options()
        width = min(max((len(f.title) for f in self.items), default=0), 28)
        for f in self.items:
            new = len(f.new)
            fmt = f.choice().label
            if f.error:
                state = ("couldn't check", "red")
            elif new:
                state = (f"{new} new", "bold")
            else:
                state = (f"up to date · {f.checked_text}", "dim")
            ol.add_option(
                Option(
                    Text.assemble(
                        ("● " if new else "○ ", "bold" if new else "dim"),
                        (f.title[:width].ljust(width), "bold"),
                        "  ",
                        (state[0].ljust(24), state[1]),
                        (fmt, "dim"),
                    )
                )
            )
        has = bool(self.items)
        ol.display = has
        self.query_one("#following-empty").display = not has
        self.query_one("#following-help").display = has
        if has:
            ol.highlighted = min(keep or 0, len(self.items) - 1)

    @property
    def current(self) -> following.Followed | None:
        ol = self.query_one("#following", OptionList)
        if ol.highlighted is None or ol.highlighted >= len(self.items):
            return None
        return self.items[ol.highlighted]

    @on(OptionList.OptionSelected, "#following")
    def _get_one(self) -> None:
        f = self.current
        if f is None or not f.new:
            self.notify("Nothing new there yet.")
            return
        self.dismiss(GetNew([f]))

    def action_get_all(self) -> None:
        items = [f for f in self.items if f.new]
        if not items:
            self.notify("Nothing new yet.")
            return
        self.dismiss(GetNew(items))

    def action_sync(self) -> None:
        if self.on_sync and not self.syncing:
            self.on_sync()
            self.refresh_rows(syncing=True)

    def action_unfollow(self) -> None:
        f = self.current
        if f is None:
            return
        following.unfollow(f.url)
        self.notify(f"Stopped following {f.title}.")
        self.refresh_rows()

    def action_close(self) -> None:
        self.dismiss(None)
