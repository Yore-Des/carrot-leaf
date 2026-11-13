//! The CARROT wordmark: solid letters with a box-drawn shadow, shaded top to bottom.

use super::draw::{bold, fg};
use super::theme::Rgb;
use ratatui::text::{Line, Span};

// figlet "ANSI Shadow". Block cells are the letter faces; box-drawing cells are the shadow.
pub const BIG: [&str; 6] = [
    " ██████╗ █████╗ ██████╗ ██████╗  ██████╗ ████████╗",
    "██╔════╝██╔══██╗██╔══██╗██╔══██╗██╔═══██╗╚══██╔══╝",
    "██║     ███████║██████╔╝██████╔╝██║   ██║   ██║   ",
    "██║     ██╔══██║██╔══██╗██╔══██╗██║   ██║   ██║   ",
    "╚██████╗██║  ██║██║  ██║██║  ██║╚██████╔╝   ██║   ",
    " ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝    ╚═╝   ",
];
const SMALL: [&str; 4] = [
    "  ___   _   ___ ___  ___ _____ ",
    " / __| /_\\ | _ \\ _ \\/ _ \\_   _|",
    "| (__ / _ \\|   /   / (_) || |  ",
    " \\___/_/ \\_\\_|_\\_|_\\\\___/ |_|  ",
];
const BIG_WIDTH: usize = 50;
const SMALL_WIDTH: usize = 31;

/// Lines of the logo for `width` columns. `gradient` colours faces row by row.
pub fn render(width: usize, gradient: &[Rgb], shadow: Rgb) -> Vec<Line<'static>> {
    let shade = |row: usize| gradient[row.min(gradient.len() - 1)];
    if width >= BIG_WIDTH {
        BIG.iter()
            .enumerate()
            .map(|(row, line)| {
                // Runs of the same kind of cell share one span.
                let mut spans: Vec<Span<'static>> = vec![];
                let mut run = String::new();
                let mut face_run = None;
                for ch in line.chars() {
                    let face = ch == '█';
                    if face_run != Some(face) && !run.is_empty() {
                        let style = if face_run == Some(true) { bold(shade(row)) } else { fg(shadow) };
                        spans.push(Span::styled(std::mem::take(&mut run), style));
                    }
                    face_run = Some(face);
                    run.push(ch);
                }
                if !run.is_empty() {
                    let style = if face_run == Some(true) { bold(shade(row)) } else { fg(shadow) };
                    spans.push(Span::styled(run, style));
                }
                Line::from(spans)
            })
            .collect()
    } else if width >= SMALL_WIDTH {
        SMALL.iter().enumerate().map(|(row, l)| Line::from(Span::styled(l.to_string(), bold(shade(row))))).collect()
    } else {
        vec![Line::from(Span::styled("CARROT", bold(shade(0))))]
    }
}

