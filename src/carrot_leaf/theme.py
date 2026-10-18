"""Carrot orange + leaf green, in dark and light variants."""

from textual.theme import Theme

CARROT_DARK = Theme(
    name="carrot",
    primary="#FF8A1F",
    secondary="#7BC74D",
    accent="#FFB45C",
    warning="#FFC145",
    error="#FF5E57",
    success="#7BC74D",
    foreground="#F5EBDD",
    background="#17120E",
    surface="#211912",
    panel="#2E2219",
    dark=True,
)

CARROT_LIGHT = Theme(
    name="carrot-light",
    primary="#D96200",
    secondary="#3F8F2A",
    accent="#B85A00",
    warning="#B07800",
    error="#C9302C",
    success="#3F8F2A",
    foreground="#2B1D12",
    background="#FFF8F0",
    surface="#FFF0E0",
    panel="#F5DFC8",
    dark=False,
)

THEMES = (CARROT_DARK, CARROT_LIGHT)
