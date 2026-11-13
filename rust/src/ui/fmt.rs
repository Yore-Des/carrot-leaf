//! Human-readable sizes, times and paths.

use std::path::Path;

pub fn human_size(n: f64) -> String {
    if n <= 0.0 {
        return String::new();
    }
    let mut n = n;
    for unit in ["B", "KB", "MB", "GB"] {
        if n < 1024.0 {
            return if unit == "B" { format!("{n:.0} B") } else { format!("{n:.1} {unit}") };
        }
        n /= 1024.0;
    }
    format!("{n:.1} TB")
}

pub fn human_eta(seconds: f64) -> String {
    let s = seconds.max(0.0) as i64;
    let (m, s) = (s / 60, s % 60);
    let (h, m) = (m / 60, m % 60);
    if h > 0 { format!("{h}:{m:02}:{s:02}") } else { format!("{m}:{s:02}") }
}

pub fn short_path(path: &str) -> String {
    if let Some(home) = dirs::home_dir() {
        if let Ok(rel) = Path::new(path).strip_prefix(&home) {
            return format!("~/{}", rel.to_string_lossy().replace('\\', "/"));
        }
    }
    path.to_string()
}

/// `1:05`, `1:02:03`, or "" for nothing.
pub fn duration_text(seconds: Option<f64>) -> String {
    let Some(s) = seconds.filter(|s| *s > 0.0) else { return String::new() };
    let total = s.round() as i64;
    let (m, s) = (total / 60, total % 60);
    let (h, m) = (m / 60, m % 60);
    if h > 0 { format!("{h}:{m:02}:{s:02}") } else { format!("{m}:{s:02}") }
}
