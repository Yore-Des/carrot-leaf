//! The animated wordmark. The big CARROT logo builds itself block by block on start-up and
//! now and then while idle, turns into a carrot leaf while a link or file is being read, and
//! becomes a face that cycles through expressions while something downloads or converts.
//!
//! Everything is drawn on one 50×6 grid, so the layout never moves. A change of scene is a
//! morph: each cell flips from the old picture to the new one at its own moment, passing
//! through ░▒▓ on the way, in a loose left-to-right sweep. It only needs ticks while a morph,
//! the leaf or a face is on screen; at rest the app still sleeps until the next idle replay.

use super::draw::{bold, fg};
use super::theme::{LogoStyle, Rgb, Theme};
use ratatui::style::Style;
use ratatui::text::{Line, Span};
use std::time::{Duration, Instant};

pub const W: usize = 50;
pub const H: usize = 6;

/// How long the logo sits still before it rebuilds itself once.
const IDLE_REPLAY: Duration = Duration::from_secs(20);
/// The leaf stays at least this long after a paste or drop, so a fast read still shows it.
const LEAF_HOLD: Duration = Duration::from_millis(2400);
const FACE_EVERY: Duration = Duration::from_millis(1700);
const BLANK_HOLD: Duration = Duration::from_millis(350);

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Scene {
    Blank,
    Text,
    Leaf,
    Face(usize),
}

pub struct Mascot {
    from: Scene,
    to: Scene,
    start: Instant,
    len: Duration,
    face_at: Instant,
    leaf_until: Option<Instant>,
    off: bool,
}

impl Mascot {
    pub fn new() -> Self {
        let now = Instant::now();
        let off = std::env::var_os("CARROT_NO_ANIMATION").is_some();
        let from = if off { Scene::Text } else { Scene::Blank };
        Mascot { from, to: Scene::Text, start: now, len: Duration::from_millis(1700), face_at: now, leaf_until: None, off }
    }

    /// A link was pasted or files were dropped.
    pub fn poke(&mut self) {
        self.leaf_until = Some(Instant::now() + LEAF_HOLD);
    }

    fn go(&mut self, to: Scene, ms: u64, now: Instant) {
        // Start from whatever is on screen now, which is the old target once a morph is done.
        self.from = self.to;
        self.to = to;
        self.start = now;
        self.len = Duration::from_millis(ms);
    }

    fn morphing(&self, now: Instant) -> bool {
        now < self.start + self.len
    }

    /// Move towards what the app is doing. Called on every tick and message.
    pub fn update(&mut self, working: bool, reading: bool, focused: bool) {
        let now = Instant::now();
        if self.off || self.morphing(now) {
            return;
        }
        let reading = reading || self.leaf_until.is_some_and(|t| now < t);
        let target = if working {
            Scene::Face(0)
        } else if reading {
            Scene::Leaf
        } else {
            Scene::Text
        };
        let since = now - self.start;
        match (self.to, target) {
            (Scene::Face(i), Scene::Face(_)) => {
                if now - self.face_at >= FACE_EVERY {
                    self.face_at = now;
                    self.go(Scene::Face((i + 1) % FACES.len()), 700, now);
                }
            }
            (Scene::Text, Scene::Text) => {
                if focused && since >= IDLE_REPLAY {
                    self.go(Scene::Blank, 1200, now);
                }
            }
            (Scene::Blank, _) => {
                if since >= BLANK_HOLD {
                    self.go(target, 1600, now);
                }
            }
            (a, b) if a == b => {}
            (_, b) => {
                self.face_at = now;
                self.go(b, 1400, now);
            }
        }
    }

    /// True while the logo needs frames.
    pub fn animating(&self) -> bool {
        // A little past the end, so the finished picture gets drawn.
        Instant::now() < self.start + self.len + Duration::from_millis(200) || matches!(self.to, Scene::Leaf | Scene::Face(_) | Scene::Blank)
    }

    /// When the next idle replay is due, for the main loop's sleep.
    pub fn next_wake(&self) -> Option<Duration> {
        if self.off || self.to != Scene::Text {
            return None;
        }
        Some((self.start + IDLE_REPLAY).saturating_duration_since(Instant::now()))
    }

    /// Frames come faster while cells are flipping, so each step of the dissolve shows.
    pub fn frame(&self) -> Duration {
        if self.morphing(Instant::now()) { Duration::from_millis(40) } else { Duration::from_millis(80) }
    }

    pub fn render(&self, t: &Theme) -> Vec<Line<'static>> {
        let now = Instant::now();
        let elapsed = now.saturating_duration_since(self.start);
        let b = grid(self.to, t, now);
        let p = (elapsed.as_secs_f64() / self.len.as_secs_f64()).min(1.0);
        let cells: Vec<Vec<Cell>> = if p >= 1.0 {
            b
        } else {
            let a = grid(self.from, t, now);
            blend(&a, &b, p, t)
        };
        cells.into_iter().map(to_line).collect()
    }
}

type Cell = (char, Style);

// ---------- the morph ----------

/// How much of the morph each cell spends changing: wide, so many cells are mid-change at
/// once and the sweep reads as a band rather than a few sparks.
const EDGE: f64 = 0.38;
/// The old glyph wears away, then the new one builds up.
const OUT: [char; 4] = ['▓', '▒', '░', '·'];
const IN: [char; 4] = ['·', '░', '▒', '▓'];

fn blend(a: &[Vec<Cell>], b: &[Vec<Cell>], p: f64, t: &Theme) -> Vec<Vec<Cell>> {
    let q = p * (1.0 + EDGE);
    // Cells mid-change glow: the old one fades in the light shade, the new one builds in the accent.
    let fading = fg(t.primary_l2);
    let building = bold(if t.accent == t.primary { t.primary_l1 } else { t.accent });
    (0..H)
        .map(|r| {
            (0..W)
                .map(|c| {
                    let (ca, sa) = a[r][c];
                    let (cb, sb) = b[r][c];
                    if ca == ' ' && cb == ' ' {
                        return (' ', Style::new());
                    }
                    // A loose sweep: mostly left to right, with some scatter.
                    let order = 0.35 * noise(r, c) + 0.65 * c as f64 / W as f64;
                    if q < order {
                        return (ca, sa);
                    }
                    if q > order + EDGE {
                        return (cb, sb);
                    }
                    let f = (q - order) / EDGE;
                    if f < 0.5 {
                        if ca == ' ' {
                            (' ', Style::new())
                        } else {
                            (OUT[((f * 2.0 * 4.0) as usize).min(3)], fading)
                        }
                    } else if cb == ' ' {
                        (' ', Style::new())
                    } else {
                        (IN[(((f - 0.5) * 2.0 * 4.0) as usize).min(3)], building)
                    }
                })
                .collect()
        })
        .collect()
}

fn noise(r: usize, c: usize) -> f64 {
    let mut x = (r as u32).wrapping_mul(0x9E37_79B9) ^ (c as u32).wrapping_mul(0x85EB_CA6B) ^ 0x2545_F491;
    x ^= x >> 15;
    x = x.wrapping_mul(0x2C1B_3C6D);
    x ^= x >> 12;
    x = x.wrapping_mul(0x297A_2D39);
    x ^= x >> 15;
    (x % 10_000) as f64 / 10_000.0
}

fn to_line(row: Vec<Cell>) -> Line<'static> {
    let mut spans: Vec<Span<'static>> = vec![];
    let mut run = String::new();
    let mut style = None;
    for (ch, s) in row {
        let s = if ch == ' ' { Style::new() } else { s };
        if style != Some(s) && !run.is_empty() {
            spans.push(Span::styled(std::mem::take(&mut run), style.unwrap_or_default()));
        }
        style = Some(s);
        run.push(ch);
    }
    if !run.is_empty() {
        spans.push(Span::styled(run, style.unwrap_or_default()));
    }
    Line::from(spans)
}

// ---------- pictures ----------

/// The logo's top-to-bottom face colours for the theme's logo style.
pub fn logo_shades(t: &Theme) -> Vec<Rgb> {
    match t.look.logo {
        LogoStyle::Gradient => vec![t.primary_l1, t.primary, t.primary, t.primary_d1, t.primary_d2],
        LogoStyle::Duo => (0..5).map(|i| t.primary.over(t.accent, i as f64 / 4.0)).collect(),
        LogoStyle::Flat => vec![t.primary],
    }
}

pub fn logo_shadow(t: &Theme) -> Rgb {
    match t.look.logo {
        LogoStyle::Flat => t.panel_l1,
        _ if !t.dark => t.primary_l2,
        LogoStyle::Gradient => t.primary_d3,
        LogoStyle::Duo => t.accent.darken(0.25),
    }
}

fn shadow_of(t: &Theme, c: Rgb) -> Rgb {
    if t.dark { c.darken(0.25) } else { c.lighten(0.2) }
}

/// Lay `rows` centred on the grid: `█` cells take `face(row)`, everything else `shadow(row)`.
fn paint(rows: &[String], face: impl Fn(usize) -> Rgb, shadow: impl Fn(usize) -> Rgb) -> Vec<Vec<Cell>> {
    let wide = rows.iter().map(|r| r.chars().count()).max().unwrap_or(0);
    let left = W.saturating_sub(wide) / 2;
    let top = H.saturating_sub(rows.len()) / 2;
    let mut g = vec![vec![(' ', Style::new()); W]; H];
    for (r, line) in rows.iter().enumerate().take(H) {
        for (i, ch) in line.chars().enumerate() {
            let c = left + i;
            if c >= W || ch == ' ' {
                continue;
            }
            let style = if ch == '█' { bold(face(r)) } else { fg(shadow(r)) };
            g[top + r][c] = (ch, style);
        }
    }
    g
}

fn grid(scene: Scene, t: &Theme, now: Instant) -> Vec<Vec<Cell>> {
    let shades = logo_shades(t);
    let shade = |r: usize| shades[r.min(shades.len() - 1)];
    let shadow = logo_shadow(t);
    match scene {
        Scene::Blank => vec![vec![(' ', Style::new()); W]; H],
        Scene::Text => paint(&super::logo::BIG.map(String::from), shade, |_| shadow),
        Scene::Face(i) => paint(&face(FACES[i % FACES.len()]), shade, |_| shadow),
        Scene::Leaf => {
            // The tips of the leaves sway: 0, +1, 0, -1 columns, a step every 240 ms.
            let step = (clock_ms(now) / 240) % 4;
            let sway = [0isize, 1, 0, -1][step as usize];
            let rows: Vec<String> = LEAF
                .iter()
                .enumerate()
                .map(|(r, l)| {
                    let s = if r == 0 { sway } else { 0 };
                    match s {
                        1 => format!(" {l}"),
                        -1 => l.chars().skip(1).collect::<String>() + " ",
                        _ => format!("{l} "),
                    }
                })
                .collect();
            let leaf = |r: usize| if r < 4 { t.success } else { t.primary };
            paint(&rows, leaf, |r| shadow_of(t, leaf(r)))
        }
    }
}

/// Milliseconds since the first frame, for the leaf's sway.
fn clock_ms(now: Instant) -> u64 {
    use std::sync::OnceLock;
    static EPOCH: OnceLock<Instant> = OnceLock::new();
    now.saturating_duration_since(*EPOCH.get_or_init(Instant::now)).as_millis() as u64
}

// Leaves on a carrot top, in the same ANSI Shadow style as the wordmark.
const LEAF: [&str; 6] = [
    "  ██╗     ██╗     ██╗  ",
    "  ╚██╗    ██║    ██╔╝  ",
    "   ╚██╗   ██║   ██╔╝   ",
    "    ╚██╗  ██║  ██╔╝    ",
    "    ██████████████╗    ",
    "    ╚═════════════╝    ",
];

const FACES: [&str; 6] = ["o_o", "^_^", "O_o", ">_<", "O_O", "-_-"];

/// Each glyph is six rows of equal width.
fn glyph(c: char) -> [&'static str; 6] {
    match c {
        'O' => [" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
        'o' => ["        ", "        ", " █████╗ ", "██╔══██╗", "╚█████╔╝", " ╚════╝ "],
        '_' => ["        ", "        ", "        ", "        ", "███████╗", "╚══════╝"],
        '>' => ["██╗   ", "╚██╗  ", " ╚██╗ ", " ██╔╝ ", "██╔╝  ", "╚═╝   "],
        '<' => ["   ██╗", "  ██╔╝", " ██╔╝ ", " ╚██╗ ", "  ╚██╗", "   ╚═╝"],
        '^' => ["  ███╗  ", " ██╔██╗ ", "██╔╝╚██╗", "╚═╝  ╚═╝", "        ", "        "],
        '-' => ["       ", "       ", "██████╗", "╚═════╝", "       ", "       "],
        _ => ["  ", "  ", "  ", "  ", "  ", "  "],
    }
}

fn face(s: &str) -> Vec<String> {
    (0..H)
        .map(|r| s.chars().map(|c| glyph(c)[r]).collect::<Vec<_>>().join(" "))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn art_is_rectangular() {
        for c in "Oo_><^-".chars() {
            let g = glyph(c);
            assert!(g.iter().all(|r| r.chars().count() == g[0].chars().count()), "glyph {c}");
        }
        assert!(LEAF.iter().all(|r| r.chars().count() == LEAF[0].chars().count()));
        for f in FACES {
            assert!(face(f).iter().all(|r| r.chars().count() <= W), "{f}");
        }
    }
}
