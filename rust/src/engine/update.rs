//! Check for newer yt-dlp and Carrot Leaf releases and upgrade them in place.
//!
//! The Rust build calls the `yt-dlp` program rather than importing it, so the upgrade goes
//! through whatever installed that program: Carrot Leaf's own environment, Homebrew, pipx,
//! uv, pip, or yt-dlp's own `-U`. Carrot Leaf upgrades itself when you quit, if it was
//! installed with uv or pipx.

use super::deps;
use std::process::{Command, Stdio};

pub const PYPI_URL: &str = "https://pypi.org/pypi/yt-dlp/json";
pub const APP_PYPI_URL: &str = "https://pypi.org/pypi/carrot-leaf/json";

/// yt-dlp's version, from the program itself. Asking it costs a Python startup, so the
/// answer is kept next to the settings, keyed by the program's path and modified time.
pub fn installed_version() -> String {
    let exe = deps::yt_dlp();
    let resolved = deps::which(&exe).unwrap_or_else(|| std::path::PathBuf::from(&exe));
    let stamp = std::fs::metadata(&resolved)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let key = format!("{}\t{stamp}", resolved.display());
    let cache = crate::config::config_dir().join("yt-dlp-version.txt");
    if let Ok(text) = std::fs::read_to_string(&cache) {
        if let Some((k, v)) = text.rsplit_once('\n') {
            if k == key && !v.is_empty() {
                return v.to_string();
            }
        }
    }
    let version = Command::new(&exe)
        .arg("--version")
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .filter(|s| !s.is_empty());
    match version {
        Some(v) => {
            let _ = std::fs::create_dir_all(crate::config::config_dir());
            let _ = std::fs::write(&cache, format!("{key}\n{v}"));
            v
        }
        None => "not found".into(),
    }
}

pub fn key(version: &str) -> Vec<u64> {
    version.split('.').filter_map(|p| p.parse().ok()).collect()
}

pub fn latest_version() -> Option<String> {
    latest_from(PYPI_URL)
}

fn latest_from(url: &str) -> Option<String> {
    let resp = ureq::get(url).timeout(std::time::Duration::from_secs(4)).call().ok()?;
    let v: serde_json::Value = resp.into_json().ok()?;
    v["info"]["version"].as_str().map(String::from)
}

/// The newer version string, or None if up to date or offline.
pub fn update_available(installed: &str) -> Option<String> {
    let latest = latest_version()?;
    (key(&latest) > key(installed)).then_some(latest)
}

pub fn upgrade_command() -> Vec<String> {
    let ytdlp = std::fs::canonicalize(deps::yt_dlp()).unwrap_or_default();
    let exe = ytdlp.to_string_lossy().replace('\\', "/");
    let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect();
    // yt-dlp that came with Carrot Leaf: upgrade just it, inside that environment.
    if let Some(dir) = deps::install_bin_dir() {
        if ytdlp.parent() == std::fs::canonicalize(&dir).ok().as_deref() {
            let dir_s = dir.to_string_lossy().into_owned();
            if deps::in_uv_tools(&dir_s) {
                if let Some(uv) = deps::which("uv") {
                    let python = dir.join(if cfg!(windows) { "python.exe" } else { "python" });
                    return vec![
                        uv.to_string_lossy().into(),
                        "pip".into(),
                        "install".into(),
                        "--python".into(),
                        python.to_string_lossy().into(),
                        "-U".into(),
                        "yt-dlp[default]".into(),
                    ];
                }
            }
            if dir_s.replace('\\', "/").contains("/pipx/") {
                return s(&["pipx", "runpip", "carrot-leaf", "install", "-U", "yt-dlp[default]"]);
            }
        }
    }
    if exe.contains("/Cellar/") || exe.contains("/homebrew/") {
        return s(&["brew", "upgrade", "yt-dlp"]);
    }
    if exe.contains("/pipx/venvs/") {
        return s(&["pipx", "upgrade", "yt-dlp"]);
    }
    if deps::in_uv_tools(&exe) {
        return s(&["uv", "tool", "upgrade", "yt-dlp"]);
    }
    vec![deps::yt_dlp(), "-U".into()]
}

/// Run the upgrade. Returns (ok, output).
pub fn upgrade() -> (bool, String) {
    let cmd = upgrade_command();
    match Command::new(&cmd[0]).args(&cmd[1..]).stdin(Stdio::null()).output() {
        Ok(o) => {
            let text = format!("{}{}", String::from_utf8_lossy(&o.stdout), String::from_utf8_lossy(&o.stderr));
            (o.status.success(), text.trim().to_string())
        }
        Err(e) => (false, e.to_string()),
    }
}

// ---------- Carrot Leaf itself ----------

/// The newer Carrot Leaf on PyPI, or None if up to date, offline, or not self-updatable.
pub fn app_update_available() -> Option<String> {
    if std::env::var_os("CARROT_NO_AUTO_UPDATE").is_some() {
        return None;
    }
    self_upgrade_command()?;
    let latest = latest_from(APP_PYPI_URL)?;
    (key(&latest) > key(crate::VERSION)).then_some(latest)
}

/// How to upgrade this install, or None for installs we shouldn't touch (pip, dev builds).
pub fn self_upgrade_command() -> Option<Vec<String>> {
    let dir = deps::install_bin_dir()?.to_string_lossy().replace('\\', "/");
    let (tool, args): (&str, &[&str]) = if deps::in_uv_tools(&dir) {
        ("uv", &["tool", "upgrade", "carrot-leaf"])
    } else if dir.contains("/pipx/") {
        ("pipx", &["upgrade", "carrot-leaf"])
    } else {
        return None;
    };
    let exe = deps::which(tool)?;
    Some(std::iter::once(exe.to_string_lossy().into_owned()).chain(args.iter().map(|a| a.to_string())).collect())
}

/// Start the upgrade detached from this process, so quitting isn't held up. True if started.
pub fn start_self_upgrade() -> bool {
    let Some(cmd) = self_upgrade_command() else {
        return false;
    };
    let mut c = Command::new(&cmd[0]);
    c.args(&cmd[1..]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        c.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        c.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP);
    }
    c.spawn().is_ok()
}
