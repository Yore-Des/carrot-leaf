//! Check for a newer yt-dlp and upgrade it in place.
//!
//! The Rust build calls the `yt-dlp` program rather than importing it, so the upgrade goes
//! through whatever installed that program: Homebrew, pipx, uv, pip, or yt-dlp's own `-U`.

use super::deps;
use std::process::{Command, Stdio};

pub const PYPI_URL: &str = "https://pypi.org/pypi/yt-dlp/json";

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
    let resp = ureq::get(PYPI_URL).timeout(std::time::Duration::from_secs(4)).call().ok()?;
    let v: serde_json::Value = resp.into_json().ok()?;
    v["info"]["version"].as_str().map(String::from)
}

/// The newer version string, or None if up to date or offline.
pub fn update_available(installed: &str) -> Option<String> {
    let latest = latest_version()?;
    (key(&latest) > key(installed)).then_some(latest)
}

pub fn upgrade_command() -> Vec<String> {
    let exe = std::fs::canonicalize(deps::yt_dlp()).unwrap_or_default().to_string_lossy().replace('\\', "/");
    let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect();
    if exe.contains("/Cellar/") || exe.contains("/homebrew/") {
        return s(&["brew", "upgrade", "yt-dlp"]);
    }
    if exe.contains("/pipx/venvs/") {
        return s(&["pipx", "upgrade", "yt-dlp"]);
    }
    if exe.contains("/uv/tools/") {
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
