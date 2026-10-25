"""Pick a clip by eye: one big frame, a timeline, and keys to mark the start and end."""

from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Static

from ..engine.frames import FrameSource
from ..engine.options import time_text
from ..widgets.thumbnail import Thumbnail

HELP = (
    "[b $primary]←→[/] move  [b $primary]shift ←→[/] 10s  [b $primary]\\[[/] start  "
    "[b $primary]][/] end  [b $primary]backspace[/] clear  [b $primary]enter[/] use"
)


class Timeline(Widget):
    """The whole video as a bar: the clip in the accent colour, a caret under the playhead."""

    DEFAULT_CSS = "Timeline { height: 2; }"

    def __init__(self, duration: float, **kwargs) -> None:
        super().__init__(**kwargs)
        self.duration = max(duration, 0.001)
        self.at = 0.0
        self.start: float | None = None
        self.end: float | None = None

    def set(self, at: float, start: float | None, end: float | None) -> None:
        self.at, self.start, self.end = at, start, end
        self.refresh()

    def render(self) -> Text:
        width = max(self.size.width, 10)
        from .main import rich_color  # ANSI themes name colours Rich can't read

        primary = rich_color(self.app.theme_variables.get("primary"), "#ff8700")
        muted = rich_color(self.app.theme_variables.get("panel"), "#3a3a3a")

        def col(t: float) -> int:
            return min(width - 1, int(t / self.duration * width))

        lo = col(self.start) if self.start is not None else None
        hi = col(self.end) if self.end is not None else None
        bar = Text(no_wrap=True)
        for x in range(width):
            in_clip = (lo is not None or hi is not None) and (lo or 0) <= x <= (
                hi if hi is not None else width - 1
            )
            if x == lo:
                bar.append("[", style=f"bold {primary}")
            elif x == hi:
                bar.append("]", style=f"bold {primary}")
            else:
                bar.append("━", style=primary if in_clip else muted)
        # Second line: a caret under the playhead with its time beside it, on whichever
        # side has room.
        caret = col(self.at)
        label = time_text(self.at)
        if caret + len(label) + 2 <= width:
            under = Text.assemble((" " * caret), ("▲", f"bold {primary}"), (f" {label}", "bold"))
        else:
            pad = max(caret - len(label) - 1, 0)
            under = Text.assemble((" " * pad), (f"{label} ", "bold"), ("▲", f"bold {primary}"))
        return Text.assemble(bar, "\n", under)


class ClipScreen(ModalScreen[tuple[float | None, float | None] | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("left", "move(-1)", show=False, priority=True),
        Binding("right", "move(1)", show=False, priority=True),
        Binding("shift+left", "move(-10, True)", show=False, priority=True),
        Binding("shift+right", "move(10, True)", show=False, priority=True),
        Binding("left_square_bracket", "mark_start", show=False),
        Binding("right_square_bracket", "mark_end", show=False),
        Binding("backspace", "clear", show=False),
        Binding("enter", "use", show=False, priority=True),
    ]

    def __init__(
        self, source: FrameSource, title: str, start: float | None, end: float | None
    ) -> None:
        super().__init__()
        self.source = source
        self.title = title
        self.start, self.end = start, end
        self.at = start or 0.0
        self.token = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="clip-picker"):
            yield Static(f"[b]Pick a clip[/b]  [dim]{escape(self.title)}[/dim]", id="dialog-title")
            with Vertical(classes="panel", id="clip-frame-panel") as panel:
                panel.border_title = "Frame · 0:00"
                yield Thumbnail(id="clip-frame")
            with Horizontal(id="clip-ends"):
                yield Static("0:00", id="clip-zero")
                yield Static(time_text(self.source.duration), id="clip-total")
            yield Timeline(self.source.duration, id="clip-timeline")
            yield Static(id="clip-range")
            yield Static(HELP, id="clip-help")
            with Horizontal(id="dialog-actions"):
                yield Button("Use clip", id="go", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._redraw()
        self._load(self.at, self.token)

    def _redraw(self) -> None:
        self.query_one(Timeline).set(self.at, self.start, self.end)
        self.query_one("#clip-frame-panel").border_title = f"Frame · {time_text(self.at)}"
        if self.start is None and self.end is None:
            text = "[dim]The whole video. Press \\[ and ] to mark a clip.[/dim]"
        else:
            a = self.start or 0.0
            b = self.end if self.end is not None else self.source.duration
            text = f"[b]{time_text(a)} → {time_text(b)}[/b]  [dim]{time_text(max(b - a, 0))}[/dim]"
        self.query_one("#clip-range", Static).update(text)

    @work(thread=True, exclusive=True, group="clip-frame")
    def _load(self, at: float, token: int) -> None:
        image = self.source.frame(at)
        if image is not None:
            self.app.call_from_thread(self._show, token, image)

    def _show(self, token: int, image) -> None:
        if token == self.token:
            self.query_one("#clip-frame", Thumbnail).image = image

    def action_move(self, steps: float, seconds: bool = False) -> None:
        delta = steps if seconds else steps * self.source.step
        self.at = min(max(self.at + delta, 0.0), self.source.duration)
        self.token += 1
        self._redraw()
        self._load(self.at, self.token)

    def action_mark_start(self) -> None:
        self.start = self.at
        if self.end is not None and self.end <= self.start:
            self.end = None
        self._redraw()

    def action_mark_end(self) -> None:
        self.end = self.at
        if self.start is not None and self.start >= self.end:
            self.start = None
        self._redraw()

    def action_clear(self) -> None:
        self.start = self.end = None
        self._redraw()

    @on(Button.Pressed, "#go")
    def action_use(self) -> None:
        self.dismiss((self.start, self.end))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
