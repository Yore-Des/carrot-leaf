"""A list of rows you tick with space, drawn with the same [✓] / [ ] marks as Toggle."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option


class TickList(OptionList):
    BINDINGS = [Binding("space", "tick", "Tick", show=False)]

    class SelectedChanged(Message):
        def __init__(self, ticks: TickList) -> None:
            super().__init__()
            self.ticks = ticks

        @property
        def control(self) -> TickList:
            return self.ticks

    def __init__(self, rows: list[Text], selected: set[int], **kwargs) -> None:
        self._rows = rows
        self._selected = set(selected)
        super().__init__(*(Option(self._prompt(i), id=str(i)) for i in range(len(rows))), **kwargs)

    @property
    def selected(self) -> list[int]:
        return sorted(self._selected)

    def _prompt(self, i: int) -> Text:
        on = i in self._selected
        mark = Text("[✓] " if on else "[ ] ", style="bold" if on else "dim")
        return Text.assemble(mark, self._rows[i])

    def _redraw(self, indexes) -> None:
        for i in indexes:
            self.replace_option_prompt_at_index(i, self._prompt(i))
        self.post_message(self.SelectedChanged(self))

    def select(self, i: int) -> None:
        self._selected.add(i)
        self._redraw([i])

    def select_all(self) -> None:
        self._selected = set(range(len(self._rows)))
        self._redraw(range(len(self._rows)))

    def deselect_all(self) -> None:
        changed = list(self._selected)
        self._selected.clear()
        self._redraw(changed)

    def action_tick(self) -> None:
        i = self.highlighted
        if i is None:
            return
        self._selected ^= {i}
        self._redraw([i])
