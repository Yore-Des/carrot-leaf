//! Fetch a video's details without downloading it, via `yt-dlp -J`.

use super::{deps, errors};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::process::{Command, Stdio};
use std::sync::LazyLock;

/// Channels list their newest videos first; past this many, the picker gets unwieldy.
pub const PLAYLIST_LIMIT: usize = 500;
const CHANNEL_TABS: &[&str] = &["/videos", "/shorts", "/streams", "/live", "/podcasts", "/releases"];

#[derive(Clone, Debug)]
pub struct VideoInfo {
    pub url: String,
    pub id: String,
    pub title: String,
    pub channel: String,
    pub duration: Option<f64>,
    pub upload_date: Option<String>,
    pub thumbnail: Option<String>,
    pub extractor: String,
    pub max_height: Option<u32>,
    pub raw: Value,
}

impl VideoInfo {
    pub fn duration_text(&self) -> String {
        match self.duration.filter(|d| *d > 0.0) {
            None => if self.raw.get("is_live").and_then(Value::as_bool).unwrap_or(false) { "live".into() } else { "—".into() },
            Some(d) => duration_text(Some(d)),
        }
    }
    pub fn chapters(&self) -> usize {
        self.raw.get("chapters").and_then(Value::as_array).map(|a| a.len()).unwrap_or(0)
    }
    /// The channel this video is from, for following it.
    pub fn channel_url(&self) -> Option<String> {
        str_of(&self.raw, "channel_url").or_else(|| str_of(&self.raw, "uploader_url"))
    }
    pub fn is_youtube(&self) -> bool {
        self.extractor.to_lowercase().starts_with("youtube")
    }
    pub fn date_text(&self) -> String {
        match &self.upload_date {
            Some(d) if d.len() == 8 => format!("{}-{}-{}", &d[..4], &d[4..6], &d[6..]),
            _ => "—".into(),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Entry {
    pub url: String,
    pub id: String,
    pub title: String,
    #[serde(default)]
    pub duration: Option<i64>,
}

impl Entry {
    pub fn duration_text(&self) -> String {
        duration_text(self.duration.map(|d| d as f64))
    }
}

#[derive(Clone, Debug)]
pub struct PlaylistInfo {
    pub url: String,
    pub id: String,
    pub title: String,
    pub channel: String,
    pub entries: Vec<Entry>,
    pub thumbnail: Option<String>,
    pub extractor: String,
    pub total: Option<i64>,
}

impl PlaylistInfo {
    pub fn truncated(&self) -> bool {
        self.total.is_some_and(|t| t > self.entries.len() as i64)
    }
    pub fn duration(&self) -> Option<i64> {
        let known: Vec<i64> = self.entries.iter().filter_map(|e| e.duration).filter(|d| *d > 0).collect();
        if known.is_empty() { None } else { Some(known.iter().sum()) }
    }
}

#[derive(Clone, Debug)]
pub enum Probed {
    Video(Box<VideoInfo>),
    Playlist(Box<PlaylistInfo>),
}

pub fn duration_text(seconds: Option<f64>) -> String {
    let Some(s) = seconds.filter(|s| *s > 0.0) else { return "—".into() };
    let s = s as i64;
    let (h, rem) = (s / 3600, s % 3600);
    let (m, s) = (rem / 60, rem % 60);
    if h > 0 { format!("{h}:{m:02}:{s:02}") } else { format!("{m}:{s:02}") }
}

pub fn str_of(v: &Value, key: &str) -> Option<String> {
    v.get(key).and_then(Value::as_str).filter(|s| !s.is_empty()).map(String::from)
}

fn num_of(v: &Value, key: &str) -> Option<f64> {
    v.get(key).and_then(Value::as_f64)
}

/// Largest resolution on offer, using the short side so vertical videos count right.
pub fn max_video_height(formats: &[Value]) -> Option<u32> {
    let mut best = 0u32;
    for f in formats {
        let vcodec = f.get("vcodec").and_then(Value::as_str);
        let (w, h) = (num_of(f, "width"), num_of(f, "height"));
        if matches!(vcodec, None | Some("none")) && h.is_none() {
            continue;
        }
        let side = match (w, h) {
            (Some(w), Some(h)) if w > 0.0 && h > 0.0 => Some(w.min(h)),
            (_, h) => h,
        };
        if let Some(s) = side {
            best = best.max(s as u32);
        }
    }
    (best > 0).then_some(best)
}

/// A small widescreen jpg is plenty for a terminal preview and loads fast.
pub fn pick_thumbnail(info: &Value) -> Option<String> {
    let mut candidates: Vec<(bool, bool, i64, String)> = vec![];
    for t in info.get("thumbnails").and_then(Value::as_array).into_iter().flatten() {
        let (Some(w), Some(h), Some(url)) = (num_of(t, "width"), num_of(t, "height"), str_of(t, "url")) else { continue };
        if w < 300.0 || h <= 0.0 {
            continue;
        }
        let widescreen = (w / h - 16.0 / 9.0).abs() < 0.1;
        candidates.push((!widescreen, url.contains(".webp"), w as i64, url));
    }
    candidates.sort();
    candidates.into_iter().next().map(|c| c.3).or_else(|| str_of(info, "thumbnail"))
}

fn run_json(url: &str, cookies_browser: &str, limit: usize, whole_list: bool) -> Result<Value, String> {
    let mut cmd = Command::new(deps::yt_dlp());
    // A watch link inside a playlist or mix means just that video, unless asked for the list.
    cmd.arg("-J").arg(if whole_list { "--yes-playlist" } else { "--no-playlist" });
    cmd.args(["--flat-playlist", "--no-warnings", "--playlist-end"]).arg(limit.to_string());
    if !cookies_browser.is_empty() {
        cmd.args(["--cookies-from-browser", cookies_browser]);
    }
    cmd.arg("--").arg(url).stdin(Stdio::null());
    let out = cmd.output().map_err(|e| format!("Couldn't run yt-dlp: {e}"))?;
    if !out.status.success() {
        let err = errors::error_line(&String::from_utf8_lossy(&out.stderr));
        return Err(if err.is_empty() { "No video found at that link.".into() } else { err });
    }
    serde_json::from_slice(&out.stdout).map_err(|_| "No video found at that link.".to_string())
}

/// Look up a video, or the list of videos in a playlist or channel.
pub fn probe(url: &str, cookies_browser: &str, limit: usize) -> Result<Probed, String> {
    probe_with(url, cookies_browser, limit, false)
}

/// `probe`, but a video link that carries a playlist or mix loads the whole list.
pub fn probe_with(url: &str, cookies_browser: &str, limit: usize, whole_list: bool) -> Result<Probed, String> {
    let mut info = run_json(&channel_home_to_videos(url), cookies_browser, limit, whole_list)?;
    if info.get("_type").and_then(Value::as_str) == Some("playlist") {
        if let Some(tab) = channel_videos_tab(&info) {
            info = run_json(&tab, cookies_browser, limit, false)?;
        }
    }
    if info.is_null() {
        return Err("No video found at that link.".into());
    }
    if info.get("_type").and_then(Value::as_str) == Some("playlist") {
        return playlist_info(&videos_tab(&info), url).map(|p| Probed::Playlist(Box::new(p)));
    }
    Ok(Probed::Video(Box::new(video_info(info, url))))
}

/// A YouTube watch link that also names a playlist: `Some(true)` for a mix (an auto-made
/// radio list, `list=RD…`), `Some(false)` for an ordinary playlist, `None` otherwise.
pub fn list_in_video_link(url: &str) -> Option<bool> {
    let rest = url.trim().split("://").last()?;
    let (host, path) = rest.split_once('/').unwrap_or((rest, ""));
    let host = host.to_lowercase();
    let youtube = host.ends_with("youtube.com") || host == "youtu.be" || host.ends_with(".youtu.be");
    if !youtube {
        return None;
    }
    let (route, query) = path.split_once('?').unwrap_or((path, ""));
    let params: Vec<(&str, &str)> = query.split(['&', '#']).filter_map(|kv| kv.split_once('=')).collect();
    let list = params.iter().find(|(k, _)| *k == "list").map(|(_, v)| *v).filter(|v| !v.is_empty())?;
    let has_video = params.iter().any(|(k, v)| *k == "v" && !v.is_empty()) || (host.contains("youtu.be") && !route.is_empty()) || route.starts_with("shorts/");
    has_video.then_some(list.starts_with("RD"))
}

static YT_CHANNEL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(https?://(?:www\.|m\.)?youtube\.com/(?:@[^/?#]+|channel/[\w-]+|c/[^/?#]+|user/[^/?#]+))/?(?:[?#].*)?$").unwrap()
});

/// A YouTube channel's home page lists every tab; go straight to its videos.
pub fn channel_home_to_videos(url: &str) -> String {
    match YT_CHANNEL.captures(url.trim()) {
        Some(m) => format!("{}/videos", &m[1]),
        None => url.to_string(),
    }
}

fn entries(info: &Value) -> Vec<&Value> {
    info.get("entries").and_then(Value::as_array).map(|a| a.iter().filter(|e| !e.is_null()).collect()).unwrap_or_default()
}

/// For a channel home page whose entries are links to its tabs, the videos tab's URL.
pub fn channel_videos_tab(info: &Value) -> Option<String> {
    let urls: Vec<String> = entries(info).iter().map(|e| str_of(e, "url").unwrap_or_default()).collect();
    let tabs: Vec<&String> = urls.iter().filter(|u| CHANNEL_TABS.iter().any(|t| u.trim_end_matches('/').ends_with(t))).collect();
    if urls.is_empty() || tabs.len() != urls.len() {
        return None;
    }
    tabs.iter().find(|u| u.trim_end_matches('/').ends_with("/videos")).or(tabs.first()).map(|s| s.to_string())
}

/// A channel home page comes back as one playlist per tab; use the videos.
pub fn videos_tab(info: &Value) -> Value {
    let es = entries(info);
    if es.is_empty() || !es.iter().all(|e| e.get("_type").and_then(Value::as_str) == Some("playlist")) {
        return info.clone();
    }
    let pick = es
        .iter()
        .find(|e| {
            let w = str_of(e, "webpage_url").or_else(|| str_of(e, "original_url")).unwrap_or_default();
            w.trim_end_matches('/').ends_with("/videos") || str_of(e, "title").unwrap_or_default().ends_with(" - Videos")
        })
        .unwrap_or(&es[0]);
    let mut out = (*pick).clone();
    if let Some(t) = str_of(info, "title").or_else(|| str_of(pick, "title")) {
        out["title"] = Value::String(t);
    }
    out
}

static VIDEOS_SUFFIX: LazyLock<Regex> = LazyLock::new(|| Regex::new(r" - Videos$").unwrap());

pub fn playlist_info(info: &Value, url: &str) -> Result<PlaylistInfo, String> {
    let mut out = vec![];
    for e in entries(info) {
        let Some(mut link) = str_of(e, "url").or_else(|| str_of(e, "webpage_url")) else { continue };
        if !link.contains("://") && str_of(e, "ie_key").as_deref() == Some("Youtube") {
            link = format!("https://www.youtube.com/watch?v={link}");
        }
        let title = str_of(e, "title").unwrap_or_default();
        if title == "[Private video]" || title == "[Deleted video]" {
            continue;
        }
        let duration = num_of(e, "duration").filter(|d| *d > 0.0).map(|d| d as i64);
        out.push(Entry {
            url: link,
            id: str_of(e, "id").unwrap_or_default(),
            title: if title.is_empty() { "Untitled".into() } else { title },
            duration,
        });
    }
    if out.is_empty() {
        return Err("That playlist has no videos Carrot Leaf can get.".into());
    }
    let title = VIDEOS_SUFFIX.replace(&str_of(info, "title").unwrap_or_default(), "").to_string();
    let first_thumb = || entries(info).iter().find_map(|e| pick_thumbnail(e));
    Ok(PlaylistInfo {
        url: str_of(info, "webpage_url").unwrap_or_else(|| url.to_string()),
        id: str_of(info, "id").unwrap_or_default(),
        title: if title.is_empty() { "Untitled playlist".into() } else { title },
        channel: str_of(info, "channel").or_else(|| str_of(info, "uploader")).unwrap_or_else(|| "—".into()),
        entries: out,
        thumbnail: pick_thumbnail(info).or_else(first_thumb),
        extractor: str_of(info, "extractor_key").unwrap_or_default(),
        total: info.get("playlist_count").and_then(Value::as_i64),
    })
}

pub fn video_info(info: Value, url: &str) -> VideoInfo {
    let formats: Vec<Value> = info.get("formats").and_then(Value::as_array).cloned().unwrap_or_default();
    VideoInfo {
        url: str_of(&info, "webpage_url").unwrap_or_else(|| url.to_string()),
        id: str_of(&info, "id").unwrap_or_default(),
        title: str_of(&info, "title").unwrap_or_else(|| "Untitled".into()),
        channel: str_of(&info, "channel").or_else(|| str_of(&info, "uploader")).unwrap_or_else(|| "—".into()),
        duration: num_of(&info, "duration"),
        upload_date: str_of(&info, "upload_date"),
        thumbnail: pick_thumbnail(&info),
        extractor: str_of(&info, "extractor_key").unwrap_or_default(),
        max_height: max_video_height(&formats),
        raw: info,
    }
}

/// Download a picture, or None if it can't be fetched.
pub fn fetch_image(url: &str, timeout_s: u64) -> Option<image::RgbImage> {
    let resp = ureq::get(url)
        .set("User-Agent", "Mozilla/5.0 carrot-leaf")
        .timeout(std::time::Duration::from_secs(timeout_s))
        .call()
        .ok()?;
    let mut data = vec![];
    std::io::Read::read_to_end(&mut resp.into_reader(), &mut data).ok()?;
    image::load_from_memory(&data).ok().map(|i| i.to_rgb8())
}

#[cfg(test)]
mod list_link_tests {
    use super::list_in_video_link as k;

    #[test]
    fn spots_videos_inside_lists() {
        assert_eq!(k("https://www.youtube.com/watch?v=cjqrPliPP_s&list=RDcjqrPliPP_s&start_radio=1"), Some(true));
        assert_eq!(k("https://www.youtube.com/watch?v=abc&list=PL123"), Some(false));
        assert_eq!(k("https://youtu.be/abc?list=PL123"), Some(false));
        assert_eq!(k("https://music.youtube.com/watch?v=abc&list=RDAMVMabc"), Some(true));
        assert_eq!(k("https://www.youtube.com/playlist?list=PL123"), None);
        assert_eq!(k("https://www.youtube.com/watch?v=abc"), None);
        assert_eq!(k("https://vimeo.com/123?list=x&v=y"), None);
    }
}
