//! The terminal app: one event loop fed by a channel. Key presses, mouse clicks and every
//! worker thread's results arrive as `Msg`s; the screen redraws only after something
//! changed, and ticks for animation only while something animates.

pub mod convert;
pub mod dialogs;
pub mod draw;
pub mod fmt;
pub mod logo;
mod mascot;
pub mod screen;
pub mod theme;
pub mod widgets;

use crate::config::{self, Settings};
use crate::engine::frames::FrameSource;
use crate::engine::local::FileInfo;
use crate::engine::options::Choice;
use crate::engine::probe::{PlaylistInfo, Probed, VideoInfo};
use crate::engine::runner::Progress;
use crate::library::{Followed, Preset};
use crate::transcripts::Transcript;
use crossterm::event::{
    self, DisableBracketedPaste, DisableFocusChange, DisableMouseCapture, EnableBracketedPaste, EnableFocusChange,
    EnableMouseCapture, Event, KeyEvent,
};
use crossterm::execute;
use crossterm::terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen};
use draw::Painter;
use image::RgbImage;
use ratatui::backend::CrosstermBackend;
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::Terminal;
use std::any::Any;
use std::collections::BTreeSet;
use std::io::Write;
use std::path::PathBuf;
use std::sync::mpsc::{self, RecvTimeoutError, Sender};
use std::sync::Arc;
use std::time::{Duration, Instant};
use theme::Theme;
use widgets::Toasts;

/// Everything that can wake the loop.
pub enum Msg {
    Term(Event),
    Probed { token: u64, result: Result<Probed, String> },
    Thumb { token: u64, image: RgbImage },
    Progress { id: u64, p: Progress },
    Finished { id: u64, result: Result<String, String> },
    FileRead { result: Result<FileInfo, (Option<PathBuf>, String)>, count: usize },
    FilesStatus(String),
    Clipboard(String),
    YtDlpVersion(String),
    UpdateFound(String),
    UpdateDone(bool, String),
    Ffmpeg(bool),
    Sync(bool),
    Followed(Result<Followed, String>),
    Transcripts(Vec<Transcript>),
    Frame { token: u64, image: RgbImage },
    Sites(Vec<(String, bool)>),
}

/// What a dialog closes with, or hands to the main screen while it stays open.
pub enum Res {
    Choice(Choice),
    Settings(Box<Settings>),
    Browser(String),
    Redownload(String),
    GetNew(Vec<String>),
    SyncNow,
    Picked(BTreeSet<usize>),
    Clip(Option<f64>, Option<f64>),
    Preset(Preset),
    File(PathBuf),
    ClipFile(String, f64, f64),
    Theme(String),
    /// From the mix/playlist question: true loads the whole list to pick from.
    WholeList(bool),
}

pub enum Out {
    None,
    Close(Option<Res>),
    Push(Box<dyn Dialog>),
    Main(Res),
}

/// Why a dialog is open, so its result lands in the right place.
pub enum Purpose {
    None,
    Video(Box<VideoInfo>),
    Playlist(Box<PlaylistInfo>),
    Follow { url: String, feed: Option<Box<PlaylistInfo>> },
    Files(Vec<FileInfo>),
    Cookies,
    Pick(String),
    Settings,
    History,
    Following,
    Search,
    Browse,
    Theme(String),
    ListAsk(String),
}

pub struct Cx<'a> {
    pub tx: &'a Sender<Msg>,
    pub toasts: &'a mut Toasts,
}

pub trait Dialog {
    /// Width of the frame, before the 95% cap.
    fn width(&self) -> u16;
    /// A fixed frame height (lists that fill it), or None to fit the content.
    fn height(&self) -> Option<u16> {
        None
    }
    fn content_height(&self, w: u16) -> u16;
    /// Draws into `r` (inside border and padding); returns the focused row, to scroll to.
    fn draw(&self, p: &mut Painter, r: Rect) -> u16;
    fn key(&mut self, k: &KeyEvent, cx: &mut Cx) -> Out;
    fn paste(&mut self, _text: &str, _cx: &mut Cx) -> Out {
        Out::None
    }
    fn click(&mut self, _id: &str, _cx: &mut Cx) -> Out {
        Out::None
    }
    /// A worker's result meant for this dialog. True if it took it.
    fn msg(&mut self, _m: &Msg, _cx: &mut Cx) -> bool {
        false
    }
    /// A child dialog closed with `res`.
    fn child(&mut self, _res: Res, _cx: &mut Cx) -> Out {
        Out::None
    }
    fn animating(&self) -> bool {
        false
    }
    fn any(&mut self) -> &mut dyn Any;
}

pub fn spawn(f: impl FnOnce() + Send + 'static) {
    std::thread::spawn(f);
}

/// Spawn `f` and send what it returns.
pub fn work<T: Send + 'static>(tx: &Sender<Msg>, f: impl FnOnce() -> T + Send + 'static, wrap: impl FnOnce(T) -> Msg + Send + 'static) {
    let tx = tx.clone();
    spawn(move || {
        let v = f();
        let _ = tx.send(wrap(v));
    });
}

pub type Frames = Arc<dyn FrameSource>;

fn restore() {
    let _ = disable_raw_mode();
    let mut out = std::io::stdout();
    let _ = execute!(out, DisableMouseCapture, DisableBracketedPaste, DisableFocusChange, LeaveAlternateScreen, crossterm::cursor::Show);
}

pub fn run(initial: String) -> i32 {
    let mut settings = config::load();
    if theme::themes().iter().all(|t| t.name != settings.theme) {
        settings.theme = "carrot".into();
    }
    if enable_raw_mode().is_err() {
        eprintln!("carrot: this needs a terminal");
        return 1;
    }
    let mut out = std::io::stdout();
    let _ = execute!(out, EnterAlternateScreen, EnableMouseCapture, EnableBracketedPaste, EnableFocusChange);
    let hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        restore();
        hook(info);
    }));
    let mut terminal = match Terminal::new(CrosstermBackend::new(std::io::stdout())) {
        Ok(t) => t,
        Err(e) => {
            restore();
            eprintln!("carrot: {e}");
            return 1;
        }
    };
    let _ = terminal.hide_cursor();
    let (tx, rx) = mpsc::channel::<Msg>();
    {
        let tx = tx.clone();
        spawn(move || {
            while let Ok(ev) = event::read() {
                if tx.send(Msg::Term(ev)).is_err() {
                    break;
                }
            }
        });
    }
    let size = terminal.size().map(|s| (s.width, s.height)).unwrap_or((80, 24));
    let mut app = screen::App::new(settings, tx, size);
    app.start(initial);
    loop {
        if app.dirty {
            app.dirty = false;
            let _ = terminal.draw(|f| {
                let area = f.area();
                app.render(f.buffer_mut(), area);
            });
            if std::mem::take(&mut app.toasts.bell) {
                let _ = std::io::stdout().write_all(b"\x07");
                let _ = std::io::stdout().flush();
            }
        }
        if app.quit {
            break;
        }
        let timeout = app.timeout();
        match rx.recv_timeout(timeout) {
            Ok(msg) => {
                app.handle(msg);
                // Take whatever else is already waiting before drawing again.
                let start = Instant::now();
                while let Ok(m) = rx.try_recv() {
                    app.handle(m);
                    if start.elapsed() > Duration::from_millis(30) {
                        break;
                    }
                }
                app.update_mascot();
            }
            Err(RecvTimeoutError::Timeout) => app.on_tick(),
            Err(RecvTimeoutError::Disconnected) => break,
        }
    }
    app.shutdown();
    drop(terminal);
    restore();
    0
}

/// The modal frame: dims what's under it, then a rounded accent box with padding 1 2,
/// centred, scrolling its content when it doesn't fit.
pub fn draw_dialog(buf: &mut Buffer, t: &'static Theme, clicks: &mut Vec<(Rect, String)>, tick: u64, area: Rect, d: &dyn Dialog) {
    {
        let mut p = Painter { buf, t, clicks, tick };
        p.dim(0.7);
    }
    let max_w = (area.width as u32 * 95 / 100) as u16;
    let max_h = (area.height as u32 * 95 / 100) as u16;
    let w = d.width().min(max_w).max(20.min(area.width));
    let inner_w = w.saturating_sub(6);
    let content_h = d.content_height(inner_w);
    let h = match d.height() {
        Some(fixed) => fixed.min(max_h),
        None => (content_h + 4).min(max_h),
    };
    let x = area.x + (area.width - w) / 2;
    let y = area.y + (area.height.saturating_sub(h)) / 2;
    let frame = Rect::new(x, y, w, h);
    let visible = h.saturating_sub(4);
    let content = Rect::new(x + 3, y + 2, inner_w, visible);
    {
        let mut p = Painter { buf, t, clicks, tick };
        p.clear(frame, t.bg);
        p.border(frame, true, t.primary, "", draw::fg(t.primary));
        // Clicking the backdrop does nothing, but a click inside shouldn't fall through.
    }
    if d.height().is_some() || content_h <= visible {
        let mut p = Painter { buf, t, clicks, tick };
        d.draw(&mut p, Rect::new(content.x, content.y, content.width, visible.max(content_h.min(visible))));
        return;
    }
    // Too tall: draw into a scratch buffer and copy the part around the focus.
    let full = Rect::new(content.x, content.y, content.width, content_h);
    let mut scratch = Buffer::empty(full);
    let mut inner_clicks = vec![];
    let focus_y = {
        let mut p = Painter { buf: &mut scratch, t, clicks: &mut inner_clicks, tick };
        p.fill(full, t.bg);
        d.draw(&mut p, full)
    };
    let rel = focus_y.saturating_sub(content.y);
    let scroll = rel.saturating_sub(visible / 2).min(content_h - visible);
    for row in 0..visible {
        for col in 0..content.width {
            let src = scratch[(content.x + col, content.y + scroll + row)].clone();
            buf[(content.x + col, content.y + row)] = src;
        }
    }
    for (r, id) in inner_clicks {
        if r.y >= content.y + scroll && r.y < content.y + scroll + visible {
            clicks.push((Rect::new(r.x, r.y - scroll, r.width, r.height.min(content.y + scroll + visible - r.y)), id));
        }
    }
    // A scrollbar on the frame's right edge.
    let mut p = Painter { buf, t, clicks, tick };
    let bar = ((visible as u32 * visible as u32) / content_h as u32).max(1) as u16;
    let top = (scroll as u32 * visible as u32 / content_h as u32) as u16;
    for i in 0..visible {
        let c = if i >= top && i < top + bar { t.panel_l2 } else { t.panel };
        p.text(x + w - 2, content.y + i, 1, "▐", draw::fg(c));
    }
}
