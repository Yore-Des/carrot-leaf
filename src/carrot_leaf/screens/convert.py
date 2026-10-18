"""Pick a format and quality, then download."""

from __future__ import annotations

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option

from ..config import Settings
from ..engine.options import (
    AUDIO_FORMATS,
    VIDEO_FORMATS,
    Choice,
    audio_qualities,
    video_qualities,
)
from ..engine.probe import VideoInfo

FORMAT_NOTES = {
    "mp4": "plays everywhere",
    "mkv": "keeps every stream",
    "webm": "open web format",
    "mov": "Apple / editing apps",
    "gif": "short clips, no sound",
    "mp3": "plays everywhere",
    "m4a": "AAC, Apple devices",
    "opus": "small and sharp",
    "flac": "lossless",
    "wav": "uncompressed",
    "ogg": "Vorbis",
}


def quality_label(kind: str, q: str) -> str:
    if q == "best":
        return "Best available"
    return f"{q}p" if kind == "video" else f"{q} kbps"


class ConvertScreen(ModalScreen[Choice | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, info: VideoInfo, settings: Settings) -> None:
        super().__init__()
        self.info = info
        self.settings = settings

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"[b]Convert[/b]  [dim]{escape(self.info.title)}[/dim]", id="dialog-title")
            with TabbedContent(initial=self.settings.last_kind, id="kind"):
                for kind, formats in (("video", VIDEO_FORMATS), ("audio", AUDIO_FORMATS)):
                    with TabPane(kind.title(), id=kind):
                        with Horizontal(classes="pickers"):
                            with Vertical(classes="picker"):
                                yield Label("Format", classes="picker-label")
                                yield OptionList(
                                    *(
                                        Option(
                                            f"[b]{f.upper():<5}[/b] [dim]{FORMAT_NOTES[f]}[/dim]",
                                            id=f,
                                        )
                                        for f in formats
                                    ),
                                    id=f"{kind}-format",
                                )
                            with Vertical(classes="picker"):
                                yield Label("Quality", classes="picker-label")
                                yield OptionList(id=f"{kind}-quality")
            yield Static(id="summary")
            with Horizontal(id="dialog-actions"):
                yield Button("download", id="go", variant="primary")
                yield Button("cancel", id="cancel")

    def on_mount(self) -> None:
        s = self.settings
        for kind, fmt, formats in (
            ("video", s.last_video_format, VIDEO_FORMATS),
            ("audio", s.last_audio_format, AUDIO_FORMATS),
        ):
            fmt_list = self.query_one(f"#{kind}-format", OptionList)
            fmt_list.highlighted = formats.index(fmt) if fmt in formats else 0
            self._fill_qualities(kind)
        self.query_one(f"#{s.last_kind}-format", OptionList).focus()
        self._update_summary()

    @property
    def kind(self) -> str:
        return self.query_one("#kind", TabbedContent).active or "video"

    def _selected(self, list_id: str) -> str | None:
        ol = self.query_one(f"#{list_id}", OptionList)
        if ol.highlighted is None:
            return None
        return ol.get_option_at_index(ol.highlighted).id

    def _fill_qualities(self, kind: str) -> None:
        fmt = self._selected(f"{kind}-format")
        if fmt is None:
            return
        qualities = (
            video_qualities(fmt, self.info.max_height) if kind == "video" else audio_qualities(fmt)
        )
        ql = self.query_one(f"#{kind}-quality", OptionList)
        previous = self._selected(f"{kind}-quality") or getattr(
            self.settings, f"last_{kind}_quality"
        )
        ql.clear_options()
        ql.add_options([Option(quality_label(kind, q), id=q) for q in qualities])
        ql.highlighted = qualities.index(previous) if previous in qualities else 0

    def choice(self) -> Choice | None:
        kind = self.kind
        fmt, q = self._selected(f"{kind}-format"), self._selected(f"{kind}-quality")
        if not fmt or not q:
            return None
        return Choice(kind, fmt, q)

    def _update_summary(self) -> None:
        c = self.choice()
        from ..widgets.queue import short_path

        where = escape(short_path(str(self.settings.download_path)))
        text = f"[b]{c.label}[/b]  [dim]→ {where}[/dim]" if c else ""
        self.query_one("#summary", Static).update(text)

    @on(OptionList.OptionHighlighted)
    def _highlighted(self, event: OptionList.OptionHighlighted) -> None:
        list_id = event.option_list.id or ""
        if list_id.endswith("-format"):
            self._fill_qualities(list_id.split("-")[0])
        self._update_summary()

    @on(OptionList.OptionSelected)
    def _selected_option(self, event: OptionList.OptionSelected) -> None:
        # Enter on a format moves to quality; Enter on a quality downloads.
        list_id = event.option_list.id or ""
        if list_id.endswith("-format"):
            self.query_one(f"#{list_id.split('-')[0]}-quality", OptionList).focus()
        else:
            self.action_go()

    @on(TabbedContent.TabActivated)
    def _tab(self) -> None:
        self._update_summary()

    @on(Button.Pressed, "#go")
    def action_go(self) -> None:
        self.dismiss(self.choice())

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
