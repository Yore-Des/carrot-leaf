//! Run one download job with yt-dlp and report progress through a callback.

use super::options::{BuildArgs, Choice, DEFAULT_TEMPLATE, build_args};
use super::proc::{Cancel, RunError, run_lines};
use super::{deps, errors, local};
use crate::transcripts;
use serde_json::Value;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

static IDS: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Queued,
    Preparing,
    Downloading,
    Converting,
    Done,
    Failed,
    Cancelled,
}

impl State {
    pub fn finished(self) -> bool {
        matches!(self, State::Done | State::Failed | State::Cancelled)
    }
}

#[derive(Clone, Debug)]
pub struct Progress {
    pub state: State,
    pub status: String,
    pub fraction: Option<f64>,
    pub speed: Option<f64>,
    pub eta: Option<f64>,
}

impl Progress {
    pub fn new(state: State, status: &str, fraction: Option<f64>) -> Self {
        Progress { state, status: status.into(), fraction, speed: None, eta: None }
    }
}

#[derive(Clone, Debug)]
pub struct Job {
    pub id: u64,
    pub url: String,
    pub title: String,
    pub choice: Choice,
    pub state: State,
    pub status: String,
    pub fraction: Option<f64>,
    pub speed: Option<f64>,
    pub eta: Option<f64>,
    pub filepath: Option<String>,
    pub error: Option<String>,
    /// Set when this job converts a file on disk rather than downloading `url`.
    pub file: Option<local::FileInfo>,
    pub template: String,
    pub fields: HashMap<String, String>,
    pub started: Option<std::time::Instant>,
    pub keep_transcript: bool,
    pub cancel: Cancel,
}

impl Job {
    pub fn new(url: &str, title: &str, choice: Choice) -> Self {
        Job {
            id: IDS.fetch_add(1, Ordering::Relaxed),
            url: url.into(),
            title: title.into(),
            choice,
            state: State::Queued,
            status: "Waiting".into(),
            fraction: Some(0.0),
            speed: None,
            eta: None,
            filepath: None,
            error: None,
            file: None,
            template: DEFAULT_TEMPLATE.into(),
            fields: HashMap::new(),
            started: None,
            keep_transcript: false,
            cancel: Arc::new(AtomicBool::new(false)),
        }
    }
    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::Relaxed);
    }
    pub fn cancelled(&self) -> bool {
        self.cancel.load(Ordering::Relaxed)
    }
}

pub const CANCELLED: &str = "Cancelled";

fn pp_label(name: &str) -> Option<&'static str> {
    Some(match name {
        "ExtractAudio" => "Converting audio",
        "VideoConvertor" => "Converting video",
        "VideoRemuxer" => "Packaging",
        "Merger" => "Merging video and audio",
        "Metadata" => "Adding details",
        "EmbedThumbnail" => "Adding cover art",
        "EmbedSubtitle" => "Adding subtitles",
        "SubtitlesConvertor" => "Converting subtitles",
        "SponsorBlock" => "Finding sponsor segments",
        "ModifyChapters" => "Cutting sponsor segments",
        "SplitChapters" => "Splitting into chapters",
        _ => return None,
    })
}

const SCRATCH_SUFFIXES: &[&str] = &["part", "ytdl", "temp", "jpg", "webp", "png", "json"];

/// Download and convert `job`. Blocks; returns the final file path.
pub fn run_job(job: &Job, download_dir: &Path, on_progress: &mut dyn FnMut(Progress), cookies_browser: &str) -> Result<String, String> {
    on_progress(Progress::new(State::Preparing, "Getting ready", None));
    let mut ffmpeg = deps::find_ffmpeg(false)?;
    if ffmpeg.is_none() {
        on_progress(Progress::new(State::Preparing, "Fetching ffmpeg (one time, ~70 MB)", None));
        ffmpeg = deps::find_ffmpeg(true)?;
    }
    std::fs::create_dir_all(download_dir).map_err(|e| e.to_string())?;
    // Work in a private folder so intermediate files never clash with files already there.
    let work = tempfile::Builder::new().prefix(".carrot-leaf-").tempdir_in(download_dir).map_err(|e| e.to_string())?;
    run(job, work.path(), download_dir, ffmpeg.as_ref().map(|t| t.path.as_str()), on_progress, cookies_browser)
}

fn run(job: &Job, work: &Path, download_dir: &Path, ffmpeg: Option<&str>, on_progress: &mut dyn FnMut(Progress), cookies_browser: &str) -> Result<String, String> {
    let native_vorbis = ffmpeg.is_some_and(|f| !deps::ffmpeg_has_encoder(f, "libvorbis"));
    let mut args = build_args(
        &job.choice,
        &work.to_string_lossy(),
        &BuildArgs { ffmpeg_location: ffmpeg, cookies_browser, native_vorbis, template: &job.template, fields: &job.fields },
    );
    args.extend(
        [
            "--quiet",
            "--progress",
            "--newline",
            "--no-simulate",
            "--progress-template",
            "download:[carrot] %(progress.status)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s|%(info.format_id)s",
            "--progress-template",
            "postprocess:[carrotpp] %(progress.status)s|%(progress.postprocessor)s",
        ]
        .map(String::from),
    );
    let fields = if job.keep_transcript {
        "id,title,webpage_url,language,filepath,subtitles,automatic_captions"
    } else {
        "id,title,webpage_url,filepath"
    };
    args.push("--print".into());
    args.push(format!("after_move:[carrotdone] %(.{{{fields}}})j"));
    let mut cmd = deps::yt_dlp_command();
    cmd.args(&args).arg("--").arg(&job.url);

    let mut seen: Vec<String> = vec![];
    let mut done_info: Option<Value> = None;
    let result = run_lines(cmd, &job.cancel, |line| {
        if let Some(rest) = line.strip_prefix("[carrot] ") {
            let p: Vec<&str> = rest.split('|').collect();
            if p.len() < 7 {
                return;
            }
            let num = |s: &str| s.parse::<f64>().ok();
            let fmt = p[6].to_string();
            if !seen.contains(&fmt) {
                seen.push(fmt);
            }
            if p[0] == "downloading" {
                let total = num(p[2]).or_else(|| num(p[3])).filter(|t| *t > 0.0);
                let done = num(p[1]).unwrap_or(0.0);
                let label = if seen.len() <= 1 { "Downloading".to_string() } else { format!("Downloading part {}", seen.len()) };
                on_progress(Progress {
                    state: State::Downloading,
                    status: label,
                    fraction: total.map(|t| (done / t).min(1.0)),
                    speed: num(p[4]),
                    eta: num(p[5]),
                });
            }
        } else if let Some(rest) = line.strip_prefix("[carrotpp] ") {
            if let Some((status, name)) = rest.split_once('|') {
                if status == "started" {
                    if let Some(label) = pp_label(name) {
                        on_progress(Progress::new(State::Converting, label, None));
                    }
                }
            }
        } else if let Some(rest) = line.strip_prefix("[carrotdone] ") {
            done_info = serde_json::from_str(rest).ok();
        }
    });
    let finished = match result {
        Err(RunError::Cancelled) => return Err(CANCELLED.into()),
        Err(RunError::Spawn(e)) => return Err(format!("Couldn't run yt-dlp: {e}")),
        Ok(f) => f,
    };
    if !finished.status.success() {
        let line = errors::error_line(&finished.stderr);
        return Err(if line.is_empty() { format!("yt-dlp failed ({})", finished.status) } else { line });
    }
    let info = done_info.unwrap_or(Value::Null);
    let produced = info.get("filepath").and_then(Value::as_str).map(PathBuf::from).or_else(|| largest_file(work));
    let Some(produced) = produced.filter(|p| p.is_file()) else {
        return Err("Download finished but no file was produced.".into());
    };
    let mut main = produced.clone();
    if job.choice.extras.split_chapters {
        let tracks = produced.with_extension("");
        if tracks.is_dir() && std::fs::read_dir(&tracks).map(|mut d| d.next().is_some()).unwrap_or(false) {
            let _ = std::fs::remove_file(&produced);
            main = tracks;
        }
    }
    let extras = &job.choice.extras;
    if job.choice.kind == "audio" && (extras.normalize || extras.clipped()) {
        let files: Vec<PathBuf> = if main.is_dir() {
            let mut v: Vec<PathBuf> = std::fs::read_dir(&main).map(|d| d.flatten().map(|e| e.path()).collect()).unwrap_or_default();
            v.sort();
            v
        } else {
            vec![main.clone()]
        };
        let n_files = files.len();
        for (n, path) in files.iter().enumerate() {
            let mut label = if extras.normalize { "Evening out volume".to_string() } else { "Cutting the clip".to_string() };
            if n_files > 1 {
                label += &format!(" {}/{}", n + 1, n_files);
            }
            let whole = !main.is_dir();
            local::refine_audio(
                path,
                &job.choice.fmt,
                ffmpeg,
                on_progress,
                &job.cancel,
                if whole { extras.start } else { None },
                if whole { extras.end } else { None },
                extras.normalize,
                &label,
            )?;
        }
    }
    let result = collect(work, download_dir, &main)?;
    if job.keep_transcript && !info.is_null() {
        if let Some((lang, cues)) = transcripts::fetch(&info) {
            let s = |k: &str| info.get(k).and_then(Value::as_str).unwrap_or("").to_string();
            transcripts::save(&transcripts::Transcript {
                id: Some(s("id")).filter(|v| !v.is_empty()).unwrap_or_else(|| job.url.clone()),
                title: Some(s("title")).filter(|v| !v.is_empty()).unwrap_or_else(|| job.title.clone()),
                path: result.to_string_lossy().into(),
                url: Some(s("webpage_url")).filter(|v| !v.is_empty()).unwrap_or_else(|| job.url.clone()),
                lang,
                cues,
            });
        }
    }
    Ok(result.to_string_lossy().into())
}

fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
    if let Ok(rd) = std::fs::read_dir(dir) {
        for e in rd.flatten() {
            let p = e.path();
            if p.is_dir() {
                walk(&p, out);
            } else {
                out.push(p);
            }
        }
    }
}

fn largest_file(work: &Path) -> Option<PathBuf> {
    let mut files = vec![];
    walk(work, &mut files);
    files.into_iter().filter(|p| !is_scratch(p)).max_by_key(|p| p.metadata().map(|m| m.len()).unwrap_or(0))
}

fn is_scratch(p: &Path) -> bool {
    p.extension().map(|e| SCRATCH_SUFFIXES.contains(&e.to_string_lossy().to_lowercase().as_str())).unwrap_or(false)
}

fn move_path(from: &Path, to: &Path) -> Result<(), String> {
    if let Some(p) = to.parent() {
        std::fs::create_dir_all(p).map_err(|e| e.to_string())?;
    }
    if std::fs::rename(from, to).is_ok() {
        return Ok(());
    }
    // Across file systems: copy, then delete.
    if from.is_dir() {
        copy_dir(from, to)?;
        std::fs::remove_dir_all(from).map_err(|e| e.to_string())
    } else {
        std::fs::copy(from, to).map_err(|e| e.to_string())?;
        std::fs::remove_file(from).map_err(|e| e.to_string())
    }
}

fn copy_dir(from: &Path, to: &Path) -> Result<(), String> {
    std::fs::create_dir_all(to).map_err(|e| e.to_string())?;
    for e in std::fs::read_dir(from).map_err(|e| e.to_string())?.flatten() {
        let t = to.join(e.file_name());
        if e.path().is_dir() {
            copy_dir(&e.path(), &t)?;
        } else {
            std::fs::copy(e.path(), t).map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

pub fn move_into(from: &Path, to: &Path) -> Result<(), String> {
    move_path(from, to)
}

/// Move every result out of `work` into `download_dir`, keeping subfolders the filename
/// template made, and return where `main` ended up.
pub fn collect(work: &Path, download_dir: &Path, main: &Path) -> Result<PathBuf, String> {
    let mut moved = main.to_path_buf();
    let rel_main = main.strip_prefix(work).map_err(|e| e.to_string())?.to_path_buf();
    if main.is_dir() {
        let target = unique_path(&download_dir.join(&rel_main));
        move_path(main, &target)?;
        moved = target;
    }
    let mut files = vec![];
    walk(work, &mut files);
    files.sort();
    for path in files {
        if path != main && is_scratch(&path) {
            continue;
        }
        let rel = path.strip_prefix(work).map_err(|e| e.to_string())?;
        let target = unique_path(&download_dir.join(rel));
        move_path(&path, &target)?;
        if path == main {
            moved = target;
        }
    }
    Ok(moved)
}

/// `path`, or `name (2).ext`, `name (3).ext`... if it is already taken.
pub fn unique_path(path: &Path) -> PathBuf {
    if !path.exists() {
        return path.to_path_buf();
    }
    let stem = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    let ext = path.extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default();
    (2..).map(|n| path.with_file_name(format!("{stem} ({n}){ext}"))).find(|c| !c.exists()).unwrap()
}
