"""Translate yt-dlp failures into plain language plus a suggested next step."""

from __future__ import annotations

import re
from dataclasses import dataclass

LOGIN = "login"
OUTDATED = "outdated"
NETWORK = "network"
UNAVAILABLE = "unavailable"
UNSUPPORTED = "unsupported"
PLAYLIST = "playlist"
GENERIC = "generic"


@dataclass(frozen=True)
class FriendlyError:
    kind: str
    message: str
    hint: str = ""


_RULES: list[tuple[str, tuple[str, ...], str, str]] = [
    (
        PLAYLIST,
        ("playlist or channel",),
        "That's a playlist or channel.",
        "Paste a link to a single video.",
    ),
    (
        LOGIN,
        (
            "sign in to confirm your age",
            "age-restricted",
            "inappropriate for some users",
            "members-only",
            "join this channel",
            "sign in to confirm you",
            "login required",
            "requires authentication",
            "use --cookies",
        ),
        "This video needs you to be signed in.",
        "Use cookies from your browser to sign in.",
    ),
    (
        UNAVAILABLE,
        (
            "private video",
            "video unavailable",
            "has been removed",
            "not available in your country",
            "this video is not available",
            "premieres in",
        ),
        "This video isn't available.",
        "It may be private, removed, region-locked or not out yet.",
    ),
    (
        UNSUPPORTED,
        ("unsupported url", "is not a valid url"),
        "That link isn't a video Carrot Leaf can read.",
        "Check the link and try again.",
    ),
    (
        OUTDATED,
        (
            "http error 403",
            "signature extraction failed",
            "nsig extraction failed",
            "unable to extract",
            "requested format is not available",
            "n challenge",
            "please report this issue",
            "only images are available",
        ),
        "The site may have changed how it serves videos.",
        "Update yt-dlp (press u) and try again.",
    ),
    (
        NETWORK,
        (
            "getaddrinfo",
            "network is unreachable",
            "timed out",
            "connection refused",
            "temporary failure in name resolution",
            "nodename nor servname",
            "connection reset",
        ),
        "Couldn't reach the site.",
        "Check your internet connection and try again.",
    ),
]

_PREFIX = re.compile(r"^(ERROR:\s*)?(\[[^\]]+\]\s*)?([\w-]+:\s+)?")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def clean(text: str) -> str:
    line = _ANSI.sub("", text).strip().splitlines()[0] if text.strip() else ""
    return _PREFIX.sub("", line).strip() or "Something went wrong."


def explain(exc: BaseException | str) -> FriendlyError:
    text = str(exc)
    low = text.lower()
    for kind, needles, message, hint in _RULES:
        if any(n in low for n in needles):
            return FriendlyError(kind, message, hint)
    if "ffmpeg" in low and ("not found" in low or "not installed" in low):
        return FriendlyError(
            GENERIC, "ffmpeg is missing.", "Install ffmpeg, or reinstall Carrot Leaf."
        )
    return FriendlyError(GENERIC, clean(text))
