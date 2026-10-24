"""Pick a format and quality, then download (a link) or convert (files on disk)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    Input,
    Label,
    OptionList,
    Select,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option

from ..config import Settings
from ..engine.options import (
    AUDIO_FORMATS,
    FRAME_RATE_LABELS,
    H264_FIRST,
    SUBTITLE_EMBED_EXTS,
    VIDEO_FORMATS,
    Choice,
    Extras,
    audio_qualities,
    check_clip,
    parse_time,
    time_text,
    video_qualities,
)
from ..engine.probe import VideoInfo
from ..widgets.toggle import Toggle

TAB_NAMES = {"video": "Video", "audio": "Audio", "image": "Image", "frames": "Frames"}

# Extras a source can offer, and the tabs each one shows on.
CLIP, SUBTITLES, SPONSORBLOCK, CHAPTERS, NORMALIZE, IMAGE_TOOLS = (
    "clip",
    "subtitles",
    "sponsorblock",
    "chapters",
    "normalize",
    "image",
)
EXTRA_KINDS = {
    CLIP: ("video", "audio", "frames"),
    SUBTITLES: ("video", "audio"),
    SPONSORBLOCK: ("video", "audio"),
    CHAPTERS: ("video", "audio"),
    NORMALIZE: ("audio",),
    IMAGE_TOOLS: ("image",),
}
SUBTITLE_CHOICES = {
    "video": [("Off", ""), ("Inside the video", "embed"), ("Separate .srt file", "srt")],
    "audio": [("Off", ""), ("Separate .srt file", "srt")],
}
SPONSOR_CHOICES = [("Off", ""), ("Cut out sponsors", "remove"), ("Mark as chapters", "mark")]

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
    extras: frozenset[str] = frozenset()  # CLIP, SUBTITLES, ...: what the source can do
    duration: float | None = None  # for checking a clip's times


def for_video(info: VideoInfo, where: str) -> ConvertOptions:
    extras = {CLIP, SUBTITLES} if info.duration else {SUBTITLES}
    if info.is_youtube:
        extras.add(SPONSORBLOCK)
    if len(info.chapters) > 1:
        extras.add(CHAPTERS)
    return ConvertOptions(
        title=info.title,
        kinds=["video", "audio"],
        formats={"video": VIDEO_FORMATS, "audio": AUDIO_FORMATS},
        qualities=lambda kind, fmt: (
            video_qualities(fmt, info.max_height) if kind == "video" else audio_qualities(fmt)
        ),
        where=where,
        extras=frozenset(extras | {NORMALIZE}),
        duration=info.duration,
    )


def for_playlist(title: str, count: int, where: str, youtube: bool) -> ConvertOptions:
    """Several videos at once: no clip (each is a different length) and no chapter split."""
    extras = {SUBTITLES, NORMALIZE} | ({SPONSORBLOCK} if youtube else set())
    return ConvertOptions(
        title=f"{title} · {count} video{'s' if count != 1 else ''}",
        kinds=["video", "audio"],
        formats={"video": VIDEO_FORMATS, "audio": AUDIO_FORMATS},
        qualities=lambda kind, fmt: (
            video_qualities(fmt, None) if kind == "video" else audio_qualities(fmt)
        ),
        where=where,
        action=f"download {count}",
        extras=frozenset(extras),
    )


def remembered(settings: Settings, choice: Choice) -> Settings:
    """Settings with `choice` saved, so the dialog reopens where the user left it."""
    extras = choice.extras
    sticky = {}
    if choice.kind in ("video", "audio"):
        sticky = {
            "last_subtitles": extras.subtitles,
            "last_sub_langs": extras.sub_langs,
            "last_sponsorblock": extras.sponsorblock,
        }
    if choice.kind == "audio":
        sticky["last_normalize"] = extras.normalize
    return replace(
        settings,
        last_kind=choice.kind,
        **{f"last_{choice.kind}_format": choice.fmt, f"last_{choice.kind}_quality": choice.quality},
        **sticky,
    )


# What the folded Options line lists, per tab, when nothing is switched on yet.
EXTRA_NAMES = {
    CLIP: "clip",
    SUBTITLES: "subtitles",
    SPONSORBLOCK: "SponsorBlock",
    CHAPTERS: "chapters",
    NORMALIZE: "volume",
    IMAGE_TOOLS: "size, metadata",
}


class ConvertScreen(ModalScreen[Choice | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("o", "toggle_options", "Options", show=False),
    ]

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
            if opts.extras:
                yield from self._compose_extras()
            yield Static(id="summary")
            with Horizontal(id="dialog-actions"):
                yield Button(opts.action, id="go", variant="primary")
                yield Button("cancel", id="cancel")

    def _compose_extras(self) -> ComposeResult:
        # A two-column form under the pickers: labels line up on the left, and every
        # control stretches to the same right edge as the Quality list above it.
        # Folded by default into one line that says what's on; open, it's a two-column form
        # whose controls end at the same right edge as the Quality list above.
        s, extras = self.settings, self.options.extras
        with Collapsible(
            title="Options", collapsed=True, collapsed_symbol="▸", expanded_symbol="▾", id="extras"
        ):
            if CLIP in extras:
                with Horizontal(classes="extra-row", id="clip-row"):
                    yield Label("Clip", classes="extra-label")
                    with Horizontal(classes="extra-controls"):
                        yield Input(placeholder="from 0:00", id="clip-start", compact=True)
                        yield Label("→", classes="extra-to")
                        end = time_text(self.options.duration) if self.options.duration else "end"
                        yield Input(placeholder=f"to {end}", id="clip-end", compact=True)
            if SUBTITLES in extras:
                with Horizontal(classes="extra-row", id="subs-row"):
                    yield Label("Subtitles", classes="extra-label")
                    with Horizontal(classes="extra-controls"):
                        yield Select(
                            SUBTITLE_CHOICES["video"],
                            value=s.last_subtitles if s.last_subtitles in ("embed", "srt") else "",
                            allow_blank=False,
                            compact=True,
                            id="subs",
                        )
                        yield Input(
                            s.last_sub_langs,
                            placeholder="languages",
                            id="sub-langs",
                            compact=True,
                            tooltip="Languages, comma separated: en, de, es",
                        )
            if SPONSORBLOCK in extras:
                with Horizontal(classes="extra-row", id="sponsor-row"):
                    yield Label("SponsorBlock", classes="extra-label")
                    with Horizontal(classes="extra-controls"):
                        yield Select(
                            SPONSOR_CHOICES,
                            value=s.last_sponsorblock
                            if s.last_sponsorblock in ("remove", "mark")
                            else "",
                            allow_blank=False,
                            compact=True,
                            id="sponsor",
                        )
            if CHAPTERS in extras:
                with Horizontal(classes="extra-row", id="chapters-row"):
                    yield Label("Chapters", classes="extra-label")
                    with Horizontal(classes="extra-controls"):
                        yield Toggle("One file per chapter", id="split")
            if NORMALIZE in extras:
                with Horizontal(classes="extra-row", id="volume-row"):
                    yield Label("Volume", classes="extra-label")
                    with Horizontal(classes="extra-controls"):
                        yield Toggle("Even out loudness", s.last_normalize, id="normalize")
            if IMAGE_TOOLS in extras:
                with Horizontal(classes="extra-row", id="size-row"):
                    yield Label("Max size", classes="extra-label")
                    with Horizontal(classes="extra-controls"):
                        yield Input(
                            placeholder="no limit", id="max-kb", compact=True, type="integer"
                        )
                        yield Label("KB", classes="extra-unit")
                with Horizontal(classes="extra-row", id="metadata-row"):
                    yield Label("Metadata", classes="extra-label")
                    with Horizontal(classes="extra-controls"):
                        yield Toggle("Remove EXIF and location", id="strip")

    def _show_extras(self) -> None:
        """Only the extras that apply to the current tab."""
        if not self.options.extras:
            return
        kind = self.kind
        for row, name in (
            ("clip-row", CLIP),
            ("subs-row", SUBTITLES),
            ("sponsor-row", SPONSORBLOCK),
            ("chapters-row", CHAPTERS),
            ("volume-row", NORMALIZE),
            ("size-row", IMAGE_TOOLS),
            ("metadata-row", IMAGE_TOOLS),
        ):
            for widget in self.query(f"#{row}"):
                widget.display = kind in EXTRA_KINDS[name]
        subs_kind = "audio" if kind == "audio" else "video"
        if self.query("#subs") and getattr(self, "_subs_kind", "video") != subs_kind:
            # Audio can't hold subtitles, so its list has no "inside" choice.
            self._subs_kind = subs_kind
            subs = self.query_one("#subs", Select)
            choices = SUBTITLE_CHOICES[subs_kind]
            keep = subs.value if any(v == subs.value for _, v in choices) else "srt"
            keep = keep if subs.value else ""
            subs.set_options(choices)
            subs.value = keep
        self._sync_langs()
        self.query_one("#extras").display = any(w.display for w in self.query(".extra-row"))

    def _sync_langs(self) -> None:
        # Languages only matter with subtitles on; greyed out, the row still lines up.
        if self.query("#subs"):
            self.query_one("#sub-langs").disabled = not self.query_one("#subs", Select).value

    def _value(self, selector: str, default=None):
        found = self.query(selector)
        if not found:
            return default
        widget = found.first()
        # Rows for another tab don't count. A folded section still does: it only hides them.
        node = widget
        while node is not None and node.id != "extras":
            if node.has_class("extra-row") and not node.display:
                return default
            node = node.parent
        value = widget.value
        return default if value is Select.BLANK else value

    def extras(self) -> Extras:
        """The extras picked for the current tab. Raises ValueError for a bad clip time."""
        kind = self.kind
        start = parse_time(self._value("#clip-start", "") or "")
        end = parse_time(self._value("#clip-end", "") or "")
        if problem := check_clip(start, end, self.options.duration):
            raise ValueError(problem)
        if self.options.duration and end is not None and end >= self.options.duration:
            end = None  # "to the end", which lets yt-dlp skip the cut there
        max_kb = self._value("#max-kb", "") or ""
        return Extras(
            start=start or None,  # from 0 is no cut at the start
            end=end,
            subtitles=self._value("#subs", "") or "",
            sub_langs=(self._value("#sub-langs", "") or "en").replace(" ", ""),
            sponsorblock=self._value("#sponsor", "") or "",
            split_chapters=bool(self._value("#split", False)),
            normalize=kind == "audio" and bool(self._value("#normalize", False)),
            strip_metadata=bool(self._value("#strip", False)),
            max_kb=int(max_kb) if str(max_kb).isdigit() and int(max_kb) > 0 else None,
        )

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
        self._show_extras()
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
        """The pick, or None when nothing is picked. Raises ValueError for a bad clip time."""
        kind = self.kind
        fmt, q = self._selected(f"{kind}-format"), self._selected(f"{kind}-quality")
        if not fmt or not q:
            return None
        return Choice(kind, fmt, q, self.extras())

    def _update_options_title(self, choice: Choice | None, problem: str = "") -> None:
        if not self.options.extras:
            return
        if problem:
            detail = Content.styled(problem, "$error")
        elif choice and choice.extras.label:
            detail = Content.styled(choice.extras.label, "$primary")
        else:
            offered = [
                EXTRA_NAMES[name]
                for name in EXTRA_NAMES
                if name in self.options.extras and self.kind in EXTRA_KINDS[name]
            ]
            detail = Content.styled(", ".join(offered), "$text-muted")
        self.query_one("#extras", Collapsible).title = Content.assemble("Options   ", detail)

    def action_toggle_options(self) -> None:
        if self.options.extras:
            box = self.query_one("#extras", Collapsible)
            box.collapsed = not box.collapsed

    def _update_summary(self) -> None:
        try:
            c = self.choice()
        except ValueError as exc:
            self.query_one("#summary", Static).update(f"[b $error]{escape(str(exc))}[/]")
            self._update_options_title(None, str(exc))
            return
        self._update_options_title(c)
        text = ""
        if c:
            text = f"[b]{escape(c.label)}[/b]  [dim]→ {escape(self.options.where)}[/dim]"
            notes = [n for n in (self.options.note(c), extras_note(c)) if n]
            if notes:
                text += f"\n[dim]{escape(' '.join(notes))}[/dim]"
        self.query_one("#summary", Static).update(text)

    @on(Input.Changed)
    @on(Select.Changed)
    @on(Checkbox.Changed)
    def _extras_changed(self) -> None:
        self._sync_langs()
        self._update_summary()

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
        self._show_extras()
        self._update_summary()

    @on(Input.Submitted)
    @on(Button.Pressed, "#go")
    def action_go(self) -> None:
        try:
            choice = self.choice()
        except ValueError:
            self.app.bell()  # the summary already says what's wrong
            return
        self.dismiss(choice)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


def extras_note(choice: Choice) -> str:
    """What the extras will actually do, where that isn't obvious from the label."""
    e = choice.extras
    bits = []
    if e.subtitles == "embed" and choice.kind == "video" and choice.fmt not in SUBTITLE_EMBED_EXTS:
        bits.append(f"{choice.fmt.upper()} can't hold subtitles, so they come as a .srt file.")
    if e.clipped and choice.kind != "image":
        bits.append("Unless it is re-encoded, a clip starts at the nearest keyframe.")
    if e.max_kb and choice.fmt not in ("jpg", "webp", "avif", "heic", "jxl"):
        bits.append(
            f"Max size works for JPG, WebP, AVIF, HEIC and JPEG XL, not {choice.fmt.upper()}."
        )
    if e.sponsorblock == "remove":
        bits.append("Sponsor segments come from SponsorBlock's community list.")
    return " ".join(bits)
