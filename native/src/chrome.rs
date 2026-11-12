//! Visual chrome shared by the native screens: spinners, connection badge,
//! large pairing digits and palette ranking. Mirrors `aurora_cli/visuals.py`.
use crate::Ui;
use serde_json::Value;

const DOTS: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
const ARC: [&str; 6] = ["◜", "◠", "◝", "◞", "◡", "◟"];
const WAVE: [&str; 14] = [
    "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█", "▇", "▆", "▅", "▄", "▃", "▂",
];
const PULSE: [&str; 14] = [
    "█", "▉", "▊", "▋", "▌", "▍", "▎", "▏", "▎", "▍", "▌", "▋", "▊", "▉",
];
const ASCII_DOTS: [&str; 4] = ["|", "/", "-", "\\"];
const ASCII_ARC: [&str; 4] = ["-", "\\", "|", "/"];
const ASCII_WAVE: [&str; 4] = ["_", ".", "-", "~"];
const ASCII_PULSE: [&str; 4] = ["#", "*", "+", "."];

pub fn frames(kind: &str, unicode: bool) -> &'static [&'static str] {
    match (kind, unicode) {
        ("arc", true) => &ARC,
        ("wave", true) => &WAVE,
        ("pulse", true) => &PULSE,
        (_, true) => &DOTS,
        ("arc", false) => &ASCII_ARC,
        ("wave", false) => &ASCII_WAVE,
        ("pulse", false) => &ASCII_PULSE,
        (_, false) => &ASCII_DOTS,
    }
}

/// Seconds per frame for an animation budget; `None` keeps a still glyph.
pub fn frame_seconds(animation: &str) -> Option<f64> {
    match animation {
        "full" => Some(0.1),
        "reduced" => Some(0.25),
        _ => None,
    }
}

/// One clock for every animated element of the native screen.
pub fn spinner(ui: &Ui) -> String {
    let cycle = frames(&ui.component("spinner"), ui.unicode());
    let animation = ui.text("animation");
    let animate = ui
        .state
        .get("animate")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        && ui.depth() != "none";
    match (animate, frame_seconds(&animation)) {
        (true, Some(step)) => {
            let index = (ui.epoch.elapsed().as_secs_f64() / step) as usize % cycle.len();
            cycle[index].to_owned()
        }
        _ => cycle[0].to_owned(),
    }
}

/// Normalised connection state, also derived from older engines' text.
pub fn connection_state(ui: &Ui) -> String {
    let state = ui.text("connection_state");
    if !state.is_empty() {
        return state;
    }
    match ui.text("connection").as_str() {
        "connecté" | "en ligne" | "flux reçu" => "online",
        "dégradé" => "degraded",
        "reconnexion" => "reconnecting",
        "hors ligne" => "offline",
        "local" => "local",
        "autorisation requise" | "non configuré" => "unpaired",
        "appairage" => "pairing",
        _ => "checking",
    }
    .into()
}

/// `(glyph, palette tone)` of the connection badge.
pub fn badge(ui: &Ui) -> (String, &'static str) {
    let unicode = ui.unicode();
    let pick = |u: &str, a: &str| if unicode { u.to_owned() } else { a.to_owned() };
    match connection_state(ui).as_str() {
        "online" => (pick("●", "*"), "success"),
        "degraded" => (pick("◐", "~"), "warning"),
        "offline" => (pick("○", "o"), "error"),
        "local" => (pick("◆", "#"), "accent"),
        "unpaired" => (pick("◇", "?"), "warning"),
        "reconnecting" => (spinner(ui), "warning"),
        "pairing" => (spinner(ui), "accent"),
        _ => (spinner(ui), "info"),
    }
}

const BIG: [[&str; 3]; 10] = [
    ["█▀█", "█ █", "▀▀▀"],
    ["▀█ ", " █ ", "▀▀▀"],
    ["▀▀█", "█▀▀", "▀▀▀"],
    ["▀▀█", " ▀█", "▀▀▀"],
    ["█ █", "▀▀█", "  ▀"],
    ["█▀▀", "▀▀█", "▀▀▀"],
    ["█▀▀", "█▀█", "▀▀▀"],
    ["▀▀█", "  █", "  ▀"],
    ["█▀█", "█▀█", "▀▀▀"],
    ["█▀█", "▀▀█", "▀▀▀"],
];

/// Six digits three rows high, grouped 3 + 3 for easy copying.
pub fn big_code(code: &str, unicode: bool) -> Vec<String> {
    let digits = code
        .chars()
        .filter(char::is_ascii_digit)
        .take(12)
        .collect::<Vec<_>>();
    if !unicode {
        let spaced = digits
            .iter()
            .map(char::to_string)
            .collect::<Vec<_>>()
            .join(" ");
        return vec![if digits.len() == 6 {
            format!("{}   {}", &spaced[..5], &spaced[6..])
        } else {
            spaced
        }];
    }
    let mut rows = vec![String::new(), String::new(), String::new()];
    for (index, digit) in digits.iter().enumerate() {
        let gap = if index == 3 && digits.len() == 6 {
            "   "
        } else if index > 0 {
            " "
        } else {
            ""
        };
        let glyph = BIG[digit.to_digit(10).unwrap_or(0) as usize];
        for (row, part) in rows.iter_mut().zip(glyph) {
            row.push_str(gap);
            row.push_str(part);
        }
    }
    rows
}

fn fold(text: &str) -> String {
    text.chars()
        .map(|c| match c {
            'é' | 'è' | 'ê' | 'ë' | 'É' | 'È' | 'Ê' => 'e',
            'à' | 'â' | 'ä' | 'À' | 'Â' => 'a',
            'î' | 'ï' | 'Î' => 'i',
            'ô' | 'ö' | 'Ô' => 'o',
            'ù' | 'û' | 'ü' | 'Ù' => 'u',
            'ç' | 'Ç' => 'c',
            other => other.to_ascii_lowercase(),
        })
        .collect()
}

fn token_score(token: &str, field: &str) -> i64 {
    if field.is_empty() {
        return 0;
    }
    if field
        .split(|c: char| !c.is_alphanumeric())
        .any(|word| word == token)
    {
        return 30;
    }
    if field
        .split(|c: char| !c.is_alphanumeric())
        .any(|word| word.starts_with(token))
    {
        return 22;
    }
    if field.contains(token) {
        return 14;
    }
    let mut position = 0;
    for ch in token.chars() {
        match field[position..].find(ch) {
            Some(found) => position += found + ch.len_utf8(),
            None => return 0,
        }
    }
    if token.chars().count() >= 3 { 5 } else { 0 }
}

/// Palette relevance: name first, then kind and hint; 0 hides the item.
pub fn palette_score(query: &str, item: &Value) -> i64 {
    let label = fold(item.get("label").and_then(Value::as_str).unwrap_or(""));
    let kind = fold(item.get("kind").and_then(Value::as_str).unwrap_or(""));
    let hint = fold(item.get("hint").and_then(Value::as_str).unwrap_or(""));
    let tokens = fold(query)
        .split_whitespace()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    if tokens.is_empty() {
        return 1;
    }
    let mut total = 0;
    let mut matched = 0;
    for token in &tokens {
        let best = (token_score(token, &label) * 3)
            .max(token_score(token, &kind) * 2)
            .max(token_score(token, &hint));
        if best > 0 {
            matched += 1;
            total += best;
        }
    }
    if matched == 0 {
        return 0;
    }
    total * matched / tokens.len() as i64
}

pub fn kind_icon(kind: &str, unicode: bool) -> &'static str {
    match (kind, unicode) {
        ("vue", true) => "▣",
        ("thème", true) => "◐",
        ("skill", true) => "✦",
        ("outil MCP", true) => "⚙",
        ("serveur MCP", true) => "◈",
        ("modèle", true) => "◆",
        (_, true) => "›",
        ("vue", false) => "#",
        ("skill", false) => "*",
        ("outil MCP", false) => "%",
        (_, false) => ">",
    }
}
