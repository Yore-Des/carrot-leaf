"""The CARROT wordmark: solid letters with a box-drawn shadow, shaded top to bottom."""

from __future__ import annotations

from rich.text import Text

# figlet "ANSI Shadow". Block cells are the letter faces; box-drawing cells are the shadow.
BIG = [
    " ██████╗ █████╗ ██████╗ ██████╗  ██████╗ ████████╗",
    "██╔════╝██╔══██╗██╔══██╗██╔══██╗██╔═══██╗╚══██╔══╝",
    "██║     ███████║██████╔╝██████╔╝██║   ██║   ██║   ",
    "██║     ██╔══██║██╔══██╗██╔══██╗██║   ██║   ██║   ",
    "╚██████╗██║  ██║██║  ██║██║  ██║╚██████╔╝   ██║   ",
    " ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝    ╚═╝   ",
]
SMALL = [
    "  ___   _   ___ ___  ___ _____ ",
    " / __| /_\\ | _ \\ _ \\/ _ \\_   _|",
    "| (__ / _ \\|   /   / (_) || |  ",
    " \\___/_/ \\_\\_|_\\_|_\\\\___/ |_|  ",
]

FACE = "█"
BIG_WIDTH = len(BIG[0])
SMALL_WIDTH = len(SMALL[0])


def render(width: int, gradient: list[str], shadow: str) -> Text:
    """Logo that fits in `width` columns.

    `gradient` colours the letter faces row by row, top to bottom (its last colour repeats);
    `shadow` colours the box-drawn edges. Lines are equal width so the block centres as one.
    """
    out = Text(no_wrap=True, overflow="crop")
    if width >= BIG_WIDTH:
        for row, line in enumerate(BIG):
            if row:
                out.append("\n")
            face = gradient[min(row, len(gradient) - 1)]
            for ch in line:
                out.append(ch, f"bold {face}" if ch == FACE else shadow)
    elif width >= SMALL_WIDTH:
        for row, line in enumerate(SMALL):
            if row:
                out.append("\n")
            out.append(line, f"bold {gradient[min(row, len(gradient) - 1)]}")
    else:
        out.append("CARROT", f"bold {gradient[0]}")
    return out
