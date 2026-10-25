"""Name the current Convert setup as a preset, and maybe make it a site's default."""

from __future__ import annotations

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from ..engine.options import Choice
from ..presets import Preset, default_for, from_choice, site_name
from ..widgets.toggle import Toggle


class PresetSaveScreen(ModalScreen[Preset | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, choice: Choice, site: str = "") -> None:
        super().__init__()
        self.choice, self.site = choice, site

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="preset-save"):
            yield Static("[b]Save as a preset[/b]", id="dialog-title")
            yield Static(f"[dim]{escape(self.choice.label)}[/dim]", id="preset-what")
            with Horizontal(classes="settings-row"):
                yield Label("Name", classes="preset-label")
                yield Input(placeholder="Podcast", id="preset-name", compact=True)
            if self.site:
                current = default_for(self.site)
                with Horizontal(classes="settings-row"):
                    yield Label("", classes="preset-label")
                    yield Toggle(f"Default for {site_name(self.site)} links", id="preset-site")
                if current:
                    yield Static(
                        f"[dim]Replaces {escape(current.name)} as the default.[/dim]",
                        classes="preset-note",
                    )
            yield Static(id="preset-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#preset-name", Input).focus()
        self._note()

    @on(Toggle.Changed, "#preset-site")
    def _note(self) -> None:
        on_ = bool(self.query("#preset-site")) and self.query_one("#preset-site", Toggle).value
        for note in self.query(".preset-note"):
            note.display = on_

    @on(Input.Submitted, "#preset-name")
    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        name = self.query_one("#preset-name", Input).value.strip()
        if not name:
            self.query_one("#preset-error", Static).update("[$error]Give it a name.[/]")
            self.query_one("#preset-name", Input).focus()
            return
        site = (
            self.site
            if self.query("#preset-site") and self.query_one("#preset-site", Toggle).value
            else ""
        )
        self.dismiss(from_choice(name[:24], self.choice, site))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
