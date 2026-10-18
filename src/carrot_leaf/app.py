"""The Textual application."""

from __future__ import annotations

from textual.app import App

from . import config
from .screens.main import MainScreen
from .theme import THEMES


class CarrotLeafApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "Carrot Leaf"
    ENABLE_COMMAND_PALETTE = True

    def __init__(self, initial_url: str = "") -> None:
        super().__init__()
        self.initial_url = initial_url
        self.settings = config.load()

    def on_mount(self) -> None:
        for theme in THEMES:
            self.register_theme(theme)
        self.theme = "carrot"

    def get_default_screen(self) -> MainScreen:
        return MainScreen(self.initial_url)

    def save_settings(self, settings: config.Settings) -> None:
        self.settings = settings
        try:
            config.save(settings)
        except OSError as exc:
            self.notify(f"Couldn't save settings: {exc}", severity="error")
