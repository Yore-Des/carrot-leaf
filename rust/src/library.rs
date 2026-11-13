//! What Carrot keeps next to the config: history, followed channels and presets.
//! Same JSON shapes as the Python version, so either can read the other's files.

use crate::config;
use crate::engine::options::{Choice, Extras};
use crate::engine::probe::{Entry, PlaylistInfo};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

pub fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

fn write_atomic(path: &PathBuf, text: &str) -> std::io::Result<()> {
    if let Some(p) = path.parent() {
        std::fs::create_dir_all(p)?;
    }
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, text)?;
    std::fs::rename(tmp, path)
}

/// Seconds since the epoch as local "HH:MM" today, else "YYYY-MM-DD".
pub fn local_time_text(when: f64, today_only: bool) -> String {
    // Without a timezone crate: ask the C library for local time.
    #[cfg(unix)]
    {
        let t = when as libc_time::time_t;
        let mut tm: libc_time::tm = unsafe { std::mem::zeroed() };
        let now_t = now() as libc_time::time_t;
        let mut now_tm: libc_time::tm = unsafe { std::mem::zeroed() };
        unsafe {
            libc_time::localtime_r(&t, &mut tm);
            libc_time::localtime_r(&now_t, &mut now_tm);
        }
        if today_only && now() - when < 86400.0 && tm.tm_mday == now_tm.tm_mday {
            return format!("{:02}:{:02}", tm.tm_hour, tm.tm_min);
        }
        format!("{:04}-{:02}-{:02}", tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday)
    }
    #[cfg(not(unix))]
    {
        let _ = today_only;
        let days = (when / 86400.0) as i64;
        let (y, m, d) = civil(days);
        format!("{y:04}-{m:02}-{d:02}")
    }
}

#[cfg(not(unix))]
fn civil(z: i64) -> (i64, i64, i64) {
    let z = z + 719468;
    let era = z.div_euclid(146097);
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (yoe + era * 400 + i64::from(m <= 2), m, d)
}

#[cfg(unix)]
#[allow(non_camel_case_types)]
mod libc_time {
    pub type time_t = i64;
    #[repr(C)]
    pub struct tm {
        pub tm_sec: i32,
        pub tm_min: i32,
        pub tm_hour: i32,
        pub tm_mday: i32,
        pub tm_mon: i32,
        pub tm_year: i32,
        pub tm_wday: i32,
        pub tm_yday: i32,
        pub tm_isdst: i32,
        pub tm_gmtoff: i64,
        pub tm_zone: *const i8,
    }
    unsafe extern "C" {
        pub fn localtime_r(t: *const time_t, out: *mut tm) -> *mut tm;
    }
}

// ---------- history ----------

pub const MAX_HISTORY: usize = 1000;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct HistoryEntry {
    pub title: String,
    pub path: String,
    pub label: String,
    pub source: String,
    #[serde(default = "download_kind")]
    pub kind: String,
    #[serde(default)]
    pub when: f64,
}

fn download_kind() -> String {
    "download".into()
}

impl HistoryEntry {
    pub fn new(title: &str, path: &str, label: &str, source: &str, kind: &str) -> Self {
        HistoryEntry { title: title.into(), path: path.into(), label: label.into(), source: source.into(), kind: kind.into(), when: 0.0 }
    }
    pub fn exists(&self) -> bool {
        std::path::Path::new(&self.path).exists()
    }
    pub fn is_link(&self) -> bool {
        self.source.contains("://")
    }
    pub fn when_text(&self) -> String {
        if self.when == 0.0 { String::new() } else { local_time_text(self.when, true) }
    }
}

pub fn history_path() -> PathBuf {
    config::config_dir().join("history.jsonl")
}

pub fn history_add(mut entry: HistoryEntry) {
    use std::io::Write;
    let path = history_path();
    if entry.when == 0.0 {
        entry.when = now();
    }
    let _ = (|| -> std::io::Result<()> {
        std::fs::create_dir_all(path.parent().unwrap())?;
        let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&path)?;
        writeln!(f, "{}", serde_json::to_string(&entry)?)?;
        if f.metadata()?.len() > (MAX_HISTORY * 600) as u64 {
            let all = history_load();
            history_save(&all[all.len().saturating_sub(MAX_HISTORY)..])?;
        }
        Ok(())
    })();
}

/// Oldest first. Broken lines are skipped.
pub fn history_load() -> Vec<HistoryEntry> {
    let Ok(text) = std::fs::read_to_string(history_path()) else { return vec![] };
    text.lines().filter_map(|l| serde_json::from_str(l).ok()).collect()
}

pub fn history_save(entries: &[HistoryEntry]) -> std::io::Result<()> {
    let text: String = entries.iter().filter_map(|e| serde_json::to_string(e).ok()).map(|l| l + "\n").collect();
    write_atomic(&history_path(), &text)
}

pub fn history_remove(entry: &HistoryEntry) {
    let all: Vec<HistoryEntry> = history_load().into_iter().filter(|e| !(e.when == entry.when && e.path == entry.path)).collect();
    let _ = history_save(&all);
}

// ---------- presets ----------

pub const MAX_PRESETS: usize = 9;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Preset {
    pub name: String,
    pub kind: String,
    pub fmt: String,
    #[serde(default = "best")]
    pub quality: String,
    #[serde(default)]
    pub extras: Map<String, Value>,
    #[serde(default)]
    pub site: String,
}

fn best() -> String {
    "best".into()
}

fn extras_from(map: &Map<String, Value>) -> Extras {
    let mut full = serde_json::to_value(Extras::default()).unwrap();
    for (k, v) in map {
        if full.get(k).is_some() {
            full[k] = v.clone();
        }
    }
    serde_json::from_value(full).unwrap_or_default()
}

impl Preset {
    pub fn choice(&self) -> Choice {
        Choice::new(&self.kind, &self.fmt, &self.quality).with_extras(extras_from(&self.extras))
    }
    pub fn matches(&self, choice: &Choice) -> bool {
        without_clip(choice) == self.choice()
    }
}

pub fn without_clip(choice: &Choice) -> Choice {
    let mut c = choice.clone();
    c.extras.start = None;
    c.extras.end = None;
    c
}

pub fn preset_from_choice(name: &str, choice: &Choice, site: &str) -> Preset {
    let c = without_clip(choice);
    let defaults = serde_json::to_value(Extras::default()).unwrap();
    let mine = serde_json::to_value(&c.extras).unwrap();
    let extras = mine.as_object().unwrap().iter().filter(|(k, v)| defaults.get(*k) != Some(v)).map(|(k, v)| (k.clone(), v.clone())).collect();
    Preset { name: name.trim().into(), kind: c.kind, fmt: c.fmt, quality: c.quality, extras, site: site.into() }
}

/// One name per site from yt-dlp's extractor: YoutubeTab and Youtube are both "youtube".
pub fn site_key(extractor: &str) -> String {
    let mut chars = extractor.chars().peekable();
    let mut out = String::new();
    if let Some(c) = chars.peek().copied() {
        if c.is_ascii_uppercase() {
            out.push(c);
            chars.next();
        }
    }
    for c in chars {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            out.push(c);
        } else {
            break;
        }
    }
    if out.chars().any(|c| c.is_ascii_lowercase() || c.is_ascii_digit()) { out.to_lowercase() } else { String::new() }
}

pub fn site_name(key: &str) -> String {
    match key {
        "youtube" => "YouTube".into(),
        "soundcloud" => "SoundCloud".into(),
        "tiktok" => "TikTok".into(),
        k => {
            let mut c = k.chars();
            c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default()
        }
    }
}

pub fn presets_path() -> PathBuf {
    config::config_dir().join("presets.json")
}

pub fn presets_load() -> Vec<Preset> {
    let Ok(text) = std::fs::read_to_string(presets_path()) else { return vec![] };
    let Ok(Value::Array(items)) = serde_json::from_str::<Value>(&text) else { return vec![] };
    items.into_iter().filter_map(|i| serde_json::from_value(i).ok()).take(MAX_PRESETS).collect()
}

pub fn presets_save(presets: &[Preset]) {
    if let Ok(text) = serde_json::to_string_pretty(presets) {
        let _ = write_atomic(&presets_path(), &text);
    }
}

pub fn preset_upsert(preset: Preset) -> Vec<Preset> {
    let mut presets: Vec<Preset> = presets_load().into_iter().filter(|p| p.name.to_lowercase() != preset.name.to_lowercase()).collect();
    if !preset.site.is_empty() {
        for p in presets.iter_mut() {
            if p.site == preset.site {
                p.site.clear();
            }
        }
    }
    presets.push(preset);
    let start = presets.len().saturating_sub(MAX_PRESETS);
    let presets = presets[start..].to_vec();
    presets_save(&presets);
    presets
}

pub fn default_preset_for(extractor: &str) -> Option<Preset> {
    let key = site_key(extractor);
    if key.is_empty() {
        return None;
    }
    presets_load().into_iter().find(|p| p.site == key)
}

// ---------- following ----------

pub const SYNC_DEPTH: usize = 30;
pub const MAX_SEEN: usize = 2000;
pub const SYNC_EVERY: f64 = 15.0 * 60.0;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Followed {
    pub url: String,
    pub title: String,
    #[serde(default = "video")]
    pub kind: String,
    #[serde(default = "mp4")]
    pub fmt: String,
    #[serde(default = "best")]
    pub quality: String,
    #[serde(default)]
    pub extras: Map<String, Value>,
    #[serde(default)]
    pub seen: Vec<String>,
    #[serde(default)]
    pub new: Vec<Entry>,
    #[serde(default)]
    pub checked: f64,
    #[serde(default)]
    pub error: String,
}

fn video() -> String {
    "video".into()
}
fn mp4() -> String {
    "mp4".into()
}

impl Followed {
    pub fn choice(&self) -> Choice {
        Choice::new(&self.kind, &self.fmt, &self.quality).with_extras(extras_from(&self.extras))
    }
    pub fn checked_text(&self) -> String {
        if self.checked == 0.0 {
            return "never synced".into();
        }
        let ago = now() - self.checked;
        if ago < 90.0 {
            "just now".into()
        } else if ago < 3600.0 {
            format!("{}m ago", (ago / 60.0) as i64)
        } else if ago < 86400.0 {
            format!("{}h ago", (ago / 3600.0) as i64)
        } else {
            format!("{}d ago", (ago / 86400.0) as i64)
        }
    }
}

pub fn following_path() -> PathBuf {
    config::config_dir().join("following.json")
}

pub fn following_load() -> Vec<Followed> {
    let Ok(text) = std::fs::read_to_string(following_path()) else { return vec![] };
    let Ok(Value::Array(items)) = serde_json::from_str::<Value>(&text) else { return vec![] };
    items.into_iter().filter_map(|i| serde_json::from_value(i).ok()).collect()
}

pub fn following_save(items: &[Followed]) {
    if let Ok(text) = serde_json::to_string_pretty(items) {
        let _ = write_atomic(&following_path(), &text);
    }
}

pub fn following_find(url: &str) -> Option<Followed> {
    following_load().into_iter().find(|f| f.url == url)
}

/// Start following `feed`. What's in it now counts as seen: only later videos are new.
pub fn follow(feed: &PlaylistInfo, choice: &Choice) -> Followed {
    let p = preset_from_choice(&feed.title, choice, "");
    let seen: Vec<String> = feed.entries.iter().map(|e| e.id.clone()).collect();
    let item = Followed {
        url: feed.url.clone(),
        title: feed.title.clone(),
        kind: p.kind,
        fmt: p.fmt,
        quality: p.quality,
        extras: p.extras,
        seen: seen[seen.len().saturating_sub(MAX_SEEN)..].to_vec(),
        new: vec![],
        checked: now(),
        error: String::new(),
    };
    let mut items: Vec<Followed> = following_load().into_iter().filter(|f| f.url != feed.url).collect();
    items.push(item.clone());
    following_save(&items);
    item
}

pub fn unfollow(url: &str) {
    following_save(&following_load().into_iter().filter(|f| f.url != url).collect::<Vec<_>>());
}

/// Record a sync of `item`: new videos join its waiting list. Returns how many are new.
pub fn apply_sync(item: &mut Followed, feed: Option<&PlaylistInfo>, error: &str) -> usize {
    item.checked = now();
    item.error = error.into();
    let Some(feed) = feed else { return 0 };
    let fresh: Vec<Entry> = feed
        .entries
        .iter()
        .filter(|e| !e.id.is_empty() && !item.seen.contains(&e.id) && !item.new.iter().any(|n| n.id == e.id))
        .cloned()
        .collect();
    let n = fresh.len();
    item.new.extend(fresh.into_iter().rev());
    n
}

/// Take the waiting videos off the list and remember them as seen.
pub fn mark_fetched(item: &mut Followed) -> Vec<Entry> {
    let entries = std::mem::take(&mut item.new);
    item.seen.extend(entries.iter().map(|e| e.id.clone()));
    let start = item.seen.len().saturating_sub(MAX_SEEN);
    item.seen.drain(..start);
    entries
}

pub fn following_update(item: &Followed) {
    let mut items = following_load();
    if let Some(slot) = items.iter_mut().find(|f| f.url == item.url) {
        *slot = item.clone();
        following_save(&items);
    }
}

pub fn total_new(items: &[Followed]) -> usize {
    items.iter().map(|f| f.new.len()).sum()
}

pub fn sync_due(items: &[Followed]) -> bool {
    items.iter().any(|f| now() - f.checked > SYNC_EVERY)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sites() {
        assert_eq!(site_key("YoutubeTab"), "youtube");
        assert_eq!(site_key("SoundcloudPlaylist"), "soundcloud");
        assert_eq!(site_name("youtube"), "YouTube");
    }

    #[test]
    fn presets_round_trip() {
        let mut c = Choice::new("audio", "mp3", "128");
        c.extras.normalize = true;
        c.extras.start = Some(3.0);
        let p = preset_from_choice("Podcast", &c, "");
        assert_eq!(p.extras.len(), 1);
        assert!(p.matches(&c));
    }
}
