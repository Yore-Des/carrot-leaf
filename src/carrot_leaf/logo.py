"""The CARROT wordmark: extruded 3D blocks when there's room, flat text when there isn't."""

from __future__ import annotations

from rich.text import Text

CARROT = [
    " ██████  █████  ██████  ██████   ██████  ████████",
    "██      ██   ██ ██   ██ ██   ██ ██    ██    ██   ",
    "██      ███████ ██████  ██████  ██    ██    ██   ",
    "██      ██   ██ ██   ██ ██   ██ ██    ██    ██   ",
    " ██████ ██   ██ ██   ██ ██   ██  ██████     ██   ",
]
SMALL = [
    "  ___   _   ___ ___  ___ _____ ",
    " / __| /_\\ | _ \\ _ \\/ _ \\_   _|",
    "| (__ / _ \\|   /   / (_) || |  ",
    " \\___/_/ \\_\\_|_\\_|_\\\\___/ |_|  ",
]

DEPTH = 2  # extrusion: one step to the right and down per level
FACE, SIDE, SHADOW = "█", "▓", "░"


def extrude(rows: list[str]) -> list[str]:
    """Face cells become FACE; the copies behind them, offset down-right, become SIDE then SHADOW."""
    height, width = len(rows), len(rows[0])
    grid = [[" "] * (width + DEPTH) for _ in range(height + DEPTH)]
    for depth, glyph in ((2, SHADOW), (1, SIDE)):
        for r, row in enumerate(rows):
            for c, ch in enumerate(row):
                if ch != " ":
                    grid[r + depth][c + depth] = glyph
    for r, row in enumerate(rows):
        for c, ch in enumerate(row):
            if ch != " ":
                grid[r][c] = FACE
    return ["".join(line) for line in grid]


BIG = extrude(CARROT)
BIG_WIDTH = len(BIG[0])
SMALL_WIDTH = len(SMALL[0])


def render(width: int, face: str, side: str, shadow: str) -> Text:
    """Logo that fits in `width` columns.

    Every line is padded to the same width so the block centres as one piece.
    """
    out = Text(no_wrap=True, overflow="crop")
    if width >= BIG_WIDTH:
        styles = {FACE: face, SIDE: side, SHADOW: shadow}
        for i, line in enumerate(BIG):
            if i:
                out.append("\n")
            for ch in line:
                out.append(ch, styles.get(ch))
        return out
    elif width >= SMALL_WIDTH:
        out.append("\n".join(SMALL), f"bold {face}")
    else:
        out.append("CARROT", f"bold {face}")
    return out
