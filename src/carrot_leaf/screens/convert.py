"""Pick a format and quality, then download (a link) or convert (files on disk)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

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
    FRAME_RATE_LABELS,
    H264_FIRST,
    VIDEO_FORMATS,
    Choice,
    audio_qualities,
    video_qualities,
)
from ..engine.probe import VideoInfo

TAB_NAMES = {"video": "Video", "audio": "Audio", "image": "Image", "frames": "Frames"}

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


def quality_label(kind: str, q: str, fmt: str = "", local: bool = False) -> str:
    if kind == "frames":
        return FRAME_RATE_LABELS.get(q, q).capitalize()
    if kind == "image":
        if q == "best":
            return "Original size"
        return "Half size" if q == "50" else f"Up to {q}px"
    if q == "best" and local:
        return "Same as original" if kind == "video" else "Best"
    if q == "best":
        # See options.H264_FIRST: "best" MP4/MOV is the best H.264, so nothing is re-encoded.
        return "Best that plays everywhere" if fmt in H264_FIRST else "Best available"
    label = f"{q}p" if kind == "video" else f"{q} kbps"
    if fmt == "mp4" and kind == "video" and int(q) > 1080 and not local:
        label += "  [dim]VP9/AV1, newer players[/dim]"
    return label


def format_note(kind: str, fmt: str) -> str:
    return FORMAT_NOTES.get(fmt, "")


def format_option(kind: str, fmt: str, note: str, missing: str) -> Option:
    if missing:
        # Shown, not hidden, so people can see the format exists and what unlocks it.
        return Option(f"[dim]{fmt.upper():<5} needs {missing}[/dim]", id=fmt, disabled=True)
    return Option(f"[b]{fmt.upper():<5}[/b] [dim]{escape(note)}[/dim]", id=fmt)


@dataclass
class ConvertOptions:
    """What the dialog offers. `for_video` builds it for a link; the Files tab for files."""

    title: str
    kinds: list[str]
    formats: dict[str, tuple[str, ...]]
    qualities: Callable[[str, str], list[str]]
    where: str  # shown after the arrow in the summary
    action: str = "download"
    local: bool = False  # files on disk, not a link: "best" keeps the original's quality
    note: Callable[[Choice], str] = lambda _choice: ""  # extra summary text for a choice
    note_for: Callable[[str, str], str] = format_note  # one-line note beside each format
    # The encoder a format needs and this ffmpeg lacks, or "". Such formats show greyed out.
    missing: Callable[[str, str], str] = lambda _kind, _fmt: ""


def for_video(info: VideoInfo, where: str) -> ConvertOptions:
    return ConvertOptions(
        title=info.title,
        kinds=["video", "audio"],
        formats={"video": VIDEO_FORMATS, "audio": AUDIO_FORMATS},
        qualities=lambda kind, fmt: (
            video_qualities(fmt, info.max_height) if kind == "video" else audio_qualities(fmt)
        ),
        where=where,
    )


def remembered(settings: Settings, choice: Choice) -> Settings:
    """Settings with `choice` saved, so the dialog reopens where the user left it."""
    return replace(
        settings,
        last_kind=choice.kind,
        **{f"last_{choice.kind}_format": choice.fmt, f"last_{choice.kind}_quality": choice.quality},
    )


class ConvertScreen(ModalScreen[Choice | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, source: VideoInfo | ConvertOptions, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        if isinstance(source, ConvertOptions):
            self.options = source
        else:
            from ..widgets.queue import short_path

            self.options = for_video(source, short_path(str(settings.download_path)))

    def compose(self) -> ComposeResult:
        opts = self.options
        initial = self.settings.last_kind if self.settings.last_kind in opts.kinds else None
        with Vertical(id="dialog"):
            yield Static(f"[b]Convert[/b]  [dim]{escape(opts.title)}[/dim]", id="dialog-title")
            with TabbedContent(initial=initial or opts.kinds[0], id="kind"):
                for kind in opts.kinds:
                    with TabPane(TAB_NAMES[kind], id=kind):
                        with Horizontal(classes="pickers"):
                            with Vertical(classes="picker"):
                                yield Label("Format", classes="picker-label")
                                yield OptionList(
                                    *(
                                        format_option(
                                            kind, f, opts.note_for(kind, f), opts.missing(kind, f)
                                        )
                                        for f in opts.formats[kind]
                                    ),
                                    id=f"{kind}-format",
                                )
                            with Vertical(classes="picker"):
                                yield Label(
                                    "Rate"
                                    if kind == "frames"
                                    else "Size"
                                    if kind == "image"
                                    else "Quality",
                                    classes="picker-label",
                                )
                                yield OptionList(id=f"{kind}-quality")
            yield Static(id="summary")
            with Horizontal(id="dialog-actions"):
                yield Button(opts.action, id="go", variant="primary")
                yield Button("cancel", id="cancel")

    def on_mount(self) -> None:
        for kind in self.options.kinds:
            formats = self.options.formats[kind]
            last = getattr(self.settings, f"last_{kind}_format", "")
            fmt_list = self.query_one(f"#{kind}-format", OptionList)
            usable = [i for i, f in enumerate(formats) if not self.options.missing(kind, f)]
            index = formats.index(last) if last in formats else -1
            fmt_list.highlighted = index if index in usable else (usable or [0])[0]
            self._fill_qualities(kind)
        self.query_one(f"#{self.kind}-format", OptionList).focus()
        self._update_summary()

    @property
    def kind(self) -> str:
        return self.query_one("#kind", TabbedContent).active or self.options.kinds[0]

    def _selected(self, list_id: str) -> str | None:
        ol = self.query_one(f"#{list_id}", OptionList)
        if ol.highlighted is None:
            return None
        return ol.get_option_at_index(ol.highlighted).id

    def _fill_qualities(self, kind: str) -> None:
        fmt = self._selected(f"{kind}-format")
        if fmt is None:
            return
        qualities = self.options.qualities(kind, fmt)
        ql = self.query_one(f"#{kind}-quality", OptionList)
        previous = self._selected(f"{kind}-quality") or getattr(
            self.settings, f"last_{kind}_quality", ""
        )
        ql.clear_options()
        ql.add_options(
            [Option(quality_label(kind, q, fmt, self.options.local), id=q) for q in qualities]
        )
        ql.highlighted = qualities.index(previous) if previous in qualities else 0

    def choice(self) -> Choice | None:
        kind = self.kind
        fmt, q = self._selected(f"{kind}-format"), self._selected(f"{kind}-quality")
        if not fmt or not q:
            return None
        return Choice(kind, fmt, q)

    def _update_summary(self) -> None:
        c = self.choice()
        text = ""
        if c:
            text = f"[b]{escape(c.label)}[/b]  [dim]→ {escape(self.options.where)}[/dim]"
            if note := self.options.note(c):
                text += f"\n[dim]{escape(note)}[/dim]"
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
