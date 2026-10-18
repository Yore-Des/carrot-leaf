"""Settings: download folder and browser cookies."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Label, OptionList, Select, Static
from textual.widgets.option_list import Option

from ..config import Settings

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
                yield Button("browse", id="browse")
            yield FolderTree(Path.home(), id="tree")
            yield Static(id="folder-error")
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
                yield Button("save", id="save", variant="primary")
                yield Button("cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#tree").display = False
        self.query_one("#folder", Input).focus()

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
        cookies = self.query_one("#cookies", Select).value
        self.dismiss(
            replace(
                self.settings,
                download_dir=str(path),
                cookies_browser=cookies if isinstance(cookies, str) else "",
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
                yield Button("cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _picked(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
