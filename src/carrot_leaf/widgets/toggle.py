"""A checkbox that reads as on or off at a glance: `[✓]` in the accent colour, or `[ ]`.

Textual's own draws a dim `X` when off, which looks ticked in most themes.
"""

from __future__ import annotations

from textual.content import Content
from textual.widgets import Checkbox


class Toggle(Checkbox):
    DEFAULT_CSS = """
    Toggle {
        & > .toggle--button {
            color: $text-muted;
            background: transparent;
        }
        &.-on > .toggle--button {
            color: $primary;
            background: transparent;
            text-style: bold;
        }
    }
    """

    def __init__(self, label: str = "", value: bool = False, **kwargs) -> None:
        kwargs.setdefault("compact", True)
        super().__init__(label, value, **kwargs)

    @property
    def _button(self) -> Content:
        style = self.get_visual_style("toggle--button")
        return Content.assemble(("[✓]" if self.value else "[ ]", style))
