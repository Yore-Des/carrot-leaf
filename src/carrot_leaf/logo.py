"""The Carrot Leaf logo at three sizes, picked to fit the terminal width."""

from __future__ import annotations

from rich.text import Text

CARROT = [
    ("  \\ | /  ", "leaf"),
    ("   \\|/   ", "leaf"),
    (" ▐█████▌ ", "carrot"),
    ("  ▐███▌  ", "carrot"),
    ("   ▐█▌   ", "carrot"),
    ("    ▀    ", "carrot"),
]

BIG_CARROT = [
    " ██████╗ █████╗ ██████╗ ██████╗  ██████╗ ████████╗",
    "██╔════╝██╔══██╗██╔══██╗██╔══██╗██╔═══██╗╚══██╔══╝",
    "██║     ███████║██████╔╝██████╔╝██║   ██║   ██║   ",
    "██║     ██╔══██║██╔══██╗██╔══██╗██║   ██║   ██║   ",
    "╚██████╗██║  ██║██║  ██║██║  ██║╚██████╔╝   ██║   ",
    " ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝    ╚═╝   ",
]
BIG_LEAF = [
    "██╗     ███████╗ █████╗ ███████╗",
    "██║     ██╔════╝██╔══██╗██╔════╝",
    "██║     █████╗  ███████║█████╗  ",
    "██║     ██╔══╝  ██╔══██║██╔══╝  ",
    "███████╗███████╗██║  ██║██║     ",
    "╚══════╝╚══════╝╚═╝  ╚═╝╚═╝     ",
]
SMALL = [
    "  ___   _   ___ ___  ___ _____   _    ___   _   ___ ",
    " / __| /_\\ | _ \\ _ \\/ _ \\_   _| | |  | __| /_\\ | __|",
    "| (__ / _ \\|   /   / (_) || |   | |__| _| / _ \\| _| ",
    " \\___/_/ \\_\\_|_\\_|_\\\\___/ |_|   |____|___/_/ \\_\\_|  ",
]
SMALL_SPLIT = 31  # column where "LEAF" starts in SMALL

BIG_WIDTH = len(CARROT[0]) + 2 + len(BIG_CARROT[0]) + 1 + len(BIG_LEAF[0])
SMALL_WIDTH = len(CARROT[0]) + 2 + len(SMALL[0])


def render(width: int, carrot: str, leaf: str) -> Text:
    """Logo that fits in `width` columns, coloured with the two theme colours."""
    styles = {"carrot": f"bold {carrot}", "leaf": f"bold {leaf}"}
    out = Text(no_wrap=True, overflow="crop")
    if width >= BIG_WIDTH:
        for i, (art, part) in enumerate(CARROT):
            out.append(art, styles[part])
            out.append("  ")
            out.append(BIG_CARROT[i], styles["carrot"])
            out.append(" ")
            out.append(BIG_LEAF[i], styles["leaf"])
            out.append("\n")
    elif width >= SMALL_WIDTH:
        art_rows = CARROT[1:5]
        for i, (art, part) in enumerate(art_rows):
            out.append(art, styles[part])
            out.append("  ")
            out.append(SMALL[i][:SMALL_SPLIT], styles["carrot"])
            out.append(SMALL[i][SMALL_SPLIT:], styles["leaf"])
            out.append("\n")
    else:
        out.append("\\|/ ", styles["leaf"])
        out.append("CARROT ", styles["carrot"])
        out.append("LEAF", styles["leaf"])
    out.rstrip()
    return out
