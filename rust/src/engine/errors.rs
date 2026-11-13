//! Translate yt-dlp failures into plain language plus a suggested next step.

use regex::Regex;
use std::sync::LazyLock;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Login,
    Outdated,
    Network,
    Unavailable,
    Unsupported,
    Playlist,
    Generic,
}

#[derive(Clone, Debug)]
pub struct FriendlyError {
    pub kind: Kind,
    pub message: String,
    pub hint: String,
    /// yt-dlp's own first line, shown small so odd cases can be diagnosed.
    pub detail: String,
}

struct Rule {
    kind: Kind,
    needles: &'static [&'static str],
    message: &'static str,
    hint: &'static str,
}

const RULES: &[Rule] = &[
    Rule {
        kind: Kind::Unsupported,
        needles: &["invalid data found when processing input", "isn't audio, video or an image"],
        message: "Carrot Leaf can't read this file.",
        hint: "It may be damaged, or it isn't audio, video or an image.",
    },
    Rule {
        kind: Kind::Playlist,
        needles: &["playlist or channel"],
        message: "That's a playlist or channel.",
        hint: "Paste a link to a single video.",
    },
    Rule {
        kind: Kind::Login,
        needles: &["not a bot"],
        message: "YouTube wants to check you're not a bot.",
        hint: "It does this after many requests from one connection and usually clears within an \
               hour. Using cookies from a browser where you're signed in to YouTube gets past it now.",
    },
    Rule {
        kind: Kind::Login,
        needles: &[
            "sign in to confirm your age",
            "age-restricted",
            "inappropriate for some users",
            "members-only",
            "join this channel",
            "sign in to confirm you",
            "login required",
            "requires authentication",
            "use --cookies",
        ],
        message: "This video needs you to be signed in.",
        hint: "Use cookies from your browser to sign in.",
    },
    Rule {
        kind: Kind::Unavailable,
        needles: &[
            "private video",
            "video unavailable",
            "has been removed",
            "not available in your country",
            "this video is not available",
            "premieres in",
        ],
        message: "This video isn't available.",
        hint: "It may be private, removed, region-locked or not out yet.",
    },
    Rule {
        kind: Kind::Unsupported,
        needles: &["unsupported url", "is not a valid url"],
        message: "That link isn't a video Carrot Leaf can read.",
        hint: "Check the link and try again.",
    },
    Rule {
        kind: Kind::Outdated,
        needles: &[
            "http error 403",
            "signature extraction failed",
            "nsig extraction failed",
            "unable to extract",
            "requested format is not available",
            "n challenge",
            "please report this issue",
            "only images are available",
        ],
        message: "The site may have changed how it serves videos.",
        hint: "Update yt-dlp (press u) and try again.",
    },
    Rule {
        kind: Kind::Network,
        needles: &[
            "getaddrinfo",
            "network is unreachable",
            "timed out",
            "connection refused",
            "temporary failure in name resolution",
            "nodename nor servname",
            "connection reset",
        ],
        message: "Couldn't reach the site.",
        hint: "Check your internet connection and try again.",
    },
];

static PREFIX: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(ERROR:\s*)?(\[[^\]]+\]\s*)?([\w-]+:\s+)?").unwrap());
static ANSI: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\x1b\[[0-9;]*m").unwrap());

pub fn clean(text: &str) -> String {
    let stripped = ANSI.replace_all(text, "");
    let line = stripped.trim().lines().next().unwrap_or("");
    let out = PREFIX.replace(line, "").trim().to_string();
    if out.is_empty() { "Something went wrong.".into() } else { out }
}

/// The line that says what went wrong in a yt-dlp run's stderr: its ERROR line, else the last.
pub fn error_line(stderr: &str) -> String {
    let lines: Vec<&str> = stderr.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
    lines
        .iter()
        .find(|l| l.starts_with("ERROR:"))
        .or(lines.last())
        .map(|s| s.to_string())
        .unwrap_or_default()
}

pub fn explain(text: &str) -> FriendlyError {
    let low = text.to_lowercase();
    for r in RULES {
        if r.needles.iter().any(|n| low.contains(n)) {
            return FriendlyError { kind: r.kind, message: r.message.into(), hint: r.hint.into(), detail: clean(text) };
        }
    }
    if low.contains("ffmpeg") && (low.contains("not found") || low.contains("not installed")) {
        return FriendlyError {
            kind: Kind::Generic,
            message: "ffmpeg is missing.".into(),
            hint: "Install ffmpeg, or reinstall Carrot Leaf.".into(),
            detail: String::new(),
        };
    }
    if low.contains("yt-dlp") && low.contains("no such file") {
        return FriendlyError {
            kind: Kind::Generic,
            message: "yt-dlp is missing.".into(),
            hint: "Install yt-dlp (brew install yt-dlp, or pipx install yt-dlp).".into(),
            detail: String::new(),
        };
    }
    FriendlyError { kind: Kind::Generic, message: clean(text), hint: String::new(), detail: String::new() }
}
