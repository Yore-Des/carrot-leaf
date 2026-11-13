//! The main screen: logo, status line, tabs, the URL bar with its video card and download
//! list, and the Convert files tab with its own list and queue.

use super::convert::{self, ConvertDialog, ConvertOptions};
use super::dialogs;
use super::draw::{bold, fg, on, width, wrap, Align, Painter};
use super::fmt::{duration_text, human_eta, human_size, short_path};
use super::mascot::{self, Mascot};
use super::theme::{self, Rgb, TabStyle, Theme};
use super::widgets::{block_bar, flat_button, loading_dots, Edit, ListState, Picture, Severity, TextInput, Toasts};
use super::{draw_dialog, logo, work, Cx, Dialog, Msg, Out, Purpose, Res};
use crate::config::{self, Settings};
use crate::engine::errors::{self, FriendlyError, Kind};
use crate::engine::local::{self, FileInfo, Media};
use crate::engine::options::{download_choice, safe_name, Choice, Extras};
use crate::engine::probe::{self, PlaylistInfo, Probed, VideoInfo};
use crate::engine::runner::{self, Job, Progress, State};
use crate::engine::{deps, desktop, update};
use crate::library::{self, HistoryEntry};
use crossterm::event::{Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEventKind};
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use regex::Regex;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::mpsc::Sender;
use std::sync::LazyLock;
use std::time::{Duration, Instant};

static URL_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)^\s*(https?://)?([\w-]+\.)+[a-z]{2,}(/\S*)?\s*$").unwrap());
const PROGRESS_INTERVAL: Duration = Duration::from_millis(150);
const NOTIFY_AFTER: f64 = 20.0;
const LEADER: usize = 10;

const EMPTY_HINT: &str = "YouTube and 1,300+ other sites work, playlists and channels too. Enter loads a link you typed.";
const FILES_HINT: &str = "Drag files from Finder or Explorer into this window, or press f to browse. Video, audio and images all work, and you can add several at once.";

pub fn looks_like_url(text: &str) -> bool {
    URL_RE.is_match(text) && text.contains('.')
}

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Tab {
    Download,
    Files,
}

#[derive(Clone, PartialEq, Debug)]
enum Focus {
    Url,
    Button(&'static str),
    Cancel(u64),
    FileInput,
    FileList,
    FileButton(&'static str),
}

enum Card {
    Empty,
    Loading(String),
    Error(FriendlyError),
    Video(Box<VideoInfo>, String),
    Playlist(Box<PlaylistInfo>),
}

#[derive(Default)]
struct FilesPane {
    input: TextInput,
    files: Vec<FileInfo>,
    list: ListState,
    jobs: Vec<Job>,
    running: Option<u64>,
    last_file: Option<String>,
    reading: usize,
    status: String,
    pending_clips: HashMap<PathBuf, (f64, f64)>,
    scroll: usize,
}

pub struct App {
    pub theme: &'static Theme,
    mascot: Mascot,
    pub settings: Settings,
    tx: Sender<Msg>,
    pub toasts: Toasts,
    pub quit: bool,
    pub dirty: bool,
    tick: u64,
    clicks: Vec<(Rect, String)>,
    /// The click target under the mouse, for hover highlights.
    hover: Option<String>,
    size: (u16, u16),
    modals: Vec<(Box<dyn Dialog>, Purpose)>,

    tab: Tab,
    focus: Focus,
    url: TextInput,
    card: Card,
    thumb: Picture,
    follow: Option<bool>,
    picked: BTreeSet<usize>,
    loading_url: Option<String>,
    /// The answer to "just this video, or the whole list?" for the last link that asked.
    list_mode: Option<(String, bool)>,
    /// Open the picker once the list that was asked for arrives.
    pick_next: bool,
    last_url: Option<String>,
    probe_token: u64,
    debounce: Option<(Instant, String)>,
    jobs: Vec<Job>,
    active: Vec<u64>,
    paused: bool,
    last_file: Option<String>,
    clipboard_url: Option<String>,
    seen_clipboard: HashSet<String>,
    app_focused: Option<bool>,
    batch: Vec<u64>,
    batch_started: Option<Instant>,
    new_videos: usize,
    syncing: bool,
    update_version: Option<String>,
    ytdlp_version: String,
    getting_ffmpeg: bool,
    queue_scroll: usize,
    files: FilesPane,
}

fn throttled(tx: Sender<Msg>, id: u64) -> impl FnMut(Progress) {
    let mut last = Instant::now() - PROGRESS_INTERVAL;
    let mut step: Option<(State, String)> = None;
    move |p: Progress| {
        let now = Instant::now();
        let this = (p.state, p.status.clone());
        let finished = p.fraction.map(|f| f >= 1.0).unwrap_or(false);
        if step.as_ref() == Some(&this) && !finished && now - last < PROGRESS_INTERVAL {
            return;
        }
        last = now;
        step = Some(this);
        let _ = tx.send(Msg::Progress { id, p });
    }
}

impl App {
    pub fn new(settings: Settings, tx: Sender<Msg>, size: (u16, u16)) -> Self {
        let theme = theme::by_name(&settings.theme);
        App {
            theme,
            settings,
            tx,
            toasts: Toasts::default(),
            quit: false,
            dirty: true,
            tick: 0,
            clicks: vec![],
            hover: None,
            size,
            modals: vec![],
            tab: Tab::Download,
            focus: Focus::Url,
            url: TextInput::new("", "Paste a video link…  (YouTube, Vimeo, SoundCloud, X, …)"),
            card: Card::Empty,
            thumb: Picture::default(),
            follow: None,
            picked: BTreeSet::new(),
            loading_url: None,
            list_mode: None,
            pick_next: false,
            mascot: Mascot::new(),
            last_url: None,
            probe_token: 0,
            debounce: None,
            jobs: vec![],
            active: vec![],
            paused: false,
            last_file: None,
            clipboard_url: None,
            seen_clipboard: HashSet::new(),
            app_focused: None,
            batch: vec![],
            batch_started: None,
            new_videos: 0,
            syncing: false,
            update_version: None,
            ytdlp_version: String::new(),
            getting_ffmpeg: false,
            queue_scroll: 0,
            files: FilesPane { input: TextInput::new("", "Drop files here, or press f to browse"), ..Default::default() },
        }
    }

    pub fn start(&mut self, initial: String) {
        let paths = if initial.is_empty() { vec![] } else { local::parse_paths(&initial) };
        if !paths.is_empty() {
            self.add_files(paths); // `carrot clip.mov` opens the Convert files tab
        } else if !initial.is_empty() {
            self.url.set(&initial);
            self.load(&initial);
        } else {
            self.check_clipboard();
        }
        self.check_for_update();
        self.prefetch_ffmpeg();
        let items = library::following_load();
        self.new_videos = library::total_new(&items);
        if !items.is_empty() && library::sync_due(&items) {
            self.sync_following();
        }
    }

    pub fn shutdown(&mut self) {
        for j in self.jobs.iter().chain(self.files.jobs.iter()) {
            if !j.state.finished() {
                j.cancel();
            }
        }
    }

    fn cx(&mut self) -> (Cx<'_>, &mut Vec<(Box<dyn Dialog>, Purpose)>) {
        (Cx { settings: &self.settings, tx: &self.tx, toasts: &mut self.toasts }, &mut self.modals)
    }

    fn bell(&mut self) {
        self.toasts.bell();
    }

    pub fn save_settings(&mut self, s: Settings) {
        self.settings = s;
        if let Err(e) = config::save(&self.settings) {
            self.toasts.error(&format!("Couldn't save settings: {e}"), "");
        }
    }

    /// Something is downloading or converting right now.
    fn working(&self) -> bool {
        self.jobs.iter().chain(self.files.jobs.iter()).any(|j| !j.state.finished() && j.state != State::Queued)
    }

    pub fn update_mascot(&mut self) {
        let reading = matches!(self.card, Card::Loading(_)) || self.files.reading > 0;
        self.mascot.update(self.working(), reading, self.app_focused != Some(false));
    }

    fn animating(&self) -> bool {
        self.mascot.animating()
            || matches!(self.card, Card::Loading(_))
            || self.jobs.iter().chain(self.files.jobs.iter()).any(|j| !j.state.finished() && j.state != State::Queued && (j.fraction.is_none() || j.cancelled()))
            || self.modals.iter().any(|(d, _)| d.animating())
    }

    pub fn timeout(&self) -> Duration {
        let mut t = Duration::from_secs(3600);
        if self.animating() {
            t = t.min(Duration::from_millis(80));
        }
        if self.mascot.animating() {
            t = t.min(self.mascot.frame());
        }
        if let Some(d) = self.mascot.next_wake() {
            t = t.min(d + Duration::from_millis(5));
        }
        if let Some(d) = self.toasts.next_deadline() {
            t = t.min(d.saturating_duration_since(Instant::now()) + Duration::from_millis(5));
        }
        if let Some((at, _)) = &self.debounce {
            t = t.min(at.saturating_duration_since(Instant::now()));
        }
        t
    }

    pub fn on_tick(&mut self) {
        let was = self.mascot.animating();
        self.update_mascot();
        if was || self.animating() {
            self.tick += 1;
            self.dirty = true;
        }
        if self.toasts.expire() {
            self.dirty = true;
        }
        if let Some((at, value)) = self.debounce.clone() {
            if Instant::now() >= at {
                self.debounce = None;
                self.load(&value);
                self.dirty = true;
            }
        }
    }

    // ---------- messages ----------

    pub fn handle(&mut self, msg: Msg) {
        self.dirty = true;
        if self.toasts.expire() {
            self.dirty = true;
        }
        match msg {
            Msg::Term(ev) => self.on_event(ev),
            Msg::Probed { token, result } => self.probe_done(token, result),
            Msg::Thumb { token, image } => {
                if token == self.probe_token {
                    self.thumb.set(Some(image));
                }
            }
            Msg::Progress { id, p } => {
                if let Some(job) = self.job_mut(id) {
                    if !job.state.finished() {
                        job.state = p.state;
                        job.status = p.status;
                        job.fraction = p.fraction;
                        job.speed = p.speed;
                        job.eta = p.eta;
                    }
                }
            }
            Msg::Finished { id, result } => self.job_finished(id, result),
            Msg::FileRead { result, count } => self.file_read(result, count),
            Msg::FilesStatus(s) => self.files.status = s,
            Msg::Clipboard(url) => self.clipboard_found(url),
            Msg::YtDlpVersion(v) => self.ytdlp_version = v,
            Msg::UpdateFound(v) => self.update_version = Some(v),
            Msg::UpdateDone(ok, output) => {
                if ok {
                    self.update_version = None;
                    self.ytdlp_version = update::installed_version();
                    self.toasts.notify("Restart Carrot Leaf to use the new version.", "yt-dlp updated", Severity::Info);
                } else {
                    let last = output.trim().lines().last().unwrap_or("unknown error").to_string();
                    self.toasts.notify_for(&format!("Update failed: {last}"), "", Severity::Error, 10.0);
                }
            }
            Msg::Ffmpeg(busy) => self.getting_ffmpeg = busy,
            Msg::Sync(busy) => self.sync_state(busy),
            Msg::Followed(result) => match result {
                Ok(item) => {
                    self.follow = Some(true);
                    let where_ = short_path(&self.settings.download_path().join(safe_name(&item.title)).to_string_lossy());
                    self.toasts.notify_for(
                        &format!("New videos will be saved as {} in {where_}. Press w to see what you follow.", item.choice().base_label()),
                        &format!("Following {}", item.title),
                        Severity::Info,
                        8.0,
                    );
                }
                Err(e) => self.toasts.error(&errors::explain(&e).message, "Couldn't follow"),
            },
            other => {
                let (mut cx, modals) = self.cx();
                for (d, _) in modals.iter_mut().rev() {
                    if d.msg(&other, &mut cx) {
                        break;
                    }
                }
            }
        }
    }

    fn job_mut(&mut self, id: u64) -> Option<&mut Job> {
        self.jobs.iter_mut().chain(self.files.jobs.iter_mut()).find(|j| j.id == id)
    }

    fn on_event(&mut self, ev: Event) {
        match ev {
            Event::Key(k) if k.kind != KeyEventKind::Release => self.on_key(k),
            Event::Paste(text) => self.on_paste(&text),
            Event::Mouse(m) => match m.kind {
                MouseEventKind::Down(MouseButton::Left) => {
                    let hit = self.clicks.iter().rev().find(|(r, _)| m.column >= r.x && m.column < r.right() && m.row >= r.y && m.row < r.bottom()).map(|(_, id)| id.clone());
                    if let Some(id) = hit {
                        self.on_click(&id);
                    } else {
                        self.dirty = false;
                    }
                }
                MouseEventKind::ScrollDown | MouseEventKind::ScrollUp => {
                    let down = matches!(m.kind, MouseEventKind::ScrollDown);
                    if self.modals.is_empty() {
                        let scroll = if self.tab == Tab::Download { &mut self.queue_scroll } else { &mut self.files.scroll };
                        *scroll = if down { *scroll + 1 } else { scroll.saturating_sub(1) };
                    } else {
                        let k = KeyEvent::new(if down { KeyCode::Down } else { KeyCode::Up }, KeyModifiers::NONE);
                        self.modal_key(k);
                    }
                }
                MouseEventKind::Moved => {
                    // Only hover targets that change their look matter; redraw when it changes.
                    let hit = self.clicks.iter().rev().find(|(r, _)| m.column >= r.x && m.column < r.right() && m.row >= r.y && m.row < r.bottom()).map(|(_, id)| id.clone()).filter(|id| id.starts_with("key:"));
                    self.dirty = hit != self.hover;
                    self.hover = hit;
                }
                _ => self.dirty = false,
            },
            Event::Resize(w, h) => self.size = (w, h),
            Event::FocusGained => {
                self.app_focused = Some(true);
                self.check_clipboard();
            }
            Event::FocusLost => self.app_focused = Some(false),
            _ => self.dirty = false,
        }
    }

    // ---------- dialogs ----------

    fn push(&mut self, d: Box<dyn Dialog>, purpose: Purpose) {
        self.modals.push((d, purpose));
    }

    fn apply_out(&mut self, out: Out) {
        match out {
            Out::None => {}
            Out::Push(d) => self.modals.push((d, Purpose::None)),
            Out::Main(res) => {
                let purpose = self.modals.last().map(|(_, p)| match p {
                    Purpose::Theme(old) => Purpose::Theme(old.clone()),
                    _ => Purpose::None,
                });
                self.modal_result(purpose.unwrap_or(Purpose::None), res, true);
            }
            Out::Close(res) => {
                let Some((_, purpose)) = self.modals.pop() else { return };
                if let Purpose::Theme(old) = &purpose {
                    if res.is_none() {
                        self.set_theme(&old.clone(), false);
                    }
                }
                let Some(res) = res else { return };
                if matches!(purpose, Purpose::None) && !self.modals.is_empty() {
                    // A child dialog: its parent takes the result.
                    let (mut cx, modals) = self.cx();
                    let out = modals.last_mut().unwrap().0.child(res, &mut cx);
                    self.apply_out(out);
                } else {
                    self.modal_result(purpose, res, false);
                }
            }
        }
    }

    fn modal_key(&mut self, k: KeyEvent) {
        let (mut cx, modals) = self.cx();
        let out = modals.last_mut().unwrap().0.key(&k, &mut cx);
        self.apply_out(out);
    }

    fn modal_result(&mut self, purpose: Purpose, res: Res, still_open: bool) {
        match (purpose, res) {
            (_, Res::Theme(name)) => self.set_theme(&name, !still_open),
            (_, Res::SyncNow) => {
                if !self.syncing {
                    self.sync_following();
                }
            }
            (Purpose::Video(info), Res::Choice(c)) => {
                self.remember(&c);
                self.enqueue(&info, c);
            }
            (Purpose::Playlist(pl), Res::Choice(c)) => {
                self.remember(&c);
                self.enqueue_playlist(&pl, c);
            }
            (Purpose::Follow { url, feed }, Res::Choice(c)) => {
                self.remember(&c);
                self.start_following(url, feed.map(|b| *b), c);
            }
            (Purpose::Files(files), Res::Choice(c)) => {
                self.remember(&c);
                for f in files {
                    if local::kinds_for(&f).contains(&c.kind.as_str()) {
                        self.files_enqueue(f, c.clone());
                    }
                }
            }
            (Purpose::Settings, Res::Settings(s)) => {
                let where_ = short_path(&s.download_dir);
                self.save_settings(*s);
                self.pump();
                self.toasts.notify(&format!("Saving to {where_}"), "Settings saved", Severity::Info);
            }
            (Purpose::Cookies, Res::Browser(b)) => {
                let mut s = self.settings.clone();
                s.cookies_browser = b;
                self.save_settings(s);
                if let Some(u) = self.loading_url.clone() {
                    self.load(&u);
                }
            }
            (Purpose::Pick(url), Res::Picked(set)) => {
                if let Card::Playlist(pl) = &self.card {
                    if pl.url == url {
                        self.picked = set;
                    }
                }
            }
            (Purpose::History, Res::Redownload(url)) => {
                if self.tab != Tab::Download {
                    self.show_tab(Tab::Download);
                }
                self.url.set(&url);
                self.load(&url);
            }
            (Purpose::Following, Res::GetNew(urls)) => self.get_new(&urls),
            (Purpose::Search, Res::ClipFile(path, start, end)) => {
                self.clip_file(PathBuf::from(path), start, end);
                if self.tab != Tab::Files {
                    self.show_tab(Tab::Files);
                }
            }
            (Purpose::Browse, Res::File(path)) => self.files_add(vec![path]),
            (Purpose::ListAsk(url), Res::WholeList(whole)) => {
                self.list_mode = Some((url.clone(), whole));
                self.pick_next = whole;
                self.load(&url);
            }
            _ => {}
        }
    }

    fn set_theme(&mut self, name: &str, save: bool) {
        self.theme = theme::by_name(name);
        if save && self.settings.theme != name {
            let mut s = self.settings.clone();
            s.theme = name.into();
            self.save_settings(s);
        }
    }

    fn remember(&mut self, c: &Choice) {
        let s = convert::remembered(&self.settings, c);
        self.save_settings(s);
    }

    // ---------- keys ----------

    fn typing(&self) -> bool {
        matches!(self.focus, Focus::Url | Focus::FileInput)
    }

    fn focus_chain(&self) -> Vec<Focus> {
        let mut out = vec![];
        if self.tab == Tab::Download {
            out.push(Focus::Url);
            out.extend(self.card_buttons().into_iter().map(|(id, _)| Focus::Button(id)));
            out.extend(self.jobs.iter().rev().filter(|j| !j.state.finished() && !j.cancelled()).map(|j| Focus::Cancel(j.id)));
        } else {
            out.push(Focus::FileInput);
            if !self.files.files.is_empty() {
                out.push(Focus::FileList);
                out.push(Focus::FileButton("convert"));
                out.push(Focus::FileButton("remove"));
            }
            out.extend(self.files.jobs.iter().rev().filter(|j| !j.state.finished() && !j.cancelled()).map(|j| Focus::Cancel(j.id)));
        }
        out
    }

    fn move_focus(&mut self, forward: bool) {
        let chain = self.focus_chain();
        let i = chain.iter().position(|f| *f == self.focus).unwrap_or(0);
        let n = chain.len();
        self.focus = chain[if forward { (i + 1) % n } else { (i + n - 1) % n }].clone();
        self.url.selected = false;
        self.files.input.selected = false;
    }

    fn on_key(&mut self, k: KeyEvent) {
        let ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
        if ctrl && matches!(k.code, KeyCode::Char('q')) {
            self.quit = true;
            return;
        }
        if !self.modals.is_empty() {
            self.modal_key(k);
            return;
        }
        if ctrl && matches!(k.code, KeyCode::Char('t')) {
            return self.action("switch_tab");
        }
        if ctrl && matches!(k.code, KeyCode::Char('c')) && !self.typing() {
            self.quit = true;
            return;
        }
        match k.code {
            KeyCode::Tab => return self.move_focus(true),
            KeyCode::BackTab => return self.move_focus(false),
            KeyCode::Esc => return self.action("focus_url"),
            _ => {}
        }
        if self.typing() {
            let files = self.focus == Focus::FileInput;
            let input = if files { &mut self.files.input } else { &mut self.url };
            match input.key(&k) {
                Edit::Changed => {
                    if files {
                        self.file_input_changed();
                    } else {
                        self.url_changed();
                    }
                }
                Edit::Submitted => {
                    if files {
                        self.file_input_submitted();
                    } else {
                        self.url_submitted();
                    }
                }
                Edit::None => {}
                Edit::Ignored => self.dirty = false,
            }
            return;
        }
        if self.focus == Focus::FileList {
            let n = self.files.files.len();
            if self.files.list.key(&k, n, None) {
                return;
            }
        }
        if matches!(k.code, KeyCode::Enter | KeyCode::Char(' ')) {
            match self.focus.clone() {
                Focus::Button(id) => return self.press(id),
                Focus::FileButton(id) => return self.action(if id == "convert" { "convert" } else { "remove_file" }),
                Focus::Cancel(id) => return self.cancel(id),
                Focus::FileList if k.code == KeyCode::Enter => return self.action("convert"),
                _ => {}
            }
        }
        let KeyCode::Char(c) = k.code else {
            self.dirty = false;
            return;
        };
        if ctrl || k.modifiers.contains(KeyModifiers::ALT) {
            self.dirty = false;
            return;
        }
        let name = match c {
            'd' => "download",
            'c' => "convert",
            'l' => "sites",
            's' => "settings",
            't' => "themes",
            'x' => "cancel_download",
            'o' => "reveal",
            'u' => "update",
            'f' => "browse",
            'r' => "remove_file",
            'e' => "pick",
            'p' => "pause",
            'h' => "history",
            'w' => "following",
            'a' => "follow",
            '/' => "search",
            'v' => "paste_clipboard",
            'q' => "quit",
            _ => {
                self.dirty = false;
                return;
            }
        };
        self.action(name);
    }

    fn on_paste(&mut self, text: &str) {
        if !self.modals.is_empty() {
            let (mut cx, modals) = self.cx();
            let out = modals.last_mut().unwrap().0.paste(text, &mut cx);
            self.apply_out(out);
            return;
        }
        let t = text.trim();
        let paths = local::parse_paths(t);
        if !paths.is_empty() {
            self.add_files(paths);
            return;
        }
        match self.focus {
            Focus::Url => {
                self.url.insert(text);
                self.url_changed();
            }
            Focus::FileInput => {
                self.files.input.insert(text);
                self.file_input_changed();
            }
            _ => {
                if looks_like_url(t) {
                    if self.tab != Tab::Download {
                        self.show_tab(Tab::Download);
                    }
                    self.focus = Focus::Url;
                    self.url.set(t);
                    self.url_changed();
                }
            }
        }
    }

    fn on_click(&mut self, id: &str) {
        if id == "toast" {
            self.toasts.items.clear();
            return;
        }
        if !self.modals.is_empty() {
            let (mut cx, modals) = self.cx();
            let out = modals.last_mut().unwrap().0.click(id, &mut cx);
            self.apply_out(out);
            return;
        }
        let (kind, arg) = id.split_once(':').unwrap_or((id, ""));
        match kind {
            "key" => self.action(&arg.to_string()),
            "tab" => self.show_tab(if arg == "files" { Tab::Files } else { Tab::Download }),
            "url" => {
                self.focus = if self.tab == Tab::Files { Focus::FileInput } else { Focus::Url };
            }
            "btn" => {
                let b = self.card_buttons().into_iter().find(|(i, _)| *i == arg).map(|(i, _)| i);
                if let Some(b) = b {
                    self.focus = Focus::Button(b);
                    self.press(b);
                }
            }
            "cancel" => {
                if let Ok(n) = arg.parse() {
                    self.cancel(n);
                }
            }
            "files" => {
                if let Ok(n) = arg.parse() {
                    self.files.list.index = n;
                    self.focus = Focus::FileList;
                }
            }
            "fbtn" => self.action(if arg == "convert" { "convert" } else { "remove_file" }),
            _ => self.dirty = false,
        }
    }

    // ---------- URL handling ----------

    fn url_changed(&mut self) {
        self.debounce = None;
        let value = self.url.value.trim().to_string();
        let paths = local::parse_paths(&value);
        if !paths.is_empty() {
            self.url.set("");
            self.add_files(paths);
            return;
        }
        if looks_like_url(&value) && Some(&value) != self.last_url.as_ref() {
            // Paste arrives as one change; a short wait avoids firing while someone types.
            self.debounce = Some((Instant::now() + Duration::from_millis(350), value));
        }
    }

    fn url_submitted(&mut self) {
        let value = self.url.value.trim().to_string();
        if value.is_empty() {
            return;
        }
        self.debounce = None;
        let paths = local::parse_paths(&value);
        if !paths.is_empty() {
            self.url.set("");
            self.add_files(paths);
            return;
        }
        if Some(&value) == self.last_url.as_ref() && matches!(self.card, Card::Video(..) | Card::Playlist(_)) {
            self.focus = Focus::Button("download");
            return;
        }
        self.load(&value);
    }

    fn load(&mut self, raw: &str) {
        let raw = raw.trim().to_string();
        self.last_url = Some(raw.clone());
        let url = if raw.contains("://") { raw.clone() } else { format!("https://{raw}") };
        self.mascot.poke();
        self.seen_clipboard.insert(raw.clone());
        self.seen_clipboard.insert(url.clone());
        if self.clipboard_url.as_ref().is_some_and(|c| *c == raw || *c == url) {
            self.clipboard_url = None;
        }
        // A video inside a playlist or mix: ask first, once per link.
        let decided = self.list_mode.as_ref().filter(|(u, _)| *u == url).map(|(_, whole)| *whole);
        let whole_list = match (probe::list_in_video_link(&url), decided) {
            (None, _) => false,
            (Some(_), Some(whole)) => whole,
            (Some(mix), None) => {
                if !self.modals.iter().any(|(_, p)| matches!(p, Purpose::ListAsk(_))) {
                    self.push(Box::new(dialogs::ListAsk::new(mix)), Purpose::ListAsk(url));
                }
                return;
            }
        };
        self.loading_url = Some(url.clone());
        self.probe_token += 1;
        self.card = Card::Loading(url.clone());
        self.thumb.set(None);
        self.follow = None;
        let token = self.probe_token;
        let cookies = self.settings.cookies_browser.clone();
        let tx = self.tx.clone();
        super::spawn(move || {
            let result = probe::probe_with(&url, &cookies, probe::PLAYLIST_LIMIT, whole_list);
            let thumb = match &result {
                Ok(Probed::Video(v)) => v.thumbnail.clone(),
                Ok(Probed::Playlist(p)) => p.thumbnail.clone(),
                Err(_) => None,
            };
            let _ = tx.send(Msg::Probed { token, result });
            if let Some(image) = thumb.and_then(|u| probe::fetch_image(&u, 10)) {
                let _ = tx.send(Msg::Thumb { token, image });
            }
        });
    }

    fn probe_done(&mut self, token: u64, result: Result<Probed, String>) {
        if token != self.probe_token {
            return;
        }
        match result {
            Ok(Probed::Playlist(pl)) => {
                self.picked = (0..pl.entries.len()).collect();
                self.follow = Some(library::following_find(&pl.url).is_some());
                self.card = Card::Playlist(pl);
                self.focus = Focus::Button("download");
                // Asked for the whole mix or playlist: go straight to picking from it.
                if std::mem::take(&mut self.pick_next) {
                    self.action("pick");
                }
            }
            Ok(Probed::Video(info)) => {
                let preset = library::default_preset_for(&info.extractor).map(|p| p.name).unwrap_or_default();
                self.follow = info.channel_url().map(|_| false);
                self.card = Card::Video(info, preset);
                self.focus = Focus::Button("download");
            }
            Err(e) => {
                self.card = Card::Error(errors::explain(&e));
                self.focus = Focus::Url;
            }
        }
    }

    // ---------- tabs ----------

    fn show_tab(&mut self, tab: Tab) {
        self.tab = tab;
        self.action("focus_url");
    }

    fn add_files(&mut self, paths: Vec<PathBuf>) {
        if self.tab != Tab::Files {
            self.show_tab(Tab::Files);
        }
        self.files_add(paths);
    }

    // ---------- actions ----------

    fn action(&mut self, name: &str) {
        let files = self.tab == Tab::Files;
        match name {
            "quit" => self.quit = true,
            "switch_tab" => self.show_tab(if files { Tab::Download } else { Tab::Files }),
            "focus_url" => {
                if files {
                    self.focus = Focus::FileInput;
                    self.files.input.select_all();
                } else {
                    self.focus = Focus::Url;
                    self.url.select_all();
                }
            }
            "leave_url" => self.move_focus(true),
            "browse" => {
                if !files {
                    self.show_tab(Tab::Files);
                }
                self.push(Box::new(dialogs::FilePicker::new(dirs::home_dir().unwrap_or_default(), false)), Purpose::Browse);
            }
            "remove_file" => {
                if files && !self.files.files.is_empty() {
                    let i = self.files.list.index.min(self.files.files.len() - 1);
                    self.files.files.remove(i);
                    self.files.list.clamp(self.files.files.len());
                    if self.files.files.is_empty() && self.focus != Focus::FileInput {
                        self.focus = Focus::FileInput;
                    }
                } else {
                    self.bell();
                }
            }
            "download" => {
                if files {
                    return self.bell();
                }
                match &self.card {
                    Card::Playlist(pl) => {
                        let pl = pl.clone();
                        let c = library::default_preset_for(&pl.extractor).map(|p| p.choice()).unwrap_or_else(download_choice);
                        self.enqueue_playlist(&pl, c);
                    }
                    Card::Video(info, _) => {
                        let info = info.clone();
                        let c = library::default_preset_for(&info.extractor).map(|p| p.choice()).unwrap_or_else(download_choice);
                        self.enqueue(&info, c);
                    }
                    _ => self.bell(),
                }
            }
            "pick" => match &self.card {
                Card::Playlist(pl) if !files => {
                    let d = dialogs::PlaylistPick::new(pl, &self.picked);
                    let url = pl.url.clone();
                    self.push(Box::new(d), Purpose::Pick(url));
                }
                _ => self.bell(),
            },
            "convert" => {
                if files {
                    return self.files_convert();
                }
                match &self.card {
                    Card::Playlist(pl) => {
                        let pl = pl.clone();
                        if self.picked.is_empty() {
                            self.toasts.warn("No videos picked. Press e to pick some.");
                            return;
                        }
                        let where_ = short_path(&self.settings.download_path().join(safe_name(&pl.title)).to_string_lossy());
                        let yt = pl.extractor.to_lowercase().starts_with("youtube");
                        let opts = convert::for_playlist(&pl.title, self.picked.len(), &where_, yt);
                        let d = ConvertDialog::new(opts, &self.settings, None);
                        self.push(Box::new(d), Purpose::Playlist(pl));
                    }
                    Card::Video(info, _) => {
                        let info = info.clone();
                        let opts = convert::for_video(&info, &short_path(&self.settings.download_path().to_string_lossy()));
                        let d = ConvertDialog::new(opts, &self.settings, None);
                        self.push(Box::new(d), Purpose::Video(info));
                    }
                    _ => self.bell(),
                }
            }
            "pause" => {
                if files {
                    return self.bell();
                }
                self.paused = !self.paused;
                self.pump();
                if self.paused {
                    self.toasts.info("Running downloads finish; waiting ones hold until you press p.");
                }
            }
            "history" => self.push(Box::new(dialogs::History::new()), Purpose::History),
            "follow" => self.follow_action(),
            "following" => self.push(Box::new(dialogs::Following::new(self.syncing)), Purpose::Following),
            "search" => {
                let d = dialogs::Search::new(&self.tx);
                self.push(Box::new(d), Purpose::Search);
            }
            "paste_clipboard" => {
                let Some(url) = self.clipboard_url.take() else { return self.bell() };
                if self.tab != Tab::Download {
                    self.show_tab(Tab::Download);
                }
                self.url.set(&url);
                self.url_changed();
            }
            "settings" => self.push(Box::new(dialogs::SettingsDialog::new(&self.settings)), Purpose::Settings),
            "sites" => {
                let d = dialogs::Sites::new(&self.tx);
                self.push(Box::new(d), Purpose::None);
            }
            "themes" => {
                let old = self.theme.name.to_string();
                self.push(Box::new(dialogs::Themes::new(&old)), Purpose::Theme(old));
            }
            "reveal" => {
                let last = if files { self.files.last_file.clone() } else { self.last_file.clone() };
                match last.filter(|l| Path::new(l).exists()) {
                    Some(l) => desktop::reveal(&l),
                    None => self.toasts.warn(&format!("Nothing {} yet.", if files { "converted" } else { "downloaded" })),
                }
            }
            "update" => {
                self.toasts.notify("Updating yt-dlp…", "Update", Severity::Info);
                work(&self.tx, update::upgrade, |(ok, out)| Msg::UpdateDone(ok, out));
            }
            "cancel_download" => {
                let jobs = if files { &self.files.jobs } else { &self.jobs };
                let running = if files { self.files.running } else { self.active.first().copied() };
                let target = running.or_else(|| jobs.iter().find(|j| j.state == State::Queued).map(|j| j.id));
                match target {
                    Some(id) => self.cancel(id),
                    None => self.bell(),
                }
            }
            _ => {}
        }
    }

    fn press(&mut self, id: &str) {
        match id {
            "download" => self.action("download"),
            "convert" => self.action("convert"),
            "pick" => self.action("pick"),
            "follow" => self.action("follow"),
            "update-ytdlp" => self.action("update"),
            "retry" => {
                if let Some(u) = self.loading_url.clone() {
                    self.load(&u);
                }
            }
            "use-cookies" => self.push(Box::new(dialogs::Cookies::new()), Purpose::Cookies),
            _ => {}
        }
    }

    fn card_buttons(&self) -> Vec<(&'static str, String)> {
        match &self.card {
            Card::Video(..) => {
                let mut b = vec![("download", "[d] download".to_string()), ("convert", "[c] convert".into())];
                if let Some(f) = self.follow {
                    b.push(("follow", if f { "following ✓".into() } else { "[a] follow channel".into() }));
                }
                b
            }
            Card::Playlist(pl) => {
                let all = self.picked.len() == pl.entries.len();
                let dl = if all { "[d] download all".to_string() } else { format!("[d] download {}", self.picked.len()) };
                let mut b = vec![("download", dl), ("convert", "[c] convert".into()), ("pick", "[e] pick videos".into())];
                if let Some(f) = self.follow {
                    b.push(("follow", if f { "following ✓".into() } else { "[a] follow".into() }));
                }
                b
            }
            Card::Error(e) => {
                let mut b = vec![];
                if e.kind == Kind::Login {
                    b.push(("use-cookies", "use browser cookies".to_string()));
                }
                if e.kind == Kind::Outdated {
                    b.push(("update-ytdlp", "update yt-dlp".into()));
                }
                if matches!(e.kind, Kind::Network | Kind::Outdated | Kind::Generic | Kind::Login) {
                    b.push(("retry", "try again".into()));
                }
                b
            }
            _ => vec![],
        }
    }

    // ---------- following ----------

    fn follow_action(&mut self) {
        if self.tab == Tab::Files {
            return self.bell();
        }
        let (feed_url, title, count, extractor, playlist) = match &self.card {
            Card::Playlist(pl) => (pl.url.clone(), pl.title.clone(), pl.entries.len(), pl.extractor.clone(), Some(pl.clone())),
            Card::Video(info, _) if info.channel_url().is_some() => (info.channel_url().unwrap(), info.channel.clone(), 0, info.extractor.clone(), None),
            _ => return self.bell(),
        };
        if library::following_find(&feed_url).is_some() || (playlist.is_none() && library::following_find(&probe::channel_home_to_videos(&feed_url)).is_some()) {
            return self.action("following");
        }
        let yt = extractor.to_lowercase().starts_with("youtube");
        let where_ = short_path(&self.settings.download_path().join(safe_name(&title)).to_string_lossy());
        let mut opts = convert::for_playlist(&title, count.max(1), &where_, yt);
        opts.title = format!("{title} · new videos");
        opts.action = "follow".into();
        let d = ConvertDialog::new(opts, &self.settings, None);
        self.push(Box::new(d), Purpose::Follow { url: feed_url, feed: playlist });
    }

    fn start_following(&mut self, url: String, feed: Option<PlaylistInfo>, choice: Choice) {
        let cookies = self.settings.cookies_browser.clone();
        work(
            &self.tx,
            move || {
                let feed = match feed {
                    Some(f) => f,
                    None => match probe::probe(&url, &cookies, library::SYNC_DEPTH) {
                        Ok(Probed::Playlist(p)) => *p,
                        Ok(_) => return Err("That channel has no video list Carrot Leaf can follow.".to_string()),
                        Err(e) => return Err(e),
                    },
                };
                Ok(library::follow(&feed, &choice))
            },
            Msg::Followed,
        );
    }

    fn get_new(&mut self, urls: &[String]) {
        let mut template = self.settings.filename_template.clone();
        if !template.contains("{playlist}") {
            template = format!("{{playlist}}/{template}");
        }
        let mut count = 0;
        for mut item in library::following_load().into_iter().filter(|f| urls.contains(&f.url)) {
            let choice = item.choice();
            for entry in library::mark_fetched(&mut item) {
                let mut job = Job::new(&entry.url, &entry.title, choice.clone());
                job.template = template.clone();
                job.fields = HashMap::from([("playlist".into(), safe_name(&item.title)), ("index".into(), String::new())]);
                self.add_job(job);
                count += 1;
            }
            library::following_update(&item);
        }
        self.new_videos = library::total_new(&library::following_load());
        if self.tab != Tab::Download {
            self.show_tab(Tab::Download);
        }
        self.pump();
        if count > 0 {
            self.toasts.info(&format!("Getting {count} new video{}.", if count != 1 { "s" } else { "" }));
        }
    }

    fn sync_following(&mut self) {
        let cookies = self.settings.cookies_browser.clone();
        let tx = self.tx.clone();
        let _ = tx.send(Msg::Sync(true));
        super::spawn(move || {
            for mut item in library::following_load() {
                match probe::probe(&item.url, &cookies, library::SYNC_DEPTH) {
                    Ok(Probed::Playlist(feed)) => {
                        library::apply_sync(&mut item, Some(&feed), "");
                    }
                    Ok(_) => {
                        library::apply_sync(&mut item, None, "No longer a list of videos.");
                    }
                    Err(e) => {
                        library::apply_sync(&mut item, None, &errors::explain(&e).message);
                    }
                }
                library::following_update(&item);
            }
            let _ = tx.send(Msg::Sync(false));
        });
    }

    fn sync_state(&mut self, busy: bool) {
        let before = self.new_videos;
        self.syncing = busy;
        self.new_videos = library::total_new(&library::following_load());
        for (d, _) in self.modals.iter_mut() {
            if let Some(f) = d.any().downcast_mut::<dialogs::Following>() {
                f.refresh(Some(busy));
            }
        }
        if !busy && self.new_videos > before && self.app_focused == Some(false) {
            desktop::notify("New videos", &format!("{} new in what you follow.", self.new_videos));
        }
    }

    // ---------- background checks ----------

    fn check_clipboard(&mut self) {
        if !self.settings.watch_clipboard {
            return;
        }
        let tx = self.tx.clone();
        super::spawn(move || {
            let text = desktop::read_clipboard();
            let text = text.trim();
            if !text.is_empty() && !text.contains('\n') && looks_like_url(text) {
                let _ = tx.send(Msg::Clipboard(text.to_string()));
            }
        });
    }

    fn clipboard_found(&mut self, url: String) {
        if self.seen_clipboard.contains(&url) || self.last_url.as_ref() == Some(&url) {
            self.dirty = false;
            return;
        }
        self.seen_clipboard.insert(url.clone());
        self.clipboard_url = Some(url.clone());
        self.toasts.notify_for(&format!("{url}\nPress v to load it."), "Link on your clipboard", Severity::Info, 8.0);
    }

    fn check_for_update(&mut self) {
        let tx = self.tx.clone();
        super::spawn(move || {
            let v = update::installed_version();
            let _ = tx.send(Msg::YtDlpVersion(v.clone()));
            if std::env::var_os("CARROT_NO_UPDATE_CHECK").is_some() {
                return;
            }
            if let Some(newer) = update::update_available(&v) {
                let _ = tx.send(Msg::UpdateFound(newer));
            }
        });
    }

    fn prefetch_ffmpeg(&mut self) {
        // Only fetch the bundled copy when there is no ffmpeg + ffprobe on PATH to use.
        if std::env::var_os("CARROT_NO_FFMPEG_FETCH").is_some()
            || deps::system_ffmpeg().is_some()
            || deps::bundled_ffmpeg_ready()
        {
            return;
        }
        let tx = self.tx.clone();
        super::spawn(move || {
            let _ = tx.send(Msg::Ffmpeg(true));
            let _ = deps::fetch_bundled_ffmpeg(false);
            let _ = tx.send(Msg::Ffmpeg(false));
        });
    }

    // ---------- download queue ----------

    fn enqueue(&mut self, info: &VideoInfo, choice: Choice) {
        let job = Job::new(&info.url, &info.title, choice);
        self.add_job(job);
        self.pump();
    }

    fn enqueue_playlist(&mut self, pl: &PlaylistInfo, choice: Choice) {
        if self.picked.is_empty() {
            self.toasts.warn("No videos picked. Press e to pick some.");
            return;
        }
        let mut template = self.settings.filename_template.clone();
        if !template.contains("{playlist}") {
            template = format!("{{playlist}}/{template}");
        }
        let width = pl.entries.len().to_string().len().max(2);
        for &i in &self.picked.clone() {
            let Some(entry) = pl.entries.get(i) else { continue };
            let mut job = Job::new(&entry.url, &entry.title, choice.clone());
            job.template = template.clone();
            job.fields = HashMap::from([("playlist".into(), safe_name(&pl.title)), ("index".into(), format!("{:0width$}", i + 1))]);
            self.add_job(job);
        }
        self.pump();
    }

    fn add_job(&mut self, mut job: Job) {
        if job.fields.is_empty() {
            job.template = self.settings.filename_template.clone();
        }
        job.keep_transcript = self.settings.keep_transcripts;
        self.jobs.push(job);
        self.queue_scroll = 0;
    }

    fn cancel(&mut self, id: u64) {
        let Some(job) = self.job_mut(id) else { return };
        if job.state.finished() || job.cancelled() {
            return;
        }
        job.cancel();
        if job.state == State::Queued {
            job.state = State::Cancelled;
        }
        if self.focus == Focus::Cancel(id) {
            self.move_focus(true);
        }
        self.pump();
        self.files_pump();
    }

    fn pump(&mut self) {
        let limit = self.settings.parallel_downloads.clamp(1, 4) as usize;
        while !self.paused && self.active.len() < limit {
            let Some(job) = self.jobs.iter_mut().find(|j| j.state == State::Queued) else { break };
            job.state = State::Preparing;
            job.status = "Starting".into();
            job.fraction = None;
            job.started = Some(Instant::now());
            if self.batch_started.is_none() {
                self.batch_started = job.started;
            }
            self.active.push(job.id);
            let job = job.clone();
            let dir = self.settings.download_path();
            let cookies = self.settings.cookies_browser.clone();
            let tx = self.tx.clone();
            super::spawn(move || {
                let mut on_progress = throttled(tx.clone(), job.id);
                let result = runner::run_job(&job, &dir, &mut on_progress, &cookies);
                let _ = tx.send(Msg::Finished { id: job.id, result });
            });
        }
        let waiting = self.jobs.iter().any(|j| j.state == State::Queued);
        if self.active.is_empty() && !waiting && !self.batch.is_empty() {
            self.batch_done();
        }
    }

    fn job_finished(&mut self, id: u64, result: Result<String, String>) {
        let is_file = self.files.jobs.iter().any(|j| j.id == id);
        let Some(job) = self.job_mut(id) else { return };
        match result {
            Ok(path) => {
                job.state = State::Done;
                job.fraction = Some(1.0);
                job.filepath = Some(path.clone());
                let kind = if is_file { "convert" } else { "download" };
                library::history_add(HistoryEntry::new(&job.title, &path, &job.choice.label(), &job.url, kind));
                if is_file {
                    self.files.last_file = Some(path);
                } else {
                    self.last_file = Some(path);
                }
            }
            Err(_) if job.cancelled() => job.state = State::Cancelled,
            Err(e) => {
                job.state = State::Failed;
                job.error = Some(e);
            }
        }
        if is_file {
            self.files.running = None;
            self.files_pump();
        } else {
            let cancelled = self.jobs.iter().any(|j| j.id == id && j.state == State::Cancelled);
            if !cancelled {
                self.batch.push(id);
            }
            self.active.retain(|&a| a != id);
            self.pump();
        }
        if self.focus == Focus::Cancel(id) {
            self.move_focus(true);
        }
    }

    fn batch_done(&mut self) {
        let batch = std::mem::take(&mut self.batch);
        let took = self.batch_started.take().map(|s| s.elapsed().as_secs_f64()).unwrap_or(0.0);
        if !self.settings.notify_when_done || self.app_focused == Some(true) {
            return;
        }
        if self.app_focused.is_none() && took < NOTIFY_AFTER {
            return;
        }
        let jobs: Vec<&Job> = self.jobs.iter().filter(|j| batch.contains(&j.id)).collect();
        let done: Vec<&&Job> = jobs.iter().filter(|j| j.state == State::Done).collect();
        let failed = jobs.len() - done.len();
        let (title, message) = if jobs.len() == 1 {
            ((if done.is_empty() { "Download failed" } else { "Downloaded" }).to_string(), jobs[0].title.clone())
        } else {
            let title = format!("{} download{} finished", done.len(), if done.len() != 1 { "s" } else { "" });
            let mut message = if failed > 0 { format!("{failed} didn't work.") } else { done.iter().take(3).map(|j| j.title.clone()).collect::<Vec<_>>().join(", ") };
            if failed == 0 && done.len() > 3 {
                message += &format!(" and {} more", done.len() - 3);
            }
            (title, message)
        };
        desktop::notify(&title, &message);
    }

    // ---------- files ----------

    fn file_input_changed(&mut self) {
        let paths = local::parse_paths(&self.files.input.value);
        if !paths.is_empty() {
            self.files.input.set("");
            self.files_add(paths);
        }
    }

    fn file_input_submitted(&mut self) {
        let text = self.files.input.value.trim().to_string();
        if text.is_empty() {
            if !self.files.files.is_empty() {
                self.files_convert();
            }
            return;
        }
        let paths = local::parse_paths(&text);
        if paths.is_empty() {
            self.toasts.warn("Carrot Leaf can't find that file.");
            return;
        }
        self.files.input.set("");
        self.files_add(paths);
    }

    fn files_add(&mut self, paths: Vec<PathBuf>) {
        let known: HashSet<PathBuf> = self.files.files.iter().map(|f| f.path.clone()).collect();
        let new: Vec<PathBuf> = paths.into_iter().filter(|p| !known.contains(p)).collect();
        if new.is_empty() {
            return;
        }
        self.files.reading += new.len();
        self.mascot.poke();
        let tx = self.tx.clone();
        super::spawn(move || {
            if deps::ffmpeg_candidates().is_empty() {
                let _ = tx.send(Msg::FilesStatus("Getting ffmpeg (one time, ~50 MB)…".into()));
                if let Err(e) = deps::fetch_bundled_ffmpeg(false) {
                    let _ = tx.send(Msg::FileRead { result: Err((None, e)), count: new.len() });
                    return;
                }
            }
            let probe_dir = deps::ffmpeg_candidates().first().map(|t| t.path.clone());
            // Each read waits on ffprobe, so a dropped batch reads a few at a time, in order.
            for chunk in new.chunks(4) {
                let handles: Vec<_> = chunk
                    .iter()
                    .map(|p| {
                        let p = p.clone();
                        let dir = probe_dir.clone();
                        std::thread::spawn(move || local::probe_file(&p, dir.as_deref()).map_err(|e| (Some(p), e)))
                    })
                    .collect();
                for h in handles {
                    let result = h.join().unwrap_or_else(|_| Err((None, "Couldn't read that file.".into())));
                    let _ = tx.send(Msg::FileRead { result, count: 1 });
                }
            }
        });
    }

    fn file_read(&mut self, result: Result<FileInfo, (Option<PathBuf>, String)>, count: usize) {
        self.files.reading = self.files.reading.saturating_sub(count);
        match result {
            Ok(info) => {
                let path = info.path.clone();
                if self.files.files.iter().all(|f| f.path != info.path) {
                    self.files.files.push(info.clone());
                }
                if let Some((s, e)) = self.files.pending_clips.remove(&path) {
                    self.enqueue_clip(info, s, e);
                }
            }
            Err((path, e)) => {
                let name = path.map(|p| format!("{}: ", p.file_name().unwrap_or_default().to_string_lossy())).unwrap_or_default();
                self.toasts.error(&format!("{name}{}", errors::explain(&e).message), "Couldn't add file");
            }
        }
    }

    fn clip_file(&mut self, path: PathBuf, start: f64, end: f64) {
        if let Some(known) = self.files.files.iter().find(|f| f.path == path).cloned() {
            self.enqueue_clip(known, start, end);
            return;
        }
        self.files.pending_clips.insert(path.clone(), (start, end));
        self.files_add(vec![path]);
    }

    fn enqueue_clip(&mut self, info: FileInfo, start: f64, end: f64) {
        let kind = if info.media == Media::Audio { "audio" } else { "video" };
        let ext = info.path.extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default();
        let fmt = if local::formats_for(kind).contains(&ext.as_str()) { ext } else if kind == "audio" { "mp3".into() } else { "mp4".into() };
        let extras = Extras { start: if start > 0.0 { Some(start) } else { None }, end: Some(end), ..Default::default() };
        self.files_enqueue(info, Choice::new(kind, &fmt, "best").with_extras(extras));
    }

    fn files_convert(&mut self) {
        if self.files.files.is_empty() {
            return self.bell();
        }
        let files = self.files.files.clone();
        let opts: ConvertOptions = convert::for_files(&files);
        let d = ConvertDialog::new(opts, &self.settings, None);
        self.push(Box::new(d), Purpose::Files(files));
    }

    fn files_enqueue(&mut self, info: FileInfo, choice: Choice) {
        let name = info.name();
        let mut job = Job::new(&info.path.to_string_lossy(), &name, choice);
        job.file = Some(info);
        self.files.jobs.push(job);
        self.files.scroll = 0;
        self.files_pump();
    }

    fn files_pump(&mut self) {
        if self.files.running.is_some() {
            return;
        }
        let Some(job) = self.files.jobs.iter_mut().find(|j| j.state == State::Queued) else { return };
        job.state = State::Preparing;
        job.status = "Starting".into();
        job.fraction = None;
        self.files.running = Some(job.id);
        let job = job.clone();
        let tx = self.tx.clone();
        super::spawn(move || {
            let info = job.file.clone().unwrap();
            let dir = convert::ffmpeg_for(&job.choice.kind, &job.choice.fmt);
            let mut on_progress = throttled(tx.clone(), job.id);
            let result = local::run_convert(&info, &job.choice, &mut on_progress, &job.cancel, dir.as_deref());
            let _ = tx.send(Msg::Finished { id: job.id, result });
        });
    }

    // ---------- drawing ----------

    pub fn render(&mut self, buf: &mut Buffer, area: Rect) {
        self.size = (area.width, area.height);
        let mut clicks = vec![];
        let t = self.theme;
        convert::CURRENT.with(|c| *c.borrow_mut() = t);
        {
            let mut p = Painter { buf, t, clicks: &mut clicks, tick: self.tick };
            p.clear(area, t.bg);
            self.draw_main(&mut p, area);
        }
        for (d, _) in &self.modals {
            draw_dialog(buf, t, &mut clicks, self.tick, area, d.as_ref());
        }
        {
            let mut p = Painter { buf, t, clicks: &mut clicks, tick: self.tick };
            self.toasts.draw(&mut p, area);
        }
        self.clicks = clicks;
    }

    fn status_line(&self) -> Line<'static> {
        let t = self.theme;
        let dim = fg(t.muted.over(t.bg, 0.6));
        let muted = fg(t.muted);
        let mut bits: Vec<Vec<Span<'static>>> = vec![vec![Span::styled("carrot", dim), Span::styled(format!(" {}", crate::VERSION), muted)]];
        if let Some(v) = &self.update_version {
            bits.push(vec![Span::styled(format!("yt-dlp {v} available · press u"), bold(t.warning))]);
        } else if !self.ytdlp_version.is_empty() {
            bits.push(vec![Span::styled("yt-dlp", dim), Span::styled(format!(" {}", self.ytdlp_version), muted)]);
        }
        if self.getting_ffmpeg {
            bits.push(vec![Span::styled("getting ffmpeg…", dim)]);
        }
        if self.paused {
            bits.push(vec![Span::styled("queue paused · press p", bold(t.warning))]);
        }
        if self.new_videos > 0 {
            bits.push(vec![Span::styled(format!("{} new · press w", self.new_videos), bold(t.primary))]);
        }
        if !self.settings.cookies_browser.is_empty() {
            bits.push(vec![Span::styled("cookies", dim), Span::styled(format!(" {}", self.settings.cookies_browser), muted)]);
        }
        if self.tab == Tab::Files {
            bits.push(vec![Span::styled("saving next to each original", dim)]);
        } else {
            bits.push(vec![Span::styled("saving to", dim), Span::styled(format!(" {}", short_path(&self.settings.download_path().to_string_lossy())), muted)]);
        }
        let mut spans = vec![];
        for (i, b) in bits.into_iter().enumerate() {
            if i > 0 {
                spans.push(Span::styled("  ·  ", dim));
            }
            spans.extend(b);
        }
        Line::from(spans)
    }

    /// The key bar: (key, label, action, priority). When a narrow window can't show them
    /// all, the lowest priorities go first; quit always stays.
    fn key_bar(&self) -> Vec<(String, String, String, u8)> {
        let typing = self.typing();
        let files = self.tab == Tab::Files;
        let mut keys: Vec<(String, String, String, u8)> = vec![];
        let mut k = |key: &str, label: &str, action: &str, pri: u8| keys.push((key.into(), label.into(), action.into(), pri));
        if files {
            k("c", "convert", "convert", 9);
            k("f", "browse", "browse", 8);
            k("r", "remove", "remove_file", 6);
            k("x", "cancel", "cancel_download", 7);
            k("o", "open", "reveal", 3);
            k("h", "history", "history", 4);
            if typing { k("tab", "use keys", "leave_url", 5) } else { k("esc", "file bar", "focus_url", 5) }
            k("ctrl+t", "download", "switch_tab", 6);
            k("t", "themes", "themes", 2);
            k("q", "quit", "quit", 10);
        } else {
            if self.clipboard_url.is_some() {
                k("v", "load copied link", "paste_clipboard", 9);
            }
            k("d", "download", "download", 9);
            k("c", "convert", "convert", 8);
            if matches!(self.card, Card::Playlist(_)) {
                k("e", "pick videos", "pick", 8);
            }
            k("x", "cancel", "cancel_download", 7);
            k("p", if self.paused { "resume" } else { "pause" }, "pause", 6);
            k("h", "history", "history", 5);
            k("w", "following", "following", 3);
            k("/", "search", "search", 4);
            k("l", "sites", "sites", 1);
            k("s", "settings", "settings", 4);
            k("o", "open", "reveal", 2);
            k("u", "update", "update", 1);
            if typing { k("tab", "use keys", "leave_url", 5) } else { k("esc", "url bar", "focus_url", 5) }
            k("ctrl+t", "files", "switch_tab", 6);
            k("t", "themes", "themes", 2);
            k("q", "quit", "quit", 10);
        }
        keys
    }

    /// The key bar as a grid, the way gingertea lays out its footer: the keys split into as
    /// few evenly balanced rows as fit, every row sharing the same column widths. Rows get a
    /// blank line between them on a tall window. When even the most rows allowed don't fit,
    /// the least-used keys go first; quit always stays.
    fn key_layout(&self, w: u16, h: u16) -> KeyLayout {
        let keys = self.key_bar();
        let item_w = |i: usize| (width(&keys[i].0) + 3 + width(&keys[i].1)) as u16;
        let step = if h >= 30 { 2 } else { 1 };
        let max_rows = if h >= 30 { 4 } else if h >= 24 { 2 } else { 1 };
        let mut shown: Vec<usize> = (0..keys.len()).collect();
        loop {
            for gap in [KEY_GAP, KEY_GAP_TIGHT] {
                for count in 1..=max_rows.min(shown.len()) {
                    let rows = balanced(&shown, count);
                    let cols = grid_columns(&rows, &item_w);
                    if cols.iter().sum::<u16>() + gap * cols.len().saturating_sub(1) as u16 <= w {
                        return KeyLayout { rows, cols, gap, step };
                    }
                }
            }
            let Some(pos) = (0..shown.len()).filter(|&n| keys[shown[n]].3 < 10).min_by_key(|&n| (keys[shown[n]].3, usize::MAX - n)) else {
                let rows = vec![shown];
                let cols = grid_columns(&rows, &item_w);
                return KeyLayout { rows, cols, gap: KEY_GAP_TIGHT, step };
            };
            shown.remove(pos);
        }
    }

    fn draw_main(&self, p: &mut Painter, area: Rect) {
        let t = self.theme;
        let (w, h) = (area.width, area.height);
        if w < 10 || h < 6 {
            return;
        }
        let x0 = 2u16;
        let iw = w.saturating_sub(4);
        let mut y = 0u16;
        // Logo (hidden on short terminals).
        if h >= 30 {
            y += 1;
            let lines = if iw as usize >= mascot::W { self.mascot.render(t) } else { logo::render(iw as usize, &mascot::logo_shades(t), mascot::logo_shadow(t)) };
            // Lines are equal width so the block centres as one.
            let lw = lines.iter().map(super::draw::line_width).max().unwrap_or(0) as u16;
            let lx = x0 + iw.saturating_sub(lw) / 2;
            for l in lines {
                p.line(lx, y, iw, l, Align::Left);
                y += 1;
            }
        }
        // Status line.
        y += 1;
        p.line(x0, y, iw, self.status_line(), Align::Center);
        y += 1;
        // Tabs.
        y += 1;
        let mut tx = x0;
        for (tab, label, id) in [(Tab::Download, "Download", "download"), (Tab::Files, "Convert files", "files")] {
            let active = self.tab == tab;
            let text = match (t.look.tabs, active) {
                (TabStyle::Bracket, true) => format!("[ {label} ]"),
                (TabStyle::Bracket, false) => format!("  {label}  "),
                _ => format!(" {label} "),
            };
            let tw = width(&text) as u16;
            let style = match (t.look.tabs, active) {
                (_, false) => fg(t.muted),
                (TabStyle::Chip, true) => Style::new().fg(t.bg.color()).bg(t.primary.color()).add_modifier(Modifier::BOLD),
                (_, true) => bold(t.primary),
            };
            p.text(tx, y, tw, &text, style);
            if active && t.look.tabs == TabStyle::Underline {
                p.text(tx, y + 1, tw, &"▔".repeat(tw as usize), fg(t.primary));
            }
            p.click(Rect::new(tx, y, tw, 1), format!("tab:{id}"));
            tx += tw + 2;
        }
        y += 1;
        // Key bar at the bottom.
        let layout = self.key_layout(iw, h);
        let n_rows = layout.height();
        p.text(0, h - 1 - n_rows, w, &t.look.rule.repeat(w as usize), fg(t.panel));
        self.draw_keys(p, x0, h - n_rows, iw, &layout);
        let bottom = h - 1 - n_rows;
        // Source row.
        y += 1;
        let files = self.tab == Tab::Files;
        let focused = if files { self.focus == Focus::FileInput } else { self.focus == Focus::Url };
        let border = if focused { t.primary } else { t.primary.over(t.bg, 0.5) };
        let row = Rect::new(x0, y, iw, 3);
        p.border(row, false, border, if files { "files" } else { "url" }, bold(t.primary));
        p.text(x0 + 2, y + 1, 1, t.look.prompt, bold(t.primary));
        let input = if files { &self.files.input } else { &self.url };
        input.draw(p, x0 + 4, y + 1, iw.saturating_sub(6), t.bg, t.text, focused);
        p.click(Rect::new(x0 + 1, y + 1, iw - 2, 1), "url");
        y += 3;
        if files {
            self.draw_files(p, x0, y, iw, bottom);
        } else {
            y = self.draw_card(p, x0, y, iw, bottom);
            self.draw_queue(p, x0, y, iw, bottom, &self.jobs, "downloads", "downloading", "Nothing yet. Downloads show up here.", self.queue_scroll, self.paused, self.active.len());
        }
    }

    /// One key: a small chip, then its label; solid while the mouse is over it.
    fn draw_key(&self, p: &mut Painter, x: u16, y: u16, i: usize, keys: &[(String, String, String, u8)]) -> u16 {
        let t = self.theme;
        let (k, label, action, _) = &keys[i];
        let id = format!("key:{action}");
        let hot = self.modals.is_empty() && self.hover.as_deref() == Some(id.as_str());
        let chip = format!(" {k} ");
        let cw = width(&chip) as u16;
        let lw = width(label) as u16 + 1;
        if hot {
            let solid = Style::new().fg(t.bg.color()).bg(t.primary.color()).add_modifier(Modifier::BOLD);
            p.text(x, y, cw, &chip, solid);
            p.text(x + cw, y, lw + 1, &format!(" {label} "), solid);
        } else {
            p.text(x, y, cw, &chip, bold(t.primary).bg(t.panel.color()));
            p.text(x + cw, y, lw, &format!(" {label}"), fg(t.muted));
        }
        p.click(Rect::new(x, y, cw + lw + 1, 1), id);
        cw + lw
    }

    fn draw_keys(&self, p: &mut Painter, x: u16, y: u16, w: u16, layout: &KeyLayout) {
        let keys = self.key_bar();
        let total = layout.cols.iter().sum::<u16>() + layout.gap * layout.cols.len().saturating_sub(1) as u16;
        let gx = x + w.saturating_sub(total) / 2;
        for (r, row) in layout.rows.iter().enumerate() {
            let mut cx = gx;
            for (c, &i) in row.iter().enumerate() {
                self.draw_key(p, cx, y + r as u16 * layout.step, i, &keys);
                cx += layout.cols[c] + layout.gap;
            }
        }
    }

    fn draw_card(&self, p: &mut Painter, x: u16, y: u16, w: u16, bottom: u16) -> u16 {
        let t = self.theme;
        let mut y = y + 1;
        if y >= bottom {
            return y;
        }
        let title = match &self.card {
            Card::Playlist(_) => "playlist",
            _ => "video",
        };
        p.rule(x, y, w, title, bold(t.primary));
        y += 2;
        match &self.card {
            Card::Empty => {
                p.text(x, y, w, "Paste a link above", bold(t.muted));
                let n = width("Paste a link above") as u16;
                p.text(x + n, y, w.saturating_sub(n), " to get started.", fg(t.muted));
                y += 2;
                y += p.para(x, y, w, vec![Span::styled(EMPTY_HINT, fg(t.muted.over(t.bg, 0.6)))], 4);
            }
            Card::Loading(url) => {
                loading_dots(p, x, y);
                p.line(x + 8, y, w.saturating_sub(8), Line::from(vec![Span::styled("Looking up ", fg(t.muted)), Span::styled(url.clone(), bold(t.muted)), Span::styled("…", fg(t.muted))]), Align::Left);
                y += 1;
            }
            Card::Error(e) => {
                y += p.para(x, y, w, vec![Span::styled(format!("✗ {}", e.message), bold(t.error))], 3);
                y += 1;
                let mut spans = vec![Span::styled(e.hint.clone(), fg(t.muted))];
                if !e.detail.is_empty() && e.detail != e.message {
                    spans.push(Span::styled(format!("\n\nyt-dlp: {}", e.detail), fg(t.muted.over(t.bg, 0.6))));
                }
                y += p.para(x, y, w, spans, 8);
                y += 1;
                let mut bx = x;
                for (id, label) in self.card_buttons() {
                    let bw = flat_button(p, bx, y, &label, self.focus == Focus::Button(id), id == "retry", &format!("btn:{id}"));
                    bx += bw + 2;
                }
                y += 1;
            }
            Card::Video(..) | Card::Playlist(_) => {
                let thumb = Rect::new(x, y, 36.min(w), 9);
                self.thumb.draw(p, thumb);
                let dx = x + 39;
                let dw = w.saturating_sub(39);
                let (title, rows, hint) = match &self.card {
                    Card::Video(info, preset) => {
                        let mut rows = vec![("channel", info.channel.clone()), ("length", info.duration_text()), ("uploaded", info.date_text())];
                        if let Some(hh) = info.max_height {
                            rows.push(("quality", format!("up to {hh}p")));
                        }
                        if !info.extractor.is_empty() {
                            rows.push(("site", info.extractor.clone()));
                        }
                        let hint = if preset.is_empty() { "best mp4 · pick a format".to_string() } else { format!("{preset} preset") };
                        (info.title.clone(), rows, hint)
                    }
                    Card::Playlist(pl) => {
                        let count = if pl.truncated() { format!("first {} of {}", pl.entries.len(), pl.total.unwrap_or(0)) } else { pl.entries.len().to_string() };
                        let mut rows = vec![("channel", pl.channel.clone()), ("videos", count)];
                        if let Some(d) = pl.duration().filter(|d| *d > 0) {
                            let (hh, rem) = (d / 3600, d % 3600);
                            rows.push(("length", if hh > 0 { format!("{hh}h {:02}m", rem / 60) } else { format!("{}m", rem / 60) }));
                        }
                        if !pl.extractor.is_empty() {
                            rows.push(("site", pl.extractor.clone()));
                        }
                        (pl.title.clone(), rows, format!("{} of {} picked", self.picked.len(), pl.entries.len()))
                    }
                    _ => unreachable!(),
                };
                let mut dy = y;
                dy += p.para(dx, dy, dw, vec![Span::styled(title, bold(t.text))], 3);
                dy += 1;
                for (k, v) in rows {
                    let line = Line::from(vec![
                        Span::styled(k.to_string(), fg(t.muted)),
                        Span::styled(format!(" {} ", "·".repeat(LEADER - k.len())), fg(t.disabled)),
                        Span::styled(v, fg(t.text)),
                    ]);
                    p.line(dx, dy, dw, line, Align::Left);
                    dy += 1;
                }
                dy += 1;
                let mut bx = dx;
                for (id, label) in self.card_buttons() {
                    let bw = flat_button(p, bx, dy, &label, self.focus == Focus::Button(id), false, &format!("btn:{id}"));
                    bx += bw + 2;
                }
                p.text(bx, dy, (dx + dw).saturating_sub(bx), &hint, fg(t.muted));
                dy += 1;
                y = dy.max(y + 9);
            }
        }
        y
    }

    #[allow(clippy::too_many_arguments)]
    fn draw_queue(&self, p: &mut Painter, x: u16, y: u16, w: u16, bottom: u16, jobs: &[Job], title: &str, busy: &str, empty: &str, scroll: usize, paused: bool, running: usize) {
        let t = self.theme;
        let y = y + 1;
        if y >= bottom {
            return;
        }
        let queued = jobs.iter().filter(|j| j.state == State::Queued).count();
        let mut bits = vec![];
        if running > 0 {
            bits.push(format!("{running} {busy}"));
        }
        if queued > 0 {
            bits.push(format!("{queued} waiting"));
        }
        if paused && queued > 0 {
            bits.push("paused".into());
        }
        let title = if bits.is_empty() { title.to_string() } else { format!("{title} · {}", bits.join(", ")) };
        p.rule(x, y, w, &title, bold(t.muted));
        let mut y = y + 2;
        if jobs.is_empty() {
            if y < bottom {
                p.text(x, y, w, empty, fg(t.muted));
            }
            return;
        }
        let rows = (bottom.saturating_sub(y) as usize).div_ceil(3);
        let max_scroll = jobs.len().saturating_sub(rows.max(1));
        for job in jobs.iter().rev().skip(scroll.min(max_scroll)) {
            if y + 1 >= bottom {
                break;
            }
            self.draw_job(p, x, y, w, job);
            y += 3;
        }
    }

    fn draw_job(&self, p: &mut Painter, x: u16, y: u16, w: u16, job: &Job) {
        let t = self.theme;
        let running = !job.state.finished() && job.state != State::Queued;
        let (mark, mark_color) = match job.state {
            State::Done => ("✓", t.success),
            State::Failed => ("✗", t.error),
            State::Cancelled => ("–", t.muted),
            State::Queued => ("·", t.muted),
            _ => (if job.file.is_some() { "⟳" } else { "↓" }, t.primary),
        };
        p.text(x, y, 2, mark, bold(if running { t.primary } else { mark_color }));
        let mut kind = job.choice.label();
        if let Some(f) = &job.file {
            let ext = f.path.extension().map(|e| e.to_string_lossy().to_uppercase()).unwrap_or_else(|| "FILE".into());
            kind = format!("{ext} → {kind}");
        }
        let cancel_w = 8u16;
        let kw = width(&kind) as u16;
        let right = x + w;
        let cancel_x = right.saturating_sub(cancel_w);
        let kind_x = cancel_x.saturating_sub(2 + kw);
        let title_w = kind_x.saturating_sub(x + 2 + 2);
        p.text(x + 2, y, title_w, &job.title, bold(t.text));
        p.text(kind_x, y, kw, &kind, fg(t.primary));
        if !job.state.finished() {
            let focused = self.focus == Focus::Cancel(job.id);
            let style = if focused { Style::new().fg(t.bg.color()).bg(t.error.color()).add_modifier(Modifier::BOLD) } else if job.cancelled() { fg(t.disabled) } else { bold(t.muted) };
            p.text(cancel_x, y, cancel_w, " cancel ", style);
            p.click(Rect::new(cancel_x, y, cancel_w, 1), format!("cancel:{}", job.id));
        }
        // Body: the bar, then what's happening.
        let by = y + 1;
        let (fraction, fill) = match job.state {
            State::Done => (Some(1.0), t.success),
            State::Cancelled | State::Failed | State::Queued => (Some(0.0), t.primary),
            _ if job.cancelled() => (None, t.primary),
            _ => (job.fraction, t.primary),
        };
        let fill = if matches!(job.state, State::Failed | State::Cancelled) { t.panel } else { fill };
        let bar_w = 30.min(w.saturating_sub(4));
        block_bar(p, x + 2, by, bar_w, fraction, fill, t.panel);
        let sx = x + 2 + bar_w + 2;
        let sw = (x + w).saturating_sub(sx);
        let status: Line<'static> = match job.state {
            State::Done => Line::from(vec![
                Span::styled("✓ Saved", bold(t.success)),
                Span::styled(format!(" → {}", short_path(job.filepath.as_deref().unwrap_or(""))), fg(t.muted)),
            ]),
            State::Cancelled => Line::from(Span::styled("Cancelled", fg(t.muted.over(t.bg, 0.6)))),
            _ if job.cancelled() => Line::from(Span::styled("Cancelling…", fg(t.muted.over(t.bg, 0.6)))),
            State::Failed => {
                let f = errors::explain(job.error.as_deref().unwrap_or(""));
                let hint = if f.kind == Kind::Login { "Press s to choose a browser for cookies, then try again.".to_string() } else { f.hint.clone() };
                let mut spans = vec![Span::styled(format!("✗ {}", f.message), bold(t.error))];
                if !hint.is_empty() {
                    spans.push(Span::styled(format!(" {hint}"), fg(t.muted.over(t.bg, 0.6))));
                }
                Line::from(spans)
            }
            State::Queued => Line::from(Span::styled("Waiting in line", fg(t.muted.over(t.bg, 0.6)))),
            _ => match job.fraction {
                None => Line::from(Span::styled(format!("{}…", job.status), fg(t.muted))),
                Some(f) => {
                    let mut bits = vec![job.status.clone(), format!("{:.0}%", f * 100.0)];
                    if let Some(s) = job.speed.filter(|s| *s > 0.0) {
                        bits.push(format!("{}/s", human_size(s)));
                    }
                    if let Some(e) = job.eta {
                        bits.push(format!("{} left", human_eta(e)));
                    }
                    Line::from(Span::styled(bits.join(" · "), fg(t.muted)))
                }
            },
        };
        p.line(sx, by, sw, status, Align::Left);
    }

    fn draw_files(&self, p: &mut Painter, x: u16, y: u16, w: u16, bottom: u16) {
        let t = self.theme;
        let fp = &self.files;
        let mut y = y + 1;
        let n = fp.files.len();
        let title = if n > 0 { format!("{n} file{}", if n != 1 { "s" } else { "" }) } else { "files".into() };
        p.rule(x, y, w, &title, bold(t.primary));
        y += 2;
        let max_card = (bottom.saturating_sub(y)) / 2;
        let card_top = y;
        if n == 0 && fp.reading == 0 {
            p.text(x, y, w, "Drop files above", bold(t.muted));
            let dw = width("Drop files above") as u16;
            p.text(x + dw, y, w.saturating_sub(dw), " to convert them.", fg(t.muted));
            y += 2;
            y += p.para(x, y, w, vec![Span::styled(FILES_HINT, fg(t.muted.over(t.bg, 0.6)))], 4);
        }
        if fp.reading > 0 {
            let text = if fp.status.is_empty() { format!("Reading {} file{}…", fp.reading, if fp.reading != 1 { "s" } else { "" }) } else { fp.status.clone() };
            p.text(x, y, w, &text, fg(t.muted.over(t.bg, 0.6)));
            y += 1;
        }
        if n > 0 {
            let name_w = fp.files.iter().map(|f| width(&f.name())).max().unwrap_or(0).min(32);
            let rows: Vec<Line<'static>> = fp
                .files
                .iter()
                .map(|f| {
                    let name = super::draw::pad(&super::draw::cut(&f.name(), name_w), name_w);
                    Line::from(vec![Span::styled("▸ ", bold(t.text)), Span::styled(name, bold(t.text)), Span::raw("  "), Span::styled(describe(f), fg(t.muted.over(t.bg, 0.6)))])
                })
                .collect();
            let list_h = (n as u16).min(12).min(max_card.saturating_sub(3).max(1));
            fp.list.draw(p, Rect::new(x, y, w, list_h), rows, self.focus == Focus::FileList, "files");
            y += list_h + 1;
            let mut bx = x;
            let convert = if n > 1 { "[c] convert all" } else { "[c] convert" };
            bx += flat_button(p, bx, y, convert, self.focus == Focus::FileButton("convert"), false, "fbtn:convert") + 2;
            bx += flat_button(p, bx, y, "[r] remove", self.focus == Focus::FileButton("remove"), false, "fbtn:remove") + 2;
            p.text(bx, y, (x + w).saturating_sub(bx), "saves next to each original", fg(t.muted));
            y += 1;
        }
        let _ = card_top;
        self.draw_queue(p, x, y, w, bottom, &fp.jobs, "conversions", "converting", "Nothing yet. Conversions show up here.", fp.scroll, false, usize::from(fp.running.is_some()));
    }
}

const KEY_GAP: u16 = 5;
const KEY_GAP_TIGHT: u16 = 3;

struct KeyLayout {
    rows: Vec<Vec<usize>>,
    cols: Vec<u16>,
    gap: u16,
    /// Rows apart: 2 leaves a blank line between them.
    step: u16,
}

impl KeyLayout {
    fn height(&self) -> u16 {
        (self.rows.len() as u16).saturating_sub(1) * self.step + 1
    }
}

/// `items` split into `count` rows in order, the first rows taking any extra.
fn balanced(items: &[usize], count: usize) -> Vec<Vec<usize>> {
    let mut rows = vec![];
    let mut next = 0;
    for row in 0..count {
        let len = items.len() / count + usize::from(row < items.len() % count);
        rows.push(items[next..next + len].to_vec());
        next += len;
    }
    rows
}

/// Column widths shared by every row: the widest key in each position.
fn grid_columns(rows: &[Vec<usize>], item_w: &dyn Fn(usize) -> u16) -> Vec<u16> {
    let n = rows.iter().map(|g| g.len()).max().unwrap_or(0);
    (0..n).map(|c| rows.iter().filter_map(|g| g.get(c)).map(|&i| item_w(i)).max().unwrap_or(0)).collect()
}

/// One line of facts for the file list: `video · 1920×1080 · HEVC · 2:14 · 412 MB`.
pub fn describe(info: &FileInfo) -> String {
    let mut bits: Vec<String> = vec![match info.media {
        Media::Animated => "animated",
        Media::Audio => "audio",
        Media::Image => "image",
        Media::Video => "video",
    }
    .into()];
    if info.media != Media::Audio {
        if let Some(v) = &info.video {
            if let (Some(vw), Some(vh)) = (v.width, v.height) {
                bits.push(format!("{vw}×{vh}"));
            }
        }
    }
    if info.media == Media::Video {
        if let Some(v) = &info.video {
            bits.push(v.codec.to_uppercase());
        }
    } else if info.media == Media::Audio {
        if let Some(a) = &info.audio {
            bits.push(a.codec.to_uppercase());
        }
    }
    bits.push(duration_text(info.duration));
    bits.push(human_size(info.size as f64));
    bits.into_iter().filter(|b| !b.is_empty()).collect::<Vec<_>>().join(" · ")
}

#[allow(dead_code)]
fn unused(_: Rgb, _: Style) {
    let _ = on;
    let _ = wrap;
}
