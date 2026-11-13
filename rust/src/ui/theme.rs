//! Carrot orange, in dark and light variants, plus a few classic themes for `t`.
//!
//! The derived shades (lighten/darken, muted text, 70% overlays) follow Textual's own
//! colour system, so the port renders the same colours the Python app does.

use ratatui::style::Color;
use std::sync::OnceLock;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rgb(pub u8, pub u8, pub u8);

impl Rgb {
    pub fn hex(s: &str) -> Rgb {
        let s = s.trim_start_matches('#');
        let v = u32::from_str_radix(s, 16).unwrap_or(0);
        Rgb((v >> 16) as u8, (v >> 8) as u8, v as u8)
    }
    /// `self` laid over `under` at `alpha` (1.0 = all self).
    pub fn over(self, under: Rgb, alpha: f64) -> Rgb {
        let m = |a: u8, b: u8| (a as f64 * alpha + b as f64 * (1.0 - alpha)).round().clamp(0.0, 255.0) as u8;
        Rgb(m(self.0, under.0), m(self.1, under.1), m(self.2, under.2))
    }
    pub fn lighten(self, amount: f64) -> Rgb {
        let (l, a, b) = to_lab(self);
        from_lab(((l + amount * 100.0).clamp(0.0, 100.0), a, b))
    }
    pub fn darken(self, amount: f64) -> Rgb {
        self.lighten(-amount)
    }
    pub fn color(self) -> Color {
        if truecolor() { Color::Rgb(self.0, self.1, self.2) } else { Color::Indexed(nearest_256(self)) }
    }
    pub fn from_color(c: Color) -> Option<Rgb> {
        match c {
            Color::Rgb(r, g, b) => Some(Rgb(r, g, b)),
            Color::Indexed(i) => Some(xterm(i)),
            _ => None,
        }
    }
}

fn truecolor() -> bool {
    static T: OnceLock<bool> = OnceLock::new();
    *T.get_or_init(|| {
        let ct = std::env::var("COLORTERM").unwrap_or_default().to_lowercase();
        ct.contains("truecolor") || ct.contains("24bit")
    })
}

fn xterm(i: u8) -> Rgb {
    const BASE: [(u8, u8, u8); 16] = [
        (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0), (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
        (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0), (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
    ];
    match i {
        0..=15 => Rgb(BASE[i as usize].0, BASE[i as usize].1, BASE[i as usize].2),
        16..=231 => {
            let n = i - 16;
            let s = |v: u8| if v == 0 { 0 } else { 55 + v * 40 };
            Rgb(s(n / 36), s((n / 6) % 6), s(n % 6))
        }
        _ => {
            let g = 8 + (i - 232) * 10;
            Rgb(g, g, g)
        }
    }
}

fn nearest_256(c: Rgb) -> u8 {
    let mut best = (u32::MAX, 16u8);
    for i in 16..=255u8 {
        let x = xterm(i);
        let d = |a: u8, b: u8| (a as i32 - b as i32).pow(2) as u32;
        let dist = d(c.0, x.0) * 3 + d(c.1, x.1) * 4 + d(c.2, x.2) * 2;
        if dist < best.0 {
            best = (dist, i);
        }
    }
    best.1
}

fn to_lab(c: Rgb) -> (f64, f64, f64) {
    let lin = |v: u8| {
        let v = v as f64 / 255.0;
        if v > 0.04045 { ((v + 0.055) / 1.055).powf(2.4) } else { v / 12.92 }
    };
    let (r, g, b) = (lin(c.0), lin(c.1), lin(c.2));
    let x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047;
    let y = r * 0.2126 + g * 0.7152 + b * 0.0722;
    let z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883;
    let f = |t: f64| if t > 0.008856 { t.cbrt() } else { 7.787 * t + 16.0 / 116.0 };
    let (fx, fy, fz) = (f(x), f(y), f(z));
    (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))
}

fn from_lab((l, a, b): (f64, f64, f64)) -> Rgb {
    let fy = (l + 16.0) / 116.0;
    let fx = a / 500.0 + fy;
    let fz = fy - b / 200.0;
    let inv = |t: f64| if t.powi(3) > 0.008856 { t.powi(3) } else { (t - 16.0 / 116.0) / 7.787 };
    let (x, y, z) = (inv(fx) * 0.95047, inv(fy), inv(fz) * 1.08883);
    let r = x * 3.2406 + y * -1.5372 + z * -0.4986;
    let g = x * -0.9689 + y * 1.8758 + z * 0.0415;
    let bl = x * 0.0557 + y * -0.2040 + z * 1.0570;
    let gamma = |v: f64| {
        let v = if v > 0.0031308 { 1.055 * v.powf(1.0 / 2.4) - 0.055 } else { 12.92 * v };
        (v * 255.0).round().clamp(0.0, 255.0) as u8
    };
    Rgb(gamma(r), gamma(g), gamma(bl))
}

#[derive(Clone, Debug)]
pub struct Theme {
    pub name: &'static str,
    pub label: &'static str,
    pub dark: bool,
    pub primary: Rgb,
    pub warning: Rgb,
    pub error: Rgb,
    pub success: Rgb,
    pub fg: Rgb,
    pub bg: Rgb,
    pub panel: Rgb,
    // derived
    pub muted: Rgb,
    pub disabled: Rgb,
    pub text: Rgb,
    pub primary_l1: Rgb,
    pub primary_l2: Rgb,
    pub primary_d1: Rgb,
    pub primary_d2: Rgb,
    pub primary_d3: Rgb,
    pub panel_l1: Rgb,
    pub panel_l2: Rgb,
    /// Second accent: the far end of the logo shading and other small touches.
    pub accent: Rgb,
    pub look: Look,
}

/// Box-drawing family for a frame.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Frame {
    Round,
    Square,
    Heavy,
    Double,
}

impl Frame {
    pub fn name(self) -> &'static str {
        match self {
            Frame::Round => "rounded",
            Frame::Square => "square",
            Frame::Heavy => "heavy",
            Frame::Double => "double",
        }
    }
    /// Corners top-left, top-right, bottom-left, bottom-right, then horizontal, vertical.
    pub fn glyphs(self) -> [&'static str; 6] {
        match self {
            Frame::Round => ["╭", "╮", "╰", "╯", "─", "│"],
            Frame::Square => ["┌", "┐", "└", "┘", "─", "│"],
            Frame::Heavy => ["┏", "┓", "┗", "┛", "━", "┃"],
            Frame::Double => ["╔", "╗", "╚", "╝", "═", "║"],
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum TabStyle {
    /// Solid accent chip behind the active tab.
    Chip,
    /// Accent text with a line under it.
    Underline,
    /// `[ Active ]` in brackets.
    Bracket,
}

impl TabStyle {
    pub fn name(self) -> &'static str {
        match self {
            TabStyle::Chip => "chip tabs",
            TabStyle::Underline => "underlined tabs",
            TabStyle::Bracket => "bracket tabs",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Highlight {
    /// The whole row filled with the accent.
    Fill,
    /// A tinted row with a `▌` accent edge.
    Bar,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum LogoStyle {
    /// Primary, shaded light to dark, with a dark drop shadow.
    Gradient,
    /// Accent at the top fading into primary.
    Duo,
    /// One flat colour, the shadow drawn in panel grey.
    Flat,
}

impl LogoStyle {
    pub fn name(self) -> &'static str {
        match self {
            LogoStyle::Gradient => "shaded logo",
            LogoStyle::Duo => "two-tone logo",
            LogoStyle::Flat => "flat logo",
        }
    }
}

/// How a theme draws things, beyond its colours.
#[derive(Clone, Copy, Debug)]
pub struct Look {
    /// Dialogs and panels.
    pub frame: Frame,
    /// The link/files input box.
    pub field: Frame,
    pub rule: &'static str,
    /// Progress bar cells: filled, empty.
    pub bar: (&'static str, &'static str),
    pub prompt: &'static str,
    pub tabs: TabStyle,
    pub highlight: Highlight,
    pub logo: LogoStyle,
}

const CLASSIC: Look = Look {
    frame: Frame::Round,
    field: Frame::Square,
    rule: "─",
    bar: ("█", "█"),
    prompt: "›",
    tabs: TabStyle::Chip,
    highlight: Highlight::Fill,
    logo: LogoStyle::Gradient,
};

const STEP: f64 = 0.075; // Textual: luminosity spread 0.15, half a step per shade

#[allow(clippy::too_many_arguments)]
fn make(name: &'static str, label: &'static str, dark: bool, primary: &str, warning: &str, error: &str, success: &str, fg: &str, bg: &str, panel: &str, accent: &str, look: Look) -> Theme {
    let (primary, fg, bg, panel) = (Rgb::hex(primary), Rgb::hex(fg), Rgb::hex(bg), Rgb::hex(panel));
    // Textual's $text-muted and $foreground-disabled, as they come out on screen.
    let muted = fg.over(bg, 0.676);
    let disabled = fg.over(bg, 0.376);
    Theme {
        name,
        label,
        dark,
        primary,
        warning: Rgb::hex(warning),
        error: Rgb::hex(error),
        success: Rgb::hex(success),
        fg,
        bg,
        panel,
        muted,
        disabled,
        text: fg.over(bg, 0.98),
        primary_l1: primary.lighten(STEP),
        primary_l2: primary.lighten(STEP * 2.0),
        primary_d1: primary.darken(STEP),
        primary_d2: primary.darken(STEP * 2.0),
        primary_d3: primary.darken(STEP * 3.0),
        panel_l1: panel.lighten(STEP),
        panel_l2: panel.lighten(STEP * 2.0),
        accent: Rgb::hex(accent),
        look,
    }
}

pub fn themes() -> &'static [Theme] {
    static T: OnceLock<Vec<Theme>> = OnceLock::new();
    T.get_or_init(|| {
        vec![
            make("carrot", "Carrot", true, "#ff8700", "#ffd700", "#ff5f5f", "#87d75f", "#e4e4e4", "#121212", "#303030", "#ffaf00", CLASSIC),
            make("carrot-light", "Carrot light", false, "#d75f00", "#af8700", "#d70000", "#5f8700", "#1c1c1c", "#f4efe6", "#e2d8c8", "#5f8700",
                Look { field: Frame::Round, tabs: TabStyle::Underline, ..CLASSIC }),
            // Calm and arctic: thin square lines, a frost-blue wordmark, underlined tabs.
            make("nord", "Nord", true, "#88C0D0", "#EBCB8B", "#BF616A", "#A3BE8C", "#D8DEE9", "#2E3440", "#3B4252", "#81A1C1",
                Look { frame: Frame::Square, field: Frame::Square, rule: "─", bar: ("━", "─"), prompt: "❯", tabs: TabStyle::Underline, highlight: Highlight::Bar, logo: LogoStyle::Flat }),
            // Retro: double lines, shaded blocks, bracketed tabs, a yellow-to-orange logo.
            make("gruvbox", "Gruvbox", true, "#fabd2f", "#fe8019", "#fb4934", "#b8bb26", "#ebdbb2", "#282828", "#3c3836", "#fe8019",
                Look { frame: Frame::Double, field: Frame::Double, rule: "═", bar: ("▓", "░"), prompt: ">", tabs: TabStyle::Bracket, highlight: Highlight::Fill, logo: LogoStyle::Duo }),
            // Gothic: heavy lines, a purple-to-pink logo, segmented bars.
            make("dracula", "Dracula", true, "#BD93F9", "#F1FA8C", "#FF5555", "#50FA7B", "#F8F8F2", "#282A36", "#44475A", "#FF79C6",
                Look { frame: Frame::Heavy, field: Frame::Heavy, rule: "━", bar: ("▰", "▱"), prompt: "λ", tabs: TabStyle::Chip, highlight: Highlight::Bar, logo: LogoStyle::Duo }),
            // Neon city: rounded everything, dashed rules, a blue-to-violet logo.
            make("tokyo-night", "Tokyo Night", true, "#7AA2F7", "#E0AF68", "#F7768E", "#9ECE6A", "#c0caf5", "#1A1B26", "#292e42", "#BB9AF7",
                Look { frame: Frame::Round, field: Frame::Round, rule: "╌", bar: ("■", "·"), prompt: "❯", tabs: TabStyle::Underline, highlight: Highlight::Bar, logo: LogoStyle::Duo }),
            make("textual-dark", "Textual dark", true, "#0178D4", "#ffa62b", "#ba3c5b", "#4EBF71", "#e0e0e0", "#121212", "#2b2b2b", "#0178D4",
                Look { frame: Frame::Square, ..CLASSIC }),
            make("textual-light", "Textual light", false, "#004578", "#ffa62b", "#ba3c5b", "#4EBF71", "#1f1f1f", "#efefef", "#d8d8d8", "#004578",
                Look { frame: Frame::Square, ..CLASSIC }),
        ]
    })
}

pub fn by_name(name: &str) -> &'static Theme {
    themes().iter().find(|t| t.name == name).unwrap_or(&themes()[0])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_textual_shades() {
        let t = by_name("carrot");
        // What the Python app paints for the logo gradient and muted text.
        let close = |a: Rgb, b: &str| {
            let b = Rgb::hex(b);
            (a.0 as i32 - b.0 as i32).abs() <= 6 && (a.1 as i32 - b.1 as i32).abs() <= 6 && (a.2 as i32 - b.2 as i32).abs() <= 8
        };
        assert!(close(t.primary_d1, "#e67300"), "{:?}", t.primary_d1);
        assert!(close(t.primary_d3, "#b64c00"), "{:?}", t.primary_d3);
        assert!(close(t.primary_l1, "#ff9b24"), "{:?}", t.primary_l1);
        assert!(close(t.muted, "#a0a0a0"), "{:?}", t.muted);
    }
}
