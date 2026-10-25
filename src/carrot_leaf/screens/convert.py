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
from textual.widgets import Button, Input, Label, OptionList, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option

from .. import presets
from ..config import Settings
from ..engine.frames import for_video as frames_for_video
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
from ..presets import site_key
from ..widgets.cycle import CycleValue

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
    "video": [("Off", ""), ("Inside the video", "embed"), (".srt file", "srt")],
    "audio": [("Off", ""), (".srt file", "srt")],
}
SPONSOR_CHOICES = [("Off", ""), ("Cut them out", "remove"), ("Mark as chapters", "mark")]
SPLIT_CHOICES = [("Off", False), ("One file each", True)]
VOLUME_CHOICES = [("As it is", False), ("Even out", True)]
METADATA_CHOICES = [("Keep", False), ("Remove", True)]
# Each Extras row: its id, the extra it belongs to, and its label.
EXTRA_ROWS = (
    ("subs-row", SUBTITLES, "Subtitles"),
    ("langs-row", SUBTITLES, "Languages"),
    ("sponsor-row", SPONSORBLOCK, "SponsorBlock"),
    ("chapters-row", CHAPTERS, "Chapters"),
    ("volume-row", NORMALIZE, "Volume"),
    ("size-row", IMAGE_TOOLS, "Max size"),
    ("metadata-row", IMAGE_TOOLS, "Metadata"),
)

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
    # Builds a frames.FrameSource for picking a clip by eye; None when there's nothing to show.
    frames: Callable[[], object] | None = None
    site: str = ""  # presets.site_key() of a link, so a preset can become that site's default


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
        frames=(lambda: frames_for_video(info)) if info.duration else None,
        site=site_key(info.extractor),
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
        site="youtube" if youtube else "",
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


class ConvertScreen(ModalScreen[Choice | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        *(Binding(str(n), f"preset({n})", show=False) for n in range(1, 10)),
    ]

    def __init__(
        self,
        source: VideoInfo | ConvertOptions,
        settings: Settings,
        initial: Extras | None = None,
    ) -> None:
        """`initial` fills in a clip, e.g. from a search hit."""
        super().__init__()
        self.settings = settings
        self.initial = initial
        self.presets: list[presets.Preset] = []  # those that fit; filled once options exist
        if isinstance(source, ConvertOptions):
            self.options = source
        else:
            from ..widgets.queue import short_path

            self.options = for_video(source, short_path(str(settings.download_path)))
        self.presets = [p for p in presets.load() if self._fits(p)]

    def compose(self) -> ComposeResult:
        # Four panels of the same size: Format and Quality (per tab) over Clip and Extras.
        # Values line up on each panel's right edge; the buttons share the width equally.
        opts = self.options
        initial = self.settings.last_kind if self.settings.last_kind in opts.kinds else None
        with Vertical(id="dialog", classes="convert"):
            yield Static(f"[b]Convert[/b]  [dim]{escape(opts.title)}[/dim]", id="dialog-title")
            with Horizontal(id="presets"):
                yield from self._preset_chips()
            with TabbedContent(initial=initial or opts.kinds[0], id="kind"):
                for kind in opts.kinds:
                    with TabPane(TAB_NAMES[kind], id=kind):
                        with Horizontal(classes="panel-row top"):
                            with Vertical(classes="panel") as panel:
                                panel.border_title = "Format"
                                yield OptionList(
                                    *(
                                        format_option(
                                            kind, f, opts.note_for(kind, f), opts.missing(kind, f)
                                        )
                                        for f in opts.formats[kind]
                                    ),
                                    id=f"{kind}-format",
                                )
                            with Vertical(classes="panel") as panel:
                                panel.border_title = {"frames": "Rate", "image": "Size"}.get(
                                    kind, "Quality"
                                )
                                yield OptionList(id=f"{kind}-quality")
            if opts.extras:
                with Horizontal(classes="panel-row bottom", id="extras"):
                    with Vertical(classes="panel", id="clip-panel") as panel:
                        panel.border_title = "Clip"
                        yield from self._compose_clip()
                    with Vertical(classes="panel", id="extras-panel") as panel:
                        panel.border_title = "Extras"
                        yield from self._compose_extras()
            yield Static(id="summary")
            with Horizontal(id="dialog-actions"):
                yield Button(opts.action.capitalize(), id="go", variant="primary")
                yield Button("Cancel", id="cancel")

    def _compose_clip(self) -> ComposeResult:
        if CLIP in self.options.extras:
            with Horizontal(classes="clip-fields"):
                with Vertical():
                    yield Label("Start", classes="clip-label")
                    yield Input(placeholder="0:00", id="clip-start", compact=True)
                with Vertical():
                    yield Label("End", classes="clip-label")
                    end = time_text(self.options.duration) if self.options.duration else "end"
                    yield Input(placeholder=end, id="clip-end", compact=True)
            if self.options.frames:
                yield Button("Pick on the timeline", id="clip-pick", classes="panel-button")
            yield Static("[dim]1:30, 90 or 1m30s · blank for all[/dim]", id="clip-hint")
        yield Static(id="clip-note")

    def _compose_extras(self) -> ComposeResult:
        s, extras = self.settings, self.options.extras
        for row_id, name, label in EXTRA_ROWS:
            if name not in extras:
                continue
            with Horizontal(classes="extra-line", id=row_id):
                yield Label(label, classes="extra-label")
                if row_id == "subs-row":
                    saved = s.last_subtitles if s.last_subtitles in ("embed", "srt") else ""
                    yield CycleValue(SUBTITLE_CHOICES["video"], saved, id="subs")
                elif row_id == "langs-row":
                    yield Input(
                        s.last_sub_langs, placeholder="en, de", id="sub-langs", compact=True
                    )
                elif row_id == "sponsor-row":
                    saved = s.last_sponsorblock if s.last_sponsorblock in ("remove", "mark") else ""
                    yield CycleValue(SPONSOR_CHOICES, saved, id="sponsor")
                elif row_id == "chapters-row":
                    yield CycleValue(SPLIT_CHOICES, False, id="split")
                elif row_id == "volume-row":
                    yield CycleValue(VOLUME_CHOICES, s.last_normalize, id="normalize")
                elif row_id == "size-row":
                    yield Input(placeholder="no limit", id="max-kb", compact=True, type="integer")
                    yield Label("KB", classes="extra-unit")
                elif row_id == "metadata-row":
                    # Photos carry camera details and often GPS; dropping them is the safe default.
                    yield CycleValue(METADATA_CHOICES, True, id="strip")
        yield Static(id="extras-note")

    def _show_extras(self) -> None:
        """Only the extras that apply to the current tab; a short note where none do."""
        if not self.options.extras:
            return
        kind = self.kind
        for row_id, name, _label in EXTRA_ROWS:
            for widget in self.query(f"#{row_id}"):
                widget.display = kind in EXTRA_KINDS[name]
        clip_ok = CLIP in self.options.extras and kind in EXTRA_KINDS[CLIP]
        for widget in self.query(".clip-fields, #clip-pick, #clip-hint"):
            widget.display = clip_ok
        note = self.query_one("#clip-note", Static)
        note.display = not clip_ok
        if not clip_ok:
            note.update(
                "[dim]Pictures have no timeline.[/dim]"
                if kind == "image"
                else "[dim]Clips work on one video at a time.[/dim]"
            )
        rows = [w for w in self.query(".extra-line") if w.display]
        empty = self.query_one("#extras-note", Static)
        empty.display = not rows
        empty.update("[dim]Nothing extra for this one.[/dim]")
        subs_kind = "audio" if kind == "audio" else "video"
        if self.query("#subs") and getattr(self, "_subs_kind", "video") != subs_kind:
            # Audio can't hold subtitles, so its choices have no "inside" one.
            self._subs_kind = subs_kind
            subs = self.query_one("#subs", CycleValue)
            keep = subs.value if subs.value in ("", "srt") else "srt"
            subs.set_choices(SUBTITLE_CHOICES[subs_kind], keep)
        self._sync_langs()

    def _sync_langs(self) -> None:
        # Languages only matter with subtitles on; greyed out, the row still lines up.
        if self.query("#subs"):
            self.query_one("#sub-langs").disabled = not self.query_one("#subs", CycleValue).value

    def _value(self, selector: str, default=None):
        found = self.query(selector)
        if not found:
            return default
        widget = found.first()
        # Rows and fields for another tab don't count.
        node = widget
        while node is not None and node.id != "extras":
            if not node.display:
                return default
            node = node.parent
        return widget.value

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
        if self.initial is not None and self.initial.clipped and self.query("#clip-start"):
            self.query_one("#clip-start", Input).value = (
                time_text(self.initial.start) if self.initial.start else ""
            )
            self.query_one("#clip-end", Input).value = (
                time_text(self.initial.end) if self.initial.end is not None else ""
            )
        self._update_summary()

    # ---------- presets ----------

    def _fits(self, preset: presets.Preset) -> bool:
        opts = self.options
        return (
            preset.kind in opts.kinds
            and preset.fmt in opts.formats[preset.kind]
            and not opts.missing(preset.kind, preset.fmt)
        )

    def _preset_chips(self) -> ComposeResult:
        yield Label("Presets", classes="extra-label")
        for n, p in enumerate(self.presets, 1):
            yield Button(f"{n} {p.name}", id=f"preset-{n}", classes="preset-chip")
        hint = "+ save" if self.presets else "+ save these settings as a preset"
        yield Button(hint, id="preset-save", classes="preset-save")

    async def _refresh_chips(self) -> None:
        self.presets = [p for p in presets.load() if self._fits(p)]
        row = self.query_one("#presets", Horizontal)
        await row.remove_children()
        await row.mount_all(list(self._preset_chips()))
        self._update_summary()

    def action_preset(self, n: int) -> None:
        if not 1 <= n <= len(self.presets):
            return
        self.apply(self.presets[n - 1].choice())

    def apply(self, choice: Choice) -> None:
        """Fill in the whole dialog from `choice` (format, quality and every option)."""
        self.query_one("#kind", TabbedContent).active = choice.kind
        formats = self.options.formats[choice.kind]
        self.query_one(f"#{choice.kind}-format", OptionList).highlighted = formats.index(choice.fmt)
        self._fill_qualities(choice.kind)
        ql = self.query_one(f"#{choice.kind}-quality", OptionList)
        ids = [ql.get_option_at_index(i).id for i in range(ql.option_count)]
        if choice.quality in ids:
            ql.highlighted = ids.index(choice.quality)
        e = choice.extras
        self._show_extras()  # the new tab's rows and subtitle choices, before setting them
        for selector, value in (
            ("#subs", e.subtitles),
            ("#sub-langs", e.sub_langs),
            ("#sponsor", e.sponsorblock),
            ("#split", e.split_chapters),
            ("#normalize", e.normalize),
            ("#strip", e.strip_metadata),
            ("#max-kb", str(e.max_kb or "")),
        ):
            for widget in self.query(selector):
                try:
                    widget.value = value
                except Exception:  # e.g. "inside the video" on the Audio tab
                    pass
        self._sync_langs()
        self._update_summary()

    @on(Button.Pressed, ".preset-chip")
    def _chip_pressed(self, event: Button.Pressed) -> None:
        self.action_preset(int((event.button.id or "0").rsplit("-", 1)[-1]))

    @on(Button.Pressed, "#preset-save")
    def _save_preset(self) -> None:
        try:
            choice = self.choice()
        except ValueError:
            self.app.bell()
            return
        if choice is None:
            return
        from .preset_save import PresetSaveScreen

        async def saved(preset: presets.Preset | None) -> None:
            if preset is not None:
                presets.upsert(preset)
                await self._refresh_chips()
                self.notify(f"Saved the {preset.name} preset.")

        self.app.push_screen(PresetSaveScreen(choice, self.options.site), saved)

    # ---------- clip picker ----------

    @on(Button.Pressed, "#clip-pick")
    def action_pick_clip(self) -> None:
        if not self.options.frames:
            return
        source = self.options.frames()
        if source is None:
            self.notify("This video has no preview frames to pick from.", severity="warning")
            return
        from .clip import ClipScreen

        try:
            start = parse_time(self.query_one("#clip-start", Input).value)
            end = parse_time(self.query_one("#clip-end", Input).value)
        except ValueError:
            start = end = None

        def picked(result: tuple[float | None, float | None] | None) -> None:
            if result is None:
                return
            a, b = result
            self.query_one("#clip-start", Input).value = time_text(a) if a else ""
            self.query_one("#clip-end", Input).value = time_text(b) if b is not None else ""

        self.app.push_screen(ClipScreen(source, self.options.title, start, end), picked)

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

    def _update_summary(self) -> None:
        try:
            c = self.choice()
        except ValueError as exc:
            self.query_one("#summary", Static).update(f"[b $error]{escape(str(exc))}[/]")
            return
        text = ""
        if c:
            text = f"[b]{escape(c.label)}[/b]  [dim]→ {escape(self.options.where)}[/dim]"
            notes = [n for n in (self.options.note(c), extras_note(c)) if n]
            site = next((p.site for p in self.presets if p.site and p.matches(c)), "")
            if site:
                notes.insert(0, f"{presets.site_name(site)} links use this when you press d.")
            if notes:
                text += f"\n[dim]{escape(' '.join(notes))}[/dim]"
        self.query_one("#summary", Static).update(text)
        for n, p in enumerate(self.presets, 1):
            for chip in self.query(f"#preset-{n}"):
                chip.set_class(bool(c) and p.matches(c), "-active")

    @on(Input.Changed)
    @on(CycleValue.Changed)
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
