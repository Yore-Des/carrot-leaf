"""Carrot orange, in dark and light variants.

Every colour is one of the 256 xterm colours, so the themes look the same in terminals
without true colour (macOS Terminal.app) as in ones with it. Green is kept for success only.
"""

from textual.theme import Theme

CARROT_DARK = Theme(
    name="carrot",
    primary="#ff8700",
    secondary="#ffaf5f",
    accent="#ffaf5f",
    warning="#ffd700",
    error="#ff5f5f",
    success="#87d75f",
    foreground="#e4e4e4",
    background="#121212",
    surface="#121212",
    panel="#303030",
    dark=True,
)

CARROT_LIGHT = Theme(
    name="carrot-light",
    primary="#d75f00",
    secondary="#af5f00",
    accent="#af5f00",
    warning="#af8700",
    error="#d70000",
    success="#5f8700",
    foreground="#1c1c1c",
    background="#eeeeee",
    surface="#eeeeee",
    panel="#d0d0d0",
    dark=False,
)

THEMES = (CARROT_DARK, CARROT_LIGHT)
