"""Pick which videos of a playlist or channel to download."""

from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, SelectionList, Static
from textual.widgets.selection_list import Selection

from ..engine.probe import PlaylistInfo


class PlaylistScreen(ModalScreen[set[int] | None]):
    """Dismisses with the indexes of the picked entries, or None when cancelled."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("a", "select_all", "All", show=False),
        Binding("n", "select_none", "None", show=False),
    ]

    def __init__(self, playlist: PlaylistInfo, selected: set[int]) -> None:
        super().__init__()
        self.playlist = playlist
        self.initial = selected

    def compose(self) -> ComposeResult:
        pl = self.playlist
        with Vertical(id="dialog", classes="playlist"):
            yield Static(f"[b]Pick videos[/b]  [dim]{escape(pl.title)}[/dim]", id="dialog-title")
            yield Static("[dim]space ticks a video · a all · n none · enter keeps your picks[/dim]")
            width = len(str(len(pl.entries)))
            yield SelectionList[int](
                *(
                    Selection(
                        Text.assemble(
                            (f"{i + 1:>{width}}  ", "dim"),
                            e.title,
                            (f"  {e.duration_text}" if e.duration else "", "dim"),
                        ),
                        i,
                        i in self.initial,
                    )
                    for i, e in enumerate(pl.entries)
                ),
                id="entries",
            )
            with Horizontal(id="latest-row"):
                yield Label("Only the first", classes="extra-label")
                yield Input(placeholder="N", id="latest", type="integer", compact=True)
                yield Static("[dim]newest first on a channel[/dim]", classes="extra-hint")
            yield Static(id="picked")
            with Horizontal(id="dialog-actions"):
                yield Button("done", id="go", variant="primary")
                yield Button("cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#entries").focus()
        self._count()

    @property
    def entries(self) -> SelectionList[int]:
        return self.query_one("#entries", SelectionList)

    def _count(self) -> None:
        n, total = len(self.entries.selected), len(self.playlist.entries)
        more = ""
        if self.playlist.truncated:
            more = f"  [dim]the first {total} of {self.playlist.total} are listed[/dim]"
        self.query_one("#picked", Static).update(f"[b]{n}[/b] of {total} picked{more}")

    @on(SelectionList.SelectedChanged)
    def _changed(self) -> None:
        self._count()

    @on(Input.Changed, "#latest")
    def _latest(self, event: Input.Changed) -> None:
        if not event.value.isdigit():
            return
        n = int(event.value)
        entries = self.entries
        entries.deselect_all()
        for i in range(min(n, len(self.playlist.entries))):
            entries.select(i)

    def action_select_all(self) -> None:
        self.entries.select_all()

    def action_select_none(self) -> None:
        self.entries.deselect_all()

    @on(Input.Submitted, "#latest")
    @on(Button.Pressed, "#go")
    def action_go(self) -> None:
        self.dismiss(set(self.entries.selected))

    def on_key(self, event) -> None:
        # Enter in the list ticks the row by default; here it means "done", as in the dialogs.
        if event.key == "enter" and self.focused is self.entries:
            event.stop()
            event.prevent_default()
            self.action_go()

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
