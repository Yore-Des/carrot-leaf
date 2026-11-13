//! User settings, stored as TOML in the platform config directory (the same file the
//! Python version reads, so both share settings).

use serde::Serialize;
use std::path::{Path, PathBuf};

pub const APP_NAME: &str = "carrot-leaf";

pub fn default_download_dir() -> String {
    dirs::download_dir()
        .or_else(|| dirs::home_dir().map(|h| h.join("Downloads")))
        .unwrap_or_else(|| PathBuf::from("."))
        .to_string_lossy()
        .into_owned()
}

pub fn config_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("CARROT_CONFIG_DIR") {
        return PathBuf::from(dir);
    }
    let base = if cfg!(windows) { dirs::config_local_dir() } else { dirs::config_dir() };
    base.unwrap_or_else(|| PathBuf::from(".")).join(APP_NAME)
}

pub fn config_path() -> PathBuf {
    config_dir().join("config.toml")
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct Settings {
    pub download_dir: String,
    pub last_kind: String,
    pub last_video_format: String,
    pub last_video_quality: String,
    pub last_audio_format: String,
    pub last_audio_quality: String,
    pub last_image_format: String,
    pub last_image_quality: String,
    pub last_frames_format: String,
    pub last_frames_quality: String,
    pub cookies_browser: String,
    pub theme: String,
    pub filename_template: String,
    pub parallel_downloads: i64,
    pub watch_clipboard: bool,
    pub notify_when_done: bool,
    pub keep_transcripts: bool,
    pub last_subtitles: String,
    pub last_sub_langs: String,
    pub last_sponsorblock: String,
    pub last_normalize: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            download_dir: default_download_dir(),
            last_kind: "video".into(),
            last_video_format: "mp4".into(),
            last_video_quality: "best".into(),
            last_audio_format: "mp3".into(),
            last_audio_quality: "best".into(),
            last_image_format: "jpg".into(),
            last_image_quality: "best".into(),
            last_frames_format: "png".into(),
            last_frames_quality: "1".into(),
            cookies_browser: String::new(),
            theme: "carrot".into(),
            filename_template: "{title} [{id}]".into(),
            parallel_downloads: 1,
            watch_clipboard: true,
            notify_when_done: true,
            keep_transcripts: true,
            last_subtitles: String::new(),
            last_sub_langs: "en".into(),
            last_sponsorblock: String::new(),
            last_normalize: false,
        }
    }
}

impl Settings {
    pub fn download_path(&self) -> PathBuf {
        expand_user(&self.download_dir)
    }

    pub fn last_format(&self, kind: &str) -> &str {
        match kind {
            "video" => &self.last_video_format,
            "audio" => &self.last_audio_format,
            "image" => &self.last_image_format,
            "frames" => &self.last_frames_format,
            _ => "",
        }
    }

    pub fn last_quality(&self, kind: &str) -> &str {
        match kind {
            "video" => &self.last_video_quality,
            "audio" => &self.last_audio_quality,
            "image" => &self.last_image_quality,
            "frames" => &self.last_frames_quality,
            _ => "",
        }
    }

    pub fn set_last(&mut self, kind: &str, fmt: &str, quality: &str) {
        let (f, q) = match kind {
            "video" => (&mut self.last_video_format, &mut self.last_video_quality),
            "audio" => (&mut self.last_audio_format, &mut self.last_audio_quality),
            "image" => (&mut self.last_image_format, &mut self.last_image_quality),
            "frames" => (&mut self.last_frames_format, &mut self.last_frames_quality),
            _ => return,
        };
        *f = fmt.into();
        *q = quality.into();
    }
}

pub fn expand_user(p: &str) -> PathBuf {
    if p == "~" {
        return dirs::home_dir().unwrap_or_default();
    }
    if let Some(rest) = p.strip_prefix("~/") {
        return dirs::home_dir().unwrap_or_default().join(rest);
    }
    PathBuf::from(p)
}

/// Keep only known keys holding the right type, so a hand-edited file can't crash startup.
pub fn load_from(path: &Path) -> Settings {
    let mut s = Settings::default();
    let Ok(text) = std::fs::read_to_string(path) else { return s };
    let Ok(table) = text.parse::<toml::Table>() else { return s };
    macro_rules! take {
        ($($field:ident: $kind:ident),* $(,)?) => {
            $( if let Some(toml::Value::$kind(v)) = table.get(stringify!($field)) { s.$field = v.clone().into(); } )*
        };
    }
    take!(
        download_dir: String, last_kind: String, last_video_format: String, last_video_quality: String,
        last_audio_format: String, last_audio_quality: String, last_image_format: String,
        last_image_quality: String, last_frames_format: String, last_frames_quality: String,
        cookies_browser: String, theme: String, filename_template: String, parallel_downloads: Integer,
        watch_clipboard: Boolean, notify_when_done: Boolean, keep_transcripts: Boolean,
        last_subtitles: String, last_sub_langs: String, last_sponsorblock: String, last_normalize: Boolean,
    );
    if s.download_dir.is_empty() {
        s.download_dir = default_download_dir();
    }
    s
}

pub fn load() -> Settings {
    load_from(&config_path())
}

pub fn save(settings: &Settings) -> std::io::Result<()> {
    let path = config_path();
    if let Some(p) = path.parent() {
        std::fs::create_dir_all(p)?;
    }
    let text = toml::to_string(settings).map_err(std::io::Error::other)?;
    std::fs::write(path, text)
}
