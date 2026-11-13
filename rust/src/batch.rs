//! `carrot get`, `carrot convert` and `carrot sync`: the same work, without the app.
//! Progress goes to stderr and each saved path to stdout. Exit status: 0 ok, 1 failed, 2 usage.

use crate::config;
use crate::engine::local::{self, FileInfo};
use crate::engine::options::{AUDIO_FORMATS, Choice, Extras, VIDEO_FORMATS, parse_time, safe_name, template_error};
use crate::engine::probe::{PLAYLIST_LIMIT, Probed, probe};
use crate::engine::runner::{self, Job, Progress};
use crate::engine::{deps, errors};
use crate::library::{self, HistoryEntry};
use crate::ui::fmt::{human_eta, human_size};
use clap::{Args, Parser, Subcommand};
use std::io::{IsTerminal, Write};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "carrot", about = "Download or convert without opening the app.", after_help = "examples:
  carrot get https://youtu.be/aqz-KE-bpKQ
  carrot get URL --format mp3 --quality 320 --normalize
  carrot get URL --start 1:30 --end 2:00 --format gif
  carrot get PLAYLIST_URL --format m4a --latest 10
  carrot convert clip.mov --format mp4
  carrot convert photo.heic --format jpg --max-kb 500 --strip-metadata
  carrot sync                      # new videos from what you follow")]
struct Cli {
    #[command(subcommand)]
    command: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// download a video, or every video in a playlist
    Get(GetArgs),
    /// convert files on your computer
    Convert(ConvertArgs),
    /// download new videos from channels and playlists you follow
    Sync {
        /// only list what's new; download nothing
        #[arg(long)]
        check: bool,
        /// no progress, just the saved paths
        #[arg(long)]
        quiet: bool,
    },
}

#[derive(Args)]
struct Shared {
    /// clip from here (1:30, 90 or 1m30s)
    #[arg(long)]
    start: Option<String>,
    /// clip to here
    #[arg(long)]
    end: Option<String>,
    /// audio: even out the volume
    #[arg(long)]
    normalize: bool,
    /// no progress, just the saved paths
    #[arg(long)]
    quiet: bool,
}

#[derive(Args)]
struct GetArgs {
    /// one or more links
    #[arg(required = true)]
    url: Vec<String>,
    /// file type (default mp4)
    #[arg(short, long, default_value = "mp4", value_parser = clap::builder::PossibleValuesParser::new(["mp4", "mkv", "webm", "mov", "gif", "mp3", "m4a", "opus", "flac", "wav", "ogg"]))]
    format: String,
    /// best, a height like 720, or a bitrate like 192
    #[arg(short, long, default_value = "best")]
    quality: String,
    /// folder to save into (default: your Carrot folder)
    #[arg(short, long)]
    out: Option<String>,
    /// filename template, like '{channel}/{title}'
    #[arg(long)]
    name: Option<String>,
    /// subtitles inside, or as .srt
    #[arg(long, value_parser = ["embed", "srt"])]
    subs: Option<String>,
    /// subtitle languages, e.g. en,de
    #[arg(long, default_value = "en")]
    sub_langs: String,
    /// YouTube sponsor segments
    #[arg(long, value_parser = ["remove", "mark"])]
    sponsorblock: Option<String>,
    /// one file per chapter
    #[arg(long)]
    split_chapters: bool,
    /// only the first N videos of a playlist
    #[arg(long)]
    latest: Option<usize>,
    /// use this browser's cookies
    #[arg(long, value_name = "BROWSER")]
    cookies: Option<String>,
    #[command(flatten)]
    shared: Shared,
}

#[derive(Args)]
struct ConvertArgs {
    /// files to convert
    #[arg(required = true)]
    files: Vec<PathBuf>,
    /// target format, e.g. mp4, mp3, webp
    #[arg(short, long)]
    format: String,
    /// best, a height, bitrate or size
    #[arg(short, long, default_value = "best")]
    quality: String,
    /// save a video's frames as pictures in --format
    #[arg(long)]
    frames: bool,
    /// images: drop EXIF and GPS
    #[arg(long)]
    strip_metadata: bool,
    /// images: keep each file under this size
    #[arg(long)]
    max_kb: Option<i64>,
    #[command(flatten)]
    shared: Shared,
}

fn usage(message: &str) -> i32 {
    eprintln!("carrot: {message}");
    2
}

/// One progress line on stderr, redrawn in place.
struct Printer {
    quiet: bool,
    label: String,
    width: usize,
}

impl Printer {
    fn new(quiet: bool, label: &str) -> Self {
        Printer { quiet: quiet || !std::io::stderr().is_terminal(), label: label.into(), width: 0 }
    }
    fn show(&mut self, p: &Progress) {
        if self.quiet {
            return;
        }
        let mut bits = vec![p.status.clone()];
        if let Some(f) = p.fraction {
            bits.push(format!("{:3.0}%", f * 100.0));
        }
        if let Some(s) = p.speed.filter(|s| *s > 0.0) {
            bits.push(format!("{}/s", human_size(s)));
        }
        if let Some(e) = p.eta {
            bits.push(format!("{} left", human_eta(e)));
        }
        let label: String = self.label.chars().take(40).collect();
        let line = format!("{label}  {}", bits.join(" · "));
        let n = line.chars().count();
        eprint!("\r{line}{}", " ".repeat(self.width.saturating_sub(n)));
        self.width = n;
        let _ = std::io::stderr().flush();
    }
    fn done(&mut self, message: &str) {
        if !self.quiet {
            let n = message.chars().count();
            eprintln!("\r{message}{}", " ".repeat(self.width.saturating_sub(n)));
        }
    }
    fn fail(&mut self, title: &str, err: &str) {
        let f = errors::explain(err);
        let hint = if f.hint.is_empty() { String::new() } else { format!(" {}", f.hint) };
        self.quiet = false;
        self.done(&format!("✗ {title}: {}{hint}", f.message));
    }
}

pub fn main(argv: &[String]) -> i32 {
    let cli = match Cli::try_parse_from(std::iter::once("carrot".to_string()).chain(argv.iter().cloned())) {
        Ok(c) => c,
        Err(e) => {
            let code = if e.use_stderr() { 2 } else { 0 };
            let _ = e.print();
            return code;
        }
    };
    match cli.command {
        Cmd::Sync { check, quiet } => sync(check, quiet),
        Cmd::Get(a) => match extras(&a.shared) {
            Ok(e) => get(a, e),
            Err(code) => code,
        },
        Cmd::Convert(a) => match extras(&a.shared) {
            Ok(e) => convert(a, e),
            Err(code) => code,
        },
    }
}

fn extras(s: &Shared) -> Result<Extras, i32> {
    let start = parse_time(s.start.as_deref().unwrap_or("")).map_err(|e| usage(&e))?;
    let end = parse_time(s.end.as_deref().unwrap_or("")).map_err(|e| usage(&e))?;
    if let (Some(a), Some(b)) = (start, end) {
        if b <= a {
            return Err(usage("--end has to come after --start."));
        }
    }
    Ok(Extras { start: start.filter(|s| *s > 0.0), end, normalize: s.normalize, ..Extras::default() })
}

fn get(a: GetArgs, mut extras: Extras) -> i32 {
    let settings = config::load();
    let kind = if AUDIO_FORMATS.contains(&a.format.as_str()) { "audio" } else { "video" };
    debug_assert!(VIDEO_FORMATS.contains(&a.format.as_str()) || kind == "audio");
    let template = a.name.clone().unwrap_or_else(|| settings.filename_template.clone());
    let problem = template_error(&template);
    if !problem.is_empty() {
        return usage(&format!("--name: {problem}"));
    }
    extras.subtitles = a.subs.clone().unwrap_or_default();
    extras.sub_langs = a.sub_langs.clone();
    extras.sponsorblock = a.sponsorblock.clone().unwrap_or_default();
    extras.split_chapters = a.split_chapters;
    extras.normalize = a.shared.normalize && kind == "audio";
    let choice = Choice::new(kind, &a.format, if a.quality.is_empty() { "best" } else { &a.quality }).with_extras(extras);
    let out = a.out.as_deref().map(config::expand_user).unwrap_or_else(|| settings.download_path());
    let cookies = a.cookies.clone().unwrap_or_else(|| settings.cookies_browser.clone());

    let mut jobs: Vec<Job> = vec![];
    let mut failed = 0;
    for url in &a.url {
        let url = if url.contains("://") { url.clone() } else { format!("https://{url}") };
        match probe(&url, &cookies, PLAYLIST_LIMIT) {
            Err(e) => {
                Printer::new(a.shared.quiet, &url).fail(&url, &e);
                failed += 1;
            }
            Ok(Probed::Playlist(info)) => {
                let entries = match a.latest {
                    Some(n) => &info.entries[..n.min(info.entries.len())],
                    None => &info.entries[..],
                };
                let width = info.entries.len().to_string().len().max(2);
                let pl_template = if template.contains("{playlist}") { template.clone() } else { format!("{{playlist}}/{template}") };
                for (i, e) in entries.iter().enumerate() {
                    let mut job = Job::new(&e.url, &e.title, choice.clone());
                    job.template = pl_template.clone();
                    job.fields.insert("playlist".into(), safe_name(&info.title));
                    job.fields.insert("index".into(), format!("{:0width$}", i + 1));
                    jobs.push(job);
                }
            }
            Ok(Probed::Video(info)) => {
                let mut job = Job::new(&info.url, &info.title, choice.clone());
                job.template = template.clone();
                jobs.push(job);
            }
        }
    }
    let total = jobs.len();
    for (n, mut job) in jobs.into_iter().enumerate() {
        job.keep_transcript = settings.keep_transcripts;
        let label = if total > 1 { format!("[{}/{total}] {}", n + 1, job.title) } else { job.title.clone() };
        let mut printer = Printer::new(a.shared.quiet, &label);
        match runner::run_job(&job, &out, &mut |p| printer.show(&p), &cookies) {
            Err(e) => {
                printer.fail(&job.title, &e);
                failed += 1;
            }
            Ok(path) => {
                printer.done(&format!("✓ {}", job.title));
                println!("{path}");
                library::history_add(HistoryEntry::new(&job.title, &path, &choice.label(), &job.url, "download"));
            }
        }
    }
    if failed > 0 || total == 0 { 1 } else { 0 }
}

fn kind_for(info: &FileInfo, fmt: &str, frames: bool) -> Option<&'static str> {
    let kinds = local::kinds_for(info);
    if frames {
        return (kinds.contains(&"frames") && local::formats_for("frames").contains(&fmt)).then_some("frames");
    }
    ["video", "audio", "image"].into_iter().find(|k| kinds.contains(k) && local::formats_for(k).contains(&fmt))
}

fn convert(a: ConvertArgs, mut extras: Extras) -> i32 {
    let fmt = a.format.to_lowercase().trim_start_matches('.').to_string();
    extras.strip_metadata = a.strip_metadata;
    extras.max_kb = a.max_kb;
    let mut dirs: Vec<String> = deps::ffmpeg_candidates().into_iter().map(|t| t.path).collect();
    if dirs.is_empty() {
        match deps::find_ffmpeg(true) {
            Ok(Some(t)) => dirs.push(t.path),
            Ok(None) => {}
            Err(e) => return usage(&format!("Couldn't get ffmpeg: {e}")),
        }
    }
    let mut failed = 0;
    for path in &a.files {
        let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let mut printer = Printer::new(a.shared.quiet, &name);
        let path = config::expand_user(&path.to_string_lossy());
        let info = match local::probe_file(&path, dirs.first().map(String::as_str)) {
            Ok(i) => i,
            Err(e) => {
                printer.fail(&name, &e);
                failed += 1;
                continue;
            }
        };
        let Some(kind) = kind_for(&info, &fmt, a.frames) else {
            printer.quiet = false;
            printer.done(&format!("✗ {name}: can't make {} from this file.", fmt.to_uppercase()));
            failed += 1;
            continue;
        };
        let qualities = local::qualities_for(&info, kind, &fmt);
        let quality = if qualities.contains(&a.quality) { a.quality.clone() } else { qualities[0].clone() };
        let choice = Choice::new(kind, &fmt, &quality).with_extras(extras.clone());
        let ffmpeg_dir = dirs
            .iter()
            .find(|d| local::missing_encoder(kind, &fmt, &|e| deps::ffmpeg_has_encoder(d, e)).is_empty())
            .or(dirs.first())
            .cloned();
        let cancel = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        match local::run_convert(&info, &choice, &mut |p| printer.show(&p), &cancel, ffmpeg_dir.as_deref()) {
            Err(e) => {
                printer.fail(&name, &e);
                failed += 1;
            }
            Ok(out) => {
                let out_name = std::path::Path::new(&out).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
                printer.done(&format!("✓ {name} → {out_name}"));
                println!("{out}");
                library::history_add(HistoryEntry::new(&name, &out, &choice.label(), &path.to_string_lossy(), "convert"));
            }
        }
    }
    if failed > 0 { 1 } else { 0 }
}

fn sync(check: bool, quiet: bool) -> i32 {
    let settings = config::load();
    let mut items = library::following_load();
    if items.is_empty() {
        eprintln!("You're not following anything. Press a on a channel or playlist in the app.");
        return 0;
    }
    let mut failed = 0;
    for item in items.iter_mut() {
        match probe(&item.url, &settings.cookies_browser, library::SYNC_DEPTH) {
            Ok(Probed::Playlist(feed)) => {
                library::apply_sync(item, Some(&feed), "");
            }
            other => {
                let msg = match other {
                    Err(e) => errors::explain(&e).message,
                    _ => "No longer a list of videos.".into(),
                };
                library::apply_sync(item, None, &msg);
                eprintln!("✗ {}: {}", item.title, item.error);
                failed += 1;
            }
        }
        library::following_update(item);
        if !quiet || check {
            eprintln!("{}: {} new", item.title, item.new.len());
        }
    }
    if check {
        return if failed > 0 { 1 } else { 0 };
    }
    let template = if settings.filename_template.contains("{playlist}") {
        settings.filename_template.clone()
    } else {
        format!("{{playlist}}/{}", settings.filename_template)
    };
    for item in items.iter_mut() {
        let choice = item.choice();
        for entry in item.new.clone() {
            let mut job = Job::new(&entry.url, &entry.title, choice.clone());
            job.template = template.clone();
            job.fields.insert("playlist".into(), safe_name(&item.title));
            job.fields.insert("index".into(), String::new());
            job.keep_transcript = settings.keep_transcripts;
            let mut printer = Printer::new(quiet, &entry.title);
            match runner::run_job(&job, &settings.download_path(), &mut |p| printer.show(&p), &settings.cookies_browser) {
                Err(e) => {
                    printer.fail(&entry.title, &e);
                    failed += 1;
                }
                Ok(path) => {
                    printer.done(&format!("✓ {}", entry.title));
                    println!("{path}");
                    library::history_add(HistoryEntry::new(&entry.title, &path, &choice.label(), &entry.url, "download"));
                    item.new.retain(|e| e.id != entry.id);
                    item.seen.push(entry.id.clone());
                    let start = item.seen.len().saturating_sub(library::MAX_SEEN);
                    item.seen.drain(..start);
                    library::following_update(item);
                }
            }
        }
    }
    if failed > 0 { 1 } else { 0 }
}
