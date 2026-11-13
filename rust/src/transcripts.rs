//! What was said in each download, kept so it can be searched. One small JSON file per
//! video in a `transcripts` folder next to the config, the same files the Python version writes.

use crate::config;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::PathBuf;
use std::sync::LazyLock;

const FORMATS: &[&str] = &["json3", "vtt", "srt"];

pub fn transcripts_dir() -> PathBuf {
    config::config_dir().join("transcripts")
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Cue {
    pub start: f64,
    pub text: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Transcript {
    pub id: String,
    pub title: String,
    pub path: String,
    pub url: String,
    #[serde(default)]
    pub lang: String,
    #[serde(default)]
    pub cues: Vec<Cue>,
}

#[derive(Clone, Copy, Debug)]
pub struct Hit {
    pub transcript: usize,
    pub index: usize,
}

/// (language, format, url) of the best caption track for search, or None.
pub fn pick_track(info: &Value) -> Option<(String, String, String)> {
    let lang = info.get("language").and_then(Value::as_str).unwrap_or("").split('-').next().unwrap_or("").to_string();
    let empty = Value::Object(Default::default());
    let manual = info.get("subtitles").filter(|v| v.is_object()).unwrap_or(&empty);
    let auto = info.get("automatic_captions").filter(|v| v.is_object()).unwrap_or(&empty);
    let mut candidates: Vec<(&Value, String)> = vec![];
    if !lang.is_empty() {
        candidates.extend([(manual, lang.clone()), (auto, format!("{lang}-orig")), (auto, lang.clone())]);
    }
    candidates.extend([(manual, "en".to_string()), (auto, "en-orig".to_string()), (auto, "en".to_string())]);
    if lang.is_empty() {
        if let Some(first) = manual.as_object().and_then(|m| m.keys().next()) {
            candidates.push((manual, first.clone()));
        }
    }
    for (tracks, key) in candidates {
        for fmt in FORMATS {
            for t in tracks.get(&key).and_then(Value::as_array).into_iter().flatten() {
                if t.get("ext").and_then(Value::as_str) == Some(fmt) {
                    if let Some(url) = t.get("url").and_then(Value::as_str) {
                        return Some((key, fmt.to_string(), url.to_string()));
                    }
                }
            }
        }
    }
    None
}

pub fn parse(fmt: &str, data: &str) -> Vec<Cue> {
    if fmt == "json3" { parse_json3(data) } else { parse_vtt(data) }
}

pub fn parse_json3(data: &str) -> Vec<Cue> {
    let Ok(v) = serde_json::from_str::<Value>(data) else { return vec![] };
    let mut cues = vec![];
    for ev in v.get("events").and_then(Value::as_array).into_iter().flatten() {
        let text: String = ev.get("segs").and_then(Value::as_array).into_iter().flatten().filter_map(|s| s.get("utf8").and_then(Value::as_str)).collect();
        let text = text.replace('\n', " ");
        let text = text.trim();
        if !text.is_empty() {
            let start = ev.get("tStartMs").and_then(Value::as_f64).unwrap_or(0.0) / 1000.0;
            cues.push(Cue { start: (start * 100.0).round() / 100.0, text: clean(text) });
        }
    }
    merge(cues)
}

static TIME: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?:(\d+):)?(\d+):(\d+)[.,](\d+)\s+-->").unwrap());
static TAGS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<[^>]+>").unwrap());
static SPACE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\s+").unwrap());

/// WebVTT or SRT. Automatic captions repeat each line as it scrolls; repeats are dropped.
pub fn parse_vtt(data: &str) -> Vec<Cue> {
    let mut cues = vec![];
    let mut start: Option<f64> = None;
    let mut seen = std::collections::HashSet::new();
    for line in data.lines() {
        if let Some(m) = TIME.captures(line) {
            let n = |i: usize| m.get(i).map(|v| v.as_str().parse::<f64>().unwrap_or(0.0)).unwrap_or(0.0);
            let frac = &m[4];
            start = Some(n(1) * 3600.0 + n(2) * 60.0 + n(3) + n(4) / 10f64.powi(frac.len() as i32));
            continue;
        }
        let text = clean(&TAGS.replace_all(line, ""));
        let Some(s) = start else { continue };
        if text.is_empty() || text.chars().all(|c| c.is_ascii_digit()) || seen.contains(&text) {
            continue;
        }
        seen.insert(text.clone());
        cues.push(Cue { start: (s * 100.0).round() / 100.0, text });
    }
    merge(cues)
}

fn clean(text: &str) -> String {
    SPACE.replace_all(&text.replace("&gt;", ">").replace("&amp;", "&"), " ").trim().to_string()
}

/// Join short fragments into lines of about 90 characters, so a hit has context.
fn merge(cues: Vec<Cue>) -> Vec<Cue> {
    let mut out: Vec<Cue> = vec![];
    for c in cues {
        if let Some(last) = out.last_mut() {
            if last.text.chars().count() + c.text.chars().count() < 90 && c.start - last.start < 8.0 {
                last.text.push(' ');
                last.text.push_str(&c.text);
                continue;
            }
        }
        out.push(c);
    }
    out
}

/// Download and parse the best caption track. None when there are none.
pub fn fetch(info: &Value) -> Option<(String, Vec<Cue>)> {
    let (lang, fmt, url) = pick_track(info)?;
    let resp = ureq::get(&url).timeout(std::time::Duration::from_secs(20)).call().ok()?;
    let data = resp.into_string().ok()?;
    let cues = parse(&fmt, &data);
    (!cues.is_empty()).then(|| (lang.replace("-orig", ""), cues))
}

static UNSAFE_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^\w.-]").unwrap());

pub fn save(t: &Transcript) {
    let safe: String = UNSAFE_ID.replace_all(&t.id, "_").chars().take(100).collect();
    let path = transcripts_dir().join(format!("{}.json", if safe.is_empty() { "video" } else { &safe }));
    let _ = std::fs::create_dir_all(transcripts_dir());
    if let Ok(text) = serde_json::to_string(t) {
        let _ = std::fs::write(path, text);
    }
}

pub fn load_all() -> Vec<Transcript> {
    let Ok(rd) = std::fs::read_dir(transcripts_dir()) else { return vec![] };
    let mut files: Vec<(std::time::SystemTime, PathBuf)> = rd
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|e| e == "json"))
        .map(|p| (p.metadata().and_then(|m| m.modified()).unwrap_or(std::time::UNIX_EPOCH), p))
        .collect();
    files.sort_by(|a, b| b.0.cmp(&a.0));
    files.into_iter().filter_map(|(_, p)| serde_json::from_str(&std::fs::read_to_string(p).ok()?).ok()).collect()
}

static QUOTED: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#""([^"]+)""#).unwrap());
static QUOTED_ANY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#""[^"]*""#).unwrap());

pub fn needles(query: &str) -> Vec<String> {
    let q = query.trim().to_lowercase();
    let mut out: Vec<String> = QUOTED.captures_iter(&q).map(|c| c[1].to_string()).collect();
    out.extend(QUOTED_ANY.replace_all(&q, " ").split_whitespace().map(String::from));
    out
}

/// Cues containing every word of `query`, newest videos first.
pub fn search(query: &str, library: &[Transcript], limit: usize) -> Vec<Hit> {
    let wanted = needles(query);
    if wanted.is_empty() {
        return vec![];
    }
    let mut hits = vec![];
    for (ti, t) in library.iter().enumerate() {
        for (i, cue) in t.cues.iter().enumerate() {
            let low = cue.text.to_lowercase();
            if wanted.iter().all(|n| low.contains(n.as_str())) {
                hits.push(Hit { transcript: ti, index: i });
                if hits.len() >= limit {
                    return hits;
                }
            }
        }
    }
    hits
}
