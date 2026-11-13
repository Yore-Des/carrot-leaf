//! The few widgets the screens are built from: a one-line text input, a scrolling option
//! list, a half-block picture, the block progress bar, buttons and toasts.

use super::draw::{bold, fg, on, wrap, Align, Painter};
use super::theme::{Highlight, Rgb};
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use image::RgbImage;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use std::cell::RefCell;
use std::time::{Duration, Instant};
use unicode_width::UnicodeWidthChar;

// ---------- text input ----------

#[derive(Clone, Default)]
pub struct TextInput {
    pub value: String,
    pub placeholder: String,
    /// Cursor position in chars.
    pub cursor: usize,
    /// Everything selected: the next edit replaces it.
    pub selected: bool,
    pub digits_only: bool,
    scroll: RefCell<usize>,
}

#[derive(PartialEq, Debug)]
pub enum Edit {
    None,
    Changed,
    Submitted,
    /// Not a key the input uses: let the screen have it.
    Ignored,
}

impl TextInput {
    pub fn new(value: &str, placeholder: &str) -> Self {
        TextInput { value: value.into(), placeholder: placeholder.into(), cursor: value.chars().count(), ..Default::default() }
    }
    pub fn set(&mut self, value: &str) {
        self.value = value.into();
        self.cursor = value.chars().count();
        self.selected = false;
    }
    pub fn select_all(&mut self) {
        self.selected = !self.value.is_empty();
        self.cursor = self.value.chars().count();
    }
    fn byte(&self, char_idx: usize) -> usize {
        self.value.char_indices().nth(char_idx).map(|(i, _)| i).unwrap_or(self.value.len())
    }
    fn take_selection(&mut self) -> bool {
        if self.selected {
            self.selected = false;
            self.value.clear();
            self.cursor = 0;
            return true;
        }
        false
    }
    pub fn insert(&mut self, text: &str) {
        self.take_selection();
        let text: String = if self.digits_only { text.chars().filter(|c| c.is_ascii_digit()).collect() } else { text.replace(['\n', '\r'], " ") };
        let at = self.byte(self.cursor);
        self.value.insert_str(at, &text);
        self.cursor += text.chars().count();
    }

    pub fn key(&mut self, k: &KeyEvent) -> Edit {
        let ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
        let alt = k.modifiers.contains(KeyModifiers::ALT);
        let len = self.value.chars().count();
        let before = self.value.clone();
        match k.code {
            KeyCode::Enter => return Edit::Submitted,
            KeyCode::Char('a') if ctrl => {
                self.selected = false;
                self.cursor = 0;
            }
            KeyCode::Char('e') if ctrl => {
                self.selected = false;
                self.cursor = len;
            }
            KeyCode::Char('u') if ctrl => {
                if !self.take_selection() {
                    let at = self.byte(self.cursor);
                    self.value.drain(..at);
                    self.cursor = 0;
                }
            }
            KeyCode::Char('k') if ctrl => {
                if !self.take_selection() {
                    let at = self.byte(self.cursor);
                    self.value.truncate(at);
                }
            }
            KeyCode::Char('w') if ctrl => {
                if !self.take_selection() {
                    let chars: Vec<char> = self.value.chars().collect();
                    let mut i = self.cursor;
                    while i > 0 && chars[i - 1] == ' ' {
                        i -= 1;
                    }
                    while i > 0 && chars[i - 1] != ' ' {
                        i -= 1;
                    }
                    let (a, b) = (self.byte(i), self.byte(self.cursor));
                    self.value.drain(a..b);
                    self.cursor = i;
                }
            }
            KeyCode::Char(c) if !ctrl && !alt => {
                if self.digits_only && !c.is_ascii_digit() {
                    return Edit::None;
                }
                self.insert(&c.to_string());
            }
            KeyCode::Backspace => {
                if !self.take_selection() && self.cursor > 0 {
                    let (a, b) = (self.byte(self.cursor - 1), self.byte(self.cursor));
                    self.value.drain(a..b);
                    self.cursor -= 1;
                }
            }
            KeyCode::Delete => {
                if !self.take_selection() && self.cursor < len {
                    let (a, b) = (self.byte(self.cursor), self.byte(self.cursor + 1));
                    self.value.drain(a..b);
                }
            }
            KeyCode::Left => {
                self.selected = false;
                self.cursor = self.cursor.saturating_sub(1);
            }
            KeyCode::Right => {
                self.selected = false;
                self.cursor = (self.cursor + 1).min(len);
            }
            KeyCode::Home => {
                self.selected = false;
                self.cursor = 0;
            }
            KeyCode::End => {
                self.selected = false;
                self.cursor = len;
            }
            _ => return Edit::Ignored,
        }
        if self.value != before { Edit::Changed } else { Edit::None }
    }

    /// One row at (x, y). `bg` fills the row; the cursor is a reversed cell while focused.
    pub fn draw(&self, p: &mut Painter, x: u16, y: u16, w: u16, bg: Rgb, text: Rgb, focused: bool) {
        if w == 0 {
            return;
        }
        let t = p.t;
        p.fill(Rect::new(x, y, w, 1), bg);
        let cur_style = Style::new().fg(bg.color()).bg(text.color());
        if self.value.is_empty() {
            let ph = Style::new().fg(t.muted.color()).bg(bg.color()).add_modifier(Modifier::ITALIC);
            p.line(x, y, w, Line::from(Span::styled(self.placeholder.clone(), ph)), Align::Left);
            if focused {
                let first = self.placeholder.chars().next().unwrap_or(' ').to_string();
                p.text(x, y, 1, &first, cur_style.add_modifier(Modifier::ITALIC));
            }
            return;
        }
        let chars: Vec<char> = self.value.chars().collect();
        let widths: Vec<usize> = chars.iter().map(|c| c.width().unwrap_or(0)).collect();
        // Keep the cursor in view.
        let room = w as usize - 1;
        let mut scroll = *self.scroll.borrow();
        scroll = scroll.min(self.cursor);
        while widths[scroll..self.cursor].iter().sum::<usize>() > room {
            scroll += 1;
        }
        *self.scroll.borrow_mut() = scroll;
        let style = if self.selected && focused { on(bg, text) } else { on(text, bg) };
        let mut cx = x;
        for (i, ch) in chars.iter().enumerate().skip(scroll) {
            if cx + widths[i] as u16 > x + w {
                break;
            }
            let s = if focused && i == self.cursor && !self.selected { cur_style } else { style };
            p.text(cx, y, widths[i] as u16, &ch.to_string(), s);
            cx += widths[i] as u16;
        }
        if focused && !self.selected && self.cursor >= chars.len() && cx < x + w {
            p.text(cx, y, 1, " ", cur_style);
        }
    }
}

// ---------- option list ----------

#[derive(Clone, Default)]
pub struct ListState {
    pub index: usize,
    offset: RefCell<usize>,
}

impl ListState {
    pub fn new(index: usize) -> Self {
        ListState { index, offset: RefCell::new(0) }
    }
    /// Scroll so the highlighted row is the first one shown.
    pub fn show_at_top(&self) {
        *self.offset.borrow_mut() = self.index;
    }
    /// Arrow keys and friends. `enabled` skips greyed-out rows. True if the key was used.
    pub fn key(&mut self, k: &KeyEvent, len: usize, enabled: Option<&[bool]>) -> bool {
        if len == 0 {
            return matches!(k.code, KeyCode::Up | KeyCode::Down | KeyCode::PageUp | KeyCode::PageDown | KeyCode::Home | KeyCode::End);
        }
        let ok = |i: usize| enabled.map(|e| e.get(i).copied().unwrap_or(true)).unwrap_or(true);
        let step = |from: usize, down: bool, n: usize| -> usize {
            let mut at = from;
            let mut moved = 0;
            let mut i = from;
            loop {
                if down {
                    if i + 1 >= len {
                        break;
                    }
                    i += 1;
                } else {
                    if i == 0 {
                        break;
                    }
                    i -= 1;
                }
                if ok(i) {
                    at = i;
                    moved += 1;
                    if moved >= n {
                        break;
                    }
                }
            }
            at
        };
        self.index = match k.code {
            KeyCode::Up => step(self.index, false, 1),
            KeyCode::Down => step(self.index, true, 1),
            KeyCode::PageUp => step(self.index, false, 10),
            KeyCode::PageDown => step(self.index, true, 10),
            KeyCode::Home => (0..len).find(|&i| ok(i)).unwrap_or(0),
            KeyCode::End => (0..len).rev().find(|&i| ok(i)).unwrap_or(0),
            _ => return false,
        };
        true
    }
    pub fn clamp(&mut self, len: usize) {
        if len == 0 {
            self.index = 0;
        } else if self.index >= len {
            self.index = len - 1;
        }
    }

    /// Rows in `r`, scrolled so the highlighted one shows. Clicks report `{id}:{row}`.
    pub fn draw(&self, p: &mut Painter, r: Rect, rows: Vec<Line<'static>>, focused: bool, id: &str) {
        if r.height == 0 || r.width == 0 {
            return;
        }
        let t = p.t;
        let h = r.height as usize;
        let mut off = *self.offset.borrow();
        if self.index < off {
            off = self.index;
        }
        if self.index >= off + h {
            off = self.index + 1 - h;
        }
        off = off.min(rows.len().saturating_sub(h));
        *self.offset.borrow_mut() = off;
        let n = rows.len();
        for (i, row) in rows.into_iter().enumerate().skip(off).take(h) {
            let y = r.y + (i - off) as u16;
            // Every row sits one column in, so both highlight styles line up the same.
            if i == self.index {
                let bar = t.look.highlight == Highlight::Bar;
                let (f, b) = match (bar, focused) {
                    (true, true) => (t.text, t.primary.over(t.bg, 0.18)),
                    (false, true) => (t.bg, t.primary),
                    (_, false) => (t.text, t.panel),
                };
                p.fill(Rect::new(r.x, y, r.width, 1), b);
                if bar {
                    p.text(r.x, y, 1, "▌", fg(if focused { t.primary } else { t.muted }).bg(b.color()));
                }
                let spans: Vec<Span<'static>> = row
                    .spans
                    .into_iter()
                    .map(|s| {
                        let st = s.style.bg(b.color()).add_modifier(Modifier::BOLD);
                        // Colour swatches keep their colour (unless it would vanish into the fill).
                        let keep = s.style.fg.is_some() && (bar || s.content.starts_with('■'));
                        let st = if keep && s.style.fg != Some(b.color()) { st } else { st.fg(f.color()) };
                        Span::styled(s.content, st)
                    })
                    .collect();
                p.line(r.x + 1, y, r.width.saturating_sub(1), Line::from(spans), Align::Left);
            } else {
                p.line(r.x + 1, y, r.width.saturating_sub(1), row, Align::Left);
            }
            p.click(Rect::new(r.x, y, r.width, 1), format!("{id}:{i}"));
        }
        // A thin scrollbar when the rows don't fit.
        if n > h && r.width > 2 {
            let bar = ((h * h) / n).max(1);
            let top = (off * h) / n;
            for i in 0..h {
                let c = if i >= top && i < top + bar { t.panel_l2 } else { t.panel };
                p.text(r.right() - 1, r.y + i as u16, 1, "▐", fg(c));
            }
        }
    }
}

// ---------- pictures ----------

#[derive(Default)]
pub struct Picture {
    pub image: Option<RgbImage>,
    cache: RefCell<Option<((u16, u16), RgbImage)>>,
}

impl Picture {
    pub fn set(&mut self, image: Option<RgbImage>) {
        self.image = image;
        *self.cache.borrow_mut() = None;
    }
    /// The picture cropped to fill `r`, two pixels per cell; a ▶ on panel when there's none.
    pub fn draw(&self, p: &mut Painter, r: Rect) {
        let t = p.t;
        p.fill(r, t.panel);
        let Some(img) = &self.image else {
            if r.height > 0 {
                p.line(r.x, r.y + r.height / 2, r.width, Line::from(Span::styled("▶", bold(t.muted).bg(t.panel.color()))), Align::Center);
            }
            return;
        };
        let (w, h) = (r.width, r.height);
        if w < 2 || h < 1 {
            return;
        }
        let mut cache = self.cache.borrow_mut();
        if cache.as_ref().map(|c| c.0) != Some((w, h)) {
            *cache = Some(((w, h), fit(img, w as u32, h as u32 * 2)));
        }
        let small = &cache.as_ref().unwrap().1;
        for row in 0..h {
            for x in 0..w {
                let top = small.get_pixel(x as u32, row as u32 * 2);
                let bottom = small.get_pixel(x as u32, row as u32 * 2 + 1);
                let style = Style::new().fg(Rgb(top[0], top[1], top[2]).color()).bg(Rgb(bottom[0], bottom[1], bottom[2]).color());
                p.text(r.x + x, r.y + row, 1, "▀", style);
            }
        }
    }
}

/// Scale `img` to cover w×h and crop the middle, as PIL's ImageOps.fit does.
pub fn fit(img: &RgbImage, w: u32, h: u32) -> RgbImage {
    let (iw, ih) = img.dimensions();
    let scale = (w as f64 / iw as f64).max(h as f64 / ih as f64);
    let (sw, sh) = (((iw as f64 * scale).round() as u32).max(w), ((ih as f64 * scale).round() as u32).max(h));
    let scaled = image::imageops::resize(img, sw, sh, image::imageops::FilterType::Triangle);
    image::imageops::crop_imm(&scaled, (sw - w) / 2, (sh - h) / 2, w, h).to_image()
}

// ---------- progress ----------

/// The theme's bar cells; `None` is "busy", a span that slides along with `tick`.
pub fn block_bar(p: &mut Painter, x: u16, y: u16, w: u16, fraction: Option<f64>, fill: Rgb, track: Rgb) {
    let w = w as usize;
    let mut cells = vec![false; w];
    match fraction {
        None => {
            let span = (w / 5).max(4);
            let start = (p.tick as usize % (w + span)) as isize - span as isize;
            for i in start.max(0) as usize..((start + span as isize).max(0) as usize).min(w) {
                cells[i] = true;
            }
        }
        Some(f) => {
            for c in cells.iter_mut().take((w as f64 * f.clamp(0.0, 1.0)).round() as usize) {
                *c = true;
            }
        }
    }
    let (full, empty) = p.t.look.bar;
    // A track drawn with its own glyph reads better a little brighter than a solid one.
    let track = if empty == full { track } else { p.t.muted.over(p.t.bg, 0.5) };
    let spans: Vec<Span<'static>> = cells.iter().map(|&on| if on { Span::styled(full, fg(fill)) } else { Span::styled(empty, fg(track)) }).collect();
    p.line(x, y, w as u16, Line::from(spans), Align::Left);
}

/// Textual's LoadingIndicator: five dots with a brightness wave running through them.
pub fn loading_dots(p: &mut Painter, x: u16, y: u16) {
    let t = p.t;
    let spans: Vec<Span<'static>> = (0..5)
        .map(|i| {
            let phase = ((p.tick as f64 * 0.35) - i as f64 * 0.6).sin() * 0.5 + 0.5;
            Span::styled("●", fg(t.primary.over(t.bg, 0.25 + 0.75 * phase)))
        })
        .collect();
    p.line(x, y, 8, Line::from(spans), Align::Left);
}

// ---------- buttons ----------

/// A main-screen button: plain bold accent text, a solid chip while focused.
pub fn flat_button(p: &mut Painter, x: u16, y: u16, label: &str, focused: bool, muted: bool, id: &str) -> u16 {
    let t = p.t;
    let text = format!(" {label} ");
    let w = super::draw::width(&text) as u16;
    let style = if focused {
        Style::new().fg(t.bg.color()).bg(if muted { t.muted } else { t.primary }.color()).add_modifier(Modifier::BOLD)
    } else {
        bold(if muted { t.muted } else { t.primary }).bg(t.bg.color())
    };
    p.text(x, y, w, &text, style);
    p.click(Rect::new(x, y, w, 1), id.to_string());
    w
}

/// A small dialog button: a one-row filled chip with padding 0 2.
pub fn chip(p: &mut Painter, x: u16, y: u16, label: &str, focused: bool, active: bool, id: &str) -> u16 {
    let t = p.t;
    let text = format!("  {label}  ");
    let w = super::draw::width(&text) as u16;
    let style = if focused {
        Style::new().fg(t.bg.color()).bg(t.primary.color()).add_modifier(Modifier::BOLD)
    } else if active {
        Style::new().fg(t.bg.color()).bg(t.primary.color()).add_modifier(Modifier::BOLD)
    } else {
        on(t.fg, t.panel)
    };
    p.text(x, y, w, &text, style);
    p.click(Rect::new(x, y, w, 1), id.to_string());
    w
}

/// The dialog's big action buttons: equal halves, three rows, solid fills.
pub fn action_buttons(p: &mut Painter, x: u16, y: u16, w: u16, buttons: &[(&str, &str, bool)], focused: Option<usize>) {
    let t = p.t;
    let n = buttons.len() as u16;
    if n == 0 {
        return;
    }
    let gap = 2;
    let bw = (w.saturating_sub(gap * (n - 1))) / n;
    for (i, (label, id, primary)) in buttons.iter().enumerate() {
        let bx = x + i as u16 * (bw + gap);
        let width = if i as u16 == n - 1 { w - (bx - x) } else { bw };
        let is_focus = focused == Some(i);
        let (fill, text) = match (primary, is_focus, *id == "cancel") {
            (true, true, _) => (t.primary_l2, t.bg),
            (true, false, _) => (t.primary, t.bg),
            (false, true, true) => (t.error.over(t.bg, 0.7), t.bg),
            (false, true, false) => (t.panel_l2, t.fg),
            (false, false, _) => (t.panel, t.fg),
        };
        let r = Rect::new(bx, y, width, 3);
        p.fill(r, fill);
        p.line(bx, y + 1, width, Line::from(Span::styled(label.to_string(), bold(text).bg(fill.color()))), Align::Center);
        p.click(r, id.to_string());
    }
}

/// `[✓]` in the accent colour or `[ ]`, then the label.
pub fn toggle(p: &mut Painter, x: u16, y: u16, w: u16, label: &str, value: bool, focused: bool, id: &str) {
    let t = p.t;
    let mark = if value { "[✓]" } else { "[ ]" };
    let ms = if focused { Style::new().fg(t.bg.color()).bg(t.primary.color()).add_modifier(Modifier::BOLD) } else if value { bold(t.primary) } else { fg(t.muted) };
    p.text(x, y, 3, mark, ms);
    let ls = if focused { bold(t.text) } else { fg(t.text) };
    p.text(x + 4, y, w.saturating_sub(4), label, ls);
    p.click(Rect::new(x, y, w, 1), id.to_string());
}

/// A value that cycles through choices with ←/→ or enter: the label, `‹›` while focused.
pub fn cycle(p: &mut Painter, x: u16, y: u16, w: u16, label: &str, focused: bool, id: &str) {
    let t = p.t;
    let bg = if focused { t.primary.over(t.bg, 0.25) } else { t.bg };
    p.fill(Rect::new(x, y, w, 1), bg);
    let room = w.saturating_sub(4) as usize;
    p.text(x, y, room as u16, &super::draw::cut(label, room), on(t.text, bg));
    if focused {
        p.text(x + w.saturating_sub(2), y, 2, "‹›", bold(t.primary).bg(bg.color()));
    }
    p.click(Rect::new(x, y, w, 1), id.to_string());
}

// ---------- toasts ----------

#[derive(Clone, Copy, PartialEq)]
pub enum Severity {
    Info,
    Warning,
    Error,
}

pub struct Toast {
    pub title: String,
    pub message: String,
    pub severity: Severity,
    pub until: Instant,
}

#[derive(Default)]
pub struct Toasts {
    pub items: Vec<Toast>,
    pub bell: bool,
}

impl Toasts {
    pub fn notify(&mut self, message: &str, title: &str, severity: Severity) {
        self.notify_for(message, title, severity, 5.0);
    }
    pub fn notify_for(&mut self, message: &str, title: &str, severity: Severity, seconds: f64) {
        self.items.push(Toast { title: title.into(), message: message.into(), severity, until: Instant::now() + Duration::from_secs_f64(seconds) });
        if self.items.len() > 4 {
            self.items.remove(0);
        }
    }
    pub fn info(&mut self, message: &str) {
        self.notify(message, "", Severity::Info);
    }
    pub fn warn(&mut self, message: &str) {
        self.notify(message, "", Severity::Warning);
    }
    pub fn error(&mut self, message: &str, title: &str) {
        self.notify(message, title, Severity::Error);
    }
    pub fn bell(&mut self) {
        self.bell = true;
    }
    /// Drop expired toasts; true if any went.
    pub fn expire(&mut self) -> bool {
        let n = self.items.len();
        let now = Instant::now();
        self.items.retain(|t| t.until > now);
        n != self.items.len()
    }
    pub fn next_deadline(&self) -> Option<Instant> {
        self.items.iter().map(|t| t.until).min()
    }

    /// Bottom-right, newest lowest, like Textual's toast rack.
    pub fn draw(&self, p: &mut Painter, area: Rect) {
        let t = p.t;
        let w = 50.min(area.width.saturating_sub(4));
        if w < 10 {
            return;
        }
        let x = area.right() - w - 2;
        let mut bottom = area.bottom().saturating_sub(3);
        for toast in self.items.iter().rev() {
            let (edge, title_color) = match toast.severity {
                Severity::Info => (t.success, t.success),
                Severity::Warning => (t.warning, t.warning),
                Severity::Error => (t.error, t.error),
            };
            let inner = (w - 3) as usize;
            let mut lines: Vec<Line<'static>> = vec![];
            if !toast.title.is_empty() {
                lines.extend(wrap(vec![Span::styled(toast.title.clone(), bold(title_color))], inner));
            }
            lines.extend(wrap(vec![Span::styled(toast.message.clone(), fg(t.text))], inner));
            let h = lines.len() as u16 + 2;
            if bottom < h + area.y {
                break;
            }
            let top = bottom - h;
            let r = Rect::new(x, top, w, h);
            p.clear(r, t.panel);
            for yy in top..bottom {
                p.text(x, yy, 1, "▌", fg(edge).bg(t.panel.color()));
            }
            for (i, l) in lines.into_iter().enumerate() {
                let l = Line::from(l.spans.into_iter().map(|s| Span::styled(s.content, s.style.bg(t.panel.color()))).collect::<Vec<_>>());
                p.line(x + 2, top + 1 + i as u16, w - 3, l, Align::Left);
            }
            p.click(r, "toast");
            bottom = top.saturating_sub(1);
        }
    }
}
