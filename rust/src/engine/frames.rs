//! Frames of a video at any moment, for picking a clip by eye.
//!
//! For a link, YouTube's storyboards: sheets of small thumbnails the site already makes for
//! its own scrub bar. For a file on disk, ffmpeg grabs the frame. Both are slow enough to
//! call from a worker thread only.

use super::{deps, probe};
use image::RgbImage;
use serde_json::Value;
use std::collections::HashMap;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};

pub trait FrameSource: Send + Sync {
    fn duration(&self) -> f64;
    /// The finest jump that shows a different picture, in seconds.
    fn step(&self) -> f64;
    fn frame(&self, t: f64) -> Option<RgbImage>;
}

pub struct StoryboardFrames {
    duration: f64,
    rows: u32,
    cols: u32,
    fps: f64,
    width: u32,
    height: u32,
    sheets: Vec<String>,
    cache: Mutex<HashMap<usize, Arc<RgbImage>>>,
}

impl FrameSource for StoryboardFrames {
    fn duration(&self) -> f64 {
        self.duration
    }
    fn step(&self) -> f64 {
        if self.fps > 0.0 { 1.0 / self.fps } else { 2.0 }
    }
    fn frame(&self, t: f64) -> Option<RgbImage> {
        let index = (t.clamp(0.0, self.duration) * self.fps) as usize;
        let per_sheet = (self.rows * self.cols) as usize;
        let sheet_no = (index / per_sheet).min(self.sheets.len().saturating_sub(1));
        let cell = (index % per_sheet) as u32;
        let sheet = self.sheet(sheet_no)?;
        let (row, col) = (cell / self.cols, cell % self.cols);
        let (x, y) = (col * self.width, row * self.height);
        if x + self.width > sheet.width() || y + self.height > sheet.height() {
            return None;
        }
        Some(image::imageops::crop_imm(sheet.as_ref(), x, y, self.width, self.height).to_image())
    }
}

impl StoryboardFrames {
    fn sheet(&self, n: usize) -> Option<Arc<RgbImage>> {
        if let Some(s) = self.cache.lock().unwrap().get(&n) {
            return Some(s.clone());
        }
        let img = Arc::new(probe::fetch_image(self.sheets.get(n)?, 10)?);
        self.cache.lock().unwrap().insert(n, img.clone());
        Some(img)
    }
}

pub struct FileFrames {
    path: PathBuf,
    duration: f64,
    ffmpeg_dir: Option<String>,
}

impl FrameSource for FileFrames {
    fn duration(&self) -> f64 {
        self.duration
    }
    fn step(&self) -> f64 {
        1.0
    }
    fn frame(&self, t: f64) -> Option<RgbImage> {
        let out = Command::new(deps::exe_in("ffmpeg", self.ffmpeg_dir.as_deref()))
            .args(["-v", "error", "-ss", &format!("{:.2}", t.max(0.0)), "-i"])
            .arg(&self.path)
            .args(["-frames:v", "1", "-vf", "scale=320:-2", "-f", "image2pipe", "-c:v", "png", "-"])
            .stdin(Stdio::null())
            .stderr(Stdio::null())
            .output()
            .ok()?;
        image::load_from_memory(&out.stdout).ok().map(|i| i.to_rgb8())
    }
}

/// The sharpest storyboard yt-dlp lists.
pub fn best_storyboard(formats: &[Value]) -> Option<&Value> {
    formats
        .iter()
        .filter(|f| {
            f.get("format_note").and_then(Value::as_str) == Some("storyboard")
                && f.get("fragments").and_then(Value::as_array).is_some_and(|a| !a.is_empty())
                && f.get("rows").is_some()
                && f.get("columns").is_some()
                && f.get("fps").is_some()
        })
        .max_by_key(|f| f.get("width").and_then(Value::as_i64).unwrap_or(0))
}

/// Frames for a looked-up link, if the site makes storyboards.
pub fn for_video(info: &probe::VideoInfo) -> Option<Arc<dyn FrameSource>> {
    let formats = info.raw.get("formats").and_then(Value::as_array)?;
    let b = best_storyboard(formats)?;
    let duration = info.duration.filter(|d| *d > 0.0)?;
    let n = |k: &str| b.get(k).and_then(Value::as_f64).unwrap_or(0.0);
    Some(Arc::new(StoryboardFrames {
        duration,
        rows: n("rows") as u32,
        cols: n("columns") as u32,
        fps: n("fps"),
        width: n("width") as u32,
        height: n("height") as u32,
        sheets: b["fragments"].as_array()?.iter().filter_map(|f| f.get("url").and_then(Value::as_str).map(String::from)).collect(),
        cache: Mutex::new(HashMap::new()),
    }))
}

/// Frames for a video on disk.
pub fn for_file(info: &super::local::FileInfo, ffmpeg_dir: Option<String>) -> Option<Arc<dyn FrameSource>> {
    if info.media != super::local::Media::Video {
        return None;
    }
    let duration = info.duration.filter(|d| *d > 0.0)?;
    Some(Arc::new(FileFrames { path: info.path.clone(), duration, ffmpeg_dir }))
}
