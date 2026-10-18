"""Draw an image in the terminal with half-block characters (two pixels per cell)."""

from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget


class Thumbnail(Widget):
    DEFAULT_CSS = """
    Thumbnail {
        width: 36;
        height: 9;
        content-align: center middle;
        color: $text-muted;
        background: $panel;
    }
    """

    image = reactive(None, always_update=True)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._cache: tuple[tuple[int, int], Text] | None = None

    def watch_image(self) -> None:
        self._cache = None

    def render(self) -> Text:
        width, height = self.size.width, self.size.height
        if self.image is None or width < 2 or height < 1:
            return Text("▶", style="bold")
        if self._cache and self._cache[0] == (width, height):
            return self._cache[1]
        text = half_blocks(self.image, width, height)
        self._cache = ((width, height), text)
        return text


def half_blocks(image, width: int, height: int) -> Text:
    from PIL import Image, ImageOps

    img = ImageOps.fit(image, (width, height * 2), Image.Resampling.LANCZOS)
    px = img.load()
    out = Text(no_wrap=True, overflow="crop")
    for y in range(0, height * 2, 2):
        for x in range(width):
            top, bottom = px[x, y][:3], px[x, y + 1][:3]
            out.append("▀", Style(color=_hex(top), bgcolor=_hex(bottom)))
        if y < height * 2 - 2:
            out.append("\n")
    return out


def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)
