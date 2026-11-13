//! Convert files already on disk with ffmpeg. No yt-dlp involved.
//!
//! `parse_paths` turns pasted or dropped text into files, `probe_file` reads one with ffprobe,
//! `build_ffmpeg_args` plans the conversion (pure, so it is easy to test), and `run_convert`
//! runs it with progress and cancel. JPEG XL and HEIC, which no common ffmpeg writes, go
//! through libjxl's `cjxl` and libheif's `heif-enc` when they're installed.

use super::options::{AUDIO_BITRATES, Choice, Extras, FRAME_RATES, LOUDNORM, gif_filter, image_sizes, video_qualities};
use super::proc::{Cancel, RunError, run_lines};
use super::runner::{CANCELLED, Progress, State, move_into, unique_path};
use super::deps;
use serde_json::Value;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Media {
    Video,
    Audio,
    Image,
    Animated,
}

pub type HasEncoder<'a> = &'a dyn Fn(&str) -> bool;

#[cfg(test)]
pub fn any_encoder(_: &str) -> bool {
    true
}

// ---------- what each format is ----------

pub struct AudioTarget {
    pub note: &'static str,
    pub encode: &'static [&'static str],
    /// Codecs taken as-is at "best"; None: any.
    pub copy: Option<&'static [&'static str]>,
    pub lossless: bool,
    pub bitrate: &'static str,
    pub best: &'static [&'static str],
    pub fallback: &'static [&'static str],
    pub ext: &'static str,
}

const fn at(note: &'static str, encode: &'static [&'static str], copy: Option<&'static [&'static str]>) -> AudioTarget {
    AudioTarget { note, encode, copy, lossless: false, bitrate: "192", best: &[], fallback: &[], ext: "" }
}

pub const LOCAL_AUDIO_FORMATS: &[&str] =
    &["mp3", "m4a", "aac", "opus", "ogg", "flac", "alac", "wav", "aiff", "wma", "ac3", "eac3", "mp2", "wv", "tta", "caf", "au", "mka"];

pub fn audio_target(fmt: &str) -> AudioTarget {
    match fmt {
        "mp3" => AudioTarget { best: &["-q:a", "0"], ..at("plays everywhere", &["-c:a", "libmp3lame"], Some(&["mp3"])) },
        "m4a" => AudioTarget { bitrate: "256", ..at("AAC, Apple devices", &["-c:a", "aac"], Some(&["aac"])) },
        "aac" => AudioTarget { bitrate: "256", ..at("raw AAC stream", &["-c:a", "aac"], Some(&["aac"])) },
        "opus" => AudioTarget { bitrate: "160", fallback: &["-c:a", "opus", "-strict", "-2"], ..at("small and sharp", &["-c:a", "libopus"], Some(&["opus"])) },
        "ogg" => AudioTarget { fallback: &["-c:a", "vorbis", "-strict", "-2", "-ac", "2"], ..at("Vorbis", &["-c:a", "libvorbis"], Some(&["vorbis"])) },
        "flac" => AudioTarget { lossless: true, ..at("lossless", &["-c:a", "flac"], Some(&["flac"])) },
        "alac" => AudioTarget { lossless: true, ext: "m4a", ..at("Apple lossless", &["-c:a", "alac"], Some(&["alac"])) },
        "wav" => AudioTarget { lossless: true, ..at("uncompressed", &["-c:a", "pcm_s16le"], Some(&["pcm_s16le"])) },
        "aiff" => AudioTarget { lossless: true, ..at("uncompressed, Apple", &["-c:a", "pcm_s16be"], Some(&["pcm_s16be"])) },
        "wma" => at("Windows Media", &["-c:a", "wmav2"], Some(&["wmav2"])),
        "ac3" => AudioTarget { bitrate: "448", ..at("Dolby Digital", &["-c:a", "ac3"], Some(&["ac3"])) },
        "eac3" => AudioTarget { bitrate: "448", ..at("Dolby Digital Plus", &["-c:a", "eac3"], Some(&["eac3"])) },
        "mp2" => AudioTarget { bitrate: "256", ..at("MPEG layer 2, broadcast", &["-c:a", "mp2"], Some(&["mp2"])) },
        "wv" => AudioTarget { lossless: true, ..at("WavPack, lossless", &["-c:a", "wavpack"], Some(&["wavpack"])) },
        "tta" => AudioTarget { lossless: true, ..at("True Audio, lossless", &["-c:a", "tta"], Some(&["tta"])) },
        "caf" => AudioTarget { lossless: true, ..at("Core Audio, lossless", &["-c:a", "alac"], Some(&["alac"])) },
        "au" => AudioTarget { lossless: true, ..at("Sun / NeXT audio", &["-c:a", "pcm_s16be"], Some(&["pcm_s16be"])) },
        "mka" => AudioTarget { lossless: true, ..at("Matroska, keeps any audio", &["-c:a", "flac"], None) },
        _ => at("", &["-c:a", "libmp3lame"], Some(&["mp3"])),
    }
}

const X264: &[&str] = &["-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p"];
const FASTSTART: &[&str] = &["-movflags", "+faststart"];

pub struct VideoTarget {
    pub note: &'static str,
    pub encode: &'static [&'static str],
    pub audio: Option<&'static str>,
    pub copy_video: Option<&'static [&'static str]>,
    pub copy_audio: Option<&'static [&'static str]>,
    pub extra: &'static [&'static str],
    pub ext: &'static str,
    pub animated: bool,
}

pub const LOCAL_VIDEO_FORMATS: &[&str] =
    &["mp4", "mkv", "webm", "mov", "m4v", "avi", "ts", "flv", "wmv", "mpg", "3gp", "ogv", "gif", "apng", "webp"];

const fn vt(note: &'static str, encode: &'static [&'static str], audio: Option<&'static str>, cv: Option<&'static [&'static str]>, ca: Option<&'static [&'static str]>) -> VideoTarget {
    VideoTarget { note, encode, audio, copy_video: cv, copy_audio: ca, extra: &[], ext: "", animated: false }
}

pub fn video_target(fmt: &str) -> VideoTarget {
    match fmt {
        "mp4" => VideoTarget { extra: FASTSTART, ..vt("plays everywhere", X264, Some("m4a"), Some(&["h264", "hevc"]), Some(&["aac", "mp3", "alac"])) },
        "mkv" => vt("keeps every stream", X264, Some("m4a"), None, None),
        "webm" => vt("open web format", &["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1"], Some("opus"), Some(&["vp8", "vp9", "av1"]), Some(&["opus", "vorbis"])),
        "mov" => VideoTarget { extra: FASTSTART, ..vt("Apple / editing apps", X264, Some("m4a"), Some(&["h264", "hevc", "prores"]), Some(&["aac", "alac", "pcm_s16le", "pcm_s24le"])) },
        "m4v" => VideoTarget { extra: FASTSTART, ..vt("iTunes / Apple TV", X264, Some("m4a"), Some(&["h264", "hevc"]), Some(&["aac", "ac3", "alac"])) },
        "avi" => vt("older players", &["-c:v", "mpeg4", "-q:v", "2", "-tag:v", "XVID", "-pix_fmt", "yuv420p"], Some("mp3"), Some(&["mpeg4", "h264", "mjpeg"]), Some(&["mp3", "ac3", "pcm_s16le"])),
        "ts" => vt("broadcast / streaming", X264, Some("m4a"), Some(&["h264", "hevc", "mpeg2video"]), Some(&["aac", "mp3", "ac3", "mp2"])),
        "flv" => vt("Flash video", X264, Some("m4a"), Some(&["h264"]), Some(&["aac", "mp3"])),
        "wmv" => vt("Windows Media", &["-c:v", "wmv2", "-q:v", "2"], Some("wma"), Some(&["wmv1", "wmv2"]), Some(&["wmav1", "wmav2"])),
        "mpg" => vt("MPEG-2, DVD players", &["-c:v", "mpeg2video", "-q:v", "2", "-pix_fmt", "yuv420p"], Some("mp2"), Some(&["mpeg1video", "mpeg2video"]), Some(&["mp2"])),
        "3gp" => vt("old phones", &["-c:v", "libx264", "-crf", "23", "-profile:v", "baseline", "-pix_fmt", "yuv420p"], Some("m4a"), Some(&["h264", "h263"]), Some(&["aac"])),
        "ogv" => vt("Ogg Theora", &["-c:v", "libtheora", "-q:v", "7"], Some("ogg"), Some(&["theora"]), Some(&["vorbis", "opus"])),
        "gif" => VideoTarget { animated: true, ..vt("short clips, no sound", &["-c:v", "gif"], None, Some(&[]), Some(&[])) },
        "apng" => VideoTarget { animated: true, ext: "png", ..vt("animated PNG, no sound", &["-c:v", "apng", "-plays", "0", "-f", "apng"], None, Some(&[]), Some(&[])) },
        "webp" => VideoTarget { animated: true, ..vt("animated WebP, no sound", &["-c:v", "libwebp_anim", "-loop", "0", "-quality", "80", "-f", "webp"], None, Some(&[]), Some(&[])) },
        _ => vt("", X264, Some("m4a"), None, None),
    }
}

pub struct ImageTarget {
    pub note: &'static str,
    pub encoders: &'static [&'static [&'static str]],
    pub ext: &'static str,
}

pub const LOCAL_IMAGE_FORMATS: &[&str] =
    &["jpg", "png", "webp", "avif", "jxl", "heic", "jp2", "tiff", "bmp", "gif", "tga", "qoi", "exr", "hdr", "ppm", "pcx", "sgi", "dpx"];

pub fn image_target(fmt: &str) -> ImageTarget {
    let (note, encoders): (&str, &'static [&'static [&'static str]]) = match fmt {
        "jpg" => ("small, for photos", &[&["-c:v", "mjpeg", "-q:v", "2"]]),
        "png" => ("lossless", &[&["-c:v", "png"]]),
        "webp" => ("smaller than JPG", &[&["-c:v", "libwebp", "-quality", "90"]]),
        "avif" => ("smallest, newer apps", &[
            &["-c:v", "libaom-av1", "-still-picture", "1", "-crf", "28"],
            &["-c:v", "libsvtav1", "-crf", "30", "-pix_fmt", "yuv420p"],
            &["-c:v", "librav1e", "-qp", "70"],
        ]),
        "jxl" => ("JPEG XL, newest", &[]),
        "heic" => ("iPhone photos", &[]),
        "jp2" => ("JPEG 2000, archives", &[&["-c:v", "libopenjpeg"], &["-c:v", "jpeg2000"]]),
        "tiff" => ("print and editing", &[&["-c:v", "tiff"]]),
        "bmp" => ("uncompressed", &[&["-c:v", "bmp"]]),
        "gif" => ("256 colours", &[&["-c:v", "gif"]]),
        "tga" => ("games and 3D", &[&["-c:v", "targa"]]),
        "qoi" => ("fast lossless", &[&["-c:v", "qoi"]]),
        "exr" => ("HDR, visual effects", &[&["-c:v", "exr"]]),
        "hdr" => ("Radiance HDR", &[&["-c:v", "hdr"]]),
        "ppm" => ("plain bitmap", &[&["-c:v", "ppm"]]),
        "pcx" => ("retro PC paint", &[&["-c:v", "pcx"]]),
        "sgi" => ("Silicon Graphics", &[&["-c:v", "sgi"]]),
        "dpx" => ("film scans", &[&["-c:v", "dpx"]]),
        _ => ("", &[]),
    };
    ImageTarget { note, encoders, ext: "" }
}

/// Formats no ffmpeg build writes: the external encoder (and package) that adds each.
pub fn tool_only(fmt: &str) -> Option<(&'static str, &'static str)> {
    match fmt {
        "jxl" => Some(("cjxl", "libjxl")),
        "heic" => Some(("heif-enc", "libheif")),
        _ => None,
    }
}

/// Still-image files a dedicated decoder reads first (ffmpeg often can't).
fn tool_decoder(ext: &str) -> Option<&'static str> {
    match ext {
        "jxl" => Some("djxl"),
        "heic" | "heif" | "hif" => Some("heif-dec"),
        _ => None,
    }
}

fn encoder_name(args: &[&str]) -> String {
    for flag in ["-c:v", "-c:a"] {
        if let Some(i) = args.iter().position(|a| *a == flag) {
            return args.get(i + 1).unwrap_or(&"").to_string();
        }
    }
    String::new()
}

fn pick<'a>(options: &'a [&'a [&'a str]], has: HasEncoder) -> Option<&'a [&'a str]> {
    options.iter().copied().find(|o| has(&encoder_name(o)))
}

// ---------- finding files in pasted text ----------

/// Files named by pasted or dropped text, or [] if it isn't only files.
pub fn parse_paths(text: &str) -> Vec<PathBuf> {
    let text = text.trim();
    if text.is_empty() {
        return vec![];
    }
    if let Some(whole) = to_path(text.trim_matches(|c| c == '\'' || c == '"')) {
        if whole.is_file() {
            return vec![whole];
        }
    }
    let parts: Vec<String> = if cfg!(windows) {
        text.split('"').enumerate().flat_map(|(i, s)| if i % 2 == 1 { vec![s.to_string()] } else { s.split_whitespace().map(String::from).collect() }).collect()
    } else {
        match shlex::split(text) {
            Some(p) => p,
            None => return vec![],
        }
    };
    let mut paths: Vec<PathBuf> = vec![];
    for part in parts {
        match to_path(&part) {
            Some(p) if p.is_file() => {
                if !paths.contains(&p) {
                    paths.push(p);
                }
            }
            _ => return vec![], // all or nothing, so stray words never half-load
        }
    }
    paths
}

fn to_path(text: &str) -> Option<PathBuf> {
    if text.is_empty() {
        return None;
    }
    let mut t = text.to_string();
    if t.to_lowercase().starts_with("file://") {
        let rest = &t[7..];
        let path = rest.find('/').map(|i| &rest[i..]).unwrap_or("");
        let mut raw = percent_encoding::percent_decode_str(path).decode_utf8_lossy().into_owned();
        if cfg!(windows) && raw.len() > 2 && raw.starts_with('/') && &raw[2..3] == ":" {
            raw = raw[1..].to_string();
        }
        t = raw;
    }
    Some(crate::config::expand_user(&t))
}

// ---------- reading a file ----------

#[derive(Clone, Debug, PartialEq)]
pub struct Stream {
    pub codec: String,
    pub width: Option<u32>,
    pub height: Option<u32>,
    pub fps: Option<f64>,
    pub channels: Option<u32>,
}

#[derive(Clone, Debug)]
pub struct FileInfo {
    pub path: PathBuf,
    pub media: Media,
    pub container: String,
    pub size: u64,
    pub duration: Option<f64>,
    pub video: Option<Stream>,
    pub audio: Option<Stream>,
    pub sample_rate: Option<u32>,
    /// "ffmpeg", or the decoder tool for images ffmpeg can't read.
    pub reader: String,
}

impl FileInfo {
    pub fn name(&self) -> String {
        self.path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
    }
    pub fn stem(&self) -> String {
        self.path.file_stem().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
    }
    /// Short side, so vertical videos get the right quality list.
    pub fn max_height(&self) -> Option<u32> {
        let v = self.video.as_ref()?;
        let h = v.height.filter(|h| *h > 0)?;
        Some(match v.width { Some(w) if w > 0 => w.min(h), _ => h })
    }
    pub fn long_side(&self) -> Option<u32> {
        let v = self.video.as_ref()?;
        let m = v.width.unwrap_or(0).max(v.height.unwrap_or(0));
        (m > 0).then_some(m)
    }
}

fn file_size(p: &Path) -> u64 {
    p.metadata().map(|m| m.len()).unwrap_or(0)
}

/// Read `path` with ffprobe, or a dedicated decoder for images ffprobe can't handle.
pub fn probe_file(path: &Path, ffmpeg_dir: Option<&str>) -> Result<FileInfo, String> {
    let ext = path.extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default();
    match ffprobe(path, ffmpeg_dir) {
        Ok(i) => Ok(i),
        Err(e) => {
            if let Some(tool) = tool_decoder(&ext).filter(|t| deps::which(t).is_some()) {
                if let Some(info) = probe_with_tool(path, tool) {
                    return Ok(info);
                }
            }
            if let Some(info) = probe_with_image_crate(path) {
                return Ok(info);
            }
            Err(e)
        }
    }
}

fn probe_with_image_crate(path: &Path) -> Option<FileInfo> {
    let reader = image::ImageReader::open(path).ok()?.with_guessed_format().ok()?;
    let fmt = reader.format()?;
    let (w, h) = reader.into_dimensions().ok()?;
    let name = format!("{fmt:?}").to_lowercase();
    Some(FileInfo {
        path: path.to_path_buf(),
        media: Media::Image,
        container: name.clone(),
        size: file_size(path),
        duration: None,
        video: Some(Stream { codec: name, width: Some(w), height: Some(h), fps: None, channels: None }),
        audio: None,
        sample_rate: None,
        reader: "image".into(),
    })
}

fn probe_with_tool(path: &Path, tool: &str) -> Option<FileInfo> {
    let dir = tempfile::tempdir().ok()?;
    let png = dir.path().join("probe.png");
    decode_with_tool(tool, path, &png).ok()?;
    let mut info = probe_with_image_crate(&png)?;
    let ext = path.extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default();
    info.path = path.to_path_buf();
    info.container = ext.clone();
    info.size = file_size(path);
    if let Some(v) = info.video.as_mut() {
        v.codec = ext;
    }
    info.reader = tool.into();
    Some(info)
}

fn decode_with_tool(tool: &str, src: &Path, png: &Path) -> Result<(), String> {
    let mut cmd = Command::new(deps::exe_in(tool, None));
    if tool == "heif-dec" {
        cmd.arg(src).arg(png);
    } else {
        cmd.arg(src).arg(png);
    }
    let out = cmd.stdin(Stdio::null()).output().map_err(|e| e.to_string())?;
    if !out.status.success() || !png.exists() {
        return Err(last_line(&String::from_utf8_lossy(&out.stderr)));
    }
    Ok(())
}

fn ffprobe(path: &Path, ffmpeg_dir: Option<&str>) -> Result<FileInfo, String> {
    let exe = deps::exe_in("ffprobe", ffmpeg_dir);
    let out = Command::new(exe)
        .args(["-v", "error", "-show_format", "-show_streams", "-of", "json"])
        .arg(path)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("Couldn't run ffprobe: {e}"))?;
    if !out.status.success() {
        let l = last_line(&String::from_utf8_lossy(&out.stderr));
        return Err(if l.is_empty() { "Can't read this file.".into() } else { l });
    }
    let data: Value = serde_json::from_slice(&out.stdout).map_err(|_| "Can't read this file.".to_string())?;
    parse_probe(&data, path)
}

fn fnum(v: Option<&Value>) -> Option<f64> {
    match v? {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.parse().ok(),
        _ => None,
    }
}

fn rate(v: Option<&Value>) -> Option<f64> {
    let s = v?.as_str()?;
    if s == "0/0" {
        return None;
    }
    let (n, d) = s.split_once('/').unwrap_or((s, "1"));
    let (n, d) = (n.parse::<f64>().ok()?, d.parse::<f64>().ok()?);
    (d != 0.0).then(|| n / d)
}

/// Turn ffprobe's JSON into a FileInfo. Pure, for tests.
pub fn parse_probe(data: &Value, path: &Path) -> Result<FileInfo, String> {
    let empty = serde_json::Map::new();
    let fmt = data.get("format").and_then(Value::as_object).unwrap_or(&empty);
    let streams: Vec<&Value> = data.get("streams").and_then(Value::as_array).map(|a| a.iter().collect()).unwrap_or_default();
    let ctype = |s: &Value| s.get("codec_type").and_then(Value::as_str).unwrap_or("").to_string();
    let video_raw = streams.iter().find(|s| {
        ctype(s) == "video" && s.get("disposition").and_then(|d| d.get("attached_pic")).and_then(Value::as_i64).unwrap_or(0) == 0
    });
    let audio_raw = streams.iter().find(|s| ctype(s) == "audio");
    let names: Vec<String> = fmt.get("format_name").and_then(Value::as_str).unwrap_or("").split(',').map(String::from).collect();
    let duration = fnum(fmt.get("duration"));
    let media = if video_raw.is_some() && names.iter().any(|n| n.ends_with("_pipe") || n == "image2") {
        Media::Image
    } else if video_raw.is_some() && names.iter().any(|n| n == "gif") {
        if duration.is_some_and(|d| d > 0.0) { Media::Animated } else { Media::Image }
    } else if video_raw.is_some() {
        Media::Video
    } else if audio_raw.is_some() {
        Media::Audio
    } else {
        return Err("Can't read this file. It isn't audio, video or an image.".into());
    };
    let s = |v: &Value, k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    let u = |v: &Value, k: &str| v.get(k).and_then(Value::as_u64).map(|x| x as u32);
    let video = video_raw.map(|v| Stream { codec: s(v, "codec_name"), width: u(v, "width"), height: u(v, "height"), fps: rate(v.get("avg_frame_rate")), channels: None });
    let audio = audio_raw.map(|a| Stream { codec: s(a, "codec_name"), width: None, height: None, fps: None, channels: u(a, "channels") });
    let sample_rate = audio_raw.and_then(|a| fnum(a.get("sample_rate"))).map(|r| r as u32).filter(|r| *r > 0);
    let size = fnum(fmt.get("size")).map(|s| s as u64).filter(|s| *s > 0).unwrap_or_else(|| file_size(path));
    Ok(FileInfo {
        path: path.to_path_buf(),
        media,
        container: names.first().cloned().unwrap_or_default(),
        size,
        duration: if media == Media::Image { None } else { duration },
        video,
        audio: if media == Media::Image { None } else { audio },
        sample_rate,
        reader: "ffmpeg".into(),
    })
}

// ---------- what the Convert dialog can offer ----------

pub fn kinds_for(info: &FileInfo) -> Vec<&'static str> {
    match info.media {
        Media::Video => if info.audio.is_some() { vec!["video", "audio", "frames"] } else { vec!["video", "frames"] },
        Media::Animated => vec!["video", "image"],
        Media::Image => vec!["image"],
        Media::Audio => vec!["audio"],
    }
}

pub fn formats_for(kind: &str) -> Vec<&'static str> {
    match kind {
        "video" => LOCAL_VIDEO_FORMATS.to_vec(),
        "audio" => LOCAL_AUDIO_FORMATS.to_vec(),
        // ffmpeg writes frames straight out, so no tool-only formats.
        "frames" => LOCAL_IMAGE_FORMATS.iter().copied().filter(|f| tool_only(f).is_none()).collect(),
        _ => LOCAL_IMAGE_FORMATS.to_vec(),
    }
}

pub fn format_note(kind: &str, fmt: &str) -> String {
    match kind {
        "video" => video_target(fmt).note,
        "audio" => audio_target(fmt).note,
        _ => image_target(fmt).note,
    }
    .to_string()
}

/// The encoder this ffmpeg lacks for `fmt`, or "" when it can write it.
pub fn missing_encoder(kind: &str, fmt: &str, has: HasEncoder) -> String {
    match kind {
        "video" => {
            let t = video_target(fmt);
            let name = encoder_name(t.encode);
            if !has(&name) {
                return name;
            }
            t.audio.map(|a| missing_encoder("audio", a, has)).unwrap_or_default()
        }
        "audio" => {
            let t = audio_target(fmt);
            let name = encoder_name(t.encode);
            if has(&name) || (!t.fallback.is_empty() && has(&encoder_name(t.fallback))) { String::new() } else { name }
        }
        _ => {
            if let Some((tool, package)) = tool_only(fmt) {
                return if deps::which(tool).is_some() { String::new() } else { package.to_string() };
            }
            let t = image_target(fmt);
            if pick(t.encoders, has).is_some() { String::new() } else { encoder_name(t.encoders[0]) }
        }
    }
}

pub fn qualities_for(info: &FileInfo, kind: &str, fmt: &str) -> Vec<String> {
    match kind {
        "video" => video_qualities(if video_target(fmt).animated { "gif" } else { fmt }, info.max_height()),
        "audio" => {
            if audio_target(fmt).lossless { vec!["best".into()] } else { std::iter::once("best").chain(AUDIO_BITRATES.iter().copied()).map(String::from).collect() }
        }
        "frames" => FRAME_RATES.iter().map(|s| s.to_string()).collect(),
        _ => image_sizes(info.long_side()),
    }
}

/// True when the conversion only repackages streams: seconds, and no quality lost.
pub fn is_fast_copy(info: &FileInfo, choice: &Choice) -> bool {
    match choice.kind.as_str() {
        "video" => {
            let (v, a) = video_copy(info, choice);
            v && (a || info.audio.is_none())
        }
        "audio" => audio_copy(info, choice),
        _ => false,
    }
}

fn scale_height(info: &FileInfo, choice: &Choice) -> Option<u32> {
    let h: u32 = choice.quality.parse().ok()?;
    match info.max_height() {
        Some(m) if h >= m => None,
        _ => Some(h),
    }
}

fn allows(codecs: Option<&[&str]>, codec: &str) -> bool {
    codecs.is_none_or(|c| c.contains(&codec))
}

fn video_copy(info: &FileInfo, choice: &Choice) -> (bool, bool) {
    let t = video_target(&choice.fmt);
    if t.animated {
        return (false, false);
    }
    let copy_v = info.video.as_ref().is_some_and(|v| info.media == Media::Video && scale_height(info, choice).is_none() && allows(t.copy_video, &v.codec));
    let copy_a = info.audio.as_ref().is_some_and(|a| allows(t.copy_audio, &a.codec));
    (copy_v, copy_a)
}

fn audio_copy(info: &FileInfo, choice: &Choice) -> bool {
    if choice.extras.normalize {
        return false;
    }
    info.audio.as_ref().is_some_and(|a| choice.quality == "best" && allows(audio_target(&choice.fmt).copy, &a.codec))
}

// ---------- planning the ffmpeg command ----------

pub fn extension(kind: &str, fmt: &str) -> String {
    let ext = match kind {
        "video" => video_target(fmt).ext,
        "audio" => audio_target(fmt).ext,
        _ => image_target(fmt).ext,
    };
    if ext.is_empty() { fmt.to_string() } else { ext.to_string() }
}

/// File (or, for frames, folder) name for the result, next to the original.
pub fn output_name(info: &FileInfo, choice: &Choice) -> String {
    let mut stem = info.stem();
    let e = &choice.extras;
    if e.clipped() && choice.kind != "image" {
        let end = e.end.map(clip_stamp).unwrap_or_else(|| "end".into());
        stem = format!("{stem} {}-{end}", clip_stamp(e.start.unwrap_or(0.0)));
    }
    if choice.kind == "frames" {
        return format!("{stem} frames");
    }
    format!("{stem}.{}", extension(&choice.kind, &choice.fmt))
}

/// 1m30s, 1h02m03s: a time that's safe in a filename on every system.
pub fn clip_stamp(seconds: f64) -> String {
    let whole = seconds as i64;
    let (h, rem) = (whole / 3600, whole % 3600);
    let (m, s) = (rem / 60, rem % 60);
    if h > 0 { format!("{h}h{m:02}m{s:02}s") } else { format!("{m}m{s:02}s") }
}

fn strs(a: &[&str]) -> Vec<String> {
    a.iter().map(|s| s.to_string()).collect()
}

/// ffmpeg arguments (without the executable) that turn `info` into `out`.
pub fn build_ffmpeg_args(info: &FileInfo, choice: &Choice, out: &Path, has: HasEncoder) -> Result<Vec<String>, String> {
    let mut args = strs(&["-hide_banner", "-nostdin", "-y"]);
    args.extend(clip_args(info, choice));
    args.push("-i".into());
    args.push(info.path.to_string_lossy().into());
    match choice.kind.as_str() {
        "video" => args.extend(video_args(info, choice, has)),
        "audio" => args.extend(audio_args(info, choice, has)),
        "image" => args.extend(image_args(choice, has)?),
        "frames" => {
            args.extend(frames_args(choice, has)?);
            let name = format!("{} %04d.{}", info.stem(), extension("image", &choice.fmt));
            args.push(out.join(name).to_string_lossy().into());
            return Ok(args);
        }
        k => return Err(format!("Unknown kind: {k}")),
    }
    args.push(out.to_string_lossy().into());
    Ok(args)
}

/// Input options that cut out the picked part.
pub fn clip_args(info: &FileInfo, choice: &Choice) -> Vec<String> {
    let e = &choice.extras;
    if choice.kind == "image" || !e.clipped() || info.media == Media::Image {
        return vec![];
    }
    let mut a = vec![];
    if let Some(s) = e.start.filter(|s| *s > 0.0) {
        a.extend(["-ss".to_string(), seconds(s)]);
    }
    if let Some(end) = e.end {
        a.extend(["-to".to_string(), seconds(end)]);
    }
    a
}

/// How long the result runs, for the progress bar.
pub fn clip_duration(info: &FileInfo, choice: &Choice) -> Option<f64> {
    let e = &choice.extras;
    if !e.clipped() || choice.kind == "image" {
        return info.duration;
    }
    let mut end = e.end.or(info.duration)?;
    if let Some(d) = info.duration.filter(|d| *d > 0.0) {
        end = end.min(d);
    }
    let len = (end - e.start.unwrap_or(0.0)).max(0.0);
    (len > 0.0).then_some(len)
}

fn seconds(v: f64) -> String {
    let s = format!("{v:.3}");
    s.trim_end_matches('0').trim_end_matches('.').to_string()
}

fn video_args(info: &FileInfo, choice: &Choice, has: HasEncoder) -> Vec<String> {
    let t = video_target(&choice.fmt);
    if t.animated {
        let height = if choice.quality.chars().all(|c| c.is_ascii_digit()) && !choice.quality.is_empty() { choice.quality.clone() } else { "480".into() };
        if choice.fmt == "gif" {
            return vec!["-vf".into(), gif_filter(&height), "-loop".into(), "0".into(), "-an".into()];
        }
        let mut a = vec!["-vf".into(), format!("fps=15,scale=-2:{height}:flags=lanczos"), "-an".into()];
        a.extend(strs(t.encode));
        return a;
    }
    let (copy_v, copy_a) = video_copy(info, choice);
    let mut a = strs(&["-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "0"]);
    if copy_v {
        a.extend(strs(&["-c:v", "copy"]));
        if ["mp4", "mov", "m4v"].contains(&choice.fmt.as_str()) && info.video.as_ref().is_some_and(|v| v.codec == "hevc") {
            a.extend(strs(&["-tag:v", "hvc1"]));
        }
    } else {
        if let Some(h) = scale_height(info, choice) {
            a.push("-vf".into());
            a.push(format!("scale='if(gt(iw,ih),-2,{h})':'if(gt(iw,ih),{h},-2)'"));
        }
        a.extend(strs(t.encode));
    }
    if info.audio.is_some() {
        if let Some(audio) = t.audio {
            if copy_a {
                a.extend(strs(&["-c:a", "copy"]));
            } else {
                a.extend(audio_encode(&audio_target(audio), "best", has));
            }
        } else {
            a.push("-an".into());
        }
    }
    a.extend(strs(t.extra));
    a
}

fn audio_encode(t: &AudioTarget, quality: &str, has: HasEncoder) -> Vec<String> {
    let mut a = if !t.fallback.is_empty() && !has(&encoder_name(t.encode)) { strs(t.fallback) } else { strs(t.encode) };
    if t.lossless {
        return a;
    }
    if quality != "best" {
        a.extend(["-b:a".to_string(), format!("{quality}k")]);
    } else if !t.best.is_empty() {
        a.extend(strs(t.best));
    } else {
        a.extend(["-b:a".to_string(), format!("{}k", t.bitrate)]);
    }
    a
}

fn audio_args(info: &FileInfo, choice: &Choice, has: HasEncoder) -> Vec<String> {
    let mut a = strs(&["-vn", "-map", "0:a:0", "-map_metadata", "0"]);
    if audio_copy(info, choice) {
        a.extend(strs(&["-c:a", "copy"]));
        return a;
    }
    if choice.extras.normalize {
        a.extend(["-af".to_string(), LOUDNORM.to_string(), "-ar".to_string(), info.sample_rate.unwrap_or(48000).to_string()]);
    }
    a.extend(audio_encode(&audio_target(&choice.fmt), &choice.quality, has));
    a
}

fn image_encode(fmt: &str, has: HasEncoder) -> Result<Vec<String>, String> {
    if tool_only(fmt).is_some() {
        return Err(format!("ffmpeg can't write {}.", fmt.to_uppercase()));
    }
    let t = image_target(fmt);
    Ok(strs(pick(t.encoders, has).unwrap_or(t.encoders[0])))
}

fn image_filters(choice: &Choice) -> Vec<String> {
    let mut filters = vec![];
    if choice.quality == "50" {
        filters.push("scale=iw/2:-1".to_string());
    } else if choice.quality.parse::<u32>().is_ok() {
        let cap = &choice.quality;
        filters.push(format!("scale='if(gte(iw,ih),min(iw,{cap}),-1)':'if(gte(iw,ih),-1,min(ih,{cap}))'"));
    }
    filters
}

fn image_args(choice: &Choice, has: HasEncoder) -> Result<Vec<String>, String> {
    let mut a = strs(&["-map", "0:v:0", "-frames:v", "1", "-update", "1"]);
    if choice.extras.strip_metadata {
        a.extend(strs(&["-map_metadata", "-1"]));
    }
    let mut filters = image_filters(choice);
    if choice.fmt == "gif" {
        filters.push("split[a][b];[a]palettegen[p];[b][p]paletteuse".into());
    }
    if !filters.is_empty() {
        a.extend(["-vf".to_string(), filters.join(",")]);
    }
    a.extend(image_encode(&choice.fmt, has)?);
    Ok(a)
}

fn frames_args(choice: &Choice, has: HasEncoder) -> Result<Vec<String>, String> {
    let mut a = strs(&["-map", "0:v:0", "-an"]);
    if choice.quality != "best" {
        a.extend(["-vf".to_string(), format!("fps={}", choice.quality)]);
    } else {
        a.extend(strs(&["-fps_mode", "passthrough"]));
    }
    a.extend(image_encode(&choice.fmt, has)?);
    Ok(a)
}

pub fn audio_format_for(path: &Path) -> Option<String> {
    let ext = path.extension()?.to_string_lossy().to_lowercase();
    LOCAL_AUDIO_FORMATS.contains(&ext.as_str()).then_some(ext)
}

/// Cut and/or even out the volume of an audio file in place, keeping its tags and cover art.
#[allow(clippy::too_many_arguments)]
pub fn refine_audio(
    path: &Path,
    fmt: &str,
    ffmpeg_dir: Option<&str>,
    on_progress: &mut dyn FnMut(Progress),
    cancel: &Cancel,
    start: Option<f64>,
    end: Option<f64>,
    normalize: bool,
    label: &str,
) -> Result<(), String> {
    let has = |name: &str| ffmpeg_dir.is_none_or(|d| deps::ffmpeg_has_encoder(d, name));
    let probed = ffprobe(path, ffmpeg_dir).unwrap_or(FileInfo {
        path: path.to_path_buf(),
        media: Media::Audio,
        container: String::new(),
        size: 0,
        duration: None,
        video: None,
        audio: None,
        sample_rate: None,
        reader: "ffmpeg".into(),
    });
    let clip = Choice::new("audio", fmt, "best").with_extras(Extras { start, end, ..Extras::default() });
    let target = audio_target(&audio_format_for(path).unwrap_or_else(|| fmt.to_string()));
    let out = path.with_file_name(format!(".finishing {}", path.file_name().unwrap().to_string_lossy()));
    let mut timed = probed.clone();
    timed.duration = clip_duration(&probed, &clip);
    on_progress(Progress::new(State::Converting, label, None));
    let mut run = |streams: &[&str]| -> Result<(), String> {
        let mut args = strs(&["-hide_banner", "-nostdin", "-y"]);
        args.extend(clip_args(&probed, &clip));
        args.extend(["-i".to_string(), path.to_string_lossy().into()]);
        args.extend(strs(streams));
        args.extend(strs(&["-map_metadata", "0", "-c", "copy"]));
        if normalize {
            args.extend(["-af".to_string(), LOUDNORM.into(), "-ar".into(), probed.sample_rate.unwrap_or(48000).to_string()]);
        }
        args.extend(audio_encode(&target, "best", &has));
        args.push(out.to_string_lossy().into());
        run_ffmpeg(timed.duration, &args, ffmpeg_dir, on_progress, cancel, label)
    };
    let result = run(&["-map", "0"]).or_else(|e| if e == CANCELLED { Err(e) } else { run(&["-map", "0:a"]) });
    let result = result.and_then(|_| std::fs::rename(&out, path).map_err(|e| e.to_string()));
    let _ = std::fs::remove_file(&out);
    result
}

// ---------- running it ----------

/// Convert `info` per `choice`. Blocks; returns the path of the new file or folder.
pub fn run_convert(info: &FileInfo, choice: &Choice, on_progress: &mut dyn FnMut(Progress), cancel: &Cancel, ffmpeg_dir: Option<&str>) -> Result<String, String> {
    let has = |name: &str| ffmpeg_dir.is_none_or(|d| deps::ffmpeg_has_encoder(d, name));
    let folder = info.path.parent().unwrap_or(Path::new(".")).to_path_buf();
    let work = tempfile::Builder::new().prefix(".carrot-leaf-").tempdir_in(&folder).map_err(|e| e.to_string())?;
    let out = work.path().join(output_name(info, choice));
    if choice.kind == "frames" {
        std::fs::create_dir(&out).map_err(|e| e.to_string())?;
    }
    if choice.kind == "image" {
        convert_image(info, choice, &out, work.path(), &has, ffmpeg_dir, on_progress, cancel)?;
    } else {
        let args = build_ffmpeg_args(info, choice, &out, &has)?;
        let fast = is_fast_copy(info, choice);
        let label = if fast { "Copying" } else { "Converting" };
        on_progress(Progress::new(State::Converting, label, None));
        run_ffmpeg(clip_duration(info, choice), &args, ffmpeg_dir, on_progress, cancel, label)?;
        if choice.kind == "frames" && std::fs::read_dir(&out).map(|mut d| d.next().is_none()).unwrap_or(true) {
            return Err("ffmpeg finished but wrote no frames.".into());
        }
        if !out.exists() {
            return Err("ffmpeg finished but no file was produced.".into());
        }
    }
    let target = unique_path(&folder.join(out.file_name().unwrap()));
    move_into(&out, &target)?;
    Ok(target.to_string_lossy().into())
}

/// Formats with a quality setting, so "keep it under N KB" can be met by lowering it.
pub const SIZE_TARGET_FORMATS: &[&str] = &["jpg", "webp", "avif", "heic", "jxl"];

pub fn fits_size(choice: &Choice) -> bool {
    choice.extras.max_kb.is_some_and(|k| k > 0) && SIZE_TARGET_FORMATS.contains(&choice.fmt.as_str())
}

#[allow(clippy::too_many_arguments)]
fn convert_image(info: &FileInfo, choice: &Choice, out: &Path, work: &Path, has: HasEncoder, ffmpeg_dir: Option<&str>, on_progress: &mut dyn FnMut(Progress), cancel: &Cancel) -> Result<(), String> {
    on_progress(Progress::new(State::Converting, "Converting", None));
    let mut source = info.clone();
    if info.reader != "ffmpeg" && info.reader != "image" {
        // e.g. HEIC from a phone: its own decoder makes a lossless PNG ffmpeg can read.
        let png = work.join("decoded.png");
        decode_with_tool(&info.reader, &info.path, &png)?;
        source.path = png;
        source.reader = "ffmpeg".into();
    }
    if let Some((tool, _)) = tool_only(&choice.fmt) {
        // ffmpeg scales and decodes into a PNG; the format's own encoder writes it.
        let png = work.join("scaled.png");
        let mut args = strs(&["-hide_banner", "-nostdin", "-y", "-i"]);
        args.push(source.path.to_string_lossy().into());
        args.extend(strs(&["-map", "0:v:0", "-frames:v", "1", "-update", "1"]));
        let filters = image_filters(choice);
        if !filters.is_empty() {
            args.extend(["-vf".to_string(), filters.join(",")]);
        }
        args.extend(strs(&["-c:v", "png"]));
        args.push(png.to_string_lossy().into());
        run_ffmpeg(None, &args, ffmpeg_dir, &mut |_| {}, cancel, "Converting")?;
        let encode = |q: u32, dest: &Path| -> Result<(), String> {
            let mut cmd = Command::new(deps::exe_in(tool, None));
            if tool == "cjxl" {
                cmd.arg(&png).arg(dest).args(["-q", &q.to_string()]);
            } else {
                cmd.args(["-q", &q.to_string()]).arg(&png).arg("-o").arg(dest);
            }
            let o = cmd.stdin(Stdio::null()).output().map_err(|e| format!("Couldn't run {tool}: {e}"))?;
            if !o.status.success() || !dest.exists() {
                return Err(format!("Couldn't write {}: {}", choice.fmt.to_uppercase(), last_line(&String::from_utf8_lossy(&o.stderr))));
            }
            Ok(())
        };
        if fits_size(choice) {
            save_under(choice, out, work, cancel, &mut |q, dest| encode(q, dest), 20, 95)?;
        } else {
            encode(if tool == "cjxl" { 90 } else { 85 }, out)?;
        }
    } else {
        let args = build_ffmpeg_args(&source, choice, out, has)?;
        if fits_size(choice) {
            // Highest quality that fits in max_kb, by halving; only quality changes.
            let mut encode = |q: u32, dest: &Path| -> Result<(), String> {
                let mut a = args.clone();
                a.pop();
                set_quality(&mut a, &choice.fmt, q);
                a.push(dest.to_string_lossy().into());
                run_ffmpeg(None, &a, ffmpeg_dir, &mut |_| {}, cancel, "Converting")
            };
            save_under(choice, out, work, cancel, &mut encode, 20, 95)?;
        } else {
            run_ffmpeg(None, &args, ffmpeg_dir, &mut |_| {}, cancel, "Converting")?;
        }
    }
    if !out.exists() {
        return Err("The conversion finished but no file was produced.".into());
    }
    on_progress(Progress::new(State::Converting, "Converting", Some(1.0)));
    Ok(())
}

/// Map a 20–95 "quality" onto each ffmpeg encoder's own scale.
fn set_quality(args: &mut Vec<String>, fmt: &str, q: u32) {
    let (flag, value) = match fmt {
        "jpg" => ("-q:v", (2 + (95 - q.min(95)) * 29 / 75).to_string()),
        "webp" => ("-quality", q.to_string()),
        "avif" => ("-crf", (63 - q.min(95) * 60 / 95).to_string()),
        _ => return,
    };
    if let Some(i) = args.iter().position(|a| a == flag) {
        args[i + 1] = value;
    } else {
        args.push(flag.into());
        args.push(value);
    }
}

fn save_under(choice: &Choice, out: &Path, work: &Path, cancel: &Cancel, encode: &mut dyn FnMut(u32, &Path) -> Result<(), String>, lo: u32, hi: u32) -> Result<(), String> {
    let limit = choice.extras.max_kb.unwrap_or(0) as u64 * 1024;
    let trial = work.join(format!("trial.{}", extension("image", &choice.fmt)));
    let (mut low, mut high) = (lo, hi);
    let mut best = false;
    while low <= high {
        if cancel.load(std::sync::atomic::Ordering::Relaxed) {
            return Err(CANCELLED.into());
        }
        let mid = (low + high) / 2;
        encode(mid, &trial)?;
        if file_size(&trial) <= limit {
            std::fs::rename(&trial, out).map_err(|e| e.to_string())?;
            best = true;
            low = mid + 1;
        } else {
            if mid == 0 {
                break;
            }
            high = mid - 1;
        }
    }
    if !best {
        return Err(format!("Couldn't get this picture under {} KB.", limit / 1024));
    }
    Ok(())
}

pub fn run_ffmpeg(duration: Option<f64>, args: &[String], ffmpeg_dir: Option<&str>, on_progress: &mut dyn FnMut(Progress), cancel: &Cancel, label: &str) -> Result<(), String> {
    let mut cmd = Command::new(deps::exe_in("ffmpeg", ffmpeg_dir));
    cmd.args(["-progress", "pipe:1", "-nostats", "-loglevel", "error"]).args(args);
    let mut block: HashMap<String, String> = HashMap::new();
    let result = run_lines(cmd, cancel, |line| {
        let (k, v) = line.trim().split_once('=').unwrap_or((line.trim(), ""));
        block.insert(k.to_string(), v.to_string());
        if k == "progress" {
            if let Some(p) = progress_from(&block, duration, label) {
                on_progress(p);
            }
            block.clear();
        }
    });
    match result {
        Err(RunError::Cancelled) => Err(CANCELLED.into()),
        Err(RunError::Spawn(e)) => Err(format!("Couldn't run ffmpeg: {e}")),
        Ok(f) if !f.status.success() => {
            let l = last_line(&f.stderr);
            Err(if l.is_empty() { format!("ffmpeg failed ({}).", f.status) } else { l })
        }
        Ok(_) => Ok(()),
    }
}

/// A Progress from one `-progress` block of key=value lines. Pure, for tests.
pub fn progress_from(block: &HashMap<String, String>, duration: Option<f64>, label: &str) -> Option<Progress> {
    if block.get("progress").map(String::as_str) == Some("end") {
        return Some(Progress::new(State::Converting, label, Some(1.0)));
    }
    let us = block.get("out_time_us").or_else(|| block.get("out_time_ms")).filter(|s| !s.is_empty());
    let done = us.and_then(|u| u.parse::<i64>().ok()).map(|u| u as f64 / 1_000_000.0);
    let (Some(d), Some(done)) = (duration.filter(|d| *d > 0.0), done.filter(|d| *d >= 0.0)) else {
        return Some(Progress::new(State::Converting, label, None));
    };
    let fraction = (done / d).min(1.0);
    let rate: f64 = block.get("speed").map(|s| s.trim_end_matches('x').trim()).and_then(|s| s.parse().ok()).unwrap_or(0.0);
    let eta = (rate > 0.0).then(|| (d - done).max(0.0) / rate);
    Some(Progress { state: State::Converting, status: label.into(), fraction: Some(fraction), speed: None, eta })
}

pub fn last_line(text: &str) -> String {
    text.lines().map(str::trim).filter(|l| !l.is_empty()).last().unwrap_or("").to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn video(codec: &str, audio: &str) -> FileInfo {
        FileInfo {
            path: PathBuf::from("/tmp/clip.mov"),
            media: Media::Video,
            container: "mov".into(),
            size: 1,
            duration: Some(60.0),
            video: Some(Stream { codec: codec.into(), width: Some(1280), height: Some(720), fps: Some(30.0), channels: None }),
            audio: Some(Stream { codec: audio.into(), width: None, height: None, fps: None, channels: Some(2) }),
            sample_rate: Some(44100),
            reader: "ffmpeg".into(),
        }
    }

    #[test]
    fn copies_when_it_can() {
        let i = video("h264", "aac");
        assert!(is_fast_copy(&i, &Choice::new("video", "mp4", "best")));
        assert!(!is_fast_copy(&i, &Choice::new("video", "webm", "best")));
        assert!(!is_fast_copy(&i, &Choice::new("video", "mp4", "360")));
        let a = build_ffmpeg_args(&i, &Choice::new("video", "webm", "360"), Path::new("/tmp/o.webm"), &any_encoder).unwrap();
        assert!(a.contains(&"libvpx-vp9".to_string()));
    }

    #[test]
    fn names_and_counts() {
        let i = video("h264", "aac");
        let mut c = Choice::new("video", "mp4", "best");
        c.extras.start = Some(90.0);
        assert_eq!(output_name(&i, &c), "clip 1m30s-end.mp4");
        assert_eq!(LOCAL_VIDEO_FORMATS.len() + LOCAL_AUDIO_FORMATS.len() + LOCAL_IMAGE_FORMATS.len(), 51);
        assert_eq!(qualities_for(&i, "video", "webm"), vec!["best", "720", "480", "360"]);
    }
}
