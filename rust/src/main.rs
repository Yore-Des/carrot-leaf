//! Carrot Leaf: a terminal video downloader and converter built on yt-dlp.
//!
//! `carrot [URL or FILE]`, `carrot get`, `carrot convert`, `carrot sync`, `carrot setup`,
//! `carrot update`, `carrot --version`.

mod batch;
mod config;
mod engine;
mod library;
mod transcripts;
mod ui;

use engine::{deps, local, update};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

const HELP: &str = "usage: carrot [-h] [--version] [url]

Download or convert videos from YouTube and 1,300+ other sites.

positional arguments:
  url         video link (or file to convert) to open straight away

options:
  -h, --help  show this help message and exit
  --version   show program's version number and exit

Without the app: `carrot get URL --format mp3` downloads, `carrot convert FILE
--format webp` converts (see `carrot get --help`). `carrot setup` downloads
ffmpeg now, `carrot update` updates yt-dlp.";

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let code = match argv.first().map(String::as_str) {
        Some("update") => update_cmd(),
        Some("setup") => setup(),
        Some("get" | "convert" | "sync") => batch::main(&argv),
        Some("--version") => {
            println!("carrot-leaf {VERSION} (yt-dlp {})", update::installed_version());
            0
        }
        Some("-h" | "--help") => {
            println!("{HELP}");
            0
        }
        Some(a) if a.starts_with('-') => {
            eprintln!("usage: carrot [-h] [--version] [url]\ncarrot: error: unrecognized arguments: {a}");
            2
        }
        first => ui::run(first.unwrap_or("").to_string()),
    };
    std::process::exit(code);
}

fn setup() -> i32 {
    if deps::bundled_ffmpeg_ready() {
        println!("ffmpeg is already set up.");
    } else {
        println!("Getting ffmpeg for Carrot Leaf (one time, about 50 MB)…");
    }
    let tool = match deps::fetch_bundled_ffmpeg(true) {
        Ok(t) => t,
        Err(e) => {
            println!("Couldn't download ffmpeg: {e}");
            println!("Carrot Leaf will try again the first time it needs ffmpeg.");
            return 1;
        }
    };
    println!("✓ ffmpeg is in {}", tool.path);
    if let Some(sys) = deps::system_ffmpeg() {
        println!("  Your own ffmpeg in {} is used too, for anything it can do.", sys.path);
    }
    let dirs: Vec<String> = deps::ffmpeg_candidates().into_iter().map(|t| t.path).collect();
    let has = |e: &str| dirs.iter().any(|d| deps::ffmpeg_has_encoder(d, e));
    for kind in ["video", "audio", "image"] {
        let formats = local::formats_for(kind);
        let missing: Vec<String> = formats.iter().filter(|f| !local::missing_encoder(kind, f, &has).is_empty()).map(|f| f.to_uppercase()).collect();
        let mut line = format!("  {} {kind} formats ready", formats.len() - missing.len());
        if !missing.is_empty() {
            line += &format!(" (not available: {})", missing.join(", "));
        }
        println!("{line}");
    }
    0
}

fn update_cmd() -> i32 {
    let current = update::installed_version();
    let latest = update::latest_version();
    if let Some(l) = &latest {
        if update::key(l) <= update::key(&current) {
            println!("yt-dlp {current} is already the latest version.");
            return 0;
        }
    }
    println!("Updating yt-dlp {current} → {}…", latest.as_deref().unwrap_or("latest"));
    println!("$ {}", update::upgrade_command().join(" "));
    let (ok, output) = update::upgrade();
    if !output.is_empty() {
        println!("{output}");
    }
    println!("{}", if ok { "Done." } else { "Update failed." });
    if ok { 0 } else { 1 }
}
