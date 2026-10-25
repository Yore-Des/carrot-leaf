"""A setting shown as its value that steps through its choices.

Enter, space or → moves to the next choice, ← to the previous, and a click steps too. It
takes one row and lines up with its label on the left, so a panel of settings reads as two
clean columns.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget


class CycleValue(Widget, can_focus=True):
    DEFAULT_CSS = """
    CycleValue {
        width: 22;
        height: 1;
        padding: 0 1;
        color: $foreground;
    }
    CycleValue:hover {
        background: $panel;
    }
    CycleValue:focus {
        background: $primary 25%;
        color: $primary;
        text-style: bold;
    }
    CycleValue.-off {
        color: $text-muted;
    }
    """
    BINDINGS = [
        Binding("enter,space,right", "step(1)", "Next", show=False),
        Binding("left", "step(-1)", "Previous", show=False),
    ]

    class Changed(Message):
        def __init__(self, cycle: CycleValue) -> None:
            super().__init__()
            self.cycle = cycle

        @property
        def control(self) -> CycleValue:
            return self.cycle

    def __init__(self, choices: list[tuple[str, Any]], value: Any = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._choices = list(choices)
        values = [v for _, v in self._choices]
        self._index = values.index(value) if value in values else 0
        self._sync_class()

    @property
    def value(self) -> Any:
        return self._choices[self._index][1]

    @value.setter
    def value(self, value: Any) -> None:
        values = [v for _, v in self._choices]
        if value not in values:
            raise ValueError(f"{value!r} isn't one of the choices")
        if values.index(value) != self._index:
            self._index = values.index(value)
            self._changed()

    @property
    def label(self) -> str:
        return self._choices[self._index][0]

    def set_choices(self, choices: list[tuple[str, Any]], value: Any = None) -> None:
        """Swap the choices, keeping `value` (or the current value) when it's still there."""
        keep = self.value if value is None else value
        self._choices = list(choices)
        values = [v for _, v in self._choices]
        self._index = values.index(keep) if keep in values else 0
        self._changed()

    def action_step(self, delta: int) -> None:
        self._index = (self._index + delta) % len(self._choices)
        self._changed()

    def on_click(self) -> None:
        self.focus()
        self.action_step(1)

    def _changed(self) -> None:
        self._sync_class()
        self.refresh()
        self.post_message(self.Changed(self))

    def _sync_class(self) -> None:
        # "Off" and other do-nothing values read quieter than ones that change something.
        self.set_class(self.value in ("", False, None), "-off")

    def render(self) -> Text:
        # The value starts where a text field's would, so a column of values and fields
        # lines up; while focused, a ‹ › marker sits at the far right without moving it.
        width = max(self.content_size.width, 1)
        if not self.has_focus:
            return Text(self.label, no_wrap=True, overflow="ellipsis")
        room = max(width - 4, 1)
        label = self.label if len(self.label) <= room else self.label[: room - 1] + "…"
        return Text(f"{label.ljust(room)}  ‹›"[:width], no_wrap=True)

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()
