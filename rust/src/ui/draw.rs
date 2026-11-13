//! Small drawing helpers on ratatui's buffer: styled text, rules, rounded panels, wrapping,
//! and the click map that makes labels and rows clickable like Textual's.

use super::theme::{Rgb, Theme};
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

pub struct Painter<'a> {
    pub buf: &'a mut Buffer,
    pub t: &'a Theme,
    pub clicks: &'a mut Vec<(Rect, String)>,
    pub tick: u64,
}

#[derive(Clone, Copy, PartialEq)]
pub enum Align {
    Left,
    Center,
    Right,
}

pub fn fg(c: Rgb) -> Style {
    Style::new().fg(c.color())
}
pub fn bold(c: Rgb) -> Style {
    fg(c).add_modifier(Modifier::BOLD)
}
pub fn on(f: Rgb, b: Rgb) -> Style {
    Style::new().fg(f.color()).bg(b.color())
}

pub fn width(s: &str) -> usize {
    UnicodeWidthStr::width(s)
}

pub fn line_width(l: &Line) -> usize {
    l.spans.iter().map(|s| width(&s.content)).sum()
}

/// Cut `line` to `max` columns, ending with "…" when something was cut.
pub fn truncate(line: Line<'static>, max: usize) -> Line<'static> {
    if line_width(&line) <= max {
        return line;
    }
    let mut out: Vec<Span<'static>> = vec![];
    let mut used = 0;
    let room = max.saturating_sub(1);
    let mut last_style = Style::default();
    'outer: for span in line.spans {
        let mut s = String::new();
        for ch in span.content.chars() {
            let w = ch.width().unwrap_or(0);
            if used + w > room {
                if !s.is_empty() {
                    out.push(Span::styled(s, span.style));
                }
                last_style = span.style;
                break 'outer;
            }
            used += w;
            s.push(ch);
        }
        last_style = span.style;
        out.push(Span::styled(s, span.style));
    }
    if max > 0 {
        out.push(Span::styled("…", last_style));
    }
    Line::from(out)
}

pub fn cut(s: &str, max: usize) -> String {
    if width(s) <= max {
        return s.to_string();
    }
    let mut out = String::new();
    let mut used = 0;
    for ch in s.chars() {
        let w = ch.width().unwrap_or(0);
        if used + w + 1 > max {
            break;
        }
        used += w;
        out.push(ch);
    }
    if max > 0 {
        out.push('…');
    }
    out
}

pub fn pad(s: &str, w: usize) -> String {
    let n = width(s);
    if n >= w { s.to_string() } else { format!("{s}{}", " ".repeat(w - n)) }
}

/// Word-wrap styled text to `w` columns. "\n" starts a new line.
pub fn wrap(spans: Vec<Span<'static>>, w: usize) -> Vec<Line<'static>> {
    let w = w.max(1);
    let mut lines: Vec<Line<'static>> = vec![];
    let mut cur: Vec<Span<'static>> = vec![];
    let mut used = 0usize;
    let flush = |lines: &mut Vec<Line<'static>>, cur: &mut Vec<Span<'static>>, used: &mut usize| {
        // Trailing spaces never count at a wrap.
        if let Some(last) = cur.last_mut() {
            let t = last.content.trim_end().to_string();
            last.content = t.into();
        }
        lines.push(Line::from(std::mem::take(cur)));
        *used = 0;
    };
    for span in spans {
        let style = span.style;
        let text = span.content.to_string();
        for (pi, para) in text.split('\n').enumerate() {
            if pi > 0 {
                flush(&mut lines, &mut cur, &mut used);
            }
            // Split into words keeping their following spaces.
            let mut word = String::new();
            let mut tokens: Vec<String> = vec![];
            for ch in para.chars() {
                word.push(ch);
                if ch == ' ' {
                    tokens.push(std::mem::take(&mut word));
                }
            }
            if !word.is_empty() {
                tokens.push(word);
            }
            for tok in tokens {
                let tw = width(tok.trim_end());
                if used > 0 && used + tw > w {
                    flush(&mut lines, &mut cur, &mut used);
                }
                if used == 0 && tok.trim().is_empty() && !cur.is_empty() {
                    continue;
                }
                let mut t = tok;
                // A word longer than the line is hard-broken.
                while width(t.trim_end()) > w {
                    let mut head = String::new();
                    let mut hw = 0;
                    let mut rest = String::new();
                    for (i, ch) in t.char_indices() {
                        let cw = ch.width().unwrap_or(0);
                        if hw + cw > w.saturating_sub(used) {
                            rest = t[i..].to_string();
                            break;
                        }
                        hw += cw;
                        head.push(ch);
                    }
                    cur.push(Span::styled(head, style));
                    flush(&mut lines, &mut cur, &mut used);
                    t = rest;
                }
                used += width(&t);
                cur.push(Span::styled(t, style));
            }
        }
    }
    if !cur.is_empty() || lines.is_empty() {
        flush(&mut lines, &mut cur, &mut used);
    }
    lines
}

impl Painter<'_> {
    pub fn fill(&mut self, r: Rect, bg: Rgb) {
        let r = r.intersection(self.buf.area);
        self.buf.set_style(r, Style::new().bg(bg.color()));
    }

    pub fn clear(&mut self, r: Rect, bg: Rgb) {
        let r = r.intersection(self.buf.area);
        for y in r.top()..r.bottom() {
            for x in r.left()..r.right() {
                let c = &mut self.buf[(x, y)];
                c.reset();
                c.set_bg(bg.color());
            }
        }
    }

    /// Draw `line` in `w` columns at (x, y), aligned, cut with "…" if it doesn't fit.
    pub fn line(&mut self, x: u16, y: u16, w: u16, line: Line<'static>, align: Align) -> u16 {
        if w == 0 || y >= self.buf.area.bottom() {
            return 0;
        }
        let line = truncate(line, w as usize);
        let lw = line_width(&line) as u16;
        let x0 = match align {
            Align::Left => x,
            Align::Center => x + (w.saturating_sub(lw)) / 2,
            Align::Right => x + w.saturating_sub(lw),
        };
        self.buf.set_line(x0, y, &line, w);
        lw
    }

    pub fn text(&mut self, x: u16, y: u16, w: u16, s: &str, style: Style) -> u16 {
        self.line(x, y, w, Line::from(Span::styled(s.to_string(), style)), Align::Left)
    }

    /// Wrapped text from (x, y); returns the number of lines drawn.
    pub fn para(&mut self, x: u16, y: u16, w: u16, spans: Vec<Span<'static>>, max_lines: u16) -> u16 {
        let lines = wrap(spans, w as usize);
        let n = (lines.len() as u16).min(max_lines);
        for (i, l) in lines.into_iter().take(n as usize).enumerate() {
            self.line(x, y + i as u16, w, l, Align::Left);
        }
        n
    }

    pub fn click(&mut self, r: Rect, id: impl Into<String>) {
        if r.width > 0 && r.height > 0 {
            self.clicks.push((r, id.into()));
        }
    }

    /// A section rule: `─title────` across `w` columns.
    pub fn rule(&mut self, x: u16, y: u16, w: u16, title: &str, title_style: Style) {
        let line_style = fg(self.t.panel);
        self.text(x, y, w, &self.t.look.rule.repeat(w as usize), line_style);
        if !title.is_empty() && w > 2 {
            self.text(x + 1, y, w - 1, title, title_style);
        }
    }

    /// A box border in the theme's line style: `round` is a dialog or panel frame, else an
    /// input field. The title sits after `─ `.
    pub fn border(&mut self, r: Rect, round: bool, color: Rgb, title: &str, title_style: Style) {
        if r.width < 2 || r.height < 2 {
            return;
        }
        let s = fg(color);
        let [tl, tr, bl, br, hz, vt] = if round { self.t.look.frame } else { self.t.look.field }.glyphs();
        let inner = (r.width - 2) as usize;
        let bottom = r.bottom() - 1;
        self.text(r.x, r.y, r.width, &format!("{tl}{}{tr}", hz.repeat(inner)), s);
        for y in r.y + 1..bottom {
            self.text(r.x, y, 1, vt, s);
            self.text(r.right() - 1, y, 1, vt, s);
        }
        self.text(r.x, bottom, r.width, &format!("{bl}{}{br}", hz.repeat(inner)), s);
        if !title.is_empty() && r.width > 6 {
            self.text(r.x + 2, r.y, r.width - 4, &format!(" {title} "), title_style);
        }
    }

    /// Lay a translucent `bg` over everything already drawn, as ModalScreen's backdrop does.
    pub fn dim(&mut self, alpha: f64) {
        let bg = self.t.bg;
        let area = self.buf.area;
        for y in area.top()..area.bottom() {
            for x in area.left()..area.right() {
                let cell = &mut self.buf[(x, y)];
                if let Some(c) = Rgb::from_color(cell.fg) {
                    cell.set_fg(bg.over(c, alpha).color());
                }
                if let Some(c) = Rgb::from_color(cell.bg) {
                    cell.set_bg(bg.over(c, alpha).color());
                }
            }
        }
    }
}
