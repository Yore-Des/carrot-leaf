"""Everything saved so far: open it, find it, copy its link or get it again."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from .. import history
from ..engine import desktop


@dataclass
class Redownload:
    """What the screen dismisses with when the user wants a link again."""

    url: str


HELP = (
    "[b $primary]enter[/] open  [b $primary]o[/] show in folder  "
    "[b $primary]r[/] get again  [b $primary]y[/] copy link  [b $primary]del[/] forget"
)


class HistoryScreen(ModalScreen[Redownload | None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("o", "reveal", "Show in folder", show=False),
        Binding("r", "redownload", "Get again", show=False),
        Binding("y", "copy", "Copy link", show=False),
        Binding("delete,backspace", "forget", "Forget", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.entries = list(reversed(history.load()))  # newest first
        self.shown: list[history.Entry] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="history"):
            yield Static("[b]History[/b]", id="dialog-title")
            yield Input(placeholder="Search by title or format", id="history-search")
            yield Static(HELP, id="history-help")
            yield OptionList(id="history")
            yield Static(id="history-empty")

    def on_mount(self) -> None:
        self._fill("")
        self.query_one("#history").focus()

    def _fill(self, query: str) -> None:
        q = query.lower().strip()
        self.shown = [
            e for e in self.entries if not q or q in e.title.lower() or q in e.label.lower()
        ]
        ol = self.query_one("#history", OptionList)
        ol.clear_options()
        for e in self.shown:
            gone = not e.exists
            row = Text.assemble(
                ("⟳ " if e.kind == "convert" else "↓ ", "dim"),
                (e.title, "strike dim" if gone else "bold"),
                "  ",
                (e.label, "dim"),
                "  ",
                ("moved or deleted" if gone else e.when_text, "dim"),
            )
            ol.add_option(Option(row))
        empty = self.query_one("#history-empty", Static)
        if not self.entries:
            empty.update("[dim]Nothing yet. Downloads and conversions show up here.[/dim]")
        elif not self.shown:
            empty.update("[dim]Nothing matches.[/dim]")
        empty.display = not self.shown
        ol.display = bool(self.shown)
        if self.shown:
            ol.highlighted = 0

    @on(Input.Changed, "#history-search")
    def _search(self, event: Input.Changed) -> None:
        self._fill(event.value)

    @on(Input.Submitted, "#history-search")
    def _search_done(self) -> None:
        self.query_one("#history").focus()

    @property
    def current(self) -> history.Entry | None:
        ol = self.query_one("#history", OptionList)
        if ol.highlighted is None or ol.highlighted >= len(self.shown):
            return None
        return self.shown[ol.highlighted]

    def _existing(self) -> history.Entry | None:
        e = self.current
        if e is None:
            self.app.bell()
            return None
        if not e.exists:
            self.notify("That file has been moved or deleted.", severity="warning")
            return None
        return e

    @on(OptionList.OptionSelected, "#history")
    def _open(self) -> None:
        if e := self._existing():
            desktop.open_file(e.path)

    def action_reveal(self) -> None:
        if e := self._existing():
            desktop.reveal(e.path)

    def action_redownload(self) -> None:
        e = self.current
        if e is None or not e.is_link:
            self.notify("Only downloads can be fetched again.", severity="warning")
            return
        self.dismiss(Redownload(e.source))

    def action_copy(self) -> None:
        e = self.current
        if e is None:
            self.app.bell()
            return
        if not desktop.write_clipboard(e.source):
            self.app.copy_to_clipboard(e.source)  # the terminal's own clipboard (OSC 52)
        self.notify("Copied the link." if e.is_link else "Copied the file's path.")

    def action_forget(self) -> None:
        if isinstance(self.focused, Input):
            return
        e = self.current
        if e is None:
            return
        history.remove(e)
        self.entries.remove(e)
        self._fill(self.query_one("#history-search", Input).value)

    def action_close(self) -> None:
        self.dismiss(None)
