"""Search what was said in everything downloaded: hits on the left, the transcript around
the highlighted one on the right."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from .. import transcripts
from ..engine import desktop
from ..engine.options import time_text

HELP = (
    "[b $primary]enter[/] play from there  [b $primary]c[/] clip 30s around it  "
    "[b $primary]o[/] show in folder"
)
CONTEXT = 4  # lines of transcript either side of a hit
CLIP_AROUND = 15.0  # seconds either side of a hit for `c`


@dataclass
class ClipRequest:
    """Dismissed with this to cut a clip out of a downloaded file."""

    path: str
    start: float
    end: float


class SearchScreen(ModalScreen[ClipRequest | None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("c", "clip", "Clip", show=False),
        Binding("o", "reveal", "Show in folder", show=False),
    ]

    def __init__(self, query: str = "") -> None:
        super().__init__()
        self.query_text = query
        self.library: list[transcripts.Transcript] = []
        self.hits: list[transcripts.Hit] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="search"):
            yield Static("[b]Search what was said[/b]", id="dialog-title")
            yield Input(
                self.query_text,
                placeholder='Words in any order, or "an exact phrase"',
                id="search-input",
            )
            yield Static(HELP, id="search-help")
            with Horizontal(id="search-panes"):
                yield OptionList(id="search-hits")
                yield Static(id="search-context")
            yield Static(id="search-status")

    def on_mount(self) -> None:
        self.query_one("#search-input").focus()
        self._load()

    @work(thread=True, exclusive=True, group="transcripts")
    def _load(self) -> None:
        library = transcripts.load_all()
        self.app.call_from_thread(self._loaded, library)

    def _loaded(self, library: list[transcripts.Transcript]) -> None:
        self.library = library
        self._search(self.query_one("#search-input", Input).value)

    @on(Input.Changed, "#search-input")
    def _changed(self, event: Input.Changed) -> None:
        self._search(event.value)

    @on(Input.Submitted, "#search-input")
    def _submitted(self) -> None:
        if self.hits:
            self.query_one("#search-hits").focus()

    def _search(self, query: str) -> None:
        self.hits = transcripts.search(query, self.library)
        ol = self.query_one("#search-hits", OptionList)
        ol.clear_options()
        for hit in self.hits:
            ol.add_option(
                Option(
                    Text.assemble(
                        (time_text(hit.cue.start).rjust(7), "bold"),
                        "  ",
                        (hit.transcript.title, ""),
                    )
                )
            )
        status = self.query_one("#search-status", Static)
        n = len(self.library)
        if not n:
            status.update(
                "[dim]Nothing to search yet. Videos you download from now on are added, "
                "as long as they have captions (most YouTube videos do).[/dim]"
            )
        elif not query.strip():
            status.update(f"[dim]Searching {n} video{'s' if n != 1 else ''}.[/dim]")
        elif not self.hits:
            status.update(f"[dim]Not said in any of your {n} videos.[/dim]")
        else:
            videos = len({id(h.transcript) for h in self.hits})
            status.update(
                f"[dim]{len(self.hits)} moment{'s' if len(self.hits) != 1 else ''} "
                f"in {videos} video{'s' if videos != 1 else ''}.[/dim]"
            )
        ol.display = bool(self.hits)
        self.query_one("#search-context").display = bool(self.hits)
        if self.hits:
            ol.highlighted = 0
        self._show_context()

    @on(OptionList.OptionHighlighted, "#search-hits")
    def _show_context(self) -> None:
        hit = self.current
        out = self.query_one("#search-context", Static)
        if hit is None:
            out.update("")
            return
        words = transcripts.needles(self.query_one("#search-input", Input).value)
        cues = hit.transcript.cues
        lo, hi = max(hit.index - CONTEXT, 0), min(hit.index + CONTEXT + 1, len(cues))
        text = Text()
        text.append(f"{hit.transcript.title}\n\n", style="bold")
        for i in range(lo, hi):
            cue = cues[i]
            line = Text(cue.text, style="" if i == hit.index else "dim")
            if i == hit.index:
                for w in words:
                    line.highlight_words([w], style="bold reverse", case_sensitive=False)
            text.append(f"{time_text(cue.start):>7}  ", style="bold" if i == hit.index else "dim")
            text.append_text(line)
            text.append("\n")
        out.update(text)

    @property
    def current(self) -> transcripts.Hit | None:
        ol = self.query_one("#search-hits", OptionList)
        if not self.hits or ol.highlighted is None or ol.highlighted >= len(self.hits):
            return None
        return self.hits[ol.highlighted]

    def _existing(self) -> transcripts.Hit | None:
        from pathlib import Path

        hit = self.current
        if hit is None:
            self.app.bell()
            return None
        if not Path(hit.transcript.path).exists():
            self.notify("That file has been moved or deleted.", severity="warning")
            return None
        return hit

    @on(OptionList.OptionSelected, "#search-hits")
    def _play(self) -> None:
        if hit := self._existing():
            if not desktop.play_at(hit.transcript.path, hit.cue.start):
                self.notify(f"Opened it. Skip to {time_text(hit.cue.start)}.")

    def action_clip(self) -> None:
        if isinstance(self.focused, Input):
            return
        if hit := self._existing():
            start = max(hit.cue.start - CLIP_AROUND, 0.0)
            self.dismiss(ClipRequest(hit.transcript.path, start, hit.cue.start + CLIP_AROUND))

    def action_reveal(self) -> None:
        if isinstance(self.focused, Input):
            return
        if hit := self._existing():
            desktop.reveal(hit.transcript.path)

    def action_close(self) -> None:
        self.dismiss(None)
