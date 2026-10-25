"""Settings: download folder, file names, the queue, browser cookies and desktop extras."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DirectoryTree,
    Input,
    Label,
    OptionList,
    Select,
    Static,
)
from textual.widgets.option_list import Option

from ..config import Settings
from ..engine.options import TEMPLATE_FIELDS, template_error, template_preview
from ..widgets.toggle import Toggle

PARALLEL = [("One at a time", 1), ("2 at once", 2), ("3 at once", 3), ("4 at once", 4)]

BROWSERS = [
    ("Chrome", "chrome"),
    ("Firefox", "firefox"),
    ("Safari", "safari"),
    ("Edge", "edge"),
    ("Brave", "brave"),
    ("Opera", "opera"),
    ("Vivaldi", "vivaldi"),
    ("Chromium", "chromium"),
]


class FolderTree(DirectoryTree):
    def filter_paths(self, paths):
        return [p for p in paths if not p.name.startswith(".") and _is_dir(p)]


def _is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:
        return False


class SettingsScreen(ModalScreen[Settings | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="settings"):
            yield Static("[b]Settings[/b]", id="dialog-title")
            yield Label("Save downloads to", classes="field-label")
            with Horizontal(id="folder-row"):
                yield Input(self.settings.download_dir, id="folder")
                yield Button("Browse", id="browse")
            yield FolderTree(Path.home(), id="tree")
            yield Static(id="folder-error")
            yield Label("Name downloads", classes="field-label")
            yield Input(self.settings.filename_template, id="template")
            yield Static(id="template-preview")
            with Horizontal(classes="settings-row"):
                yield Label("Downloads run", classes="extra-label")
                yield Select(
                    PARALLEL,
                    value=self.settings.parallel_downloads
                    if self.settings.parallel_downloads in (1, 2, 3, 4)
                    else 1,
                    allow_blank=False,
                    compact=True,
                    id="parallel",
                )
            yield Toggle(
                "Offer links I copy when I come back to Carrot",
                self.settings.watch_clipboard,
                id="watch-clipboard",
                compact=True,
            )
            yield Toggle(
                "Notify me when downloads finish in the background",
                self.settings.notify_when_done,
                id="notify",
                compact=True,
            )
            yield Toggle(
                "Keep captions so I can search what was said (press /)",
                self.settings.keep_transcripts,
                id="keep-transcripts",
                compact=True,
            )
            yield Label(
                "Browser cookies (for age-restricted or members-only videos)", classes="field-label"
            )
            yield Select(
                [("Off", ""), *BROWSERS],
                value=self.settings.cookies_browser,
                allow_blank=False,
                id="cookies",
            )
            yield Static(
                "[dim]Carrot Leaf reads that browser's YouTube login. On a Mac you may see a "
                "Keychain prompt.[/dim]",
                id="cookies-note",
            )
            with Horizontal(id="dialog-actions"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#tree").display = False
        self.query_one("#folder", Input).focus()
        self._preview(self.settings.filename_template)

    @on(Input.Changed, "#template")
    def _template_changed(self, event: Input.Changed) -> None:
        self._preview(event.value)

    def _preview(self, template: str) -> None:
        out = self.query_one("#template-preview", Static)
        if problem := template_error(template):
            out.update(f"[$error]{escape(problem)}[/]")
            return
        fields = " ".join(f"{{{f}}}" for f in TEMPLATE_FIELDS)
        out.update(
            f"[dim]→[/dim] {escape(template_preview(template))}\n"
            f"[dim]{escape(fields)}  · use / for folders[/dim]"
        )

    @on(Button.Pressed, "#browse")
    def _browse(self) -> None:
        tree = self.query_one("#tree", FolderTree)
        tree.display = not tree.display
        if tree.display:
            tree.focus()

    @on(DirectoryTree.DirectorySelected)
    def _picked(self, event: DirectoryTree.DirectorySelected) -> None:
        self.query_one("#folder", Input).value = str(event.path)

    @on(Input.Submitted, "#folder")
    @on(Input.Submitted, "#template")
    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        raw = self.query_one("#folder", Input).value.strip()
        path = Path(raw).expanduser() if raw else None
        error = self.query_one("#folder-error", Static)
        if path is None:
            error.update("[$error]Choose a folder.[/]")
            return
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            error.update(f"[$error]Can't use that folder: {exc.strerror or exc}[/]")
            return
        template = self.query_one("#template", Input).value.strip()
        if template_error(template):
            self.query_one("#template", Input).focus()
            return
        cookies = self.query_one("#cookies", Select).value
        parallel = self.query_one("#parallel", Select).value
        self.dismiss(
            replace(
                self.settings,
                download_dir=str(path),
                cookies_browser=cookies if isinstance(cookies, str) else "",
                filename_template=template,
                parallel_downloads=parallel if isinstance(parallel, int) else 1,
                watch_clipboard=self.query_one("#watch-clipboard", Checkbox).value,
                notify_when_done=self.query_one("#notify", Checkbox).value,
                keep_transcripts=self.query_one("#keep-transcripts", Checkbox).value,
            )
        )

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class CookiesScreen(ModalScreen[str | None]):
    """Quick browser pick when a video turns out to need a login."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="cookies"):
            yield Static("[b]Sign in with your browser[/b]", id="dialog-title")
            yield Static(
                "Pick the browser where you're signed in to the site. Carrot Leaf will use "
                "its cookies for this and future downloads (change it any time in Settings).\n"
                "[dim]On a Mac you may see a Keychain prompt.[/dim]"
            )
            yield OptionList(*(Option(name, id=key) for name, key in BROWSERS), id="browsers")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _picked(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
