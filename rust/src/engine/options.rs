//! Turn a format/quality choice into yt-dlp arguments. Pure: no I/O.

use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::LazyLock;

pub const VIDEO_FORMATS: &[&str] = &["mp4", "mkv", "webm", "mov", "gif"];
pub const AUDIO_FORMATS: &[&str] = &["mp3", "m4a", "opus", "flac", "wav", "ogg"];

pub const VIDEO_HEIGHTS: &[u32] = &[2160, 1440, 1080, 720, 480, 360];
pub const GIF_HEIGHTS: &[u32] = &[480, 360, 240];
pub const AUDIO_BITRATES: &[&str] = &["320", "192", "128"];
/// Image sizes: "best" keeps the original, "50" halves it, others cap the long side in pixels.
pub const IMAGE_SIZES: &[&str] = &["3840", "1920", "1280"];
/// Frames per second to pull out of a video; "best" keeps every frame.
pub const FRAME_RATES: &[&str] = &["best", "10", "1", "0.1"];
pub const LOSSLESS_AUDIO: &[&str] = &["flac", "wav"];

pub fn frame_rate_label(q: &str) -> String {
    match q {
        "best" => "every frame".into(),
        "10" => "10/s".into(),
        "1" => "1/s".into(),
        "0.1" => "1 every 10s".into(),
        other => other.to_string(),
    }
}

/// Containers that can carry embedded cover art.
const THUMBNAIL_EXTS: &[&str] = &["mp4", "mov", "mkv", "m4a", "mp3", "flac", "opus", "ogg"];
/// Containers whose "best" prefers H.264 so nothing has to be re-encoded to play everywhere.
pub const H264_FIRST: &[&str] = &["mp4", "mov"];

pub const DEFAULT_TEMPLATE: &str = "{title} [{id}]";
pub const TEMPLATE_FIELDS: &[(&str, &str)] = &[
    ("title", "%(title).180B"),
    ("id", "%(id)s"),
    ("channel", "%(channel,uploader|Unknown)s"),
    ("uploader", "%(uploader,channel|Unknown)s"),
    ("date", "%(upload_date>%Y-%m-%d|)s"),
    ("year", "%(upload_date>%Y|)s"),
    ("site", "%(extractor_key)s"),
    ("height", "%(height|)sp"),
    // Filled in by Carrot for playlist downloads, which fetch each video on its own.
    ("playlist", ""),
    ("index", ""),
];
const TEMPLATE_SAMPLE: &[(&str, &str)] = &[
    ("title", "Big Buck Bunny"),
    ("id", "aqz-KE-bpKQ"),
    ("channel", "Blender"),
    ("uploader", "Blender"),
    ("date", "2014-11-10"),
    ("year", "2014"),
    ("site", "Youtube"),
    ("height", "1080p"),
    ("playlist", "Open Movies"),
    ("index", "03"),
];
static FIELD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\{(\w+)\}").unwrap());
pub const OUTPUT_TEMPLATE: &str = "%(title).180B [%(id)s].%(ext)s";

pub const SPONSOR_REMOVE: &[&str] = &["sponsor", "selfpromo", "interaction"];
pub const SPONSOR_MARK: &[&str] =
    &["sponsor", "selfpromo", "interaction", "intro", "outro", "preview", "filler"];
pub const SUBTITLE_EMBED_EXTS: &[&str] = &["mp4", "mkv", "webm"];
pub const LOUDNORM: &str = "loudnorm=I=-16:TP=-1.5:LRA=11";

/// Everything past format and quality. The defaults change nothing.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Extras {
    pub start: Option<f64>,
    pub end: Option<f64>,
    pub subtitles: String,
    pub sub_langs: String,
    pub sponsorblock: String,
    pub split_chapters: bool,
    pub normalize: bool,
    pub strip_metadata: bool,
    pub max_kb: Option<i64>,
}

impl Default for Extras {
    fn default() -> Self {
        Extras {
            start: None,
            end: None,
            subtitles: String::new(),
            sub_langs: "en".into(),
            sponsorblock: String::new(),
            split_chapters: false,
            normalize: false,
            strip_metadata: false,
            max_kb: None,
        }
    }
}

impl Extras {
    pub fn clipped(&self) -> bool {
        self.start.is_some() || self.end.is_some()
    }

    pub fn label(&self) -> String {
        let mut bits = vec![];
        if self.clipped() {
            let end = match self.end {
                Some(e) => time_text(Some(e)),
                None => "end".into(),
            };
            bits.push(format!("{}–{}", time_text(Some(self.start.unwrap_or(0.0))), end));
        }
        if !self.subtitles.is_empty() {
            bits.push(if self.subtitles == "embed" { "subs".into() } else { "+srt".into() });
        }
        if self.sponsorblock == "remove" {
            bits.push("no sponsors".into());
        } else if self.sponsorblock == "mark" {
            bits.push("sponsor chapters".into());
        }
        if self.split_chapters {
            bits.push("by chapter".into());
        }
        if self.normalize {
            bits.push("even volume".into());
        }
        if self.strip_metadata {
            bits.push("no metadata".into());
        }
        if let Some(kb) = self.max_kb.filter(|k| *k > 0) {
            bits.push(format!("≤{kb} KB"));
        }
        bits.join(" · ")
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Choice {
    pub kind: String,
    pub fmt: String,
    pub quality: String,
    pub extras: Extras,
}

impl Choice {
    pub fn new(kind: &str, fmt: &str, quality: &str) -> Self {
        Choice { kind: kind.into(), fmt: fmt.into(), quality: quality.into(), extras: Extras::default() }
    }

    pub fn with_extras(mut self, extras: Extras) -> Self {
        self.extras = extras;
        self
    }

    pub fn label(&self) -> String {
        let extra = self.extras.label();
        if extra.is_empty() { self.base_label() } else { format!("{} · {}", self.base_label(), extra) }
    }

    pub fn base_label(&self) -> String {
        if self.kind == "frames" {
            return format!("{} frames · {}", self.fmt.to_uppercase(), frame_rate_label(&self.quality));
        }
        let q = if self.kind == "image" {
            match self.quality.as_str() {
                "best" => "original".to_string(),
                "50" => "50%".to_string(),
                q => format!("{q}px"),
            }
        } else if self.quality == "best" {
            "best".into()
        } else if self.kind == "video" {
            format!("{}p", self.quality)
        } else {
            format!("{}k", self.quality)
        };
        format!("{} · {}", self.fmt.to_uppercase(), q)
    }
}

pub fn download_choice() -> Choice {
    Choice::new("video", "mp4", "best")
}

static HMS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?$").unwrap()
});
static NUM: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\d+(\.\d+)?$").unwrap());

/// `90`, `1:30`, `1:02:03`, `1m30s` or `1:30.5` as seconds; None when blank.
pub fn parse_time(text: &str) -> Result<Option<f64>, String> {
    let text = text.trim().to_lowercase();
    if text.is_empty() {
        return Ok(None);
    }
    if let Some(m) = HMS.captures(&text) {
        if (1..=3).any(|i| m.get(i).is_some()) {
            let g = |i| m.get(i).map(|v| v.as_str().parse::<f64>().unwrap_or(0.0)).unwrap_or(0.0);
            return Ok(Some(g(1) * 3600.0 + g(2) * 60.0 + g(3)));
        }
    }
    let parts: Vec<&str> = text.split(':').collect();
    if parts.len() > 3 || !parts.iter().all(|p| NUM.is_match(p)) {
        return Err(format!("Can't read '{text}' as a time. Try 1:30 or 90."));
    }
    let mut seconds = 0.0;
    for p in parts {
        seconds = seconds * 60.0 + p.parse::<f64>().unwrap_or(0.0);
    }
    Ok(Some(seconds))
}

pub fn time_text(seconds: Option<f64>) -> String {
    let Some(seconds) = seconds.filter(|s| s.is_finite()) else { return "end".into() };
    let whole = seconds as i64;
    let (h, rem) = (whole / 3600, whole % 3600);
    let (m, s) = (rem / 60, rem % 60);
    let mut text = if h > 0 { format!("{h}:{m:02}:{s:02}") } else { format!("{m}:{s:02}") };
    let frac = ((seconds - whole as f64) * 10.0).round() / 10.0;
    if frac != 0.0 {
        let f = format!("{frac:.1}");
        text.push_str(&f[1..]);
    }
    text
}

/// Why this clip can't work, or "" when it can.
pub fn check_clip(start: Option<f64>, end: Option<f64>, duration: Option<f64>) -> String {
    if let (Some(s), Some(e)) = (start, end) {
        if e <= s {
            return "The end has to come after the start.".into();
        }
    }
    if let (Some(d), Some(s)) = (duration.filter(|d| *d > 0.0), start) {
        if s >= d {
            return format!("The video is only {} long.", time_text(Some(d)));
        }
    }
    String::new()
}

fn field_known(name: &str) -> bool {
    TEMPLATE_FIELDS.iter().any(|(k, _)| *k == name)
}

/// What's wrong with a filename template, or "" when it's fine.
pub fn template_error(template: &str) -> String {
    if template.trim().is_empty() {
        return "The name can't be empty.".into();
    }
    if let Some(unknown) = FIELD.captures_iter(template).map(|c| c[1].to_string()).find(|f| !field_known(f)) {
        let all: Vec<String> = TEMPLATE_FIELDS.iter().map(|(k, _)| format!("{{{k}}}")).collect();
        return format!("Unknown field {{{unknown}}}. Use {}", all.join(", "));
    }
    let t = template.trim();
    if t.starts_with('/') || t.starts_with('\\') || template.replace('\\', "/").split('/').any(|p| p == "..") {
        return "Keep the name inside the download folder.".into();
    }
    String::new()
}

static UNSAFE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"[<>:"/\\|?*\x00-\x1f]"#).unwrap());

/// A folder name that works on every system.
pub fn safe_name(name: &str) -> String {
    let cleaned = UNSAFE.replace_all(name, "_");
    let cleaned = cleaned.trim_matches(|c| c == ' ' || c == '.');
    let cut: String = cleaned.chars().take(120).collect();
    if cut.is_empty() { "Playlist".into() } else { cut }
}

pub fn template_preview(template: &str, ext: &str) -> String {
    let name = FIELD.replace_all(template, |c: &regex::Captures| {
        TEMPLATE_SAMPLE.iter().find(|(k, _)| *k == &c[1]).map(|(_, v)| v.to_string()).unwrap_or_else(|| c[0].to_string())
    });
    format!("{}.{ext}", name.trim())
}

/// A yt-dlp output template from `{field}` text. `fields` fills in literal values.
pub fn to_outtmpl(template: &str, fields: &HashMap<String, String>) -> String {
    let mut out = String::new();
    let mut pos = 0;
    for m in FIELD.captures_iter(template) {
        let all = m.get(0).unwrap();
        out.push_str(&template[pos..all.start()].replace('%', "%%"));
        let name = &m[1];
        if let Some(v) = fields.get(name) {
            out.push_str(&v.replace('%', "%%"));
        } else {
            out.push_str(TEMPLATE_FIELDS.iter().find(|(k, _)| *k == name).map(|(_, v)| *v).unwrap_or(""));
        }
        pos = all.end();
    }
    out.push_str(&template[pos..].replace('%', "%%"));
    let text = out.trim().replace('\\', "/");
    let parts: Vec<String> = text
        .split('/')
        .map(|p| {
            let s = p.trim_matches(|c| c == ' ' || c == '-' || c == '_' || c == '.');
            if s.is_empty() { "_".to_string() } else { s.to_string() }
        })
        .collect();
    format!("{}.%(ext)s", parts.join("/"))
}

/// Quality options for a video format, limited to what the video has.
pub fn video_qualities(fmt: &str, max_height: Option<u32>) -> Vec<String> {
    let heights = if fmt == "gif" { GIF_HEIGHTS } else { VIDEO_HEIGHTS };
    let kept: Vec<String> =
        heights.iter().filter(|h| max_height.is_none_or(|m| **h <= m)).map(|h| h.to_string()).collect();
    if fmt == "gif" {
        return if kept.is_empty() { vec!["240".into()] } else { kept };
    }
    let mut out = vec!["best".to_string()];
    out.extend(kept);
    out
}

pub fn audio_qualities(fmt: &str) -> Vec<String> {
    if LOSSLESS_AUDIO.contains(&fmt) {
        vec!["best".into()]
    } else {
        std::iter::once("best").chain(AUDIO_BITRATES.iter().copied()).map(String::from).collect()
    }
}

/// Size options for an image: original, half, and caps smaller than the image.
pub fn image_sizes(long_side: Option<u32>) -> Vec<String> {
    let mut out = vec!["best".to_string(), "50".to_string()];
    for s in IMAGE_SIZES {
        if long_side.is_none_or(|l| s.parse::<u32>().unwrap() < l) {
            out.push(s.to_string());
        }
    }
    out
}

/// A palette pass makes GIFs look far better than ffmpeg's default dithering.
pub fn gif_filter(height: &str) -> String {
    format!(
        "fps=10,scale=-2:{height}:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];\
         [b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )
}

pub struct BuildArgs<'a> {
    pub ffmpeg_location: Option<&'a str>,
    pub cookies_browser: &'a str,
    pub native_vorbis: bool,
    pub template: &'a str,
    pub fields: &'a HashMap<String, String>,
}

/// yt-dlp command-line arguments for `choice`, writing into `download_dir`.
/// The same plan as the Python version's YoutubeDL options, expressed as flags.
pub fn build_args(choice: &Choice, download_dir: &str, b: &BuildArgs) -> Vec<String> {
    let outtmpl = if b.template != DEFAULT_TEMPLATE { to_outtmpl(b.template, b.fields) } else { OUTPUT_TEMPLATE.to_string() };
    let join = |a: &str| format!("{}/{}", download_dir.trim_end_matches('/'), a);
    let mut a: Vec<String> = vec![
        "-o".into(),
        join(&outtmpl),
        "--no-playlist".into(),
        "--windows-filenames".into(),
        "--no-overwrites".into(),
        "--no-warnings".into(),
    ];
    let mut push = |a: &mut Vec<String>, items: &[&str]| a.extend(items.iter().map(|s| s.to_string()));
    if let Some(f) = b.ffmpeg_location {
        push(&mut a, &["--ffmpeg-location", f]);
    }
    if !b.cookies_browser.is_empty() {
        push(&mut a, &["--cookies-from-browser", b.cookies_browser]);
    }
    let extras = &choice.extras;
    // Audio is cut after the download (runner): yt-dlp's cut ignores the start there.
    if extras.clipped() && choice.kind != "audio" {
        let end = extras.end.map(|e| format!("{e}")).unwrap_or_else(|| "inf".into());
        push(&mut a, &["--download-sections", &format!("*{}-{end}", extras.start.unwrap_or(0.0))]);
    }
    if !extras.sponsorblock.is_empty() {
        if extras.sponsorblock == "remove" {
            push(&mut a, &["--sponsorblock-remove", &SPONSOR_REMOVE.join(",")]);
        } else {
            push(&mut a, &["--sponsorblock-mark", &SPONSOR_MARK.join(",")]);
        }
    }
    let final_ext: String;
    if choice.kind == "audio" {
        let codec = if choice.fmt == "ogg" { "vorbis" } else { choice.fmt.as_str() };
        let q = if choice.quality == "best" { "0".to_string() } else { format!("{}K", choice.quality) };
        push(&mut a, &["-f", "ba/b", "-x", "--audio-format", codec, "--audio-quality", &q]);
        if choice.fmt == "ogg" && b.native_vorbis {
            push(&mut a, &["--postprocessor-args", "ExtractAudio:-c:a vorbis -strict -2"]);
        }
        final_ext = choice.fmt.clone();
    } else if choice.fmt == "gif" {
        let height = if choice.quality != "best" { choice.quality.clone() } else { GIF_HEIGHTS[0].to_string() };
        push(&mut a, &["-f", "bv*/b", "-S", &format!("res:{height}"), "--recode-video", "gif"]);
        push(&mut a, &["--postprocessor-args", &format!("VideoConvertor:-vf {} -loop 0 -an", gif_filter(&height))]);
        final_ext = "gif".into();
    } else {
        let res = if choice.quality == "best" { "res".to_string() } else { format!("res:{}", choice.quality) };
        let sort = if H264_FIRST.contains(&choice.fmt.as_str()) && choice.quality == "best" {
            format!("vcodec:h264,acodec:aac,{res}")
        } else {
            let extra = match choice.fmt.as_str() {
                "mp4" | "mov" => ",vcodec:h264,acodec:aac",
                "webm" => ",vcodec:vp9,acodec:opus",
                _ => "",
            };
            format!("{res}{extra}")
        };
        push(&mut a, &["-f", "bv*+ba/b", "-S", &sort]);
        if choice.fmt == "mp4" {
            push(&mut a, &["--merge-output-format", "mp4", "--remux-video", "mp4"]);
        } else if choice.fmt == "mov" && choice.quality == "best" {
            push(&mut a, &["--merge-output-format", "mov/mkv", "--remux-video", "mov"]);
        } else {
            push(&mut a, &["--merge-output-format", &format!("{}/mkv", choice.fmt), "--recode-video", &choice.fmt]);
        }
        final_ext = choice.fmt.clone();
    }
    if !extras.subtitles.is_empty() && final_ext != "gif" {
        let langs: Vec<&str> = extras.sub_langs.split(',').map(str::trim).filter(|s| !s.is_empty()).collect();
        push(&mut a, &["--write-subs", "--write-auto-subs", "--sub-langs", &langs.join(",")]);
        if extras.subtitles == "embed" && SUBTITLE_EMBED_EXTS.contains(&final_ext.as_str()) {
            push(&mut a, &["--embed-subs"]);
        } else {
            push(&mut a, &["--sub-format", "srt/vtt/best", "--convert-subs", "srt"]);
        }
    }
    if final_ext != "gif" {
        push(&mut a, &["--embed-metadata", "--embed-chapters"]);
    }
    if THUMBNAIL_EXTS.contains(&final_ext.as_str()) {
        push(&mut a, &["--embed-thumbnail", "--convert-thumbnails", "jpg"]);
    }
    if extras.split_chapters && final_ext != "gif" {
        let stem = outtmpl.rsplit_once(".%(ext)s").map(|(s, _)| s).unwrap_or(&outtmpl).to_string();
        push(&mut a, &["--split-chapters", "-o", &format!("chapter:{}", join(&format!("{stem}/%(section_number)02d - %(section_title).150B.%(ext)s")))]);
    }
    a
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn times() {
        assert_eq!(parse_time("1:30").unwrap(), Some(90.0));
        assert_eq!(parse_time("1m30s").unwrap(), Some(90.0));
        assert_eq!(parse_time("90").unwrap(), Some(90.0));
        assert_eq!(parse_time("1:02:03").unwrap(), Some(3723.0));
        assert_eq!(parse_time("").unwrap(), None);
        assert!(parse_time("abc").is_err());
        assert_eq!(time_text(Some(90.0)), "1:30");
        assert_eq!(time_text(Some(90.5)), "1:30.5");
        assert_eq!(time_text(Some(3723.0)), "1:02:03");
    }

    #[test]
    fn templates() {
        let f = HashMap::new();
        assert_eq!(to_outtmpl("{channel}/{title}", &f), "%(channel,uploader|Unknown)s/%(title).180B.%(ext)s");
        assert_eq!(template_preview("{title} [{id}]", "mp4"), "Big Buck Bunny [aqz-KE-bpKQ].mp4");
        assert!(!template_error("{nope}").is_empty());
        assert!(!template_error("../x").is_empty());
        assert_eq!(safe_name("a/b: c."), "a_b_ c");
    }

    #[test]
    fn qualities() {
        assert_eq!(video_qualities("webm", Some(720)), vec!["best", "720", "480", "360"]);
        assert_eq!(video_qualities("gif", Some(200)), vec!["240"]);
        assert_eq!(audio_qualities("flac"), vec!["best"]);
    }
}
