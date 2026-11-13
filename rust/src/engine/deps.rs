//! Locate ffmpeg/ffprobe, yt-dlp and the image tools.
//!
//! Binaries on PATH win. Otherwise fall back to Carrot Leaf's own static ffmpeg build
//! (downloaded once, by `carrot setup` or on first use) in the same folder the Python
//! version uses, so both share it. Converting local files can use both ffmpegs.

use std::collections::{HashMap, HashSet};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{LazyLock, Mutex};

pub const CRUMB: &str = "installed.crumb";

fn ffmpeg_zip(key: &str) -> Option<&'static str> {
    Some(match key {
        "win32" => "https://github.com/zackees/ffmpeg_bins/raw/main/v8.0/win32.zip",
        "darwin" => "https://github.com/zackees/ffmpeg_bins/raw/main/v8.0/darwin.zip",
        "darwin_arm64" => "https://github.com/zackees/ffmpeg_bins/raw/main/v8.0/darwin_arm64.zip",
        "linux" => "https://github.com/zackees/ffmpeg_bins/raw/main/v8.0/linux.zip",
        "linux_arm64" => "https://github.com/zackees/ffmpeg_bins/raw/main/v8.0/linux_arm64.zip",
        _ => return None,
    })
}

#[derive(Clone, Debug)]
pub struct Tool {
    pub path: String,
    pub bundled: bool,
}

const EXE: &str = if cfg!(windows) { ".exe" } else { "" };

/// `name` on PATH, like shutil.which.
pub fn which(name: &str) -> Option<PathBuf> {
    which_in(name, &std::env::var_os("PATH")?)
}

pub fn which_in(name: &str, path: &std::ffi::OsStr) -> Option<PathBuf> {
    for dir in std::env::split_paths(path) {
        let candidate = dir.join(format!("{name}{EXE}"));
        if is_executable(&candidate) {
            return Some(candidate);
        }
    }
    None
}

fn is_executable(p: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        p.metadata().map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0).unwrap_or(false)
    }
    #[cfg(not(unix))]
    {
        p.is_file()
    }
}

pub fn system_ffmpeg() -> Option<Tool> {
    let (ff, fp) = (which("ffmpeg")?, which("ffprobe")?);
    if ff.parent() == fp.parent() {
        return Some(Tool { path: ff.parent()?.to_string_lossy().into(), bundled: false });
    }
    None
}

pub fn platform_key() -> String {
    let arm = matches!(std::env::consts::ARCH, "aarch64" | "arm64");
    match std::env::consts::OS {
        "windows" => "win32".into(),
        "macos" => if arm { "darwin_arm64".into() } else { "darwin".into() },
        "linux" => if arm { "linux_arm64".into() } else { "linux".into() },
        other => other.into(),
    }
}

pub fn data_dir() -> PathBuf {
    if cfg!(target_os = "linux") {
        dirs::data_dir().unwrap_or_else(|| PathBuf::from(".")).join("carrot-leaf")
    } else {
        dirs::data_local_dir().or_else(dirs::data_dir).unwrap_or_else(|| PathBuf::from(".")).join("carrot-leaf")
    }
}

/// Where the static build lives. Outside the app, so it survives upgrades.
pub fn bundled_dir() -> PathBuf {
    data_dir().join("ffmpeg").join(platform_key())
}

pub fn bundled_ffmpeg_ready() -> bool {
    bundled_dir().join(CRUMB).exists()
}

pub fn bundled_ffmpeg() -> Option<Tool> {
    bundled_ffmpeg_ready().then(|| Tool { path: bundled_dir().to_string_lossy().into(), bundled: true })
}

/// Every ffmpeg Carrot can use, the system's first.
pub fn ffmpeg_candidates() -> Vec<Tool> {
    [system_ffmpeg(), bundled_ffmpeg()].into_iter().flatten().collect()
}

/// Download the static build if it isn't there yet (about 50 MB, once). Blocks.
pub fn fetch_bundled_ffmpeg(show_progress: bool) -> Result<Tool, String> {
    let target = bundled_dir();
    if target.join(CRUMB).exists() {
        return Ok(Tool { path: target.to_string_lossy().into(), bundled: true });
    }
    let key = platform_key();
    let url = ffmpeg_zip(&key).ok_or_else(|| format!("No ffmpeg build for {key}. Install ffmpeg yourself."))?;
    let parent = target.parent().unwrap().to_path_buf();
    std::fs::create_dir_all(&parent).map_err(|e| e.to_string())?;
    let work = tempfile::Builder::new().prefix(".download-").tempdir_in(&parent).map_err(|e| e.to_string())?;
    let archive = work.path().join("ffmpeg.zip");
    download(url, &archive, show_progress)?;
    let file = std::fs::File::open(&archive).map_err(|e| e.to_string())?;
    let mut zip = zip::ZipArchive::new(file).map_err(|e| e.to_string())?;
    let unpacked_root = work.path().join("unpacked");
    zip.extract(&unpacked_root).map_err(|e| e.to_string())?;
    let _ = std::fs::remove_file(&archive);
    let unpacked = unpacked_root.join(&key);
    for name in ["ffmpeg", "ffprobe"] {
        let p = unpacked.join(format!("{name}{EXE}"));
        if !p.is_file() {
            return Err(format!("The ffmpeg download has no {name}{EXE}."));
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perm = p.metadata().map_err(|e| e.to_string())?.permissions();
            perm.set_mode(perm.mode() | 0o111);
            std::fs::set_permissions(&p, perm).map_err(|e| e.to_string())?;
        }
    }
    std::fs::write(unpacked.join(CRUMB), format!("installed from {url}")).map_err(|e| e.to_string())?;
    if target.exists() && !target.join(CRUMB).exists() {
        let _ = std::fs::remove_dir_all(&target);
    }
    if let Err(e) = std::fs::rename(&unpacked, &target) {
        if !target.join(CRUMB).exists() {
            return Err(e.to_string());
        }
    }
    Ok(Tool { path: target.to_string_lossy().into(), bundled: true })
}

fn download(url: &str, dest: &Path, show_progress: bool) -> Result<(), String> {
    let resp = ureq::get(url).set("User-Agent", "carrot-leaf").timeout(std::time::Duration::from_secs(60)).call().map_err(|e| e.to_string())?;
    let total: u64 = resp.header("Content-Length").and_then(|v| v.parse().ok()).unwrap_or(0);
    let mut reader = resp.into_reader();
    let mut out = std::fs::File::create(dest).map_err(|e| e.to_string())?;
    let mut buf = vec![0u8; 1 << 18];
    let (mut done, mut shown) = (0u64, u64::MAX);
    loop {
        let n = reader.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        out.write_all(&buf[..n]).map_err(|e| e.to_string())?;
        done += n as u64;
        if show_progress && total > 0 && done * 100 / total != shown {
            shown = done * 100 / total;
            eprint!("\r  {shown}% of {:.0} MB", total as f64 / 1048576.0);
        }
    }
    if show_progress {
        eprintln!();
    }
    Ok(())
}

/// The directory holding ffmpeg + ffprobe. With `fetch`, downloads the bundled build.
pub fn find_ffmpeg(fetch: bool) -> Result<Option<Tool>, String> {
    if let Some(t) = system_ffmpeg() {
        return Ok(Some(t));
    }
    if !fetch {
        return Ok(bundled_ffmpeg());
    }
    fetch_bundled_ffmpeg(false).map(Some)
}

static ENCODERS: LazyLock<Mutex<HashMap<String, Option<HashSet<String>>>>> = LazyLock::new(Default::default);

/// Every encoder this ffmpeg build has, or None if it couldn't be asked.
pub fn ffmpeg_encoders(dir: &str) -> Option<HashSet<String>> {
    if let Some(found) = ENCODERS.lock().unwrap().get(dir) {
        return found.clone();
    }
    let exe = exe_in("ffmpeg", Some(dir));
    let names = Command::new(exe)
        .args(["-hide_banner", "-encoders"])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .lines()
                .filter_map(|l| l.split_whitespace().nth(1).map(String::from))
                .collect::<HashSet<_>>()
        });
    ENCODERS.lock().unwrap().insert(dir.to_string(), names.clone());
    names
}

pub fn ffmpeg_has_encoder(dir: &str, encoder: &str) -> bool {
    match ffmpeg_encoders(dir) {
        None => true,
        Some(n) => n.contains(encoder),
    }
}

/// `name` inside `dir` if it's there, else on PATH, else the bare name.
pub fn exe_in(name: &str, dir: Option<&str>) -> String {
    if let Some(d) = dir {
        if let Some(p) = which_in(name, std::ffi::OsStr::new(d)) {
            return p.to_string_lossy().into();
        }
    }
    which(name).map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|| name.to_string())
}

pub fn yt_dlp() -> String {
    exe_in("yt-dlp", None)
}
