//! Responsive hero compositions. Only bundled theme data produces decoration.
use crate::{Ui, clean, markdown, token_color};
use ratatui::{
    Frame,
    layout::Rect,
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Paragraph, Wrap},
};
use serde_json::Value;

#[derive(Debug)]
pub struct Composition {
    pub art: Rect,
    pub body: Rect,
    pub brand: Rect,
    pub ornament: Rect,
    pub eyebrow: Rect,
}

/// Compact geometry reserves nine rows for the four actions before any artwork.
pub fn geometry(area: Rect, mode: &str) -> Composition {
    let w = area.width.min(132);
    let a = Rect::new(area.x + (area.width - w) / 2, area.y, w, area.height);
    let rect = |x: u16, y: u16, width: u16, height: u16| {
        Rect::new(
            a.x + x,
            a.y + y,
            width.min(w.saturating_sub(x)),
            height.min(a.height.saturating_sub(y)),
        )
    };
    let side = if a.height < 18 {
        18
    } else {
        (w / 3).clamp(24, 38)
    };
    let mut p = Composition {
        art: rect(1, 2, side, a.height.saturating_sub(3)),
        body: rect(
            side + 3,
            2,
            w.saturating_sub(side + 4),
            a.height.saturating_sub(2),
        ),
        brand: rect(side + 3, 3, w.saturating_sub(side + 4), 0),
        ornament: rect(1, a.height.saturating_sub(2), side, 0),
        eyebrow: rect(1, 0, w.saturating_sub(2), 1),
    };
    if mode == "reverse" || mode == "editorial" {
        p.art.x = a.right().saturating_sub(side + 1);
        p.body.x = a.x + 1;
        p.brand.x = a.x + 1;
    }
    if mode == "console" {
        p.eyebrow = rect(3, 1, w - 6, 1);
    }
    if a.height < 18 {
        return p;
    }
    p.body.y = a.y + 7;
    p.body.height = a.height - 7;
    p.brand.height = 3;
    p.ornament.height = 3;
    p.ornament.y = a.y + a.height - 3;
    match mode {
        "center" => {
            p.art = rect((w - 16) / 2, 0, 16, 8);
            p.eyebrow = rect((w - w.min(42)) / 2, 9, w.min(42), 1);
            p.body = rect((w - w.min(42)) / 2, 10, w.min(42), a.height - 10);
            p.brand = rect(2, 3, (w - 20) / 2, 3);
            p.ornament = rect((w + 20) / 2, 3, (w - 24) / 2, 4);
        }
        "gallery" => {
            p.art = rect(w - 26, 1, 24, 12);
            p.brand = rect(2, 2, w - 30, 3);
            p.body = rect(2, 7, w - 30, a.height - 7);
            p.eyebrow = rect(1, 0, w - 2, 1);
            p.ornament = rect(w - 26, a.height - 4, 24, 4);
        }
        "horizon" => {
            p.brand = rect(2, 2, (w / 2).max(24), 3);
            p.ornament = rect(w / 2 + 2, 1, w / 2 - 4, 4);
            p.art = rect(w - side - 1, 7, side, a.height - 7);
            p.body = rect(2, 7, w - side - 5, a.height - 7);
        }
        "editorial" => {
            p.brand = rect(1, 2, w - 2, 2);
            p.body.y = a.y + 6;
            p.body.height = a.height - 6;
            p.ornament = rect(1, 4, w - 2, 1);
            p.art.y = a.y + 7;
            p.art.height = a.height - 8;
        }
        "console" => {
            p.brand = rect(3, 3, w - 6, 2);
            p.body = rect(3, 6, w - side - 7, a.height - 7);
            p.art = rect(w - side - 3, 5, side, a.height - 7);
            p.ornament.height = 0;
        }
        "poster" => {
            p.brand = rect(2, 2, w - 4, 4);
            p.body = rect(2, 8, w - side - 6, a.height - 8);
            p.art = rect(w - side - 2, 8, side, a.height - 9);
            p.ornament = rect(1, 6, w - 2, 1);
        }
        _ => {}
    }
    p
}

fn rows(ui: &Ui, key: &str) -> Vec<String> {
    ui.state
        .pointer(&format!("/theme/components/{key}"))
        .and_then(Value::as_array)
        .map(|v| {
            v.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

fn lettering(ui: &Ui, f: &mut Frame, area: Rect, rows: &[String], slot: &str) {
    let lines = rows
        .iter()
        .take(area.height as usize)
        .map(|text| {
            let chars = clean(text, ui.unicode()).chars().collect::<Vec<_>>();
            Line::from(
                chars
                    .iter()
                    .enumerate()
                    .map(|(x, c)| {
                        let mut style = ui.style(slot);
                        if ui.depth() == "truecolor" {
                            let start = token_color(
                                ui.palette()["primary"].as_str().unwrap_or(""),
                                "truecolor",
                            );
                            let end = token_color(
                                ui.palette()["secondary"].as_str().unwrap_or(""),
                                "truecolor",
                            );
                            if let (Color::Rgb(r, g, b), Color::Rgb(rr, gg, bb)) = (start, end) {
                                let t = x as f64 / chars.len().saturating_sub(1).max(1) as f64;
                                let mix = |a, b| (a as f64 * (1.0 - t) + b as f64 * t) as u8;
                                style = style.fg(Color::Rgb(mix(r, rr), mix(g, gg), mix(b, bb)));
                            }
                        }
                        Span::styled(c.to_string(), style.add_modifier(Modifier::BOLD))
                    })
                    .collect::<Vec<_>>(),
            )
        })
        .collect::<Vec<_>>();
    f.render_widget(Paragraph::new(lines), area);
}

pub fn draw(ui: &Ui, f: &mut Frame, area: Rect) {
    let mode = ui.component("composition");
    let p = geometry(area, &mode);
    if mode == "console" {
        f.render_widget(ui.block("P·31"), area);
    }
    let eyebrow = ui.component("eyebrow");
    f.render_widget(
        Paragraph::new(clean(&eyebrow, ui.unicode())).style(ui.style("muted")),
        p.eyebrow,
    );
    if p.brand.height > 0 {
        let mut wordmark = rows(ui, "wordmark");
        if wordmark
            .iter()
            .any(|row| clean(row, ui.unicode()).chars().count() > p.brand.width as usize)
        {
            wordmark = vec!["A U R O R A".into()];
        }
        lettering(ui, f, p.brand, &wordmark, "primary");
    }
    lettering(ui, f, p.ornament, &rows(ui, "ornament"), "secondary");
    draw_pixels(ui, f, p.art);
    let mut body = ui.text("body");
    let headline = ui.component("headline");
    if !headline.is_empty() && body.starts_with('#') {
        body = format!(
            "# {}\n{}",
            headline,
            body.split_once('\n').map(|(_, tail)| tail).unwrap_or("")
        );
    }
    if matches!(
        ui.text("connection").as_str(),
        "dégradé" | "degrade" | "hors ligne"
    ) {
        body = format!(
            "{}\n\nF5 réessayer · jobia connect\n\n{}",
            ui.text("health"),
            body
        );
    }
    f.render_widget(
        Paragraph::new(markdown(
            &clean(&body, ui.unicode()),
            ui,
            p.body.width as usize,
        ))
        .wrap(Wrap { trim: false }),
        p.body,
    );
}

fn draw_pixels(ui: &Ui, f: &mut Frame, area: Rect) {
    let art = rows(ui, "pixel_art");
    let palette = rows(ui, "pixel_palette");
    let source_width = art.iter().map(|r| r.len()).max().unwrap_or(0);
    if source_width == 0 || area.width == 0 || area.height == 0 {
        return;
    }
    let width = area.width.min(area.height * 2).min(38) as usize;
    let height = (width * art.len())
        .div_ceil(source_width * 2)
        .min(area.height as usize);
    let origin_x = area.x + (area.width - width as u16) / 2;
    let moving = ui.motion_interval().is_some();
    let effect = ui
        .state
        .pointer("/theme/banner/effect")
        .and_then(Value::as_str)
        .unwrap_or("static");
    let pixel = |x: usize, y: usize| {
        art.get(y)
            .and_then(|row| row.as_bytes().get(x))
            .and_then(|c| (*c as char).to_digit(16))
            .unwrap_or(0) as usize
    };
    for y in 0..height {
        for x in 0..width {
            let sx = x * source_width / width;
            let a = pixel(sx, y * 2 * art.len() / (height * 2));
            let b = pixel(sx, (y * 2 + 1) * art.len() / (height * 2));
            if a == 0 && b == 0 {
                continue;
            }
            if moving
                && matches!(effect, "reveal" | "typewriter")
                && y * width + x > ui.frame.saturating_mul(30)
            {
                continue;
            }
            let get = |i: usize| palette.get(i).map(String::as_str).unwrap_or("");
            let style = if ui.depth() == "none" {
                Style::default()
            } else {
                Style::default()
                    .fg(token_color(get(a), &ui.depth()))
                    .bg(token_color(get(b), &ui.depth()))
            };
            let glyph = if !ui.unicode() {
                if a == b { "#" } else { "+" }
            } else if ui.depth() == "none" {
                if a != 0 && b != 0 {
                    "█"
                } else if a != 0 {
                    "▀"
                } else {
                    "▄"
                }
            } else {
                "▀"
            };
            // A short light sweep affects pixels only. No text or geometry moves.
            let mut style = style;
            if moving
                && matches!(effect, "wave" | "breathe" | "glitch")
                && ui.depth() == "truecolor"
            {
                let phase = match effect {
                    "wave" => (x + y) as f64 / 5.0 - ui.frame as f64 / 4.0,
                    "glitch" => (ui.frame / 3) as f64 * 2.0,
                    _ => ui.frame as f64 / 5.0,
                };
                let gain = 0.78 + 0.22 * (phase.sin() + 1.0) / 2.0;
                let dim = |color| match color {
                    Color::Rgb(r, g, b) => Color::Rgb(
                        (r as f64 * gain) as u8,
                        (g as f64 * gain) as u8,
                        (b as f64 * gain) as u8,
                    ),
                    other => other,
                };
                style.fg = style.fg.map(dim);
                style.bg = style.bg.map(dim);
            }
            f.render_widget(
                Paragraph::new(glyph).style(style),
                Rect::new(origin_x + x as u16, area.y + y as u16, 1, 1),
            );
        }
    }
}

/// Compact identity stays beside the transcript through every mission state.
pub fn draw_companion(ui: &Ui, f: &mut Frame, area: Rect, stacked: bool) {
    if area.width < 4 || area.height < 2 {
        return;
    }
    let (art, body, ornament) = if stacked {
        let h = area.height.saturating_sub(1).max(1);
        (
            Rect::new(area.x, area.y, area.width, h),
            Rect::new(area.x + 1, area.y + h, area.width - 2, 1),
            Rect::default(),
        )
    } else {
        let w = (area.height * 2).min(16).min(area.width - 3);
        (
            Rect::new(area.x, area.y, w, area.height),
            Rect::new(area.x + w + 2, area.y, area.width - w - 3, area.height - 1),
            Rect::new(area.x + w + 2, area.bottom() - 1, area.width - w - 3, 1),
        )
    };
    draw_pixels(ui, f, art);
    f.render_widget(
        Paragraph::new(vec![
            Line::styled(
                clean(&ui.component("headline"), ui.unicode()),
                ui.style("primary").add_modifier(Modifier::BOLD),
            ),
            Line::styled(
                clean(&ui.component("eyebrow"), ui.unicode()),
                ui.style("muted"),
            ),
        ])
        .wrap(Wrap { trim: false }),
        body,
    );
    lettering(ui, f, ornament, &rows(ui, "ornament"), "secondary");
}
