//! The other popups: settings, cookies, history, following, picking playlist videos,
//! supported sites, searching what was said, a file picker and the theme picker.

use super::convert::{inner, panel};
use super::draw::{bold, fg, wrap, Align, Painter};
use super::fmt::short_path;
use super::theme;
use super::widgets::{action_buttons, chip, toggle, Edit, ListState, TextInput};
use super::{Cx, Dialog, Msg, Out, Res};
use crate::config::Settings;
use crate::engine::options::{template_error, template_preview, time_text, TEMPLATE_FIELDS};
use crate::engine::{deps, desktop};
use crate::library::{self, Followed, HistoryEntry};
use crate::transcripts::{self, Hit, Transcript};
use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use std::any::Any;
use std::collections::BTreeSet;
use std::path::PathBuf;
use std::sync::mpsc::Sender;

fn press(k: &KeyEvent) -> bool {
    matches!(k.code, KeyCode::Enter | KeyCode::Char(' '))
}

fn title(p: &mut Painter, x: u16, y: u16, w: u16, main: &str, rest: &str) {
    let t = p.t;
    p.line(x, y, w, Line::from(vec![Span::styled(main.to_string(), bold(t.text)), Span::styled(format!("  {rest}"), fg(t.muted))]), Align::Left);
}

fn help_line(p: &mut Painter, x: u16, y: u16, w: u16, keys: &[(&str, &str)]) {
    let t = p.t;
    let spans: Vec<Span<'static>> = keys.iter().flat_map(|(k, l)| [Span::styled(k.to_string(), bold(t.primary)), Span::styled(format!(" {l}  "), fg(t.text))]).collect();
    p.line(x, y, w, Line::from(spans), Align::Left);
}

/// A one-row filled field: panel, warmer while typing.
fn field(p: &mut Painter, x: u16, y: u16, w: u16, input: &TextInput, focused: bool, id: &str) {
    let t = p.t;
    let bg = if focused { t.primary.over(t.bg, 0.25) } else { t.panel };
    p.fill(Rect::new(x, y, w, 1), bg);
    input.draw(p, x + 1, y, w.saturating_sub(2), bg, t.text, focused);
    p.click(Rect::new(x, y, w, 1), id.to_string());
}

/// A list framed like the dialog panels.
fn framed_list(p: &mut Painter, r: Rect, list: &ListState, rows: Vec<Line<'static>>, focused: bool, id: &str) {
    let t = p.t;
    p.border(r, true, if focused { t.primary } else { t.panel }, "", fg(t.panel));
    list.draw(p, Rect::new(r.x + 1, r.y + 1, r.width.saturating_sub(2), r.height.saturating_sub(2)), rows, focused, id);
}

// ---------- settings ----------

const PARALLEL: [(&str, i64); 4] = [("One at a time", 1), ("2 at once", 2), ("3 at once", 3), ("4 at once", 4)];
pub const BROWSERS: [(&str, &str); 8] = [
    ("Chrome", "chrome"),
    ("Firefox", "firefox"),
    ("Safari", "safari"),
    ("Edge", "edge"),
    ("Brave", "brave"),
    ("Opera", "opera"),
    ("Vivaldi", "vivaldi"),
    ("Chromium", "chromium"),
];

#[derive(Clone, Copy, PartialEq)]
enum SF {
    Folder,
    Browse,
    Tree,
    Template,
    Parallel,
    Clipboard,
    Notify,
    Transcripts,
    Cookies,
    Save,
    Cancel,
}

pub struct SettingsDialog {
    s: Settings,
    folder: TextInput,
    template: TextInput,
    tree: Option<DirTree>,
    parallel: usize,
    cookies: usize,
    clipboard: bool,
    notify: bool,
    keep: bool,
    error: String,
    focus: SF,
}

impl SettingsDialog {
    pub fn new(s: &Settings) -> Self {
        let parallel = PARALLEL.iter().position(|p| p.1 == s.parallel_downloads).unwrap_or(0);
        let cookies = BROWSERS.iter().position(|b| b.1 == s.cookies_browser).map(|i| i + 1).unwrap_or(0);
        SettingsDialog {
            folder: TextInput::new(&s.download_dir, ""),
            template: TextInput::new(&s.filename_template, ""),
            tree: None,
            parallel,
            cookies,
            clipboard: s.watch_clipboard,
            notify: s.notify_when_done,
            keep: s.keep_transcripts,
            error: String::new(),
            focus: SF::Folder,
            s: s.clone(),
        }
    }
    fn order(&self) -> Vec<SF> {
        let mut v = vec![SF::Folder, SF::Browse];
        if self.tree.is_some() {
            v.push(SF::Tree);
        }
        v.extend([SF::Template, SF::Parallel, SF::Clipboard, SF::Notify, SF::Transcripts, SF::Cookies, SF::Save, SF::Cancel]);
        v
    }
    fn cookie_label(&self) -> &'static str {
        if self.cookies == 0 { "Off" } else { BROWSERS[self.cookies - 1].0 }
    }
    fn save(&mut self) -> Out {
        let raw = self.folder.value.trim().to_string();
        if raw.is_empty() {
            self.error = "Choose a folder.".into();
            return Out::None;
        }
        let path = crate::config::expand_user(&raw);
        if let Err(e) = std::fs::create_dir_all(&path) {
            self.error = format!("Can't use that folder: {e}");
            return Out::None;
        }
        let template = self.template.value.trim().to_string();
        if !template_error(&template).is_empty() {
            self.focus = SF::Template;
            return Out::None;
        }
        let mut s = self.s.clone();
        s.download_dir = path.to_string_lossy().into();
        s.cookies_browser = if self.cookies == 0 { String::new() } else { BROWSERS[self.cookies - 1].1.into() };
        s.filename_template = template;
        s.parallel_downloads = PARALLEL[self.parallel].1;
        s.watch_clipboard = self.clipboard;
        s.notify_when_done = self.notify;
        s.keep_transcripts = self.keep;
        Out::Close(Some(Res::Settings(Box::new(s))))
    }
    fn activate(&mut self, f: SF, back: bool) -> Out {
        match f {
            SF::Browse => {
                if self.tree.is_some() {
                    self.tree = None;
                } else {
                    self.tree = Some(DirTree::new(dirs::home_dir().unwrap_or_default(), true));
                    self.focus = SF::Tree;
                }
            }
            SF::Parallel => self.parallel = if back { (self.parallel + 3) % 4 } else { (self.parallel + 1) % 4 },
            SF::Cookies => {
                let n = BROWSERS.len() + 1;
                self.cookies = if back { (self.cookies + n - 1) % n } else { (self.cookies + 1) % n };
            }
            SF::Clipboard => self.clipboard = !self.clipboard,
            SF::Notify => self.notify = !self.notify,
            SF::Transcripts => self.keep = !self.keep,
            SF::Save => return self.save(),
            SF::Cancel => return Out::Close(None),
            _ => {}
        }
        Out::None
    }
}

fn select(p: &mut Painter, x: u16, y: u16, w: u16, label: &str, focused: bool, id: &str) {
    let t = p.t;
    let bg = if focused { t.primary.over(t.bg, 0.25) } else { t.panel };
    p.fill(Rect::new(x, y, w, 1), bg);
    p.text(x + 1, y, w.saturating_sub(4), label, Style::new().fg(t.text.color()).bg(bg.color()));
    p.text(x + w.saturating_sub(2), y, 1, "▼", Style::new().fg(t.muted.color()).bg(bg.color()));
    p.click(Rect::new(x, y, w, 1), id.to_string());
}

impl Dialog for SettingsDialog {
    fn width(&self) -> u16 {
        76
    }
    fn content_height(&self, w: u16) -> u16 {
        let tree = if self.tree.is_some() { 12 } else { 0 };
        let err = if self.error.is_empty() { 0 } else { 1 };
        let preview = 2 + (wrap(vec![Span::raw(self.fields_text())], w as usize).len() as u16).saturating_sub(1);
        2 + 1 + 1 + tree + err + 1 + 1 + 1 + preview + 1 + 3 + 1 + 1 + 1 + 2 + 1 + 3
    }
    fn draw(&self, p: &mut Painter, r: Rect) -> u16 {
        let t = p.t;
        let (x, w) = (r.x, r.width);
        let mut y = r.y;
        let mut fy = y;
        p.text(x, y, w, "Settings", bold(t.text));
        y += 2;
        p.text(x, y, w, "Save downloads to", fg(t.muted));
        y += 1;
        let bw = 10u16;
        field(p, x, y, w - bw - 1, &self.folder, self.focus == SF::Folder, "folder");
        let style = if self.focus == SF::Browse { Style::new().fg(t.bg.color()).bg(t.primary.color()).add_modifier(Modifier::BOLD) } else { Style::new().fg(t.fg.color()).bg(t.panel.color()) };
        p.text(x + w - bw, y, bw, "  Browse  ", style);
        p.click(Rect::new(x + w - bw, y, bw, 1), "browse");
        if matches!(self.focus, SF::Folder | SF::Browse) {
            fy = y;
        }
        y += 1;
        if let Some(tree) = &self.tree {
            tree.draw(p, Rect::new(x, y, w, 12), self.focus == SF::Tree);
            if self.focus == SF::Tree {
                fy = y + 6;
            }
            y += 12;
        }
        if !self.error.is_empty() {
            p.text(x, y, w, &self.error, fg(t.error));
            y += 1;
        }
        y += 1;
        p.text(x, y, w, "Name downloads", fg(t.muted));
        y += 1;
        field(p, x, y, w, &self.template, self.focus == SF::Template, "template");
        if self.focus == SF::Template {
            fy = y;
        }
        y += 1;
        let problem = template_error(&self.template.value);
        if !problem.is_empty() {
            p.text(x, y, w, &problem, fg(t.error));
            y += 2;
        } else {
            p.line(x, y, w, Line::from(vec![Span::styled("→ ", fg(t.muted.over(t.bg, 0.6))), Span::styled(template_preview(&self.template.value, "mp4"), fg(t.text))]), Align::Left);
            y += 1;
            y += p.para(x, y, w, vec![Span::styled(self.fields_text(), fg(t.muted.over(t.bg, 0.6)))], 3);
        }
        y += 1;
        p.text(x, y, 20, "Downloads run", fg(t.muted));
        select(p, x + w - 24, y, 24, PARALLEL[self.parallel].0, self.focus == SF::Parallel, "parallel");
        y += 1;
        toggle(p, x, y, w, "Offer links I copy when I come back to Carrot", self.clipboard, self.focus == SF::Clipboard, "clipboard");
        y += 1;
        toggle(p, x, y, w, "Notify me when downloads finish in the background", self.notify, self.focus == SF::Notify, "notify");
        y += 1;
        toggle(p, x, y, w, "Keep captions so I can search what was said (press /)", self.keep, self.focus == SF::Transcripts, "keep");
        if matches!(self.focus, SF::Parallel | SF::Clipboard | SF::Notify | SF::Transcripts) {
            fy = y;
        }
        y += 2;
        p.text(x, y, w, "Browser cookies (for age-restricted or members-only videos)", fg(t.muted));
        y += 1;
        select(p, x, y, w, self.cookie_label(), self.focus == SF::Cookies, "cookies");
        y += 1;
        p.text(x, y, w, "Carrot Leaf reads that browser's YouTube login. On a Mac you may see a Keychain prompt.", fg(t.muted.over(t.bg, 0.6)));
        y += 2;
        let focused = match self.focus {
            SF::Save => Some(0),
            SF::Cancel => Some(1),
            _ => None,
        };
        if focused.is_some() || self.focus == SF::Cookies {
            fy = y + 2;
        }
        action_buttons(p, x, y, w, &[("Save", "save", true), ("Cancel", "cancel", false)], focused);
        fy
    }
    fn key(&mut self, k: &KeyEvent, _cx: &mut Cx) -> Out {
        let order = self.order();
        let i = order.iter().position(|f| *f == self.focus).unwrap_or(0);
        match k.code {
            KeyCode::Esc => return Out::Close(None),
            KeyCode::Tab => {
                self.focus = order[(i + 1) % order.len()];
                return Out::None;
            }
            KeyCode::BackTab => {
                self.focus = order[(i + order.len() - 1) % order.len()];
                return Out::None;
            }
            _ => {}
        }
        match self.focus {
            SF::Folder => {
                if self.folder.key(k) == Edit::Submitted {
                    return self.save();
                }
            }
            SF::Template => {
                if self.template.key(k) == Edit::Submitted {
                    return self.save();
                }
            }
            SF::Tree => {
                if let Some(tree) = &mut self.tree {
                    if let Some(path) = tree.key(k) {
                        self.folder.set(&path.to_string_lossy());
                    } else if k.code == KeyCode::Enter {
                        self.folder.set(&tree.dir.to_string_lossy());
                    }
                }
            }
            f @ (SF::Parallel | SF::Cookies) => match k.code {
                KeyCode::Left | KeyCode::Up => return self.activate(f, true),
                KeyCode::Right | KeyCode::Down | KeyCode::Enter | KeyCode::Char(' ') => return self.activate(f, false),
                _ => {}
            },
            f if press(k) => return self.activate(f, false),
            _ => {}
        }
        Out::None
    }
    fn paste(&mut self, text: &str, _cx: &mut Cx) -> Out {
        match self.focus {
            SF::Folder => self.folder.insert(text.trim()),
            SF::Template => self.template.insert(text.trim()),
            _ => {}
        }
        Out::None
    }
    fn click(&mut self, id: &str, _cx: &mut Cx) -> Out {
        let f = match id {
            "folder" => SF::Folder,
            "template" => SF::Template,
            "browse" => SF::Browse,
            "parallel" => SF::Parallel,
            "clipboard" => SF::Clipboard,
            "notify" => SF::Notify,
            "keep" => SF::Transcripts,
            "cookies" => SF::Cookies,
            "save" => SF::Save,
            "cancel" => SF::Cancel,
            other => {
                if let (Some(tree), Some(i)) = (&mut self.tree, other.strip_prefix("tree:")) {
                    self.focus = SF::Tree;
                    if let Ok(i) = i.parse() {
                        tree.list.index = i;
                    }
                }
                return Out::None;
            }
        };
        self.focus = f;
        if matches!(f, SF::Folder | SF::Template) {
            return Out::None;
        }
        self.activate(f, false)
    }
    fn any(&mut self) -> &mut dyn Any {
        self
    }
}

impl SettingsDialog {
    fn fields_text(&self) -> String {
        let fields: Vec<String> = TEMPLATE_FIELDS.iter().map(|(f, _)| format!("{{{f}}}")).collect();
        format!("{}  · use / for folders", fields.join(" "))
    }
}

// ---------- directory tree / file picker ----------

pub struct DirTree {
    pub dir: PathBuf,
    folders_only: bool,
    entries: Vec<(String, PathBuf, bool)>,
    pub list: ListState,
}

impl DirTree {
    pub fn new(dir: PathBuf, folders_only: bool) -> Self {
        let mut t = DirTree { dir: dir.clone(), folders_only, entries: vec![], list: ListState::default() };
        t.open(dir);
        t
    }
    fn open(&mut self, dir: PathBuf) {
        let mut entries = vec![];
        if let Some(parent) = dir.parent() {
            entries.push(("..".to_string(), parent.to_path_buf(), true));
        }
        let mut found: Vec<(String, PathBuf, bool)> = std::fs::read_dir(&dir)
            .map(|rd| {
                rd.flatten()
                    .filter_map(|e| {
                        let name = e.file_name().to_string_lossy().to_string();
                        if name.starts_with('.') {
                            return None;
                        }
                        let path = e.path();
                        let is_dir = path.is_dir();
                        if self.folders_only && !is_dir {
                            return None;
                        }
                        Some((name, path, is_dir))
                    })
                    .collect()
            })
            .unwrap_or_default();
        found.sort_by(|a, b| b.2.cmp(&a.2).then(a.0.to_lowercase().cmp(&b.0.to_lowercase())));
        entries.extend(found);
        self.entries = entries;
        self.dir = dir;
        self.list = ListState::default();
    }
    /// A key. Returns a file picked (or a folder opened, for folder trees).
    pub fn key(&mut self, k: &KeyEvent) -> Option<PathBuf> {
        if self.list.key(k, self.entries.len(), None) {
            return None;
        }
        if matches!(k.code, KeyCode::Enter | KeyCode::Right | KeyCode::Char(' ')) {
            let (_, path, is_dir) = self.entries.get(self.list.index)?.clone();
            if is_dir {
                self.open(path.clone());
                if self.folders_only {
                    return Some(path);
                }
                return None;
            }
            return Some(path);
        }
        if matches!(k.code, KeyCode::Left | KeyCode::Backspace) {
            if let Some(parent) = self.dir.parent().map(|p| p.to_path_buf()) {
                self.open(parent);
            }
        }
        None
    }
    pub fn draw(&self, p: &mut Painter, r: Rect, focused: bool) {
        let t = p.t;
        p.border(r, true, if focused { t.primary } else { t.panel }, &short_path(&self.dir.to_string_lossy()), bold(if focused { t.primary } else { t.muted }));
        let rows: Vec<Line<'static>> = self
            .entries
            .iter()
            .map(|(name, _, is_dir)| {
                if *is_dir {
                    Line::from(vec![Span::styled("📁 ", fg(t.text)), Span::styled(name.clone(), bold(t.text))])
                } else {
                    Line::from(vec![Span::styled("📄 ", fg(t.text)), Span::styled(name.clone(), fg(t.text))])
                }
            })
            .collect();
        self.list.draw(p, Rect::new(r.x + 1, r.y + 1, r.width.saturating_sub(2), r.height.saturating_sub(2)), rows, focused, "tree");
    }
}

pub struct FilePicker {
    tree: DirTree,
}

impl FilePicker {
    pub fn new(start: PathBuf, folders_only: bool) -> Self {
        FilePicker { tree: DirTree::new(start, folders_only) }
    }
}

impl Dialog for FilePicker {
    fn width(&self) -> u16 {
        80
    }
    fn height(&self) -> Option<u16> {
        Some(32)
    }
    fn content_height(&self, _w: u16) -> u16 {
        28
    }
    fn draw(&self, p: &mut Painter, r: Rect) -> u16 {
        title(p, r.x, r.y, r.width, "Pick a file", "enter opens a folder or picks a file · esc closes");
        self.tree.draw(p, Rect::new(r.x, r.y + 2, r.width, r.height.saturating_sub(2)), true);
        r.y
    }
    fn key(&mut self, k: &KeyEvent, _cx: &mut Cx) -> Out {
        if k.code == KeyCode::Esc {
            return Out::Close(None);
        }
        match self.tree.key(k) {
            Some(path) => Out::Close(Some(Res::File(path))),
            None => Out::None,
        }
    }
    fn click(&mut self, id: &str, _cx: &mut Cx) -> Out {
        if let Some(i) = id.strip_prefix("tree:").and_then(|i| i.parse().ok()) {
            if self.tree.list.index == i {
                return self.key(&KeyEvent::from(KeyCode::Enter), _cx);
            }
            self.tree.list.index = i;
        }
        Out::None
    }
    fn any(&mut self) -> &mut dyn Any {
        self
    }
}

// ---------- cookies ----------

pub struct Cookies {
    list: ListState,
    on_cancel: bool,
}

impl Cookies {
    pub fn new() -> Self {
        Cookies { list: ListState::default(), on_cancel: false }
    }
}

impl Dialog for Cookies {
    fn width(&self) -> u16 {
        60
    }
    fn content_height(&self, w: u16) -> u16 {
        2 + wrap(vec![Span::raw(COOKIES_TEXT)], w as usize).len() as u16 + 1 + 10 + 1 + 3
    }
    fn draw(&self, p: &mut Painter, r: Rect) -> u16 {
        let t = p.t;
        let (x, w) = (r.x, r.width);
        let mut y = r.y;
        p.text(x, y, w, "Sign in with your browser", bold(t.text));
        y += 2;
        y += p.para(x, y, w, vec![Span::styled(COOKIES_TEXT, fg(t.text))], 6);
        p.text(x, y, w, "On a Mac you may see a Keychain prompt.", fg(t.muted.over(t.bg, 0.6)));
        y += 1;
        let rows = BROWSERS.iter().map(|b| Line::from(Span::styled(b.0.to_string(), fg(t.text)))).collect();
        framed_list(p, Rect::new(x, y, w, 10), &self.list, rows, !self.on_cancel, "browser");
        y += 11;
        action_buttons(p, x, y, w, &[("Cancel", "cancel", false)], if self.on_cancel { Some(0) } else { None });
        r.y
    }
    fn key(&mut self, k: &KeyEvent, _cx: &mut Cx) -> Out {
        match k.code {
            KeyCode::Esc => Out::Close(None),
            KeyCode::Tab | KeyCode::BackTab => {
                self.on_cancel = !self.on_cancel;
                Out::None
            }
            KeyCode::Enter if self.on_cancel => Out::Close(None),
            KeyCode::Enter => Out::Close(Some(Res::Browser(BROWSERS[self.list.index].1.into()))),
            _ => {
                self.list.key(k, BROWSERS.len(), None);
                Out::None
            }
        }
    }
    fn click(&mut self, id: &str, _cx: &mut Cx) -> Out {
        if id == "cancel" {
            return Out::Close(None);
        }
        if let Some(i) = id.strip_prefix("browser:").and_then(|i| i.parse::<usize>().ok()) {
            return Out::Close(Some(Res::Browser(BROWSERS[i].1.into())));
        }
        Out::None
    }
    fn any(&mut self) -> &mut dyn Any {
        self
    }
}

const COOKIES_TEXT: &str = "Pick the browser where you're signed in to the site. Carrot Leaf will use its cookies for this and future downloads (change it any time in Settings).";

// ---------- history ----------

pub struct History {
    entries: Vec<HistoryEntry>,
    shown: Vec<usize>,
    search: TextInput,
    list: ListState,
    in_search: bool,
}

impl History {
    pub fn new() -> Self {
        let mut entries = library::history_load();
        entries.reverse(); // newest first
        let mut h = History { entries, shown: vec![], search: TextInput::new("", "Search by title or format"), list: ListState::default(), in_search: false };
        h.fill();
        h
    }
    fn fill(&mut self) {
        let q = self.search.value.trim().to_lowercase();
        self.shown = (0..self.entries.len()).filter(|&i| q.is_empty() || self.entries[i].title.to_lowercase().contains(&q) || self.entries[i].label.to_lowercase().contains(&q)).collect();
        self.list = ListState::default();
    }
    fn current(&self) -> Option<&HistoryEntry> {
        self.shown.get(self.list.index).map(|&i| &self.entries[i])
    }
    fn existing(&self, cx: &mut Cx) -> Option<HistoryEntry> {
        let Some(e) = self.current() else {
            cx.toasts.bell();
            return None;
        };
        if !e.exists() {
            cx.toasts.warn("That file has been moved or deleted.");
            return None;
        }
        Some(e.clone())
    }
}

impl Dialog for History {
    fn width(&self) -> u16 {
        92
    }
    fn height(&self) -> Option<u16> {
        Some(34)
    }
    fn content_height(&self, _w: u16) -> u16 {
        30
    }
    fn draw(&self, p: &mut Painter, r: Rect) -> u16 {
        let t = p.t;
        let (x, w) = (r.x, r.width);
        p.text(x, r.y, w, "History", bold(t.text));
        field(p, x, r.y + 2, w, &self.search, self.in_search, "search");
        help_line(p, x, r.y + 3, w, &[("enter", "open"), ("o", "show in folder"), ("r", "get again"), ("y", "copy link"), ("del", "forget")]);
        let lr = Rect::new(x, r.y + 4, w, r.height.saturating_sub(4));
        if self.shown.is_empty() {
            let msg = if self.entries.is_empty() { "Nothing yet. Downloads and conversions show up here." } else { "Nothing matches." };
            p.text(x, lr.y, w, msg, fg(t.muted.over(t.bg, 0.6)));
            return r.y;
        }
        let dim = fg(t.muted.over(t.bg, 0.6));
        let rows = self
            .shown
            .iter()
            .map(|&i| {
                let e = &self.entries[i];
                let gone = !e.exists();
                Line::from(vec![
                    Span::styled(if e.kind == "convert" { "⟳ " } else { "↓ " }, dim),
                    Span::styled(e.title.clone(), if gone { dim.add_modifier(Modifier::CROSSED_OUT) } else { bold(t.text) }),
                    Span::raw("  "),
                    Span::styled(e.label.clone(), dim),
                    Span::raw("  "),
                    Span::styled(if gone { "moved or deleted".to_string() } else { e.when_text() }, dim),
                ])
            })
            .collect();
        framed_list(p, lr, &self.list, rows, !self.in_search, "h");
        r.y
    }
    fn key(&mut self, k: &KeyEvent, cx: &mut Cx) -> Out {
        if k.code == KeyCode::Esc {
            return Out::Close(None);
        }
        if matches!(k.code, KeyCode::Tab | KeyCode::BackTab) {
            self.in_search = !self.in_search;
            return Out::None;
        }
        if self.in_search {
            match self.search.key(k) {
                Edit::Changed => self.fill(),
                Edit::Submitted => self.in_search = false,
                _ => {}
            }
            return Out::None;
        }
        if self.list.key(k, self.shown.len(), None) {
            return Out::None;
        }
        match k.code {
            KeyCode::Enter => {
                if let Some(e) = self.existing(cx) {
                    desktop::open_file(&e.path);
                }
            }
            KeyCode::Char('o') => {
                if let Some(e) = self.existing(cx) {
                    desktop::reveal(&e.path);
                }
            }
            KeyCode::Char('r') => match self.current() {
                Some(e) if e.is_link() => return Out::Close(Some(Res::Redownload(e.source.clone()))),
                _ => cx.toasts.warn("Only downloads can be fetched again."),
            },
            KeyCode::Char('y') => {
                if let Some(e) = self.current().cloned() {
                    if !desktop::write_clipboard(&e.source) {
                        osc52(&e.source);
                    }
                    cx.toasts.info(if e.is_link() { "Copied the link." } else { "Copied the file's path." });
                } else {
                    cx.toasts.bell();
                }
            }
            KeyCode::Delete | KeyCode::Backspace => {
                if let Some(&i) = self.shown.get(self.list.index) {
                    let e = self.entries.remove(i);
                    library::history_remove(&e);
                    let keep = self.list.index;
                    self.fill();
                    self.list.index = keep;
                    self.list.clamp(self.shown.len());
                }
            }
            KeyCode::Char(c) if !c.is_control() => {
                // Typing starts a search, as the field sits right above.
                self.in_search = true;
                self.search.insert(&c.to_string());
                self.fill();
            }
            _ => {}
        }
        Out::None
    }
    fn paste(&mut self, text: &str, _cx: &mut Cx) -> Out {
        self.in_search = true;
        self.search.insert(text.trim());
        self.fill();
        Out::None
    }
    fn click(&mut self, id: &str, cx: &mut Cx) -> Out {
        if id == "search" {
            self.in_search = true;
        } else if let Some(i) = id.strip_prefix("h:").and_then(|i| i.parse().ok()) {
            self.in_search = false;
            if self.list.index == i {
                return self.key(&KeyEvent::from(KeyCode::Enter), cx);
            }
            self.list.index = i;
        }
        Out::None
    }
    fn any(&mut self) -> &mut dyn Any {
        self
    }
}

/// Copy through the terminal itself (OSC 52), for when no clipboard tool is around.
fn osc52(text: &str) {
    use std::io::Write;
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let b = text.as_bytes();
    let mut out = String::new();
    for c in b.chunks(3) {
        let n = (c[0] as u32) << 16 | (*c.get(1).unwrap_or(&0) as u32) << 8 | *c.get(2).unwrap_or(&0) as u32;
        out.push(T[(n >> 18) as usize & 63] as char);
        out.push(T[(n >> 12) as usize & 63] as char);
        out.push(if c.len() > 1 { T[(n >> 6) as usize & 63] as char } else { '=' });
        out.push(if c.len() > 2 { T[n as usize & 63] as char } else { '=' });
    }
    let _ = write!(std::io::stdout(), "\x1b]52;c;{out}\x07");
    let _ = std::io::stdout().flush();
}

// ---------- following ----------

pub struct Following {
    items: Vec<Followed>,
    list: ListState,
    syncing: bool,
}

impl Following {
    pub fn new(syncing: bool) -> Self {
        let mut f = Following { items: vec![], list: ListState::default(), syncing };
        f.refresh(None);
        f
    }
    pub fn refresh(&mut self, syncing: Option<bool>) {
        if let Some(s) = syncing {
            self.syncing = s;
        }
        self.items = library::following_load();
        self.list.clamp(self.items.len());
    }
}

impl Dialog for Following {
    fn width(&self) -> u16 {
        92
    }
    fn height(&self) -> Option<u16> {
        Some(34)
    }
    fn content_height(&self, _w: u16) -> u16 {
        30
    }
    fn draw(&self, p: &mut Painter, r: Rect) -> u16 {
        let t = p.t;
        let (x, w) = (r.x, r.width);
        let n = library::total_new(&self.items);
        let mut spans = vec![Span::styled("Following", bold(t.text))];
        if self.syncing {
            spans.push(Span::styled("  checking for new videos…", fg(t.muted.over(t.bg, 0.6))));
        } else if n > 0 {
            spans.push(Span::styled(format!("  {n} new"), bold(t.primary)));
        }
        p.line(x, r.y, w, Line::from(spans), Align::Left);
        if self.items.is_empty() {
            p.text(x, r.y + 2, w, "You're not following anything yet.", bold(t.text));
            p.para(x, r.y + 4, w, vec![Span::styled(FOLLOW_EMPTY, fg(t.muted.over(t.bg, 0.6)))], 5);
            return r.y;
        }
        help_line(p, x, r.y + 2, w, &[("enter", "get new"), ("a", "get all new"), ("s", "sync now"), ("del", "unfollow")]);
        let width = self.items.iter().map(|f| f.title.chars().count()).max().unwrap_or(0).min(28);
        let rows = self
            .items
            .iter()
            .map(|f| {
                let new = f.new.len();
                let (state, style) = if !f.error.is_empty() {
                    ("couldn't check".to_string(), fg(t.error))
                } else if new > 0 {
                    (format!("{new} new"), bold(t.text))
                } else {
                    (format!("up to date · {}", f.checked_text()), fg(t.muted.over(t.bg, 0.6)))
                };
                let title: String = f.title.chars().take(width).collect();
                Line::from(vec![
                    Span::styled(if new > 0 { "● " } else { "○ " }, if new > 0 { bold(t.text) } else { fg(t.muted) }),
                    Span::styled(super::draw::pad(&title, width), bold(t.text)),
                    Span::raw("  "),
                    Span::styled(super::draw::pad(&state, 24), style),
                    Span::styled(f.choice().label(), fg(t.muted.over(t.bg, 0.6))),
                ])
            })
            .collect();
        framed_list(p, Rect::new(x, r.y + 3, w, r.height.saturating_sub(3)), &self.list, rows, true, "f");
        r.y
    }
    fn key(&mut self, k: &KeyEvent, cx: &mut Cx) -> Out {
        if self.list.key(k, self.items.len(), None) {
            return Out::None;
        }
        match k.code {
            KeyCode::Esc => Out::Close(None),
            KeyCode::Enter => match self.items.get(self.list.index) {
                Some(f) if !f.new.is_empty() => Out::Close(Some(Res::GetNew(vec![f.url.clone()]))),
                _ => {
                    cx.toasts.info("Nothing new there yet.");
                    Out::None
                }
            },
            KeyCode::Char('a') => {
                let urls: Vec<String> = self.items.iter().filter(|f| !f.new.is_empty()).map(|f| f.url.clone()).collect();
                if urls.is_empty() {
                    cx.toasts.info("Nothing new yet.");
                    return Out::None;
                }
                Out::Close(Some(Res::GetNew(urls)))
            }
            KeyCode::Char('s') => {
                if self.syncing {
                    return Out::None;
                }
                self.syncing = true;
                Out::Main(Res::SyncNow)
            }
            KeyCode::Delete | KeyCode::Backspace => {
                if let Some(f) = self.items.get(self.list.index).cloned() {
                    library::unfollow(&f.url);
                    cx.toasts.info(&format!("Stopped following {}.", f.title));
                    self.refresh(None);
                }
                Out::None
            }
            _ => Out::None,
        }
    }
    fn click(&mut self, id: &str, cx: &mut Cx) -> Out {
        if let Some(i) = id.strip_prefix("f:").and_then(|i| i.parse().ok()) {
            if self.list.index == i {
                return self.key(&KeyEvent::from(KeyCode::Enter), cx);
            }
            self.list.index = i;
        }
        Out::None
    }
    fn any(&mut self) -> &mut dyn Any {
        self
    }
}

const FOLLOW_EMPTY: &str = "Load a channel or playlist (or any video from one) and press a on its card. Carrot then checks for new videos each time it starts, and saves them the way you chose.";

// ---------- picking playlist videos ----------

pub struct PlaylistPick {
    title: String,
    rows: Vec<(String, String)>,
    /// Each video's length in seconds, for the total.
    secs: Vec<f64>,
    truncated: Option<(usize, i64)>,
    selected: BTreeSet<usize>,
    list: ListState,
    latest: TextInput,
    focus: usize, // 0 list, 1 latest, 2 done, 3 cancel
}

impl PlaylistPick {
    pub fn new(pl: &crate::engine::probe::PlaylistInfo, selected: &BTreeSet<usize>) -> Self {
        let mut latest = TextInput::new("", "N");
        latest.digits_only = true;
        PlaylistPick {
            title: pl.title.clone(),
            rows: pl.entries.iter().map(|e| (e.title.clone(), e.duration_text())).collect(),
            secs: pl.entries.iter().map(|e| e.duration.map(|d| d as f64).unwrap_or(0.0)).collect(),
            truncated: if pl.truncated() { Some((pl.entries.len(), pl.total.unwrap_or(0))) } else { None },
            selected: selected.clone(),
            list: ListState::default(),
            latest,
            focus: 0,
        }
    }
    fn toggle(&mut self, i: usize) {
        if !self.selected.remove(&i) {
            self.selected.insert(i);
        }
    }
    fn latest_changed(&mut self) {
        if let Ok(n) = self.latest.value.parse::<usize>() {
            self.selected = (0..n.min(self.rows.len())).collect();
        }
    }
}

impl Dialog for PlaylistPick {
    fn width(&self) -> u16 {
        92
    }
    fn height(&self) -> Option<u16> {
        Some(34)
    }
    fn content_height(&self, _w: u16) -> u16 {
        30
    }
    fn draw(&self, p: &mut Painter, r: Rect) -> u16 {
        let t = p.t;
        let (x, w) = (r.x, r.width);
        let dim = fg(t.muted.over(t.bg, 0.6));
        // Header: title on the left, the shortcuts on the right (a and n are clickable).
        let hint = [("a", " all", "all"), ("n", " none", "none"), ("space", " tick", "")];
        let hint_w: u16 = hint.iter().map(|(k, l, _)| (k.len() + l.len()) as u16).sum::<u16>() + 3 * (hint.len() as u16 - 1);
        let room = w.saturating_sub(hint_w + 2);
        let name = super::draw::cut(&self.title, room.saturating_sub(13) as usize);
        p.line(x, r.y, room, Line::from(vec![Span::styled("Pick videos", bold(t.text)), Span::styled(format!("  {name}"), fg(t.muted))]), Align::Left);
        let mut hx = x + w - hint_w;
        for (k, l, id) in hint {
            p.text(hx, r.y, k.len() as u16, k, bold(t.primary));
            p.text(hx + k.len() as u16, r.y, l.len() as u16, l, fg(t.muted));
            if !id.is_empty() {
                p.click(Rect::new(hx, r.y, (k.len() + l.len()) as u16, 1), id);
            }
            hx += (k.len() + l.len()) as u16 + 3;
        }
        // The table: a frame, a column header, then rows. Columns: tick, number, title, length.
        let frame = Rect::new(x, r.y + 2, w, r.height.saturating_sub(2 + 4));
        let focused = self.focus == 0;
        p.border(frame, true, if focused { t.primary } else { t.panel }, "", fg(t.panel));
        let num_w = self.rows.len().to_string().len().max(1);
        let len_w = self.rows.iter().map(|(_, d)| super::draw::width(d)).max().unwrap_or(4).max(6);
        // Inside the frame: 1 column of list indent and 1 of scrollbar.
        let inner_w = frame.width.saturating_sub(2) as usize;
        let title_w = inner_w.saturating_sub(1 + 3 + num_w + 2 + 2 + len_w + 2);
        let cols = |a: &str, b: &str, c: &str| format!("   {a:>num_w$}  {c:<title_w$}  {b:>len_w$}");
        p.text(frame.x + 2, frame.y + 1, inner_w as u16 - 1, &cols("#", "length", "title"), dim);
        let rows = self
            .rows
            .iter()
            .enumerate()
            .map(|(i, (title, dur))| {
                let on = self.selected.contains(&i);
                let name = super::draw::pad(&super::draw::cut(title, title_w), title_w);
                Line::from(vec![
                    Span::styled(if on { " ✓ " } else { " · " }, if on { bold(t.primary) } else { dim }),
                    Span::styled(format!("{:>num_w$}  ", i + 1), dim),
                    Span::styled(name, if on { fg(t.text) } else { dim }),
                    Span::styled(format!("  {dur:>len_w$}"), fg(t.muted)),
                ])
            })
            .collect();
        self.list.draw(p, Rect::new(frame.x + 1, frame.y + 2, frame.width.saturating_sub(2), frame.height.saturating_sub(3)), rows, focused, "e");
        // Under the table: the "first N" filter, then the count and the buttons on one row.
        let mut y = frame.bottom() + 1;
        p.text(x, y, 15, "Only the first", fg(t.muted));
        field(p, x + 15, y, 8, &self.latest, self.focus == 1, "latest");
        p.text(x + 25, y, w.saturating_sub(25), "videos · newest first on a channel", dim);
        y += 2;
        let total: f64 = self.selected.iter().filter_map(|&i| self.secs.get(i)).sum();
        let mut spans = vec![Span::styled(self.selected.len().to_string(), bold(t.text)), Span::styled(format!(" of {} picked", self.rows.len()), fg(t.text))];
        if total > 0.0 {
            spans.push(Span::styled(format!("  ·  {}", long_duration(total)), fg(t.muted)));
        }
        if let Some((n, all)) = self.truncated {
            spans.push(Span::styled(format!("  ·  first {n} of {all} listed"), dim));
        }
        p.line(x, y, w, Line::from(spans), Align::Left);
        let done_w = super::draw::width("  Done  ") as u16;
        let cancel_w = super::draw::width("  Cancel  ") as u16;
        let bx = (x + w).saturating_sub(done_w + 2 + cancel_w);
        chip(p, bx, y, "Cancel", self.focus == 3, false, "cancel");
        let done = if self.focus == 2 { Style::new().fg(t.bg.color()).bg(t.primary_l2.color()) } else { Style::new().fg(t.bg.color()).bg(t.primary.color()) };
        p.text(bx + cancel_w + 2, y, done_w, "  Done  ", done.add_modifier(Modifier::BOLD));
        p.click(Rect::new(bx + cancel_w + 2, y, done_w, 1), "go");
        r.y
    }
    fn key(&mut self, k: &KeyEvent, _cx: &mut Cx) -> Out {
        let done = || Out::Close(Some(Res::Picked(self.selected.clone())));
        match k.code {
            KeyCode::Esc => return Out::Close(None),
            KeyCode::Tab => {
                self.focus = (self.focus + 1) % 4;
                return Out::None;
            }
            KeyCode::BackTab => {
                self.focus = (self.focus + 3) % 4;
                return Out::None;
            }
            _ => {}
        }
        match self.focus {
            0 => {
                if self.list.key(k, self.rows.len(), None) {
                    return Out::None;
                }
                match k.code {
                    KeyCode::Char(' ') => self.toggle(self.list.index),
                    KeyCode::Char('a') => self.selected = (0..self.rows.len()).collect(),
                    KeyCode::Char('n') => self.selected.clear(),
                    KeyCode::Enter => return done(),
                    _ => {}
                }
            }
            1 => match self.latest.key(k) {
                Edit::Changed => self.latest_changed(),
                Edit::Submitted => return done(),
                _ => {}
            },
            2 if press(k) => return done(),
            3 if press(k) => return Out::Close(None),
            _ => {}
        }
        Out::None
    }
    fn click(&mut self, id: &str, _cx: &mut Cx) -> Out {
        match id {
            "go" => return Out::Close(Some(Res::Picked(self.selected.clone()))),
            "all" => self.selected = (0..self.rows.len()).collect(),
            "none" => self.selected.clear(),
            "cancel" => return Out::Close(None),
            "latest" => self.focus = 1,
            _ => {
                if let Some(i) = id.strip_prefix("e:").and_then(|i| i.parse().ok()) {
                    self.focus = 0;
                    self.list.index = i;
                    self.toggle(i);
                }
            }
        }
        Out::None
    }
    fn any(&mut self) -> &mut dyn Any {
        self
    }
}

// ---------- a video inside a mix or playlist ----------

/// A small question: just this video, or pick from the mix/playlist it came with?
pub struct ListAsk {
    mix: bool,
    focus: usize, // 0 just this video, 1 pick from the list
}

impl ListAsk {
    pub fn new(mix: bool) -> Self {
        ListAsk { mix, focus: 0 }
    }
    fn what(&self) -> &'static str {
        if self.mix { "mix" } else { "playlist" }
    }
}

impl Dialog for ListAsk {
    fn width(&self) -> u16 {
        62
    }
    fn content_height(&self, w: u16) -> u16 {
        2 + wrap(vec![Span::raw(self.body())], w as usize).len() as u16 + 1 + 3 + 2
    }
    fn draw(&self, p: &mut Painter, r: Rect) -> u16 {
        let t = p.t;
        let (x, w) = (r.x, r.width);
        let mut y = r.y;
        p.text(x, y, w, &format!("This video is part of a {}", self.what()), bold(t.text));
        y += 2;
        y += p.para(x, y, w, vec![Span::styled(self.body(), fg(t.text))], 4);
        y += 1;
        let pick = format!("Pick from the {}", self.what());
        action_buttons(p, x, y, w, &[("Just this video", "one", self.focus == 0), (&pick, "all", self.focus == 1)], Some(self.focus));
        y += 4;
        p.text(x, y, w, "←→ choose · enter · esc cancels", fg(t.muted.over(t.bg, 0.6)));
        r.y
    }
    fn key(&mut self, k: &KeyEvent, _cx: &mut Cx) -> Out {
        match k.code {
            KeyCode::Esc => Out::Close(None),
            KeyCode::Left | KeyCode::Right | KeyCode::Tab | KeyCode::BackTab | KeyCode::Up | KeyCode::Down => {
                self.focus = 1 - self.focus;
                Out::None
            }
            KeyCode::Enter | KeyCode::Char(' ') => Out::Close(Some(Res::WholeList(self.focus == 1))),
            _ => Out::None,
        }
    }
    fn click(&mut self, id: &str, _cx: &mut Cx) -> Out {
        match id {
            "one" => Out::Close(Some(Res::WholeList(false))),
            "all" => Out::Close(Some(Res::WholeList(true))),
            _ => Out::None,
        }
    }
    fn any(&mut self) -> &mut dyn Any {
        self
    }
}

impl ListAsk {
    fn body(&self) -> String {
        format!("Download just this one, or open the {} and tick the videos you want? Everything starts ticked.", self.what())
    }
}

// ---------- supported sites ----------

pub struct Sites {
    all: Option<Vec<(String, bool)>>,
    search: TextInput,
    list: ListState,
    in_list: bool,
}

impl Sites {
    pub fn new(tx: &Sender<Msg>) -> Self {
        super::work(
            tx,
            || {
                let out = std::process::Command::new(deps::yt_dlp()).arg("--list-extractors").output();
                let text = out.map(|o| String::from_utf8_lossy(&o.stdout).to_string()).unwrap_or_default();
                let mut works: std::collections::BTreeMap<String, bool> = Default::default();
                for line in text.lines() {
                    let line = line.trim();
                    if line.is_empty() || line == "generic" {
                        continue;
                    }
                    let broken = line.ends_with("(CURRENTLY BROKEN)");
                    let name = line.trim_end_matches("(CURRENTLY BROKEN)").trim().split(':').next().unwrap_or("").to_string();
                    if name.is_empty() {
                        continue;
                    }
                    let e = works.entry(name).or_insert(false);
                    *e = *e || !broken;
                }
                let mut v: Vec<(String, bool)> = works.into_iter().collect();
                v.sort_by_key(|a| a.0.to_lowercase());
                v
            },
            Msg::Sites,
        );
        Sites { all: None, search: TextInput::new("", "Search sites, or paste a link to check it"), list: ListState::default(), in_list: false }
    }
    fn is_link(q: &str) -> bool {
        q.contains('/') || q.starts_with("http:") || q.starts_with("https:") || q.starts_with("www.")
    }
    /// The site names a link's domain points at: "www.youtube.com/..." → youtube.
    fn matches_link(&self, q: &str) -> Vec<(String, bool)> {
        let q = q.trim();
        let rest = q.split("://").last().unwrap_or(q);
        let host = rest.split('/').next().unwrap_or("").to_lowercase();
        let labels: Vec<&str> = host.split('.').filter(|l| !["www", "m", "com", "org", "net", "tv", "co", "uk", "io", "be"].contains(l)).collect();
        let Some(all) = &self.all else { return vec![] };
        let mut out: Vec<(String, bool)> = all.iter().filter(|(n, _)| labels.iter().any(|l| n.to_lowercase() == *l)).cloned().collect();
        if out.is_empty() {
            out = all.iter().filter(|(n, _)| labels.iter().any(|l| l.len() > 3 && n.to_lowercase().starts_with(l))).cloned().collect();
        }
        if host.contains("youtu.be") {
            out.extend(all.iter().filter(|(n, _)| n == "youtube").cloned());
        }
        out
    }
    fn rows(&self) -> (Vec<(String, bool)>, Line<'static>, bool) {
        let t = theme::by_name("carrot");
        let _ = t;
        let q = self.search.value.trim().to_string();
        let Some(all) = &self.all else { return (vec![], Line::from("Loading yt-dlp's site list…"), false) };
        if Self::is_link(&q) {
            let m = self.matches_link(&q);
            let found = !m.is_empty();
            let text = if found { format!("✓ Supported  by {}", m[0].0) } else { "No dedicated support. Carrot Leaf will still try: many pages with a plain video work.".into() };
            return (m, Line::from(text), found);
        }
        let low = q.to_lowercase();
        let rows: Vec<(String, bool)> = all.iter().filter(|(n, _)| n.to_lowercase().contains(&low)).cloned().collect();
        let text = if q.is_empty() {
            "Type to filter. Sites marked broken may not work right now.".to_string()
        } else if !rows.is_empty() {
            format!("{} match{}", group(rows.len()), if rows.len() != 1 { "es" } else { "" })
        } else {
            format!("No site called “{q}”. Paste a link to check it directly.")
        };
        (rows, Line::from(text), false)
    }
}

fn group(n: usize) -> String {
    let s = n.to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(c);
    }
    out
}

/// The rail's letter for a site name: A–Z, or # for anything else.
fn site_letter(name: &str) -> char {
    match name.chars().next().map(|c| c.to_ascii_uppercase()) {
        Some(c) if c.is_ascii_uppercase() => c,
        _ => '#',
    }
}

const RAIL: &str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ#";
/// Rail columns: letters sit in pairs so all 27 fit beside a short list.
const RAIL_W: u16 = 6;

impl Sites {
    /// Move the highlight to the first site under `letter`, if any.
    fn jump(&mut self, letter: char) -> bool {
        let rows = self.rows().0;
        match rows.iter().position(|(n, _)| site_letter(n) == letter) {
            Some(i) => {
                self.list.index = i;
                self.list.show_at_top();
                self.in_list = true;
                true
            }
            None => false,
        }
    }
}

impl Dialog for Sites {
    fn width(&self) -> u16 {
        74
    }
    fn height(&self) -> Option<u16> {
        Some(34)
    }
    fn content_height(&self, _w: u16) -> u16 {
        30
    }
    fn draw(&self, p: &mut Painter, r: Rect) -> u16 {
        let t = p.t;
        let (x, w) = (r.x, r.width);
        let dim = fg(t.muted.over(t.bg, 0.6));
        let total = self.all.as_ref().map(|a| a.len()).unwrap_or(0);
        title(p, x, r.y, w, "Supported sites", &if total > 0 { format!("{} sites via yt-dlp", group(total)) } else { "via yt-dlp".into() });
        // Search box.
        let focused = !self.in_list;
        p.border(Rect::new(x, r.y + 2, w, 3), false, if focused { t.primary } else { t.panel }, "", fg(t.panel));
        p.text(x + 2, r.y + 3, 1, "⌕", bold(if focused { t.primary } else { t.muted }));
        self.search.draw(p, x + 4, r.y + 3, w.saturating_sub(6), t.bg, t.text, focused);
        p.click(Rect::new(x + 1, r.y + 3, w.saturating_sub(2), 1), "search");
        let (rows, line, ok) = self.rows();
        let q = self.search.value.trim();
        let style = if Self::is_link(q) && ok {
            bold(t.success)
        } else if Self::is_link(q) && self.all.is_some() {
            bold(t.warning)
        } else {
            dim
        };
        let text: String = line.spans.iter().map(|s| s.content.to_string()).collect();
        p.text(x, r.y + 5, w, &text, style);
        // The A–Z rail: letters with sites in the list are clickable; the current one is solid.
        let list_y = r.y + 7;
        let list_h = r.height.saturating_sub(7 + 2);
        let present: BTreeSet<char> = rows.iter().map(|(n, _)| site_letter(n)).collect();
        let current = rows.get(self.list.index).map(|(n, _)| site_letter(n));
        for (i, c) in RAIL.chars().enumerate() {
            let (cx, cy) = (x + (i as u16 % 2) * 3, list_y + 1 + i as u16 / 2);
            if cy >= list_y + list_h {
                break;
            }
            let style = if Some(c) == current {
                Style::new().fg(t.bg.color()).bg(t.primary.color()).add_modifier(Modifier::BOLD)
            } else if present.contains(&c) {
                fg(t.text)
            } else {
                fg(t.muted.over(t.bg, 0.35))
            };
            p.text(cx, cy, 3, &format!(" {c} "), style);
            if present.contains(&c) {
                p.click(Rect::new(cx, cy, 3, 1), format!("rail:{c}"));
            }
        }
        let broken = fg(t.error);
        let n_broken = rows.iter().filter(|(_, ok)| !ok).count();
        let lines = rows
            .into_iter()
            .map(|(n, ok)| if ok { Line::from(Span::styled(n, fg(t.text))) } else { Line::from(vec![Span::styled(n, fg(t.text)), Span::styled("  ● broken", broken)]) })
            .collect();
        framed_list(p, Rect::new(x + RAIL_W + 1, list_y, w.saturating_sub(RAIL_W + 1), list_h), &self.list, lines, self.in_list, "s");
        // Footer.
        let fy = list_y + list_h + 1;
        p.text(x, fy, w, "↑↓ move · a–z jump · tab search · esc close", dim);
        if n_broken > 0 {
            let note = format!("{n_broken} marked broken");
            let nw = super::draw::width(&note) as u16;
            p.text((x + w).saturating_sub(nw), fy, nw, &note, dim);
        }
        r.y
    }
    fn key(&mut self, k: &KeyEvent, _cx: &mut Cx) -> Out {
        match k.code {
            KeyCode::Esc => return Out::Close(None),
            KeyCode::Tab | KeyCode::BackTab => {
                self.in_list = !self.in_list;
                return Out::None;
            }
            _ => {}
        }
        let n = self.rows().0.len();
        if self.in_list {
            if let KeyCode::Char(c) = k.code {
                let c = c.to_ascii_uppercase();
                if RAIL.contains(c) {
                    self.jump(c);
                    return Out::None;
                }
            }
            self.list.key(k, n, None);
        } else if !self.list.key(k, n, None) && self.search.key(k) == Edit::Changed {
            self.list = ListState::default();
        }
        Out::None
    }
    fn paste(&mut self, text: &str, _cx: &mut Cx) -> Out {
        self.in_list = false;
        self.search.insert(text.trim());
        self.list = ListState::default();
        Out::None
    }
    fn click(&mut self, id: &str, _cx: &mut Cx) -> Out {
        if id == "search" {
            self.in_list = false;
        } else if let Some(c) = id.strip_prefix("rail:").and_then(|c| c.chars().next()) {
            self.jump(c);
        } else if let Some(i) = id.strip_prefix("s:").and_then(|i| i.parse().ok()) {
            self.in_list = true;
            self.list.index = i;
        }
        Out::None
    }
    fn msg(&mut self, m: &Msg, _cx: &mut Cx) -> bool {
        if let Msg::Sites(v) = m {
            self.all = Some(v.clone());
            return true;
        }
        false
    }
    fn any(&mut self) -> &mut dyn Any {
        self
    }
}

// ---------- search what was said ----------

const CONTEXT: usize = 4;
const CLIP_AROUND: f64 = 15.0;

pub struct Search {
    library: Option<Vec<Transcript>>,
    hits: Vec<Hit>,
    input: TextInput,
    list: ListState,
    in_list: bool,
}

impl Search {
    pub fn new(tx: &Sender<Msg>) -> Self {
        super::work(tx, transcripts::load_all, Msg::Transcripts);
        Search { library: None, hits: vec![], input: TextInput::new("", "Words in any order, or \"an exact phrase\""), list: ListState::default(), in_list: false }
    }
    fn search(&mut self) {
        self.hits = transcripts::search(&self.input.value, self.library.as_deref().unwrap_or(&[]), 200);
        self.list = ListState::default();
    }
    fn current(&self) -> Option<(&Transcript, &Hit)> {
        let hit = self.hits.get(self.list.index)?;
        Some((&self.library.as_ref()?[hit.transcript], hit))
    }
    fn existing(&self, cx: &mut Cx) -> Option<(String, f64)> {
        let Some((tr, hit)) = self.current() else {
            cx.toasts.bell();
            return None;
        };
        if !std::path::Path::new(&tr.path).exists() {
            cx.toasts.warn("That file has been moved or deleted.");
            return None;
        }
        Some((tr.path.clone(), tr.cues[hit.index].start))
    }
}

impl Dialog for Search {
    fn width(&self) -> u16 {
        110
    }
    fn height(&self) -> Option<u16> {
        Some(40)
    }
    fn content_height(&self, _w: u16) -> u16 {
        36
    }
    fn draw(&self, p: &mut Painter, r: Rect) -> u16 {
        let t = p.t;
        let (x, w) = (r.x, r.width);
        let dim = fg(t.muted.over(t.bg, 0.6));
        p.text(x, r.y, w, "Search what was said", bold(t.text));
        field(p, x, r.y + 2, w, &self.input, !self.in_list, "input");
        help_line(p, x, r.y + 3, w, &[("enter", "play from there"), ("c", "clip 30s around it"), ("o", "show in folder")]);
        let n = self.library.as_ref().map(|l| l.len()).unwrap_or(0);
        let status = if self.library.is_none() {
            "Loading…".to_string()
        } else if n == 0 {
            "Nothing to search yet. Videos you download from now on are added, as long as they have captions (most YouTube videos do).".into()
        } else if self.input.value.trim().is_empty() {
            format!("Searching {n} video{}.", if n != 1 { "s" } else { "" })
        } else if self.hits.is_empty() {
            format!("Not said in any of your {n} videos.")
        } else {
            let videos: BTreeSet<usize> = self.hits.iter().map(|h| h.transcript).collect();
            format!("{} moment{} in {} video{}.", self.hits.len(), if self.hits.len() != 1 { "s" } else { "" }, videos.len(), if videos.len() != 1 { "s" } else { "" })
        };
        let bottom = r.y + r.height;
        p.para(x, bottom.saturating_sub(2), w, vec![Span::styled(status, dim)], 2);
        if self.hits.is_empty() {
            return r.y;
        }
        let lib = self.library.as_ref().unwrap();
        let ph = bottom.saturating_sub(2).saturating_sub(r.y + 5);
        let lw = w * 2 / 5;
        let rows = self
            .hits
            .iter()
            .map(|h| {
                let tr = &lib[h.transcript];
                Line::from(vec![Span::styled(format!("{:>7}", time_text(Some(tr.cues[h.index].start))), bold(t.text)), Span::raw("  "), Span::styled(tr.title.clone(), fg(t.text))])
            })
            .collect();
        framed_list(p, Rect::new(x, r.y + 5, lw, ph), &self.list, rows, self.in_list, "hit");
        // The transcript around the hit.
        if let Some((tr, hit)) = self.current() {
            let cx = x + lw + 2;
            let cw = w.saturating_sub(lw + 2);
            let mut y = r.y + 5;
            y += p.para(cx, y, cw, vec![Span::styled(tr.title.clone(), bold(t.text))], 2) + 1;
            let words = transcripts::needles(&self.input.value);
            let lo = hit.index.saturating_sub(CONTEXT);
            let hi = (hit.index + CONTEXT + 1).min(tr.cues.len());
            for i in lo..hi {
                let cue = &tr.cues[i];
                let here = i == hit.index;
                let mut spans = vec![Span::styled(format!("{:>7}  ", time_text(Some(cue.start))), if here { bold(t.text) } else { dim })];
                if here {
                    spans.extend(highlight(&cue.text, &words, fg(t.text), bold(t.text).add_modifier(Modifier::REVERSED)));
                } else {
                    spans.push(Span::styled(cue.text.clone(), dim));
                }
                let lines = wrap(spans, cw as usize);
                for l in lines {
                    if y >= bottom.saturating_sub(2) {
                        break;
                    }
                    p.line(cx, y, cw, l, Align::Left);
                    y += 1;
                }
            }
        }
        r.y
    }
    fn key(&mut self, k: &KeyEvent, cx: &mut Cx) -> Out {
        match k.code {
            KeyCode::Esc => return Out::Close(None),
            KeyCode::Tab | KeyCode::BackTab => {
                self.in_list = !self.in_list && !self.hits.is_empty();
                return Out::None;
            }
            _ => {}
        }
        if !self.in_list {
            match self.input.key(k) {
                Edit::Changed => self.search(),
                Edit::Submitted => self.in_list = !self.hits.is_empty(),
                Edit::Ignored => {
                    self.list.key(k, self.hits.len(), None);
                }
                Edit::None => {}
            }
            return Out::None;
        }
        if self.list.key(k, self.hits.len(), None) {
            return Out::None;
        }
        match k.code {
            KeyCode::Enter => {
                if let Some((path, at)) = self.existing(cx) {
                    if !desktop::play_at(&path, at) {
                        cx.toasts.info(&format!("Opened it. Skip to {}.", time_text(Some(at))));
                    }
                }
            }
            KeyCode::Char('c') => {
                if let Some((path, at)) = self.existing(cx) {
                    return Out::Close(Some(Res::ClipFile(path, (at - CLIP_AROUND).max(0.0), at + CLIP_AROUND)));
                }
            }
            KeyCode::Char('o') => {
                if let Some((path, _)) = self.existing(cx) {
                    desktop::reveal(&path);
                }
            }
            _ => {}
        }
        Out::None
    }
    fn paste(&mut self, text: &str, _cx: &mut Cx) -> Out {
        self.in_list = false;
        self.input.insert(text.trim());
        self.search();
        Out::None
    }
    fn click(&mut self, id: &str, cx: &mut Cx) -> Out {
        if id == "input" {
            self.in_list = false;
        } else if let Some(i) = id.strip_prefix("hit:").and_then(|i| i.parse().ok()) {
            self.in_list = true;
            if self.list.index == i {
                return self.key(&KeyEvent::from(KeyCode::Enter), cx);
            }
            self.list.index = i;
        }
        Out::None
    }
    fn msg(&mut self, m: &Msg, _cx: &mut Cx) -> bool {
        if let Msg::Transcripts(lib) = m {
            self.library = Some(lib.clone());
            self.search();
            return true;
        }
        false
    }
    fn any(&mut self) -> &mut dyn Any {
        self
    }
}

/// `text` with every case-insensitive occurrence of `words` in `hi`.
fn highlight(text: &str, words: &[String], normal: Style, hi: Style) -> Vec<Span<'static>> {
    let lower = text.to_lowercase();
    if lower.len() != text.len() {
        return vec![Span::styled(text.to_string(), normal)];
    }
    let mut marks = vec![false; text.len()];
    for w in words.iter().filter(|w| !w.is_empty()) {
        let w = w.to_lowercase();
        let mut from = 0;
        while let Some(i) = lower[from..].find(&w) {
            let s = from + i;
            for m in marks.iter_mut().skip(s).take(w.len()) {
                *m = true;
            }
            from = s + w.len().max(1);
        }
    }
    let mut out = vec![];
    let mut cur = String::new();
    let mut cur_on = false;
    for (i, ch) in text.char_indices() {
        let on = marks[i];
        if on != cur_on && !cur.is_empty() {
            out.push(Span::styled(std::mem::take(&mut cur), if cur_on { hi } else { normal }));
        }
        cur_on = on;
        cur.push(ch);
    }
    if !cur.is_empty() {
        out.push(Span::styled(cur, if cur_on { hi } else { normal }));
    }
    out
}

// ---------- themes ----------

pub struct Themes {
    list: ListState,
}

impl Themes {
    pub fn new(current: &str) -> Self {
        Themes { list: ListState::new(theme::themes().iter().position(|t| t.name == current).unwrap_or(0)) }
    }
}

impl Dialog for Themes {
    fn width(&self) -> u16 {
        100
    }
    fn content_height(&self, _w: u16) -> u16 {
        2 + PREVIEW_H + 2
    }
    fn draw(&self, p: &mut Painter, r: Rect) -> u16 {
        let t = p.t;
        title(p, r.x, r.y, r.width, "Themes", "");
        let list_w = 30.min(r.width / 2);
        let n = theme::themes().len() as u16;
        let rows = theme::themes()
            .iter()
            .map(|th| Line::from(vec![Span::styled("■ ", fg(th.primary)), Span::styled(th.label.to_string(), fg(t.text)), Span::styled(if th.dark { "" } else { "  light" }, fg(t.muted))]))
            .collect();
        framed_list(p, Rect::new(r.x, r.y + 2, list_w, n + 2), &self.list, rows, true, "th");
        // The app already wears the highlighted theme, so the preview just draws in it.
        let px = r.x + list_w + 2;
        preview(p, Rect::new(px, r.y + 2, r.width.saturating_sub(list_w + 2), PREVIEW_H));
        p.text(r.x, r.y + 2 + PREVIEW_H + 1, r.width, "↑↓ try · enter keeps · esc goes back", fg(t.muted.over(t.bg, 0.6)));
        r.y
    }
    fn key(&mut self, k: &KeyEvent, _cx: &mut Cx) -> Out {
        match k.code {
            KeyCode::Esc => Out::Close(None),
            KeyCode::Enter => Out::Close(Some(Res::Theme(theme::themes()[self.list.index].name.into()))),
            _ => {
                if self.list.key(k, theme::themes().len(), None) {
                    return Out::Main(Res::Theme(theme::themes()[self.list.index].name.into()));
                }
                Out::None
            }
        }
    }
    fn click(&mut self, id: &str, _cx: &mut Cx) -> Out {
        if let Some(i) = id.strip_prefix("th:").and_then(|i| i.parse::<usize>().ok()) {
            if i == self.list.index {
                return Out::Close(Some(Res::Theme(theme::themes()[i].name.into())));
            }
            self.list.index = i;
            return Out::Main(Res::Theme(theme::themes()[i].name.into()));
        }
        Out::None
    }
    fn any(&mut self) -> &mut dyn Any {
        self
    }
}

const PREVIEW_H: u16 = 21;

/// `13 h 05 m`, or `22 min` under an hour.
fn long_duration(secs: f64) -> String {
    let m = (secs / 60.0).round() as i64;
    if m >= 60 { format!("{} h {:02} m", m / 60, m % 60) } else { format!("{m} min") }
}

/// A small sample of the main screen in the current theme: logo, tabs, link box, a
/// progress bar, the palette and the theme's line style.
fn preview(p: &mut Painter, r: Rect) {
    let t = p.t;
    if r.width < 36 {
        return;
    }
    p.border(r, true, t.panel_l1, "preview", fg(t.muted));
    let (x, w) = (r.x + 3, r.width - 6);
    let mut y = r.y + 2;
    // Logo.
    for line in super::logo::render(w as usize, &super::mascot::logo_shades(t), super::mascot::logo_shadow(t)) {
        p.line(x, y, w, line, Align::Center);
        y += 1;
    }
    y += 1;
    // Tabs.
    let mut tx = x;
    for (label, active) in [("Download", true), ("Convert files", false)] {
        let text = match (t.look.tabs, active) {
            (theme::TabStyle::Bracket, true) => format!("[ {label} ]"),
            (theme::TabStyle::Bracket, false) => format!("  {label}  "),
            _ => format!(" {label} "),
        };
        let tw = super::draw::width(&text) as u16;
        let style = match (t.look.tabs, active) {
            (_, false) => fg(t.muted),
            (theme::TabStyle::Chip, true) => Style::new().fg(t.bg.color()).bg(t.primary.color()).add_modifier(Modifier::BOLD),
            (_, true) => bold(t.primary),
        };
        p.text(tx, y, tw, &text, style);
        if active && t.look.tabs == theme::TabStyle::Underline {
            p.text(tx, y + 1, tw, &"▔".repeat(tw as usize), fg(t.primary));
        }
        tx += tw + 2;
    }
    y += 2;
    // Link box.
    p.border(Rect::new(x, y, w, 3), false, t.primary, "url", bold(t.primary));
    p.text(x + 2, y + 1, 1, t.look.prompt, bold(t.primary));
    p.text(x + 4, y + 1, w.saturating_sub(6), "https://youtu.be/…", fg(t.muted));
    y += 4;
    // Progress.
    let bar_w = w.saturating_sub(6);
    super::widgets::block_bar(p, x, y, bar_w, Some(0.64), t.primary, t.panel);
    p.text(x + bar_w + 1, y, 5, " 64%", fg(t.text));
    y += 2;
    // Palette and style.
    let mut cx = x;
    for (c, name) in [(t.primary, "primary"), (t.accent, "accent"), (t.success, "done"), (t.warning, "warning"), (t.error, "error")] {
        p.text(cx, y, 2, "■", fg(c));
        p.text(cx + 2, y, 8, name, fg(t.muted));
        cx += 2 + name.len() as u16 + 2;
    }
    y += 1;
    let style = format!("{} lines · {} · {}", t.look.frame.name(), t.look.tabs.name(), t.look.logo.name());
    p.text(x, y, w, &style, fg(t.muted.over(t.bg, 0.6)));
}

#[allow(dead_code)]
fn _unused(p: &mut Painter, r: Rect) {
    panel(p, r, "", false);
    let _ = inner(r);
}
