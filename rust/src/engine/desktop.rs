//! The bits of the desktop around the terminal: clipboard, notifications, Finder/Explorer.
//! Everything here is best effort; a missing tool means the feature quietly does nothing.

use super::deps::which;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

fn run(cmd: &[&str], input: Option<&str>) -> Option<String> {
    let mut c = Command::new(cmd[0]);
    c.args(&cmd[1..]).stdout(Stdio::piped()).stderr(Stdio::null());
    c.stdin(if input.is_some() { Stdio::piped() } else { Stdio::null() });
    let mut child = c.spawn().ok()?;
    if let Some(text) = input {
        child.stdin.take()?.write_all(text.as_bytes()).ok()?;
    }
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        match child.try_wait() {
            Ok(Some(s)) => {
                let mut out = String::new();
                std::io::Read::read_to_string(&mut child.stdout.take()?, &mut out).ok()?;
                return s.success().then_some(out);
            }
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(10)),
            _ => {
                let _ = child.kill();
                return None;
            }
        }
    }
}

/// The clipboard's text, or "" when it can't be read.
pub fn read_clipboard() -> String {
    if std::env::var_os("CARROT_NO_CLIPBOARD").is_some() {
        return String::new();
    }
    let cmds: &[&[&str]] = if cfg!(target_os = "macos") {
        &[&["pbpaste"]]
    } else if cfg!(windows) {
        &[&["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"]]
    } else {
        &[&["wl-paste", "--no-newline"], &["xclip", "-o", "-selection", "clipboard"], &["xsel", "--clipboard", "--output"]]
    };
    for cmd in cmds {
        if which(cmd[0]).is_some() {
            if let Some(t) = run(cmd, None) {
                return t.trim().to_string();
            }
        }
    }
    String::new()
}

pub fn write_clipboard(text: &str) -> bool {
    let cmds: &[&[&str]] = if cfg!(target_os = "macos") {
        &[&["pbcopy"]]
    } else if cfg!(windows) {
        &[&["clip"]]
    } else {
        &[&["wl-copy"], &["xclip", "-selection", "clipboard"], &["xsel", "--clipboard"]]
    };
    cmds.iter().any(|c| which(c[0]).is_some() && run(c, Some(text)).is_some())
}

fn applescript(text: &str) -> String {
    format!("\"{}\"", text.replace('\\', "\\\\").replace('"', "\\\""))
}

fn spawn(cmd: &[String]) {
    let _ = Command::new(&cmd[0]).args(&cmd[1..]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn();
}

/// A system notification. Returns straight away.
pub fn notify(title: &str, message: &str) {
    if std::env::var_os("CARROT_NO_NOTIFY").is_some() {
        return;
    }
    let cmd: Vec<String> = if cfg!(target_os = "macos") {
        vec!["osascript".into(), "-e".into(), format!("display notification {} with title {}", applescript(message), applescript(title))]
    } else if cfg!(windows) {
        let ps = |t: &str| format!("'{}'", t.replace('\'', "''"));
        vec![
            "powershell".into(),
            "-NoProfile".into(),
            "-WindowStyle".into(),
            "Hidden".into(),
            "-Command".into(),
            format!(
                "Add-Type -AssemblyName System.Windows.Forms;$n = New-Object System.Windows.Forms.NotifyIcon;\
                 $n.Icon = [System.Drawing.SystemIcons]::Information;$n.BalloonTipTitle = {}; $n.BalloonTipText = {};\
                 $n.Visible = $true; $n.ShowBalloonTip(5000); Start-Sleep -Seconds 6; $n.Dispose()",
                ps(title),
                ps(message)
            ),
        ]
    } else if which("notify-send").is_some() {
        vec!["notify-send".into(), "--app-name=Carrot Leaf".into(), title.into(), message.into()]
    } else {
        return;
    };
    spawn(&cmd);
}

/// Show a file in Finder / Explorer / the default file manager.
pub fn reveal(path: &str) {
    let cmd: Vec<String> = if cfg!(target_os = "macos") {
        vec!["open".into(), "-R".into(), path.into()]
    } else if cfg!(windows) {
        vec!["explorer".into(), "/select,".into(), path.into()]
    } else {
        vec!["xdg-open".into(), Path::new(path).parent().map(|p| p.to_string_lossy().into_owned()).unwrap_or_default()]
    };
    spawn(&cmd);
}

/// Open a file in the app the system uses for it.
pub fn open_file(path: &str) {
    let cmd: Vec<String> = if cfg!(target_os = "macos") {
        vec!["open".into(), path.into()]
    } else if cfg!(windows) {
        vec!["cmd".into(), "/C".into(), "start".into(), "".into(), path.into()]
    } else {
        vec!["xdg-open".into(), path.into()]
    };
    spawn(&cmd);
}

/// Play a file from `seconds` in a player that can start there. Falls back to opening it.
pub fn play_at(path: &str, seconds: f64) -> bool {
    let t = format!("{seconds:.0}");
    let players: [(&str, Vec<String>); 3] = [
        ("mpv", vec!["mpv".into(), format!("--start={t}"), path.into()]),
        ("iina", vec!["iina".into(), format!("--mpv-start={t}"), path.into()]),
        ("vlc", vec!["vlc".into(), format!("--start-time={t}"), path.into()]),
    ];
    for (exe, cmd) in players {
        if which(exe).is_some() {
            spawn(&cmd);
            return true;
        }
    }
    if cfg!(target_os = "macos") {
        if Path::new("/Applications/IINA.app").exists() {
            spawn(&["open", "-a", "IINA", "--args", &format!("--mpv-start={t}"), path].map(String::from));
            return true;
        }
        let vlc = "/Applications/VLC.app/Contents/MacOS/VLC";
        if Path::new(vlc).exists() {
            spawn(&[vlc, &format!("--start-time={t}"), path].map(String::from));
            return true;
        }
    }
    open_file(path);
    false
}
