//! Presentation only. Python remains the authority for missions and permissions.
pub mod ansi16;
pub mod chrome;
pub mod welcome;
use ratatui::{
    Frame,
    layout::{Constraint, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Clear, List, ListItem, ListState, Paragraph, Wrap},
};
use serde_json::{Value, json};
use std::time::{Duration, Instant};

pub const PROTOCOL_VERSION: u64 = 1;
pub const MAX_MESSAGE_BYTES: usize = 4 * 1024 * 1024;

/// Terminal control sequences never come from the engine's text channel.
pub fn clean(value: &str, unicode: bool) -> String {
    let mut result = String::with_capacity(value.len());
    let mut escape = false;
    let mut osc = false;
    let mut csi = false;
    for ch in value.chars() {
        if osc {
            if ch == '\x07' {
                osc = false;
            }
            if ch == '\x1b' {
                escape = true;
            }
            if escape && ch == '\\' {
                osc = false;
                escape = false;
            }
            continue;
        }
        if csi {
            if ('@'..='~').contains(&ch) {
                csi = false;
            }
            continue;
        }
        if escape {
            escape = false;
            if ch == '[' {
                csi = true;
            }
            if ch == ']' {
                osc = true;
            }
            continue;
        }
        if ch == '\x1b' {
            escape = true;
            continue;
        }
        if ch.is_control() && ch != '\n' && ch != '\t' {
            continue;
        }
        if unicode || ch.is_ascii() {
            result.push(ch);
            continue;
        }
        result.push(match ch {
            'é' | 'è' | 'ê' | 'ë' => 'e',
            'É' | 'È' | 'Ê' | 'Ë' => 'E',
            'à' | 'â' | 'ä' => 'a',
            'À' | 'Â' | 'Ä' => 'A',
            'î' | 'ï' => 'i',
            'Î' | 'Ï' => 'I',
            'ô' | 'ö' => 'o',
            'Ô' | 'Ö' => 'O',
            'ù' | 'û' | 'ü' => 'u',
            'Ù' | 'Û' | 'Ü' => 'U',
            'ç' => 'c',
            'Ç' => 'C',
            '·' | '•' | '◈' | '●' | '○' => '*',
            '─' | '━' | '—' | '–' => '-',
            '│' | '┃' => '|',
            '≈' => '~',
            c if ('\u{2800}'..='\u{28ff}').contains(&c) => '.',
            '▀' | '▄' | '█' | '▓' | '▒' | '░' => '#',
            '→' => '>',
            '←' => '<',
            '’' | '‘' => '\'',
            '“' | '”' => '"',
            '…' => '.',
            _ => '?',
        });
    }
    result
}

pub fn parse_state(line: &str) -> Result<Value, &'static str> {
    if line.len() > MAX_MESSAGE_BYTES {
        return Err("Message du moteur trop volumineux");
    }
    let msg: Value = serde_json::from_str(line).map_err(|_| "Message du moteur invalide")?;
    if msg.get("version").and_then(Value::as_u64) != Some(PROTOCOL_VERSION) {
        return Err("Version du protocole incompatible");
    }
    if msg.get("type").and_then(Value::as_str) != Some("state") {
        return Err("Type de message du moteur inconnu");
    }
    let state = msg
        .get("state")
        .filter(|v| v.is_object())
        .ok_or("État du moteur absent")?;
    Ok(state.clone())
}

pub fn command(name: &str, value: &str) -> String {
    json!({"version":PROTOCOL_VERSION,"type":"command","command":name,"value":value}).to_string()
}

fn string(v: &Value, key: &str) -> String {
    v.get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}
fn list(v: &Value, key: &str) -> Vec<Value> {
    v.get(key)
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
}

#[derive(Clone, Default)]
pub struct Input {
    pub text: Vec<char>,
    pub cursor: usize,
}
impl Input {
    pub fn insert(&mut self, ch: char) {
        self.text.insert(self.cursor, ch);
        self.cursor += 1;
    }
    pub fn backspace(&mut self) {
        if self.cursor > 0 {
            self.cursor -= 1;
            self.text.remove(self.cursor);
        }
    }
    pub fn delete(&mut self) {
        if self.cursor < self.text.len() {
            self.text.remove(self.cursor);
        }
    }
    pub fn left(&mut self) {
        self.cursor = self.cursor.saturating_sub(1);
    }
    pub fn right(&mut self) {
        self.cursor = (self.cursor + 1).min(self.text.len());
    }
    pub fn value(&self) -> String {
        self.text.iter().collect()
    }
    pub fn clear(&mut self) {
        self.text.clear();
        self.cursor = 0;
    }
    pub fn delete_word(&mut self) {
        while self.cursor > 0 && self.text[self.cursor - 1].is_whitespace() {
            self.backspace();
        }
        while self.cursor > 0 && !self.text[self.cursor - 1].is_whitespace() {
            self.backspace();
        }
    }
    pub fn layout(&self, width: usize, unicode: bool) -> (Vec<Line<'static>>, usize, usize) {
        let width = width.max(1);
        let mut rows = vec![String::new()];
        let (mut row, mut col) = (0, 0);
        let (mut cursor_row, mut cursor_col) = (0, 0);
        for (index, ch) in self.text.iter().enumerate() {
            let display = clean(&ch.to_string(), unicode).replace('\t', "    ");
            let cells = Line::from(display.as_str()).width();
            if *ch != '\n' && col + cells > width {
                rows.push(String::new());
                row += 1;
                col = 0;
            }
            if index == self.cursor {
                cursor_row = row;
                cursor_col = col;
            }
            if *ch == '\n' {
                rows.push(String::new());
                row += 1;
                col = 0;
            } else {
                rows[row].push_str(&display);
                col += cells;
                if col >= width {
                    rows.push(String::new());
                    row += 1;
                    col = 0;
                }
            }
        }
        if self.cursor == self.text.len() {
            cursor_row = row;
            cursor_col = col;
        }
        (
            rows.into_iter().map(Line::from).collect(),
            cursor_row,
            cursor_col,
        )
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ModalKind {
    Theme,
    Model,
    Palette,
}
pub struct Modal {
    pub kind: ModalKind,
    pub selected: usize,
    pub items: Vec<Value>,
    pub original_theme: String,
    /// Palette search text; filtering stays local for instant feedback.
    pub query: String,
}

pub struct Ui {
    pub state: Value,
    pub input: Input,
    pub modal: Option<Modal>,
    pub scroll: u16,
    pub follow: bool,
    pub notice: String,
    pub frame: usize,
    pub history: Vec<String>,
    pub history_index: Option<usize>,
    history_draft: Option<String>,
    restored_draft_revision: u64,
    pub motion_started: Option<Instant>,
    pub completion_index: usize,
    /// One clock for every animated element (badge, activity, pairing).
    pub epoch: Instant,
    /// Ctrl+K was pressed; the palette opens when the engine sends its items.
    pub palette_requested: bool,
}

impl Default for Ui {
    fn default() -> Self {
        Self {
            state: json!({"title":"AURORA","phase":"Connexion au moteur Python…","status":"starting","session":"—","target":"non observée","color_depth":"none","unicode":true}),
            input: Input::default(),
            modal: None,
            scroll: 0,
            follow: true,
            notice: String::new(),
            frame: 0,
            history: Vec::new(),
            history_index: None,
            history_draft: None,
            restored_draft_revision: 0,
            motion_started: None,
            completion_index: 0,
            epoch: Instant::now(),
            palette_requested: false,
        }
    }
}

impl Ui {
    pub fn update(&mut self, state: Value) {
        let revision = state
            .pointer("/draft_restore/revision")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        if revision > self.restored_draft_revision {
            self.restored_draft_revision = revision;
            if self.input.text.is_empty() {
                let draft = state
                    .pointer("/draft_restore/text")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                self.input.text = clean(draft, true).chars().collect();
                self.input.cursor = self.input.text.len();
            } else {
                self.notice =
                    "Demande récupérée dans la conversation ; votre brouillon reste intact".into();
            }
        }
        let changed = state.pointer("/theme/id") != self.state.pointer("/theme/id")
            || state.get("phase") != self.state.get("phase")
            || state.get("status") != self.state.get("status");
        self.state = state;
        if changed {
            self.motion_started = Some(Instant::now());
            self.frame = 0;
        }
    }
    /// Real work in progress: mission, reconnection or pairing.
    pub fn ticking(&self) -> bool {
        self.state
            .get("ticking")
            .and_then(Value::as_bool)
            .unwrap_or(false)
    }
    pub fn motion_interval(&self) -> Option<Duration> {
        let enabled = self
            .state
            .get("animate")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        if enabled && self.ticking() && self.depth() != "none" {
            // Animate only while something really happens, at the budget's pace.
            if let Some(step) = chrome::frame_seconds(&string(&self.state, "animation")) {
                return Some(Duration::from_secs_f64(step));
            }
        }
        let duration = self
            .state
            .pointer("/theme/banner/total_ms")
            .and_then(Value::as_u64)
            .unwrap_or(420)
            .min(800);
        let started = self.motion_started?;
        if !enabled
            || self
                .state
                .pointer("/theme/banner/effect")
                .and_then(Value::as_str)
                == Some("static")
            || self.depth() == "none"
            || string(&self.state, "animation") == "none"
            || started.elapsed() >= Duration::from_millis(duration)
        {
            return None;
        }
        Some(Duration::from_secs_f64(
            if string(&self.state, "animation") == "reduced" {
                1.0 / 8.0
            } else {
                1.0 / 60.0
            },
        ))
    }
    pub fn component(&self, key: &str) -> String {
        self.state
            .pointer(&format!("/theme/components/{key}"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned()
    }
    pub fn complete(&mut self) {
        let current = self.input.value();
        if !current.starts_with('/') || current.contains(' ') {
            return;
        }
        let matches = list(&self.state, "commands")
            .iter()
            .map(|v| string(v, "name"))
            .filter(|n| n.starts_with(&current))
            .collect::<Vec<_>>();
        if let Some(name) = matches.first() {
            self.input.text = format!("{name} ").chars().collect();
            self.input.cursor = self.input.text.len();
        }
    }

    pub fn unicode(&self) -> bool {
        self.state
            .get("unicode")
            .and_then(Value::as_bool)
            .unwrap_or(true)
    }
    pub fn text(&self, key: &str) -> String {
        clean(&string(&self.state, key), self.unicode())
    }
    pub fn depth(&self) -> String {
        let depth = self
            .state
            .get("color_depth")
            .map(|v| {
                v.as_str()
                    .map(str::to_owned)
                    .unwrap_or_else(|| v.to_string())
            })
            .unwrap_or_else(|| "none".into());
        match depth.as_str() {
            "1" => "none".into(),
            "4" => "ansi".into(),
            "8" | "color256" => "256".into(),
            "color8" => "ansi".into(),
            "24" => "truecolor".into(),
            _ => depth,
        }
    }
    pub fn palette(&self) -> &Value {
        self.state
            .get("theme")
            .and_then(|t| t.get("palette").or(Some(t)))
            .unwrap_or(&Value::Null)
    }
    pub fn style(&self, token: &str) -> Style {
        let depth = self.depth();
        if depth == "none" {
            return Style::default();
        }
        let value = self
            .palette()
            .get(token)
            .and_then(Value::as_str)
            .unwrap_or_default();
        Style::default().fg(token_color(value, &depth))
    }
    fn background(&self) -> Style {
        let depth = self.depth();
        if depth == "none" {
            return Style::default();
        }
        let bg = self
            .palette()
            .get("background")
            .or_else(|| self.palette().get("surface"))
            .and_then(Value::as_str)
            .unwrap_or_default();
        self.style("text").bg(token_color(bg, &depth))
    }
    pub fn theme_id(&self) -> String {
        self.state
            .get("theme")
            .map(|v| string(v, "id"))
            .unwrap_or_default()
    }
    pub fn open_modal(&mut self, kind: ModalKind) -> Option<String> {
        if kind == ModalKind::Palette {
            let items = list(&self.state, "palette");
            if items.is_empty() {
                self.palette_requested = true;
                return Some(command("palette", ""));
            }
            self.palette_requested = false;
            self.modal = Some(Modal {
                kind,
                selected: 0,
                items,
                original_theme: self.theme_id(),
                query: String::new(),
            });
            return None;
        }
        let key = if kind == ModalKind::Theme {
            "themes"
        } else {
            "models"
        };
        let mut items = list(&self.state, key);
        if kind == ModalKind::Model {
            items.insert(
                0,
                json!({"id":"", "label":"Automatique · choix du serveur"}),
            );
        }
        if items.is_empty() {
            self.notice = if kind == ModalKind::Theme {
                "Catalogue des thèmes indisponible"
            } else {
                "Aucun modèle observé ; F5 actualise le diagnostic"
            }
            .into();
            return None;
        }
        let current = if kind == ModalKind::Theme {
            self.theme_id()
        } else {
            self.text("model")
        };
        let selected = items
            .iter()
            .position(|v| item_id(v) == current)
            .unwrap_or(0);
        self.modal = Some(Modal {
            kind,
            selected,
            items,
            original_theme: self.theme_id(),
            query: String::new(),
        });
        None
    }
    /// Palette items matching the query, most relevant first.
    pub fn palette_matches(&self) -> Vec<Value> {
        let Some(m) = &self.modal else {
            return Vec::new();
        };
        let mut scored = m
            .items
            .iter()
            .enumerate()
            .filter_map(|(index, item)| {
                let score = chrome::palette_score(&m.query, item);
                (score > 0).then(|| (score, index, item.clone()))
            })
            .collect::<Vec<_>>();
        scored.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(&b.1)));
        scored
            .into_iter()
            .take(80)
            .map(|(_, _, item)| item)
            .collect()
    }
    pub fn palette_input(&mut self, ch: Option<char>) {
        if let Some(m) = self.modal.as_mut() {
            match ch {
                Some(c) if m.query.chars().count() < 200 => m.query.push(c),
                Some(_) => {}
                None => {
                    m.query.pop();
                }
            }
            m.selected = 0;
        }
    }
    pub fn move_modal(&mut self, delta: isize) -> Option<String> {
        let count = if self.modal.as_ref()?.kind == ModalKind::Palette {
            self.palette_matches().len()
        } else {
            self.modal.as_ref()?.items.len()
        };
        let m = self.modal.as_mut()?;
        if count == 0 {
            return None;
        }
        m.selected = (m.selected as isize + delta).rem_euclid(count as isize) as usize;
        if m.kind == ModalKind::Theme {
            Some(command("theme_preview", &item_id(&m.items[m.selected])))
        } else {
            None
        }
    }
    pub fn close_modal(&mut self, accept: bool) -> Option<String> {
        if self.modal.as_ref()?.kind == ModalKind::Palette {
            let matches = self.palette_matches();
            let selected = self.modal.as_ref()?.selected;
            self.modal = None;
            let cancel = command("picker_cancel", "");
            if !accept || matches.is_empty() {
                return Some(cancel);
            }
            let item = &matches[selected.min(matches.len() - 1)];
            let value = string(item, "value");
            // The palette closes first, then its action runs: two JSON lines.
            return match string(item, "action").as_str() {
                "insert" => {
                    self.input.text = value.chars().collect();
                    self.input.cursor = self.input.text.len();
                    Some(cancel)
                }
                "view" => Some(format!("{cancel}\n{}", command("page", &value))),
                "theme" => Some(format!("{cancel}\n{}", command("theme_commit", &value))),
                "model" => Some(format!("{cancel}\n{}", command("model", &value))),
                _ => {
                    self.history.push(value.clone());
                    Some(format!("{cancel}\n{}", command("submit", &value)))
                }
            };
        }
        let m = self.modal.take()?;
        match m.kind {
            ModalKind::Palette => None,
            ModalKind::Theme => {
                let value = if accept {
                    item_id(&m.items[m.selected])
                } else {
                    m.original_theme
                };
                Some(command(
                    if accept {
                        "theme_commit"
                    } else {
                        "theme_cancel"
                    },
                    &value,
                ))
            }
            ModalKind::Model => {
                if accept {
                    Some(command("model", &item_id(&m.items[m.selected])))
                } else {
                    Some(command("picker_cancel", ""))
                }
            }
        }
    }
    pub fn submit(&mut self) -> Option<String> {
        let text = self.input.value();
        let command_name = text.split_whitespace().next().unwrap_or("").to_lowercase();
        let presentation = list(&self.state, "interactive_commands")
            .iter()
            .any(|item| item.as_str() == Some(command_name.as_str()));
        if self
            .state
            .get("busy")
            .and_then(Value::as_bool)
            .unwrap_or(false)
            && text.trim() != "/quit"
            && !presentation
        {
            self.notice = "Mission active : Ctrl+C demande son arrêt ; brouillon conservé".into();
            return None;
        }
        if text.trim().is_empty() {
            return None;
        }
        self.history.push(text.clone());
        if self.history.len() > 200 {
            self.history.remove(0);
        }
        self.history_index = None;
        self.history_draft = None;
        self.input.clear();
        self.follow = true;
        self.notice.clear();
        Some(command("submit", &text))
    }
    pub fn recall(&mut self, back: bool) {
        if self.history.is_empty() {
            return;
        }
        if self.history_index.is_none() {
            self.history_draft = Some(self.input.value());
        }
        let index = self.history_index.unwrap_or(self.history.len());
        let next = if back {
            index.saturating_sub(1)
        } else {
            (index + 1).min(self.history.len())
        };
        self.history_index = if next == self.history.len() {
            None
        } else {
            Some(next)
        };
        self.input.text = if next == self.history.len() {
            self.history_draft
                .take()
                .unwrap_or_default()
                .chars()
                .collect()
        } else {
            self.history[next].chars().collect()
        };
        self.input.cursor = self.input.text.len();
    }
    pub fn draw(&mut self, f: &mut Frame) {
        let area = f.area();
        f.render_widget(Block::default().style(self.background()), area);
        if area.width < 20 || area.height < 8 {
            f.render_widget(
                Paragraph::new(clean(
                    "AURORA\nTerminal trop petit\nCtrl+Q : quitter",
                    self.unicode(),
                ))
                .wrap(Wrap { trim: false }),
                area,
            );
            return;
        }
        let gutter = self
            .state
            .pointer("/theme/spacing/gutter")
            .and_then(Value::as_u64)
            .unwrap_or(1)
            .min(3) as u16;
        let inner = Rect::new(
            area.x + gutter,
            area.y,
            area.width.saturating_sub(gutter * 2),
            area.height,
        );
        let prompt_rows = (self.input.value().lines().count().max(1) as u16 + 2).clamp(3, 6);
        let activity_rows = if area.height >= 28 || list(&self.state, "recovery_notices").len() > 1
        {
            3
        } else {
            2
        };
        let parts = Layout::vertical([
            Constraint::Length(3),
            Constraint::Length(1),
            Constraint::Min(2),
            Constraint::Length(activity_rows),
            Constraint::Length(prompt_rows),
            Constraint::Length(1),
        ])
        .split(inner);
        self.draw_header(f, parts[0]);
        self.draw_tabs(f, parts[1]);
        let welcome = self
            .state
            .get("welcome")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let wide = area.width >= 112 && !welcome && !list(&self.state, "sidebar").is_empty();
        let sidebar_width = self
            .state
            .pointer("/theme/spacing/sidebar_width")
            .and_then(Value::as_u64)
            .unwrap_or(30)
            .clamp(22, 40) as u16;
        let content = Layout::horizontal([
            Constraint::Min(30),
            Constraint::Length(if wide { sidebar_width } else { 0 }),
        ])
        .split(parts[2]);
        let mut conversation = content[0];
        if !welcome && !wide {
            let h = if area.height >= 40 { 4 } else { 3 };
            let band = Rect::new(
                conversation.x,
                conversation.y,
                conversation.width,
                h.min(conversation.height),
            );
            welcome::draw_companion(self, f, band, false);
            conversation.y += band.height;
            conversation.height = conversation.height.saturating_sub(band.height);
        }
        let lines = markdown(
            &self.text("body"),
            self,
            conversation.width.saturating_sub(4) as usize,
        );
        let body_height = conversation.height.saturating_sub(2) as usize;
        let body = Paragraph::new(lines)
            .block(self.block(&self.text("page")))
            .wrap(Wrap { trim: false });
        let height = body.line_count(conversation.width).saturating_sub(2);
        if self.follow {
            self.scroll = height.saturating_sub(body_height).min(u16::MAX as usize) as u16;
        }
        let body = body.scroll((self.scroll, 0));
        if self
            .state
            .get("welcome")
            .and_then(Value::as_bool)
            .unwrap_or(false)
            && content[0].height >= 12
            && content[0].width >= 46
        {
            self.draw_welcome(f, content[0]);
        } else {
            f.render_widget(body, conversation);
        }
        if wide {
            let side = content[1];
            let h = (area.height.saturating_sub(13) / 2)
                .clamp(10, 14)
                .min(side.height);
            welcome::draw_companion(self, f, Rect::new(side.x, side.y, side.width, h), true);
            let mission_area = Rect::new(
                side.x,
                side.y + h,
                side.width,
                side.height.saturating_sub(h),
            );
            let text = value_lines(&self.state, "sidebar", self.unicode())
                .into_iter()
                .map(|line| self.sidebar_line(line))
                .collect::<Vec<_>>();
            f.render_widget(
                Paragraph::new(text)
                    .block(self.block("Mission"))
                    .wrap(Wrap { trim: false }),
                mission_area,
            );
        }
        let activity = value_lines(&self.state, "activity", self.unicode());
        let label = self.text("activity_label");
        let status = if !self.notice.is_empty() {
            clean(&self.notice, self.unicode())
        } else if !label.is_empty() && matches!(self.text("status").as_str(), "idle" | "") {
            // Loading, reconnection or pairing explained in words while idle.
            label
        } else {
            format!("{} · {}", self.text("status"), self.text("phase"))
        };
        let glyph = if self.ticking() {
            chrome::spinner(self)
        } else if self.unicode() {
            "·".into()
        } else {
            "-".into()
        };
        let mut lines = vec![Line::from(vec![
            Span::styled(
                format!("{glyph} "),
                self.style("accent").add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                clean(&status, self.unicode()),
                self.style(if self.text("status") == "failed" {
                    "error"
                } else {
                    "accent"
                }),
            ),
        ])];
        for line in activity
            .into_iter()
            .rev()
            .take(activity_rows.saturating_sub(1) as usize)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
        {
            lines.push(Line::styled(line, self.style("muted")));
        }
        f.render_widget(Paragraph::new(lines).wrap(Wrap { trim: false }), parts[3]);
        if self.input.value().starts_with('/') {
            let query = self.input.value();
            let choices = list(&self.state, "commands")
                .iter()
                .filter(|item| string(item, "name").starts_with(&query))
                .take(3)
                .map(|item| format!("{}  {}", string(item, "name"), string(item, "description")))
                .collect::<Vec<_>>();
            if !choices.is_empty() {
                f.render_widget(
                    Paragraph::new(clean(
                        &format!("Tab complète · {}", choices.join("  /  ")),
                        self.unicode(),
                    ))
                    .style(self.style("accent")),
                    parts[3],
                );
            }
        }
        let promptblock = self.block("Votre demande · Entrée envoyer · Alt+Entrée ligne");
        let prompt_inner = promptblock.inner(parts[4]);
        let (prompt_lines, cursor_row, cursor_col) = self
            .input
            .layout(prompt_inner.width as usize, self.unicode());
        let offset = cursor_row.saturating_sub(prompt_inner.height.saturating_sub(1) as usize);
        f.render_widget(
            Paragraph::new(prompt_lines)
                .block(promptblock)
                .scroll((offset.min(u16::MAX as usize) as u16, 0)),
            parts[4],
        );
        if self.modal.is_none() {
            f.set_cursor_position((
                prompt_inner.x + cursor_col as u16,
                prompt_inner.y + (cursor_row - offset) as u16,
            ));
        }
        let keys: &[(&str, &str)] = if area.width < 70 {
            &[
                ("^K", "palette"),
                ("F2", "thème"),
                ("F4", "vue"),
                ("^C", "arrêt"),
                ("^Q", "sortie"),
            ]
        } else if area.width < 115 {
            &[
                ("Ctrl+K", "palette"),
                ("F2", "thèmes"),
                ("F3", "modèles"),
                ("F4", "vues"),
                ("F5", "état"),
                ("^C", "arrêt"),
                ("^Q", "sortie"),
            ]
        } else {
            &[
                ("Ctrl+K", "palette"),
                ("F2", "thèmes"),
                ("F3", "modèles"),
                ("F4", "vues"),
                ("F5", "état"),
                ("Pg↑↓", "journal"),
                ("Ctrl+N", "nouveau"),
                ("Ctrl+C", "arrêt"),
                ("Ctrl+Q", "sortie"),
            ]
        };
        let mut footer = Vec::new();
        let mut used = 0;
        for (key, label) in keys {
            let cap = if self.unicode() {
                format!(" {key} ")
            } else {
                format!("[{key}]")
            };
            let piece = Line::from(cap.as_str()).width() + Line::from(*label).width() + 3;
            if used + piece > parts[5].width as usize {
                break;
            }
            used += piece;
            footer.push(Span::styled(
                clean(&cap, self.unicode()),
                self.raised().add_modifier(Modifier::BOLD),
            ));
            footer.push(Span::styled(
                clean(&format!(" {label}  "), self.unicode()),
                self.style("muted"),
            ));
        }
        f.render_widget(Paragraph::new(Line::from(footer)), parts[5]);
        self.draw_toast(f, area);
        self.draw_pairing(f, area);
        self.draw_modal(f, area);
    }
    /// Mission panel line: sections, check marks and a coloured gauge.
    fn sidebar_line(&self, line: String) -> Line<'static> {
        if matches!(
            line.as_str(),
            "PLAN" | "CRITÈRES" | "CRITERES" | "OBJECTIF" | "OBSERVATIONS"
        ) {
            return Line::styled(line, self.style("secondary").add_modifier(Modifier::BOLD));
        }
        if let Some(rest) = line.strip_prefix("PROGRESSION ") {
            let (bar, pct) = rest.rsplit_once(' ').unwrap_or((rest, ""));
            let empty = if self.unicode() { '░' } else { '-' };
            let done = bar.trim_end_matches(empty);
            let todo = &bar[done.len()..];
            return Line::from(vec![
                Span::styled(
                    "PROGRESSION ",
                    self.style("secondary").add_modifier(Modifier::BOLD),
                ),
                Span::styled(done.to_owned(), self.style("primary")),
                Span::styled(todo.to_owned(), self.style("border")),
                Span::styled(format!(" {pct}"), self.style("accent")),
            ]);
        }
        if line.starts_with("✓ ") || line.starts_with("+ ") {
            return Line::styled(line, self.style("success"));
        }
        if line.starts_with("× ") || line.starts_with("x ") {
            return Line::styled(line, self.style("error"));
        }
        Line::from(line)
    }
    fn block(&self, title: &str) -> Block<'static> {
        let kind = self
            .state
            .pointer("/theme/box")
            .and_then(Value::as_str)
            .unwrap_or("rounded");
        let borders = if kind == "minimal" {
            Borders::TOP
        } else {
            Borders::ALL
        };
        let block = Block::default()
            .borders(borders)
            .title(format!(" {} ", clean(title, self.unicode())))
            .border_style(self.style("border"))
            .title_style(self.style("secondary"));
        if self.unicode() {
            block.border_type(match kind {
                "double" => ratatui::widgets::BorderType::Double,
                "heavy" => ratatui::widgets::BorderType::Thick,
                "square" | "minimal" => ratatui::widgets::BorderType::Plain,
                _ => ratatui::widgets::BorderType::Rounded,
            })
        } else {
            block.border_set(ratatui::symbols::border::Set {
                top_left: "+",
                top_right: "+",
                bottom_left: "+",
                bottom_right: "+",
                vertical_left: "|",
                vertical_right: "|",
                horizontal_top: "-",
                horizontal_bottom: "-",
            })
        }
    }
    fn raised(&self) -> Style {
        let depth = self.depth();
        if depth == "none" {
            return Style::default();
        }
        let raised = self.palette()["raised"].as_str().unwrap_or("");
        self.style("text").bg(token_color(raised, &depth))
    }
    fn tone(&self, tone: &str) -> Style {
        if self.depth() == "none" {
            return Style::default().add_modifier(Modifier::BOLD);
        }
        self.raised()
            .fg(token_color(
                self.palette()[tone].as_str().unwrap_or(""),
                &self.depth(),
            ))
            .add_modifier(Modifier::BOLD)
    }
    fn draw_header(&self, f: &mut Frame, area: Rect) {
        let title = self.text("title");
        let header = self.component("header");
        let motif = match header.as_str() {
            "tidal" => "≈",
            "orbital" => "◉",
            "facet" => "◇",
            "sunset" => "▰",
            "block" => "■",
            "terminal" => ">_",
            "editorial" => "—",
            _ => "╱",
        };
        let mut connection = self.text("connection_label");
        if connection.is_empty() {
            connection = self.text("connection");
        }
        if connection.is_empty() {
            connection = "non observée".into();
        }
        let latency = self
            .state
            .get("latency")
            .map(|v| {
                if let Some(s) = v.as_str() {
                    s.to_owned()
                } else if v.is_number() {
                    format!("{v} ms")
                } else {
                    "n/d".into()
                }
            })
            .unwrap_or_else(|| "n/d".into());
        let target = self.text("target");
        let session = self.text("session");
        let mission = self.text("mission");
        let mission = if mission.is_empty() {
            "aucune".into()
        } else {
            mission
        };
        let width = area.width as usize;
        let (glyph, tone) = chrome::badge(self);
        let pill = clean(
            &format!(" {glyph} {connection} · {latency} "),
            self.unicode(),
        );
        let pill = fit(&pill, width.saturating_sub(10).max(8), self.unicode());
        let label = string(self.state.get("theme").unwrap_or(&Value::Null), "label");
        let left = clean(
            &format!(
                " {} {}{} ",
                motif,
                if title.is_empty() { "AURORA" } else { &title },
                if label.is_empty() {
                    String::new()
                } else {
                    format!(" · {label}")
                }
            ),
            self.unicode(),
        );
        let pill_width = Line::from(pill.as_str()).width();
        let left = fit(&left, width.saturating_sub(pill_width + 1), self.unicode());
        let gap = width.saturating_sub(Line::from(left.as_str()).width() + pill_width);
        let gradient = self.depth() == "truecolor"
            && self
                .state
                .pointer("/theme/banner/gradient")
                .and_then(Value::as_bool)
                .unwrap_or(false);
        let mut band = Vec::new();
        if gradient {
            let start = self.palette()["banner"].as_str().unwrap_or("");
            let end = self.palette()["banner_alt"].as_str().unwrap_or("");
            let length = left.chars().count().saturating_sub(1).max(1) as f64;
            for (i, ch) in left.chars().enumerate() {
                let color = match (
                    token_color(start, "truecolor"),
                    token_color(end, "truecolor"),
                ) {
                    (Color::Rgb(r, g, b), Color::Rgb(x, y, z)) => {
                        let t = i as f64 / length;
                        let mix = |a, b| (a as f64 * (1.0 - t) + b as f64 * t) as u8;
                        Color::Rgb(mix(r, x), mix(g, y), mix(b, z))
                    }
                    _ => Color::Reset,
                };
                band.push(Span::styled(
                    ch.to_string(),
                    self.raised().fg(color).add_modifier(Modifier::BOLD),
                ));
            }
        } else {
            band.push(Span::styled(
                left,
                self.raised()
                    .patch(self.style("primary"))
                    .add_modifier(Modifier::BOLD),
            ));
        }
        band.push(Span::styled(" ".repeat(gap), self.raised()));
        band.push(Span::styled(pill, self.tone(tone)));
        let target = paired(
            &format!(" Cible {target}"),
            &format!("Mission {mission} "),
            width,
            self.unicode(),
        );
        let skills = list(&self.state, "active_skills")
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect::<Vec<_>>();
        let identity = paired(
            &format!(
                " Session {session}{}",
                if skills.is_empty() {
                    String::new()
                } else {
                    format!(" · Skills {}", skills.join(", "))
                }
            ),
            &format!("{} ", self.text("phase")),
            width,
            self.unicode(),
        );
        let lines = vec![
            Line::from(band),
            Line::styled(target, self.style("secondary")),
            Line::styled(identity, self.style("muted")),
        ];
        f.render_widget(Paragraph::new(lines), area);
    }
    fn draw_toast(&self, f: &mut Frame, area: Rect) {
        let Some(toast) = self.state.get("toast").filter(|t| t.is_object()) else {
            return;
        };
        let text = clean(&string(toast, "text"), self.unicode());
        if text.is_empty() || area.width < 30 {
            return;
        }
        let tone = match string(toast, "tone").as_str() {
            "success" => "success",
            "warning" => "warning",
            "error" => "error",
            "accent" => "accent",
            _ => "info",
        };
        let width = (Line::from(text.as_str()).width() as u16 + 5)
            .min(area.width.saturating_sub(4))
            .min(66);
        let inner = width.saturating_sub(4).max(1) as usize;
        let rows = Line::from(text.as_str())
            .width()
            .div_ceil(inner)
            .clamp(1, 3) as u16;
        let rect = Rect::new(area.x + area.width - width - 2, area.y + 4, width, rows);
        f.render_widget(Clear, rect);
        let bar = if self.unicode() { "▌ " } else { "| " };
        let lines = (0..rows as usize)
            .map(|row| {
                let chunk = text
                    .chars()
                    .skip(row * inner)
                    .take(inner)
                    .collect::<String>();
                Line::from(vec![
                    Span::styled(bar, self.tone(tone)),
                    Span::styled(format!("{chunk:<inner$} "), self.raised()),
                ])
            })
            .collect::<Vec<_>>();
        f.render_widget(Paragraph::new(lines), rect);
    }
    fn draw_pairing(&self, f: &mut Frame, area: Rect) {
        let Some(pairing) = self.state.get("pairing").filter(|p| p.is_object()) else {
            return;
        };
        let code = string(pairing, "code");
        if code.is_empty() || area.width < 34 || area.height < 12 {
            return;
        }
        let width = 48.min(area.width - 2);
        let rect = Rect::new(
            area.x + (area.width - width) / 2,
            area.y + (area.height.saturating_sub(11)) / 2,
            width,
            11,
        );
        f.render_widget(Clear, rect);
        let mut lines = vec![
            Line::styled(
                clean("  ◎ Associer cet ordinateur au PC Aurora", self.unicode()),
                self.tone("primary"),
            ),
            Line::styled(
                format!("  {}", clean(&string(pairing, "target"), self.unicode())),
                self.raised().patch(self.style("muted")),
            ),
            Line::styled(String::new(), self.raised()),
        ];
        for row in chrome::big_code(&code, self.unicode()) {
            lines.push(Line::styled(format!("    {row}"), self.tone("accent")));
        }
        let expires = pairing
            .get("expires_in")
            .and_then(Value::as_u64)
            .map(|left| format!("Expire dans {}:{:02} · ", left / 60, left % 60))
            .unwrap_or_default();
        lines.extend([
            Line::styled(String::new(), self.raised()),
            Line::styled(
                clean("  Saisissez ce code dans la fenêtre du PC.", self.unicode()),
                self.raised(),
            ),
            Line::styled(
                clean(&format!("  {expires}Ctrl+C annule"), self.unicode()),
                self.raised().patch(self.style("muted")),
            ),
        ]);
        f.render_widget(Paragraph::new(lines).style(self.raised()), rect);
    }
    fn draw_welcome(&self, f: &mut Frame, area: Rect) {
        welcome::draw(self, f, area);
    }
    fn draw_tabs(&self, f: &mut Frame, area: Rect) {
        let page = self.text("page");
        let tabs = list(&self.state, "tabs");
        let mut spans = vec![Span::raw(" ")];
        let compact = area.width < 70;
        let short = area.width < 110;
        for (i, t) in tabs.iter().enumerate() {
            let id = item_id(t);
            let mut label = string(t, "label");
            if label.is_empty() {
                label = id.clone();
            }
            if short {
                label = match id.as_str() {
                    "conversation" => "Chat".into(),
                    "modules" => "Mod".into(),
                    "agents" => "Agt".into(),
                    "skills" => "Skill".into(),
                    "avant_apres" => "Bil".into(),
                    "journal" => "Jrnl".into(),
                    "fichiers" => "Fich".into(),
                    _ => label,
                };
            }
            let active = id == page;
            if compact && !active {
                continue;
            }
            let count = self
                .state
                .pointer(&format!("/tab_counts/{id}"))
                .and_then(Value::as_u64)
                .filter(|n| *n > 0 && area.width >= 96)
                .map(|n| format!(" {n}"))
                .unwrap_or_default();
            let style = if active {
                if self.depth() == "none" {
                    Style::default().add_modifier(Modifier::REVERSED | Modifier::BOLD)
                } else {
                    Style::default()
                        .fg(token_color(
                            self.palette()["surface"].as_str().unwrap_or(""),
                            &self.depth(),
                        ))
                        .bg(token_color(
                            self.palette()["primary"].as_str().unwrap_or(""),
                            &self.depth(),
                        ))
                        .add_modifier(Modifier::BOLD)
                }
            } else {
                self.style("muted")
            };
            spans.push(Span::styled(
                clean(&format!(" {} {label}{count} ", i + 1), self.unicode()),
                style,
            ));
            spans.push(Span::raw(" "));
        }
        if compact {
            spans.push(Span::styled(
                clean("  F4 vues · Ctrl+K palette", self.unicode()),
                self.style("muted"),
            ));
        }
        f.render_widget(Paragraph::new(Line::from(spans)), area);
    }
    fn draw_palette(&self, f: &mut Frame, area: Rect) {
        let Some(m) = &self.modal else { return };
        let width = area.width.saturating_sub(4).min(88);
        let height = area.height.saturating_sub(6).clamp(6, 22);
        let rect = Rect::new(area.x + (area.width - width) / 2, area.y + 3, width, height);
        f.render_widget(Clear, rect);
        let matches = self.palette_matches();
        let block = self
            .block(&format!(
                "Palette · commandes, vues, thèmes, skills, MCP, modèles · {}",
                matches.len()
            ))
            .style(self.raised());
        let inner = block.inner(rect);
        f.render_widget(block, rect);
        let cursor = if self.unicode() { "› " } else { "> " };
        let mut lines = vec![Line::from(vec![
            Span::styled(cursor, self.tone("accent")),
            Span::styled(
                clean(&m.query, self.unicode()),
                self.raised().add_modifier(Modifier::BOLD),
            ),
            Span::styled(if self.unicode() { "▏" } else { "_" }, self.tone("accent")),
        ])];
        let rows = inner.height.saturating_sub(2) as usize;
        let selected = m.selected.min(matches.len().saturating_sub(1));
        let start = selected
            .saturating_sub(rows / 2)
            .min(matches.len().saturating_sub(rows));
        for (index, item) in matches.iter().enumerate().skip(start).take(rows) {
            let kind = string(item, "kind");
            let text = clean(
                &format!(
                    " {} {}  {} · {}",
                    chrome::kind_icon(&kind, self.unicode()),
                    string(item, "label"),
                    kind,
                    string(item, "hint")
                ),
                self.unicode(),
            );
            let text = fit(&text, inner.width as usize, self.unicode());
            let style = if index == selected {
                if self.depth() == "none" {
                    Style::default().add_modifier(Modifier::REVERSED)
                } else {
                    Style::default()
                        .fg(token_color(
                            self.palette()["surface"].as_str().unwrap_or(""),
                            &self.depth(),
                        ))
                        .bg(token_color(
                            self.palette()["primary"].as_str().unwrap_or(""),
                            &self.depth(),
                        ))
                        .add_modifier(Modifier::BOLD)
                }
            } else {
                self.raised()
            };
            lines.push(Line::styled(
                format!("{text:<width$}", width = inner.width as usize),
                style,
            ));
        }
        if matches.is_empty() {
            lines.push(Line::styled(
                clean(" Aucun résultat", self.unicode()),
                self.raised().patch(self.style("muted")),
            ));
        }
        let footer = Rect::new(
            inner.x,
            inner.y + inner.height.saturating_sub(1),
            inner.width,
            1,
        );
        f.render_widget(Paragraph::new(lines).style(self.raised()), inner);
        f.render_widget(
            Paragraph::new(clean(
                " ↑↓ choisir · Entrée exécuter · Échap fermer",
                self.unicode(),
            ))
            .style(self.raised().patch(self.style("muted"))),
            footer,
        );
    }
    fn draw_modal(&self, f: &mut Frame, area: Rect) {
        let Some(m) = &self.modal else { return };
        if m.kind == ModalKind::Palette {
            self.draw_palette(f, area);
            return;
        }
        let width = area.width.saturating_sub(4).min(76);
        let height = area.height.saturating_sub(4).min(m.items.len() as u16 + 6);
        let rect = Rect::new(
            area.x + (area.width - width) / 2,
            area.y + (area.height - height) / 2,
            width,
            height,
        );
        f.render_widget(Clear, rect);
        let title = if m.kind == ModalKind::Theme {
            "Thèmes · aperçu immédiat"
        } else {
            "Modèles observés"
        };
        let block = self.block(title).style(self.background());
        let inner = block.inner(rect);
        f.render_widget(block, rect);
        let items = m
            .items
            .iter()
            .map(|item| {
                let label = string(item, "label");
                let label = if label.is_empty() {
                    item_id(item)
                } else {
                    label
                };
                let desc = string(item, "description");
                ListItem::new(clean(&format!("{}  {}", label, desc), self.unicode()))
            })
            .collect::<Vec<_>>();
        let parts = Layout::vertical([Constraint::Min(1), Constraint::Length(2)]).split(inner);
        let mut state = ListState::default().with_selected(Some(m.selected));
        f.render_stateful_widget(
            List::new(items)
                .highlight_style(
                    self.style("primary")
                        .add_modifier(Modifier::REVERSED | Modifier::BOLD),
                )
                .highlight_symbol(if self.unicode() { "› " } else { "> " }),
            parts[0],
            &mut state,
        );
        f.render_widget(
            Paragraph::new(clean(
                "↑↓ choisir · Entrée confirmer\nÉchap annuler et restaurer",
                self.unicode(),
            ))
            .style(self.style("muted")),
            parts[1],
        );
    }
}

pub fn item_id(v: &Value) -> String {
    v.as_str().map(str::to_owned).unwrap_or_else(|| {
        let id = string(v, "id");
        if id.is_empty() { string(v, "name") } else { id }
    })
}
fn value_lines(v: &Value, key: &str, unicode: bool) -> Vec<String> {
    match v.get(key) {
        Some(Value::String(s)) => clean(s, unicode).lines().map(str::to_owned).collect(),
        Some(Value::Array(rows)) => rows
            .iter()
            .map(|r| {
                clean(
                    r.as_str()
                        .unwrap_or_else(|| r.get("text").and_then(Value::as_str).unwrap_or("")),
                    unicode,
                )
            })
            .collect(),
        _ => Vec::new(),
    }
}

pub fn token_color(token: &str, depth: &str) -> Color {
    if depth == "none" {
        return Color::Reset;
    }
    let token = token.trim_start_matches('#');
    let Ok(rgb) = u32::from_str_radix(token, 16) else {
        return Color::Reset;
    };
    if token.len() != 6 {
        return Color::Reset;
    }
    let (r, g, b) = ((rgb >> 16) as u8, (rgb >> 8) as u8, rgb as u8);
    if depth == "ansi" || depth == "16" {
        let colors = [
            (0, 0, 0),
            (128, 0, 0),
            (0, 128, 0),
            (128, 128, 0),
            (0, 0, 128),
            (128, 0, 128),
            (0, 128, 128),
            (192, 192, 192),
            (128, 128, 128),
            (255, 0, 0),
            (0, 255, 0),
            (255, 255, 0),
            (0, 0, 255),
            (255, 0, 255),
            (0, 255, 255),
            (255, 255, 255),
        ];
        let n = colors
            .iter()
            .enumerate()
            .min_by_key(|(_, c)| {
                (r as i32 - c.0).pow(2) + (g as i32 - c.1).pow(2) + (b as i32 - c.2).pow(2)
            })
            .map(|(i, _)| i)
            .unwrap_or(7);
        return Color::Indexed(n as u8);
    }
    if depth == "256" {
        let map = |n: u8| ((n as u16 * 5 + 127) / 255) as u8;
        return Color::Indexed(16 + 36 * map(r) + 6 * map(g) + map(b));
    }
    Color::Rgb(r, g, b)
}

/// Fit cells without splitting UTF-8; an ellipsis means the complete identity is in /status.
pub fn fit(text: &str, width: usize, unicode: bool) -> String {
    if Line::from(text).width() <= width {
        return text.into();
    }
    if width == 0 {
        return String::new();
    }
    let marker = if unicode { "…" } else { "~" };
    let mut out = String::new();
    for ch in text.chars() {
        if Line::from(format!("{out}{ch}{marker}")).width() > width {
            break;
        }
        out.push(ch);
    }
    out.push_str(marker);
    out
}
fn paired(left: &str, right: &str, width: usize, unicode: bool) -> String {
    let left = clean(left, unicode);
    let right = clean(right, unicode);
    let right_width = Line::from(right.as_str()).width().min(width / 2);
    let left_width = width.saturating_sub(right_width + 2);
    let left = fit(&left, left_width, unicode);
    let right = fit(&right, right_width, unicode);
    format!(
        "{}{}{}",
        left,
        " ".repeat(width.saturating_sub(
            Line::from(left.as_str()).width() + Line::from(right.as_str()).width()
        )),
        right
    )
}
fn inline(text: &str, ui: &Ui, base: Style) -> Vec<Span<'static>> {
    let mut out = Vec::new();
    let mut remaining = text;
    while !remaining.is_empty() {
        let bold = remaining.find("**");
        let code = remaining.find('`');
        let next = match (bold, code) {
            (Some(a), Some(b)) => Some(if a < b { (a, "**") } else { (b, "`") }),
            (Some(a), None) => Some((a, "**")),
            (None, Some(b)) => Some((b, "`")),
            _ => None,
        };
        let Some((index, delimiter)) = next else {
            out.push(Span::styled(remaining.to_owned(), base));
            break;
        };
        out.push(Span::styled(remaining[..index].to_owned(), base));
        let after = &remaining[index + delimiter.len()..];
        if let Some(end) = after.find(delimiter) {
            out.push(Span::styled(
                after[..end].to_owned(),
                if delimiter == "**" {
                    base.add_modifier(Modifier::BOLD)
                } else {
                    ui.style("secondary")
                },
            ));
            remaining = &after[end + delimiter.len()..];
        } else {
            out.push(Span::styled(remaining[index..].to_owned(), base));
            break;
        }
    }
    out
}
fn cells(line: &str) -> Vec<String> {
    line.trim()
        .trim_matches('|')
        .split('|')
        .map(|v| v.trim().to_owned())
        .collect()
}
fn table_rule(line: &str) -> bool {
    let parts = cells(line);
    parts.len() > 1
        && parts
            .iter()
            .all(|part| !part.is_empty() && part.chars().all(|c| c == '-' || c == ':' || c == ' '))
}
fn wrapped_cell(text: &str, width: usize) -> Vec<String> {
    let mut rows = vec![String::new()];
    for ch in text.chars() {
        let last = rows.last_mut().unwrap();
        if Line::from(format!("{last}{ch}")).width() > width {
            rows.push(ch.to_string());
        } else {
            last.push(ch);
        }
    }
    rows
}
pub fn markdown(text: &str, ui: &Ui, width: usize) -> Vec<Line<'static>> {
    let source = text.lines().collect::<Vec<_>>();
    let mut output = Vec::new();
    let mut code = false;
    let mut i = 0;
    while i < source.len() {
        let line = source[i];
        if !code && line.contains('|') && i + 1 < source.len() && table_rule(source[i + 1]) {
            let headings = cells(line);
            let count = headings.len();
            let mut rows = vec![headings];
            i += 2;
            while i < source.len() && source[i].contains('|') {
                rows.push(cells(source[i]));
                i += 1;
            }
            if count * 6 + count * 3 > width {
                for row in rows.iter().skip(1) {
                    for (n, cell) in row.iter().enumerate() {
                        output.push(Line::from(inline(
                            &format!(
                                "{}: {}",
                                rows[0].get(n).map(String::as_str).unwrap_or(""),
                                cell
                            ),
                            ui,
                            ui.style("text"),
                        )));
                    }
                    output.push(Line::default());
                }
            } else {
                let cell_width = width.saturating_sub((count - 1) * 3) / count;
                for (n, row) in rows.iter().enumerate() {
                    let wrapped = row
                        .iter()
                        .take(count)
                        .map(|c| wrapped_cell(c, cell_width))
                        .collect::<Vec<_>>();
                    let height = wrapped.iter().map(Vec::len).max().unwrap_or(1);
                    for y in 0..height {
                        let mut parts = Vec::new();
                        for x in 0..count {
                            let value = wrapped
                                .get(x)
                                .and_then(|v| v.get(y))
                                .cloned()
                                .unwrap_or_default();
                            parts.push(format!(
                                "{}{}",
                                value,
                                " ".repeat(
                                    cell_width.saturating_sub(Line::from(value.as_str()).width())
                                )
                            ));
                        }
                        output.push(Line::styled(
                            parts.join(if ui.unicode() { " │ " } else { " | " }),
                            if n == 0 {
                                ui.style("secondary").add_modifier(Modifier::BOLD)
                            } else {
                                ui.style("text")
                            },
                        ));
                    }
                    if n == 0 {
                        output.push(Line::styled(
                            (if ui.unicode() { "─" } else { "-" }).repeat(width),
                            ui.style("border"),
                        ));
                    }
                }
            }
            continue;
        }
        let (value, style, literal) = if line.trim_start().starts_with("```") {
            code = !code;
            (
                line.trim_start().trim_start_matches('`').to_owned(),
                ui.style("muted"),
                true,
            )
        } else if code {
            (
                format!("  {line}"),
                if line.starts_with('+') {
                    ui.style("success")
                } else if line.starts_with('-') {
                    ui.style("error")
                } else {
                    ui.style("secondary")
                },
                true,
            )
        } else if line.starts_with('#') {
            (
                line.trim_start_matches('#').trim_start().to_owned(),
                ui.style("primary").add_modifier(Modifier::BOLD),
                false,
            )
        } else if line.starts_with('+') {
            (line.to_owned(), ui.style("success"), false)
        } else if line.starts_with('-') && !line.starts_with("- ") {
            (line.to_owned(), ui.style("error"), false)
        } else if line.starts_with('>') {
            (line.to_owned(), ui.style("accent"), false)
        } else {
            (line.to_owned(), ui.style("text"), false)
        };
        output.push(if literal {
            Line::styled(value, style)
        } else {
            Line::from(inline(&value, ui, style))
        });
        i += 1;
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::{Terminal, backend::TestBackend};
    #[test]
    fn rejects_protocol_drift() {
        assert!(parse_state(r#"{"version":2,"type":"state","state":{}}"#).is_err());
        assert!(parse_state(r#"{"version":1,"type":"state","state":{}}"#).is_ok());
        assert!(parse_state("not json").is_err());
        assert!(parse_state(r#"{"version":1,"type":"state","state":[]}"#).is_err());
    }
    #[test]
    fn commands_cannot_break_framing() {
        let serialized = command("submit", "hello\n\"quoted\"");
        assert!(!serialized.contains('\n'));
        let decoded: Value = serde_json::from_str(&serialized).unwrap();
        assert_eq!(decoded["value"], "hello\n\"quoted\"");
    }
    #[test]
    fn editing_unicode_and_middle_delete() {
        let mut i = Input::default();
        for c in "aé猫".chars() {
            i.insert(c);
        }
        i.left();
        i.backspace();
        i.insert('b');
        assert_eq!(i.value(), "ab猫");
        i.delete();
        assert_eq!(i.value(), "ab");
    }
    #[test]
    fn terminal_controls_never_reach_cells() {
        assert_eq!(
            clean("a\x1b[31mred\x1b[0m\x1b]0;attack\x07b", true),
            "aredb"
        );
        assert!(clean("é → 猫", false).is_ascii());
    }
    #[test]
    fn render_all_widths_and_resize() {
        let mut ui = Ui::default();
        ui.update(json!({"title":"AURORA","target":"demo.local","latency":"42 ms","session":"test","body":"# Rapport\n```rust\nlet x = 1;\n```\n+ ajouté\n- supprimé\n","page":"conversation","unicode":true,"color_depth":"none","status":"ready"}));
        for width in [60, 80, 120, 200] {
            let mut terminal = Terminal::new(TestBackend::new(width, 32)).unwrap();
            terminal.draw(|f| ui.draw(f)).unwrap();
            let text = terminal
                .backend()
                .buffer()
                .content
                .iter()
                .map(|c| c.symbol())
                .collect::<String>();
            assert!(text.contains("AURORA"));
            assert!(text.contains("demo.local"));
            assert!(text.contains("test"));
            terminal.resize(Rect::new(0, 0, 60, 20)).unwrap();
            terminal.draw(|f| ui.draw(f)).unwrap();
        }
    }
    #[test]
    fn cancelled_preview_restores_original_theme() {
        let mut ui = Ui::default();
        ui.update(json!({"theme":{"id":"otter"},"themes":[{"id":"otter"},{"id":"aurora"}]}));
        ui.open_modal(ModalKind::Theme);
        assert!(ui.move_modal(1).unwrap().contains("theme_preview"));
        let out: Value = serde_json::from_str(&ui.close_modal(false).unwrap()).unwrap();
        assert_eq!(out["command"], "theme_cancel");
        assert_eq!(out["value"], "otter");
    }
}
