//! Pick a format and quality, then download (a link) or convert (files on disk). Also the
//! two dialogs it opens: saving a preset and picking a clip on the timeline.

use super::draw::{bold, fg, on, width, Align, Painter};
use super::fmt::short_path;
use super::widgets::{action_buttons, chip, cycle, toggle, Edit, ListState, Picture, Severity, TextInput};
use super::{Cx, Dialog, Frames, Msg, Out, Res};
use crate::config::Settings;
use crate::engine::local::{self, FileInfo, Media};
use crate::engine::options::{
    audio_qualities, check_clip, frame_rate_label, parse_time, time_text, video_qualities, Choice, Extras, AUDIO_FORMATS, H264_FIRST,
    SUBTITLE_EMBED_EXTS, VIDEO_FORMATS,
};
use crate::engine::probe::VideoInfo;
use crate::engine::{deps, frames};
use crate::library::{self, Preset};
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use std::any::Any;
use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;

pub const CLIP: &str = "clip";
pub const SUBTITLES: &str = "subtitles";
pub const SPONSORBLOCK: &str = "sponsorblock";
pub const CHAPTERS: &str = "chapters";
pub const NORMALIZE: &str = "normalize";
pub const IMAGE_TOOLS: &str = "image";

fn extra_kinds(name: &str) -> &'static [&'static str] {
    match name {
        CLIP => &["video", "audio", "frames"],
        SUBTITLES | SPONSORBLOCK | CHAPTERS => &["video", "audio"],
        NORMALIZE => &["audio"],
        _ => &["image"],
    }
}

fn tab_name(kind: &str) -> &'static str {
    match kind {
        "video" => "Video",
        "audio" => "Audio",
        "image" => "Image",
        _ => "Frames",
    }
}

fn link_note(fmt: &str) -> &'static str {
    match fmt {
        "mp4" => "plays everywhere",
        "mkv" => "keeps every stream",
        "webm" => "open web format",
        "mov" => "Apple / editing apps",
        "gif" => "short clips, no sound",
        "mp3" => "plays everywhere",
        "m4a" => "AAC, Apple devices",
        "opus" => "small and sharp",
        "flac" => "lossless",
        "wav" => "uncompressed",
        "ogg" => "Vorbis",
        _ => "",
    }
}

fn cap(s: &str) -> String {
    let mut c = s.chars();
    c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default()
}

/// The quality list's words, plus an optional dim note after them.
pub fn quality_label(kind: &str, q: &str, fmt: &str, local: bool) -> (String, String) {
    if kind == "frames" {
        return (cap(&frame_rate_label(q)), String::new());
    }
    if kind == "image" {
        return (
            match q {
                "best" => "Original size".into(),
                "50" => "Half size".into(),
                _ => format!("Up to {q}px"),
            },
            String::new(),
        );
    }
    if q == "best" && local {
        return ((if kind == "video" { "Same as original" } else { "Best" }).into(), String::new());
    }
    if q == "best" {
        return ((if H264_FIRST.contains(&fmt) { "Best that plays everywhere" } else { "Best available" }).into(), String::new());
    }
    let label = if kind == "video" { format!("{q}p") } else { format!("{q} kbps") };
    let note = if fmt == "mp4" && kind == "video" && q.parse::<u32>().unwrap_or(0) > 1080 && !local { "VP9/AV1, newer players".to_string() } else { String::new() };
    (label, note)
}

pub struct ConvertOptions {
    pub title: String,
    pub kinds: Vec<&'static str>,
    pub formats: HashMap<&'static str, Vec<&'static str>>,
    pub qualities: Box<dyn Fn(&str, &str) -> Vec<String>>,
    pub where_: String,
    pub action: String,
    pub local: bool,
    pub note: Box<dyn Fn(&Choice) -> String>,
    pub note_for: Box<dyn Fn(&str, &str) -> String>,
    pub missing: Box<dyn Fn(&str, &str) -> String>,
    pub extras: HashSet<&'static str>,
    pub duration: Option<f64>,
    pub frames: Option<Box<dyn Fn() -> Option<Frames>>>,
    pub site: String,
}

impl ConvertOptions {
    fn base(title: String, where_: &str) -> Self {
        ConvertOptions {
            title,
            kinds: vec!["video", "audio"],
            formats: HashMap::from([("video", VIDEO_FORMATS.to_vec()), ("audio", AUDIO_FORMATS.to_vec())]),
            qualities: Box::new(|_, _| vec![]),
            where_: where_.into(),
            action: "download".into(),
            local: false,
            note: Box::new(|_| String::new()),
            note_for: Box::new(|_, f| link_note(f).into()),
            missing: Box::new(|_, _| String::new()),
            extras: HashSet::new(),
            duration: None,
            frames: None,
            site: String::new(),
        }
    }
}

pub fn for_video(info: &VideoInfo, where_: &str) -> ConvertOptions {
    let mut o = ConvertOptions::base(info.title.clone(), where_);
    let max = info.max_height;
    o.qualities = Box::new(move |kind, fmt| if kind == "video" { video_qualities(fmt, max) } else { audio_qualities(fmt) });
    o.extras = HashSet::from([SUBTITLES, NORMALIZE]);
    if info.duration.is_some() {
        o.extras.insert(CLIP);
    }
    if info.is_youtube() {
        o.extras.insert(SPONSORBLOCK);
    }
    if info.chapters() > 1 {
        o.extras.insert(CHAPTERS);
    }
    o.duration = info.duration;
    if info.duration.is_some() {
        let info = info.clone();
        o.frames = Some(Box::new(move || frames::for_video(&info)));
    }
    o.site = library::site_key(&info.extractor);
    o
}

pub fn for_playlist(title: &str, count: usize, where_: &str, youtube: bool) -> ConvertOptions {
    let mut o = ConvertOptions::base(format!("{title} · {count} video{}", if count != 1 { "s" } else { "" }), where_);
    o.qualities = Box::new(|kind, fmt| if kind == "video" { video_qualities(fmt, None) } else { audio_qualities(fmt) });
    o.action = format!("download {count}");
    o.extras = HashSet::from([SUBTITLES, NORMALIZE]);
    if youtube {
        o.extras.insert(SPONSORBLOCK);
        o.site = "youtube".into();
    }
    o
}

/// The ffmpeg that can write `fmt`: the system's if it can, else the bundled one.
pub fn ffmpeg_for(kind: &str, fmt: &str) -> Option<String> {
    let dirs: Vec<String> = deps::ffmpeg_candidates().into_iter().map(|t| t.path).collect();
    for d in &dirs {
        let has = |e: &str| deps::ffmpeg_has_encoder(d, e);
        if local::missing_encoder(kind, fmt, &has).is_empty() {
            return Some(d.clone());
        }
    }
    dirs.into_iter().next()
}

pub fn for_files(files: &[FileInfo]) -> ConvertOptions {
    let title = if files.len() == 1 { files[0].name() } else { format!("{} files", files.len()) };
    let folders: HashSet<_> = files.iter().filter_map(|f| f.path.parent().map(|p| p.to_path_buf())).collect();
    let where_ = if folders.len() == 1 { short_path(&folders.iter().next().unwrap().to_string_lossy()) } else { "next to each original".into() };
    let mut o = ConvertOptions::base(title, &where_);
    let kinds: Vec<&'static str> = ["video", "audio", "image", "frames"].into_iter().filter(|k| files.iter().any(|f| local::kinds_for(f).contains(k))).collect();
    o.formats = kinds.iter().map(|k| (*k, local::formats_for(k))).collect();
    o.kinds = kinds.clone();
    o.local = true;
    o.action = "convert".into();
    o.note_for = Box::new(|k, f| local::format_note(k, f));
    // Look the ffmpegs up once for the whole dialog, and each format once.
    let dirs: Vec<String> = deps::ffmpeg_candidates().into_iter().map(|t| t.path).collect();
    let seen: RefCell<HashMap<(String, String), String>> = RefCell::new(HashMap::new());
    o.missing = Box::new(move |k, f| {
        let key = (k.to_string(), f.to_string());
        if let Some(v) = seen.borrow().get(&key) {
            return v.clone();
        }
        let has = |e: &str| dirs.is_empty() || dirs.iter().any(|d| deps::ffmpeg_has_encoder(d, e));
        let v = local::missing_encoder(k, f, &has);
        seen.borrow_mut().insert(key, v.clone());
        v
    });
    let fs: Vec<FileInfo> = files.to_vec();
    o.qualities = Box::new(move |kind, fmt| {
        // Offer what the biggest file has; smaller files simply aren't enlarged.
        let biggest = fs.iter().filter(|f| local::kinds_for(f).contains(&kind)).max_by_key(|f| f.long_side().unwrap_or(0));
        biggest.map(|b| local::qualities_for(b, kind, fmt)).unwrap_or_default()
    });
    let fs: Vec<FileInfo> = files.to_vec();
    o.note = Box::new(move |c| {
        let targets: Vec<&FileInfo> = fs.iter().filter(|f| local::kinds_for(f).contains(&c.kind.as_str())).collect();
        let mut bits = vec![];
        if targets.len() < fs.len() {
            bits.push(format!("Applies to {} of {} files.", targets.len(), fs.len()));
        }
        let fast: Vec<bool> = targets.iter().map(|f| local::is_fast_copy(f, c)).collect();
        if fast.iter().all(|f| *f) {
            bits.push("Fast copy, no quality lost.".into());
        } else if fast.iter().any(|f| *f) {
            bits.push("Some files copy fast, others re-encode.".into());
        } else {
            bits.push("Re-encodes, which can take a while.".into());
        }
        bits.join(" ")
    });
    if files.iter().any(|f| f.media != Media::Image && f.duration.is_some()) {
        o.extras.insert(CLIP);
    }
    if kinds.contains(&"audio") {
        o.extras.insert(NORMALIZE);
    }
    if kinds.contains(&"image") {
        o.extras.insert(IMAGE_TOOLS);
    }
    o.duration = files.iter().filter_map(|f| f.duration).reduce(f64::min);
    if files.len() == 1 && files[0].media == Media::Video {
        let f = files[0].clone();
        o.frames = Some(Box::new(move || frames::for_file(&f, ffmpeg_for("video", "mp4"))));
    }
    o
}

/// Settings with `choice` saved, so the dialog reopens where the user left it.
pub fn remembered(settings: &Settings, c: &Choice) -> Settings {
    let mut s = settings.clone();
    s.last_kind = c.kind.clone();
    s.set_last(&c.kind, &c.fmt, &c.quality);
    if c.kind == "video" || c.kind == "audio" {
        s.last_subtitles = c.extras.subtitles.clone();
        s.last_sub_langs = c.extras.sub_langs.clone();
        s.last_sponsorblock = c.extras.sponsorblock.clone();
    }
    if c.kind == "audio" {
        s.last_normalize = c.extras.normalize;
    }
    s
}

/// What the extras will actually do, where that isn't obvious from the label.
pub fn extras_note(c: &Choice) -> String {
    let e = &c.extras;
    let mut bits = vec![];
    if e.subtitles == "embed" && c.kind == "video" && !SUBTITLE_EMBED_EXTS.contains(&c.fmt.as_str()) {
        bits.push(format!("{} can't hold subtitles, so they come as a .srt file.", c.fmt.to_uppercase()));
    }
    if e.clipped() && c.kind != "image" {
        bits.push("Unless it is re-encoded, a clip starts at the nearest keyframe.".into());
    }
    if e.max_kb.is_some() && !["jpg", "webp", "avif", "heic", "jxl"].contains(&c.fmt.as_str()) {
        bits.push(format!("Max size works for JPG, WebP, AVIF, HEIC and JPEG XL, not {}.", c.fmt.to_uppercase()));
    }
    if e.sponsorblock == "remove" {
        bits.push("Sponsor segments come from SponsorBlock's community list.".into());
    }
    bits.join(" ")
}

#[derive(Clone, Copy, PartialEq, Debug)]
enum F {
    Preset(usize),
    PresetSave,
    Tabs,
    Format,
    Quality,
    ClipStart,
    ClipEnd,
    ClipPick,
    Subs,
    Langs,
    Sponsor,
    Split,
    Volume,
    MaxKb,
    Strip,
    Go,
    Cancel,
}

const EXTRA_ROWS: [(F, &str, &str); 7] = [
    (F::Subs, SUBTITLES, "Subtitles"),
    (F::Langs, SUBTITLES, "Languages"),
    (F::Sponsor, SPONSORBLOCK, "SponsorBlock"),
    (F::Split, CHAPTERS, "Chapters"),
    (F::Volume, NORMALIZE, "Volume"),
    (F::MaxKb, IMAGE_TOOLS, "Max size"),
    (F::Strip, IMAGE_TOOLS, "Metadata"),
];

pub struct ConvertDialog {
    opts: ConvertOptions,
    presets: Vec<Preset>,
    kind: usize,
    fmt: HashMap<&'static str, ListState>,
    enabled: HashMap<&'static str, Vec<bool>>,
    qual: HashMap<&'static str, (Vec<String>, ListState)>,
    clip_start: TextInput,
    clip_end: TextInput,
    subs: String,
    langs: TextInput,
    sponsor: String,
    split: bool,
    normalize: bool,
    max_kb: TextInput,
    strip: bool,
    focus: F,
}

impl ConvertDialog {
    pub fn new(opts: ConvertOptions, s: &Settings, initial: Option<Extras>) -> Self {
        let kind = opts.kinds.iter().position(|k| *k == s.last_kind).unwrap_or(0);
        let end_ph = opts.duration.map(|d| time_text(Some(d))).unwrap_or_else(|| "end".into());
        let mut max_kb = TextInput::new("", "no limit");
        max_kb.digits_only = true;
        let mut d = ConvertDialog {
            presets: vec![],
            kind,
            fmt: HashMap::new(),
            enabled: HashMap::new(),
            qual: HashMap::new(),
            clip_start: TextInput::new("", "0:00"),
            clip_end: TextInput::new("", &end_ph),
            subs: if ["embed", "srt"].contains(&s.last_subtitles.as_str()) { s.last_subtitles.clone() } else { String::new() },
            langs: TextInput::new(&s.last_sub_langs, "en, de"),
            sponsor: if ["remove", "mark"].contains(&s.last_sponsorblock.as_str()) { s.last_sponsorblock.clone() } else { String::new() },
            split: false,
            normalize: s.last_normalize,
            max_kb,
            strip: true,
            focus: F::Format,
            opts,
        };
        for &k in &d.opts.kinds.clone() {
            let formats = d.opts.formats[k].clone();
            let enabled: Vec<bool> = formats.iter().map(|f| (d.opts.missing)(k, f).is_empty()).collect();
            let last = s.last_format(k);
            let idx = formats.iter().position(|f| *f == last).filter(|&i| enabled[i]).or_else(|| enabled.iter().position(|e| *e)).unwrap_or(0);
            d.fmt.insert(k, ListState::new(idx));
            d.enabled.insert(k, enabled);
            d.fill_qualities(k, s.last_quality(k));
        }
        d.presets = library::presets_load().into_iter().filter(|p| d.fits(p)).collect();
        if let Some(e) = initial.filter(|e| e.clipped()) {
            if d.opts.extras.contains(CLIP) {
                d.clip_start.set(&e.start.map(|s| time_text(Some(s))).unwrap_or_default());
                d.clip_end.set(&e.end.map(|s| time_text(Some(s))).unwrap_or_default());
            }
        }
        d
    }

    fn kind_name(&self) -> &'static str {
        self.opts.kinds[self.kind]
    }

    fn fits(&self, p: &Preset) -> bool {
        self.opts.kinds.contains(&p.kind.as_str()) && self.opts.formats[p.kind.as_str()].contains(&p.fmt.as_str()) && (self.opts.missing)(&p.kind, &p.fmt).is_empty()
    }

    fn selected_fmt(&self, kind: &'static str) -> Option<&'static str> {
        self.opts.formats[kind].get(self.fmt[kind].index).copied()
    }

    fn fill_qualities(&mut self, kind: &'static str, fallback: &str) {
        let Some(fmt) = self.selected_fmt(kind) else { return };
        let previous = self.qual.get(kind).and_then(|(q, l)| q.get(l.index).cloned()).unwrap_or_else(|| fallback.to_string());
        let qualities = (self.opts.qualities)(kind, fmt);
        let idx = qualities.iter().position(|q| *q == previous).unwrap_or(0);
        self.qual.insert(kind, (qualities, ListState::new(idx)));
    }

    fn shows(&self, name: &str) -> bool {
        self.opts.extras.contains(name) && extra_kinds(name).contains(&self.kind_name())
    }

    fn sub_choices(&self) -> Vec<(&'static str, &'static str)> {
        if self.kind_name() == "audio" { vec![("Off", ""), (".srt file", "srt")] } else { vec![("Off", ""), ("Inside the video", "embed"), (".srt file", "srt")] }
    }

    fn extras(&self) -> Result<Extras, String> {
        let clip = self.shows(CLIP);
        let start = if clip { parse_time(&self.clip_start.value)? } else { None };
        let mut end = if clip { parse_time(&self.clip_end.value)? } else { None };
        let problem = check_clip(start, end, self.opts.duration);
        if !problem.is_empty() {
            return Err(problem);
        }
        if let (Some(d), Some(e)) = (self.opts.duration, end) {
            if e >= d {
                end = None;
            }
        }
        let subs_on = self.shows(SUBTITLES);
        let mut subtitles = if subs_on { self.subs.clone() } else { String::new() };
        if self.kind_name() == "audio" && subtitles == "embed" {
            subtitles = "srt".into();
        }
        let langs = self.langs.value.replace(' ', "");
        Ok(Extras {
            start: start.filter(|s| *s > 0.0),
            end,
            subtitles,
            sub_langs: if subs_on && !langs.is_empty() { langs } else { "en".into() },
            sponsorblock: if self.shows(SPONSORBLOCK) { self.sponsor.clone() } else { String::new() },
            split_chapters: self.shows(CHAPTERS) && self.split,
            normalize: self.kind_name() == "audio" && self.shows(NORMALIZE) && self.normalize,
            strip_metadata: self.shows(IMAGE_TOOLS) && self.strip,
            max_kb: if self.shows(IMAGE_TOOLS) { self.max_kb.value.parse::<i64>().ok().filter(|n| *n > 0) } else { None },
        })
    }

    fn choice(&self) -> Result<Option<Choice>, String> {
        let k = self.kind_name();
        let Some(fmt) = self.selected_fmt(k) else { return Ok(None) };
        let Some(q) = self.qual.get(k).and_then(|(q, l)| q.get(l.index)) else { return Ok(None) };
        Ok(Some(Choice::new(k, fmt, q).with_extras(self.extras()?)))
    }

    fn focusables(&self) -> Vec<F> {
        let mut v: Vec<F> = (0..self.presets.len()).map(F::Preset).collect();
        v.push(F::PresetSave);
        v.extend([F::Tabs, F::Format, F::Quality]);
        if self.shows(CLIP) {
            v.extend([F::ClipStart, F::ClipEnd]);
            if self.opts.frames.is_some() {
                v.push(F::ClipPick);
            }
        }
        for (f, name, _) in EXTRA_ROWS {
            if self.shows(name) && !(f == F::Langs && self.subs.is_empty()) {
                v.push(f);
            }
        }
        v.extend([F::Go, F::Cancel]);
        v
    }

    fn move_focus(&mut self, forward: bool) {
        let v = self.focusables();
        let i = v.iter().position(|f| *f == self.focus).unwrap_or(0);
        let n = v.len();
        self.focus = v[if forward { (i + 1) % n } else { (i + n - 1) % n }];
    }

    fn set_kind(&mut self, k: usize) {
        self.kind = k;
        if self.kind_name() == "audio" && self.subs == "embed" {
            self.subs = "srt".into();
        }
        if !self.focusables().contains(&self.focus) {
            self.focus = F::Tabs;
        }
    }

    fn go(&mut self, cx: &mut Cx) -> Out {
        match self.choice() {
            Ok(c) => Out::Close(c.map(Res::Choice)),
            Err(_) => {
                cx.toasts.bell();
                Out::None
            }
        }
    }

    fn apply(&mut self, c: Choice) {
        let Some(k) = self.opts.kinds.iter().position(|x| *x == c.kind) else { return };
        self.set_kind(k);
        let kind = self.kind_name();
        if let Some(i) = self.opts.formats[kind].iter().position(|f| *f == c.fmt) {
            self.fmt.get_mut(kind).unwrap().index = i;
        }
        self.qual.remove(kind);
        self.fill_qualities(kind, &c.quality);
        let e = c.extras;
        self.subs = if kind == "audio" && e.subtitles == "embed" { "srt".into() } else { e.subtitles };
        self.langs.set(&e.sub_langs);
        self.sponsor = e.sponsorblock;
        self.split = e.split_chapters;
        self.normalize = e.normalize;
        self.strip = e.strip_metadata;
        self.max_kb.set(&e.max_kb.map(|n| n.to_string()).unwrap_or_default());
    }

    fn cycle_value(&mut self, f: F, back: bool) {
        let step = |list: &[&str], cur: &str| -> String {
            let i = list.iter().position(|v| *v == cur).unwrap_or(0);
            let n = list.len();
            list[if back { (i + n - 1) % n } else { (i + 1) % n }].to_string()
        };
        match f {
            F::Subs => {
                let vals: Vec<&str> = self.sub_choices().iter().map(|c| c.1).collect();
                self.subs = step(&vals, &self.subs.clone());
            }
            F::Sponsor => self.sponsor = step(&["", "remove", "mark"], &self.sponsor.clone()),
            F::Split => self.split = !self.split,
            F::Volume => self.normalize = !self.normalize,
            F::Strip => self.strip = !self.strip,
            _ => {}
        }
    }

    fn pick_clip(&mut self, cx: &mut Cx) -> Out {
        let Some(make) = &self.opts.frames else { return Out::None };
        let Some(source) = make() else {
            cx.toasts.warn("This video has no preview frames to pick from.");
            return Out::None;
        };
        let start = parse_time(&self.clip_start.value).unwrap_or(None);
        let end = parse_time(&self.clip_end.value).unwrap_or(None);
        Out::Push(Box::new(ClipPicker::new(source, &self.opts.title, start, end, cx.tx)))
    }

    fn press(&mut self, f: F, cx: &mut Cx) -> Out {
        match f {
            F::Preset(i) => {
                if let Some(p) = self.presets.get(i) {
                    let c = p.choice();
                    self.apply(c);
                }
                Out::None
            }
            F::PresetSave => match self.choice() {
                Ok(Some(c)) => Out::Push(Box::new(PresetSave::new(c, &self.opts.site))),
                _ => {
                    cx.toasts.bell();
                    Out::None
                }
            },
            F::ClipPick => self.pick_clip(cx),
            F::Go => self.go(cx),
            F::Cancel => Out::Close(None),
            F::Subs | F::Sponsor | F::Split | F::Volume | F::Strip => {
                self.cycle_value(f, false);
                Out::None
            }
            _ => Out::None,
        }
    }

    fn input_mut(&mut self, f: F) -> Option<&mut TextInput> {
        match f {
            F::ClipStart => Some(&mut self.clip_start),
            F::ClipEnd => Some(&mut self.clip_end),
            F::Langs => Some(&mut self.langs),
            F::MaxKb => Some(&mut self.max_kb),
            _ => None,
        }
    }
}

impl Dialog for ConvertDialog {
    fn width(&self) -> u16 {
        92
    }

    fn content_height(&self, w: u16) -> u16 {
        let mut h = 2 + 2 + 2 + 1 + 11;
        if !self.opts.extras.is_empty() {
            h += 1 + 9;
        }
        h += 1 + self.summary(w).len() as u16;
        h + 1 + 3
    }

    fn draw(&self, p: &mut Painter, r: Rect) -> u16 {
        let t = p.t;
        let (x, w) = (r.x, r.width);
        let mut y = r.y;
        let mut focus_y = y;
        // Title.
        p.line(x, y, w, Line::from(vec![Span::styled("Convert", bold(t.text)), Span::styled(format!("  {}", self.opts.title), fg(t.muted))]), Align::Left);
        y += 2;
        // Presets.
        p.text(x, y, 10, "Presets", fg(t.muted));
        let mut cx_ = x + 10;
        let current = self.choice().ok().flatten();
        for (i, pr) in self.presets.iter().enumerate() {
            let active = current.as_ref().is_some_and(|c| pr.matches(c));
            cx_ += chip(p, cx_, y, &format!("{} {}", i + 1, pr.name), self.focus == F::Preset(i), active, &format!("preset:{i}")) + 1;
        }
        let save = if self.presets.is_empty() { "+ save these settings as a preset" } else { "+ save" };
        let sw = width(save) as u16 + 4;
        let style = if self.focus == F::PresetSave { Style::new().fg(t.bg.color()).bg(t.primary.color()).add_modifier(Modifier::BOLD) } else { fg(t.muted) };
        p.text(cx_, y, sw, &format!("  {save}  "), style);
        p.click(Rect::new(cx_, y, sw, 1), "presetsave");
        if matches!(self.focus, F::Preset(_) | F::PresetSave) {
            focus_y = y;
        }
        y += 2;
        // Tabs.
        let mut tx = x;
        let mut under: Vec<Span<'static>> = vec![];
        for (i, k) in self.opts.kinds.iter().enumerate() {
            let label = format!(" {} ", tab_name(k));
            let lw = width(&label) as u16;
            let active = i == self.kind;
            let style = if active && self.focus == F::Tabs {
                bold(t.primary).add_modifier(Modifier::UNDERLINED)
            } else if active {
                bold(t.primary)
            } else {
                fg(t.muted)
            };
            p.text(tx, y, lw, &label, style);
            p.click(Rect::new(tx, y, lw, 2), format!("tab:{i}"));
            under.push(Span::styled("━".repeat(lw as usize), fg(if active { t.primary } else { t.panel })));
            tx += lw;
        }
        under.push(Span::styled("━".repeat(w.saturating_sub(tx - x) as usize), fg(t.panel)));
        p.line(x, y + 1, w, Line::from(under), Align::Left);
        if self.focus == F::Tabs {
            focus_y = y;
        }
        y += 3;
        // Format and Quality.
        let kind = self.kind_name();
        let pw = (w - 2) / 2;
        let left = Rect::new(x, y, pw, 11);
        let right = Rect::new(x + pw + 2, y, w - pw - 2, 11);
        let qtitle = match kind {
            "frames" => "Rate",
            "image" => "Size",
            _ => "Quality",
        };
        panel(p, left, "Format", self.focus == F::Format);
        panel(p, right, qtitle, self.focus == F::Quality);
        let formats = &self.opts.formats[kind];
        let enabled = &self.enabled[kind];
        let rows: Vec<Line<'static>> = formats
            .iter()
            .enumerate()
            .map(|(i, f)| {
                if enabled[i] {
                    Line::from(vec![Span::styled(format!("{:<5}", f.to_uppercase()), bold(t.text)), Span::styled(format!(" {}", (self.opts.note_for)(kind, f)), fg(t.text))])
                } else {
                    Line::from(Span::styled(format!("{:<5} needs {}", f.to_uppercase(), (self.opts.missing)(kind, f)), fg(t.disabled)))
                }
            })
            .collect();
        self.fmt[kind].draw(p, inner(left), rows, self.focus == F::Format, "fmt");
        if let Some((qs, ql)) = self.qual.get(kind) {
            let fmt = self.selected_fmt(kind).unwrap_or("");
            let rows: Vec<Line<'static>> = qs
                .iter()
                .map(|q| {
                    let (label, note) = quality_label(kind, q, fmt, self.opts.local);
                    let mut spans = vec![Span::styled(label, fg(t.text))];
                    if !note.is_empty() {
                        spans.push(Span::styled(format!("  {note}"), fg(t.muted)));
                    }
                    Line::from(spans)
                })
                .collect();
            ql.draw(p, inner(right), rows, self.focus == F::Quality, "q");
        }
        if matches!(self.focus, F::Format | F::Quality) {
            focus_y = y + 5;
        }
        y += 12;
        // Clip and Extras.
        if !self.opts.extras.is_empty() {
            let left = Rect::new(x, y, pw, 9);
            let right = Rect::new(x + pw + 2, y, w - pw - 2, 9);
            let clip_focus = matches!(self.focus, F::ClipStart | F::ClipEnd | F::ClipPick);
            let extra_focus = matches!(self.focus, F::Subs | F::Langs | F::Sponsor | F::Split | F::Volume | F::MaxKb | F::Strip);
            panel(p, left, "Clip", clip_focus);
            panel(p, right, "Extras", extra_focus);
            if clip_focus || extra_focus {
                focus_y = y + 4;
            }
            let ci = inner(left);
            if self.shows(CLIP) {
                let half = (ci.width - 2) / 2;
                p.text(ci.x, ci.y + 1, half, "Start", fg(t.muted));
                p.text(ci.x + half + 2, ci.y + 1, half, "End", fg(t.muted));
                let fbg = |f: bool| if f { t.primary.over(t.bg, 0.25) } else { t.panel };
                // Inputs keep one cell of padding on each side, as Textual's do.
                let fs = self.focus == F::ClipStart;
                p.fill(Rect::new(ci.x, ci.y + 2, half, 1), fbg(fs));
                self.clip_start.draw(p, ci.x + 1, ci.y + 2, half - 2, fbg(fs), t.text, fs);
                p.click(Rect::new(ci.x, ci.y + 2, half, 1), "clipstart");
                let fe = self.focus == F::ClipEnd;
                p.fill(Rect::new(ci.x + half + 2, ci.y + 2, ci.width - half - 2, 1), fbg(fe));
                self.clip_end.draw(p, ci.x + half + 3, ci.y + 2, ci.width - half - 4, fbg(fe), t.text, fe);
                p.click(Rect::new(ci.x + half + 2, ci.y + 2, ci.width - half - 2, 1), "clipend");
                let mut hy = ci.y + 4;
                if self.opts.frames.is_some() {
                    let fp = self.focus == F::ClipPick;
                    let (f, b) = if fp { (t.bg, t.primary) } else { (t.fg, t.panel) };
                    p.fill(Rect::new(ci.x, hy, ci.width, 1), b);
                    p.line(ci.x, hy, ci.width, Line::from(Span::styled("Pick on the timeline", bold(f).bg(b.color()))), Align::Center);
                    p.click(Rect::new(ci.x, hy, ci.width, 1), "clippick");
                    hy += 2;
                }
                p.text(ci.x, hy, ci.width, "1:30, 90 or 1m30s · blank for all", fg(t.muted.over(t.bg, 0.6)));
            } else {
                let note = if kind == "image" { "Pictures have no timeline." } else { "Clips work on one video at a time." };
                p.text(ci.x, ci.y + 1, ci.width, note, fg(t.muted.over(t.bg, 0.6)));
            }
            let ei = inner(right);
            let mut ey = ei.y + 1;
            let vw = 22.min(ei.width / 2);
            let vx = ei.right() - vw;
            let mut any = false;
            for (f, name, label) in EXTRA_ROWS {
                if !self.shows(name) {
                    continue;
                }
                any = true;
                p.text(ei.x, ey, vx - ei.x, label, fg(t.muted));
                let focused = self.focus == f;
                let id = format!("x:{f:?}");
                match f {
                    F::Subs => {
                        let l = self.sub_choices().iter().find(|c| c.1 == self.subs).map(|c| c.0).unwrap_or("Off");
                        cycle(p, vx, ey, vw, l, focused, &id);
                    }
                    F::Sponsor => {
                        let l = match self.sponsor.as_str() {
                            "remove" => "Cut them out",
                            "mark" => "Mark as chapters",
                            _ => "Off",
                        };
                        cycle(p, vx, ey, vw, l, focused, &id);
                    }
                    F::Split => cycle(p, vx, ey, vw, if self.split { "One file each" } else { "Off" }, focused, &id),
                    F::Volume => cycle(p, vx, ey, vw, if self.normalize { "Even out" } else { "As it is" }, focused, &id),
                    F::Strip => cycle(p, vx, ey, vw, if self.strip { "Remove" } else { "Keep" }, focused, &id),
                    F::Langs => {
                        let disabled = self.subs.is_empty();
                        let bg = if disabled { t.panel.over(t.bg, 0.4) } else if focused { t.primary.over(t.bg, 0.25) } else { t.bg };
                        p.fill(Rect::new(vx, ey, vw, 1), bg);
                        self.langs.draw(p, vx + 1, ey, vw - 2, bg, if disabled { t.disabled } else { t.text }, focused);
                        p.click(Rect::new(vx, ey, vw, 1), id);
                    }
                    F::MaxKb => {
                        let kw = vw.saturating_sub(4);
                        let bg = if focused { t.primary.over(t.bg, 0.25) } else { t.bg };
                        p.fill(Rect::new(vx, ey, kw, 1), bg);
                        self.max_kb.draw(p, vx + 1, ey, kw - 2, bg, t.text, focused);
                        p.text(vx + kw + 2, ey, 2, "KB", fg(t.muted));
                        p.click(Rect::new(vx, ey, kw, 1), id);
                    }
                    _ => {}
                }
                ey += 1;
            }
            if !any {
                p.text(ei.x, ei.y + 1, ei.width, "Nothing extra for this one.", fg(t.muted.over(t.bg, 0.6)));
            }
            y += 10;
        }
        // Summary.
        for l in self.summary(w) {
            p.line(x, y, w, l, Align::Left);
            y += 1;
        }
        y += 1;
        let go = cap(&self.opts.action);
        let focused = match self.focus {
            F::Go => Some(0),
            F::Cancel => Some(1),
            _ => None,
        };
        if focused.is_some() {
            focus_y = y + 2;
        }
        action_buttons(p, x, y, w, &[(&go, "go", true), ("Cancel", "cancel", false)], focused);
        focus_y
    }

    fn key(&mut self, k: &KeyEvent, cx: &mut Cx) -> Out {
        if k.code == KeyCode::Esc {
            return Out::Close(None);
        }
        match k.code {
            KeyCode::Tab => {
                self.move_focus(true);
                return Out::None;
            }
            KeyCode::BackTab => {
                self.move_focus(false);
                return Out::None;
            }
            _ => {}
        }
        let f = self.focus;
        if let Some(input) = self.input_mut(f) {
            return match input.key(k) {
                Edit::Submitted => self.go(cx),
                Edit::Ignored => Out::None,
                _ => Out::None,
            };
        }
        // Number keys pick presets, anywhere but a text field.
        if let KeyCode::Char(c @ '1'..='9') = k.code {
            if !k.modifiers.contains(KeyModifiers::CONTROL) {
                let i = c as usize - '1' as usize;
                if i < self.presets.len() {
                    return self.press(F::Preset(i), cx);
                }
                return Out::None;
            }
        }
        let kind = self.kind_name();
        match f {
            F::Tabs => match k.code {
                KeyCode::Left => {
                    let n = self.opts.kinds.len();
                    self.set_kind((self.kind + n - 1) % n);
                }
                KeyCode::Right => {
                    let n = self.opts.kinds.len();
                    self.set_kind((self.kind + 1) % n);
                }
                _ => {}
            },
            F::Format => {
                let n = self.opts.formats[kind].len();
                let enabled = self.enabled[kind].clone();
                if self.fmt.get_mut(kind).unwrap().key(k, n, Some(&enabled)) {
                    self.fill_qualities(kind, "");
                } else if k.code == KeyCode::Enter {
                    self.focus = F::Quality;
                }
            }
            F::Quality => {
                if let Some((qs, l)) = self.qual.get_mut(kind) {
                    if !l.key(k, qs.len(), None) && k.code == KeyCode::Enter {
                        return self.go(cx);
                    }
                }
            }
            F::Subs | F::Sponsor | F::Split | F::Volume | F::Strip => match k.code {
                KeyCode::Left => self.cycle_value(f, true),
                KeyCode::Right | KeyCode::Enter | KeyCode::Char(' ') => self.cycle_value(f, false),
                _ => {}
            },
            _ => {
                if matches!(k.code, KeyCode::Enter | KeyCode::Char(' ')) {
                    return self.press(f, cx);
                }
            }
        }
        Out::None
    }

    fn paste(&mut self, text: &str, _cx: &mut Cx) -> Out {
        let f = self.focus;
        if let Some(input) = self.input_mut(f) {
            input.insert(text.trim());
        }
        Out::None
    }

    fn click(&mut self, id: &str, cx: &mut Cx) -> Out {
        let (a, b) = id.split_once(':').unwrap_or((id, ""));
        let kind = self.kind_name();
        match a {
            "tab" => {
                if let Ok(i) = b.parse() {
                    self.set_kind(i);
                }
            }
            "fmt" => {
                if let Ok(i) = b.parse::<usize>() {
                    if self.enabled[kind].get(i).copied().unwrap_or(false) {
                        self.fmt.get_mut(kind).unwrap().index = i;
                        self.fill_qualities(kind, "");
                    }
                    self.focus = F::Format;
                }
            }
            "q" => {
                if let (Ok(i), Some((_, l))) = (b.parse(), self.qual.get_mut(kind)) {
                    l.index = i;
                    self.focus = F::Quality;
                }
            }
            "preset" => {
                if let Ok(i) = b.parse() {
                    return self.press(F::Preset(i), cx);
                }
            }
            "presetsave" => return self.press(F::PresetSave, cx),
            "go" => return self.go(cx),
            "cancel" => return Out::Close(None),
            "clipstart" => self.focus = F::ClipStart,
            "clipend" => self.focus = F::ClipEnd,
            "clippick" => return self.press(F::ClipPick, cx),
            "x" => {
                let f = EXTRA_ROWS.iter().map(|r| r.0).find(|f| format!("{f:?}") == b);
                if let Some(f) = f {
                    self.focus = f;
                    if !matches!(f, F::Langs | F::MaxKb) {
                        self.cycle_value(f, false);
                    }
                }
            }
            _ => {}
        }
        Out::None
    }

    fn child(&mut self, res: Res, cx: &mut Cx) -> Out {
        match res {
            Res::Preset(preset) => {
                let name = preset.name.clone();
                library::preset_upsert(preset);
                self.presets = library::presets_load().into_iter().filter(|p| self.fits(p)).collect();
                cx.toasts.notify(&format!("Saved the {name} preset."), "", Severity::Info);
            }
            Res::Clip(a, b) => {
                self.clip_start.set(&a.filter(|v| *v > 0.0).map(|v| time_text(Some(v))).unwrap_or_default());
                self.clip_end.set(&b.map(|v| time_text(Some(v))).unwrap_or_default());
            }
            _ => {}
        }
        Out::None
    }

    fn any(&mut self) -> &mut dyn Any {
        self
    }
}

impl ConvertDialog {
    fn summary(&self, w: u16) -> Vec<Line<'static>> {
        self.summary_lines(w, CURRENT.with(|c| *c.borrow()))
    }

    fn summary_lines(&self, w: u16, t: &'static super::theme::Theme) -> Vec<Line<'static>> {
        match self.choice() {
            Err(e) => vec![Line::from(Span::styled(e, bold(t.error)))],
            Ok(None) => vec![Line::from("")],
            Ok(Some(c)) => {
                let mut lines = vec![Line::from(vec![Span::styled(c.label(), bold(t.text)), Span::styled(format!("  → {}", self.opts.where_), fg(t.muted))])];
                let mut notes: Vec<String> = [(self.opts.note)(&c), extras_note(&c)].into_iter().filter(|n| !n.is_empty()).collect();
                if let Some(site) = self.presets.iter().find(|p| !p.site.is_empty() && p.matches(&c)).map(|p| p.site.clone()) {
                    notes.insert(0, format!("{} links use this when you press d.", library::site_name(&site)));
                }
                if !notes.is_empty() {
                    lines.extend(super::draw::wrap(vec![Span::styled(notes.join(" "), fg(t.muted.over(t.bg, 0.6)))], w as usize));
                }
                lines
            }
        }
    }
}

thread_local! {
    /// The theme being drawn with, for code that builds lines before it has a painter.
    pub static CURRENT: RefCell<&'static super::theme::Theme> = RefCell::new(super::theme::by_name("carrot"));
}

/// A rounded panel with its title in the border, lit up while focus is inside.
pub fn panel(p: &mut Painter, r: Rect, title: &str, focused: bool) {
    let t = p.t;
    let (c, tc) = if focused { (t.primary, t.primary) } else { (t.panel, t.muted) };
    p.border(r, true, c, title, bold(tc));
}

/// Inside a panel's border with its padding 0 1.
pub fn inner(r: Rect) -> Rect {
    Rect::new(r.x + 2, r.y + 1, r.width.saturating_sub(4), r.height.saturating_sub(2))
}

// ---------- preset save ----------

pub struct PresetSave {
    choice: Choice,
    site: String,
    name: TextInput,
    default: bool,
    error: String,
    focus: usize, // 0 name, 1 toggle, 2 save, 3 cancel
}

impl PresetSave {
    pub fn new(choice: Choice, site: &str) -> Self {
        PresetSave { choice, site: site.into(), name: TextInput::new("", "Podcast"), default: false, error: String::new(), focus: 0 }
    }
    fn order(&self) -> Vec<usize> {
        if self.site.is_empty() { vec![0, 2, 3] } else { vec![0, 1, 2, 3] }
    }
    fn save(&mut self) -> Out {
        let name = self.name.value.trim().to_string();
        if name.is_empty() {
            self.error = "Give it a name.".into();
            self.focus = 0;
            return Out::None;
        }
        let name: String = name.chars().take(24).collect();
        let site = if self.default { self.site.clone() } else { String::new() };
        Out::Close(Some(Res::Preset(library::preset_from_choice(&name, &self.choice, &site))))
    }
}

impl Dialog for PresetSave {
    fn width(&self) -> u16 {
        60
    }
    fn content_height(&self, _w: u16) -> u16 {
        let mut h = 2 + 1 + 1 + 1;
        if !self.site.is_empty() {
            h += 1;
            if self.default && library::default_preset_for(&self.site).is_some() {
                h += 1;
            }
        }
        h + 1 + 1 + 3
    }
    fn draw(&self, p: &mut Painter, r: Rect) -> u16 {
        let t = p.t;
        let (x, w) = (r.x, r.width);
        let mut y = r.y;
        p.text(x, y, w, "Save as a preset", bold(t.text));
        y += 2;
        p.text(x, y, w, &self.choice.label(), fg(t.muted.over(t.bg, 0.6)));
        y += 2;
        p.text(x, y, 8, "Name", fg(t.muted));
        let bg = if self.focus == 0 { t.primary.over(t.bg, 0.25) } else { t.panel };
        p.fill(Rect::new(x + 8, y, w - 8, 1), bg);
        self.name.draw(p, x + 9, y, w - 10, bg, t.text, self.focus == 0);
        y += 1;
        if !self.site.is_empty() {
            toggle(p, x + 8, y, w - 8, &format!("Default for {} links", library::site_name(&self.site)), self.default, self.focus == 1, "default");
            y += 1;
            if self.default {
                if let Some(cur) = library::default_preset_for(&self.site) {
                    p.text(x + 8, y, w - 8, &format!("Replaces {} as the default.", cur.name), fg(t.muted.over(t.bg, 0.6)));
                    y += 1;
                }
            }
        }
        if !self.error.is_empty() {
            p.text(x, y, w, &self.error, fg(t.error));
        }
        y += 2;
        let focused = match self.focus {
            2 => Some(0),
            3 => Some(1),
            _ => None,
        };
        action_buttons(p, x, y, w, &[("Save", "save", true), ("Cancel", "cancel", false)], focused);
        y
    }
    fn key(&mut self, k: &KeyEvent, _cx: &mut Cx) -> Out {
        let order = self.order();
        let i = order.iter().position(|f| *f == self.focus).unwrap_or(0);
        match k.code {
            KeyCode::Esc => return Out::Close(None),
            KeyCode::Tab => self.focus = order[(i + 1) % order.len()],
            KeyCode::BackTab => self.focus = order[(i + order.len() - 1) % order.len()],
            _ => match self.focus {
                0 => {
                    if self.name.key(k) == Edit::Submitted {
                        return self.save();
                    }
                }
                1 if matches!(k.code, KeyCode::Enter | KeyCode::Char(' ')) => self.default = !self.default,
                2 if matches!(k.code, KeyCode::Enter | KeyCode::Char(' ')) => return self.save(),
                3 if matches!(k.code, KeyCode::Enter | KeyCode::Char(' ')) => return Out::Close(None),
                _ => {}
            },
        }
        Out::None
    }
    fn paste(&mut self, text: &str, _cx: &mut Cx) -> Out {
        if self.focus == 0 {
            self.name.insert(text.trim());
        }
        Out::None
    }
    fn click(&mut self, id: &str, _cx: &mut Cx) -> Out {
        match id {
            "default" => {
                self.focus = 1;
                self.default = !self.default;
            }
            "save" => return self.save(),
            "cancel" => return Out::Close(None),
            _ => {}
        }
        Out::None
    }
    fn any(&mut self) -> &mut dyn Any {
        self
    }
}

// ---------- clip picker ----------

pub struct ClipPicker {
    source: Frames,
    title: String,
    start: Option<f64>,
    end: Option<f64>,
    at: f64,
    token: u64,
    frame: Picture,
    tx: std::sync::mpsc::Sender<Msg>,
    focus: usize, // 0 timeline, 1 use, 2 cancel
}

impl ClipPicker {
    pub fn new(source: Frames, title: &str, start: Option<f64>, end: Option<f64>, tx: &std::sync::mpsc::Sender<Msg>) -> Self {
        let mut c = ClipPicker { source, title: title.into(), start, end, at: start.unwrap_or(0.0), token: 0, frame: Picture::default(), tx: tx.clone(), focus: 0 };
        c.load();
        c
    }
    fn load(&mut self) {
        self.token += 1;
        let (token, at, source, tx) = (self.token, self.at, Arc::clone(&self.source), self.tx.clone());
        super::spawn(move || {
            if let Some(image) = source.frame(at) {
                let _ = tx.send(Msg::Frame { token, image });
            }
        });
    }
    fn step(&mut self, delta: f64) {
        self.at = (self.at + delta).clamp(0.0, self.source.duration());
        self.load();
    }
}

impl Dialog for ClipPicker {
    fn width(&self) -> u16 {
        84
    }
    fn content_height(&self, _w: u16) -> u16 {
        2 + 20 + 2 + 2 + 1 + 1 + 1 + 3
    }
    fn draw(&self, p: &mut Painter, r: Rect) -> u16 {
        let t = p.t;
        let (x, w) = (r.x, r.width);
        let mut y = r.y;
        let dur = self.source.duration().max(0.001);
        p.line(x, y, w, Line::from(vec![Span::styled("Pick a clip", bold(t.text)), Span::styled(format!("  {}", self.title), fg(t.muted))]), Align::Left);
        y += 2;
        let fr = Rect::new(x, y, w, 20);
        panel(p, fr, &format!("Frame · {}", time_text(Some(self.at))), self.focus == 0);
        self.frame.draw(p, inner(fr));
        y += 21;
        p.text(x, y, 6, "0:00", fg(t.muted));
        p.line(x, y, w, Line::from(Span::styled(time_text(Some(dur)), fg(t.muted))), Align::Right);
        y += 1;
        // The timeline: the clip in the accent colour, a caret under the playhead.
        let tw = w.max(10) as usize;
        let col = |v: f64| ((v / dur * tw as f64) as usize).min(tw - 1);
        let lo = self.start.map(col);
        let hi = self.end.map(col);
        let mut bar: Vec<Span<'static>> = vec![];
        for i in 0..tw {
            let in_clip = (lo.is_some() || hi.is_some()) && lo.unwrap_or(0) <= i && i <= hi.unwrap_or(tw - 1);
            if Some(i) == lo {
                bar.push(Span::styled("[", bold(t.primary)));
            } else if Some(i) == hi {
                bar.push(Span::styled("]", bold(t.primary)));
            } else {
                bar.push(Span::styled("━", fg(if in_clip { t.primary } else { t.panel })));
            }
        }
        p.line(x, y, w, Line::from(bar), Align::Left);
        let caret = col(self.at) as u16;
        let label = time_text(Some(self.at));
        let lw = width(&label) as u16;
        if caret + lw + 2 <= w {
            p.text(x + caret, y + 1, 1, "▲", bold(t.primary));
            p.text(x + caret + 2, y + 1, lw, &label, bold(t.text));
        } else {
            let pad = caret.saturating_sub(lw + 1);
            p.text(x + pad, y + 1, lw, &label, bold(t.text));
            p.text(x + caret, y + 1, 1, "▲", bold(t.primary));
        }
        y += 2;
        let range = if self.start.is_none() && self.end.is_none() {
            Line::from(Span::styled("The whole video. Press [ and ] to mark a clip.", fg(t.muted.over(t.bg, 0.6))))
        } else {
            let a = self.start.unwrap_or(0.0);
            let b = self.end.unwrap_or(dur);
            Line::from(vec![
                Span::styled(format!("{} → {}", time_text(Some(a)), time_text(Some(b))), bold(t.text)),
                Span::styled(format!("  {}", time_text(Some((b - a).max(0.0)))), fg(t.muted)),
            ])
        };
        p.line(x, y, w, range, Align::Left);
        y += 1;
        let help: Vec<Span<'static>> = [("←→", "move"), ("shift ←→", "10s"), ("[", "start"), ("]", "end"), ("backspace", "clear"), ("enter", "use")]
            .iter()
            .flat_map(|(k, l)| [Span::styled(k.to_string(), bold(t.primary)), Span::styled(format!(" {l}  "), fg(t.text))])
            .collect();
        p.line(x, y, w, Line::from(help), Align::Left);
        y += 2;
        let focused = match self.focus {
            1 => Some(0),
            2 => Some(1),
            _ => None,
        };
        action_buttons(p, x, y, w, &[("Use clip", "go", true), ("Cancel", "cancel", false)], focused);
        r.y
    }
    fn key(&mut self, k: &KeyEvent, _cx: &mut Cx) -> Out {
        let shift = k.modifiers.contains(KeyModifiers::SHIFT);
        let step = self.source.step();
        match k.code {
            KeyCode::Esc => return Out::Close(None),
            KeyCode::Left => self.step(if shift { -10.0 } else { -step }),
            KeyCode::Right => self.step(if shift { 10.0 } else { step }),
            KeyCode::Char('[') => {
                self.start = Some(self.at);
                if self.end.is_some_and(|e| e <= self.at) {
                    self.end = None;
                }
            }
            KeyCode::Char(']') => {
                self.end = Some(self.at);
                if self.start.is_some_and(|s| s >= self.at) {
                    self.start = None;
                }
            }
            KeyCode::Backspace => {
                self.start = None;
                self.end = None;
            }
            KeyCode::Tab => self.focus = (self.focus + 1) % 3,
            KeyCode::BackTab => self.focus = (self.focus + 2) % 3,
            KeyCode::Enter | KeyCode::Char(' ') => {
                if self.focus == 2 {
                    return Out::Close(None);
                }
                return Out::Close(Some(Res::Clip(self.start, self.end)));
            }
            _ => {}
        }
        Out::None
    }
    fn click(&mut self, id: &str, _cx: &mut Cx) -> Out {
        match id {
            "go" => Out::Close(Some(Res::Clip(self.start, self.end))),
            "cancel" => Out::Close(None),
            _ => Out::None,
        }
    }
    fn msg(&mut self, m: &Msg, _cx: &mut Cx) -> bool {
        if let Msg::Frame { token, image } = m {
            if *token == self.token {
                self.frame.set(Some(image.clone()));
            }
            return true;
        }
        false
    }
    fn any(&mut self) -> &mut dyn Any {
        self
    }
}

#[allow(dead_code)]
fn _unused() {
    let _ = on;
}
