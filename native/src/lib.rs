//! Presentation only. Python remains the authority for missions and permissions.
pub mod ansi16;
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

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ModalKind {
    Theme,
    Model,
}
pub struct Modal {
    pub kind: ModalKind,
    pub selected: usize,
    pub items: Vec<Value>,
    pub original_theme: String,
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
    pub fn motion_interval(&self) -> Option<Duration> {
        let enabled = self
            .state
            .get("animate")
            .and_then(Value::as_bool)
            .unwrap_or(false);
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
        });
        None
    }
    pub fn move_modal(&mut self, delta: isize) -> Option<String> {
        let m = self.modal.as_mut()?;
        m.selected = (m.selected as isize + delta).rem_euclid(m.items.len() as isize) as usize;
        if m.kind == ModalKind::Theme {
            Some(command("theme_preview", &item_id(&m.items[m.selected])))
        } else {
            None
        }
    }
    pub fn close_modal(&mut self, accept: bool) -> Option<String> {
        let m = self.modal.take()?;
        match m.kind {
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
            Constraint::Length(4),
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
                .map(Line::from)
                .collect::<Vec<_>>();
            f.render_widget(
                Paragraph::new(text)
                    .block(self.block("Mission"))
                    .wrap(Wrap { trim: false }),
                mission_area,
            );
        }
        let activity = value_lines(&self.state, "activity", self.unicode());
        let status = if !self.notice.is_empty() {
            clean(&self.notice, self.unicode())
        } else {
            format!("{} · {}", self.text("status"), self.text("phase"))
        };
        let mut lines = vec![Line::styled(
            clean(&status, self.unicode()),
            self.style(if self.text("status") == "failed" {
                "error"
            } else {
                "accent"
            }),
        )];
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
        let footer = if area.width < 70 {
            "F2 thème · F3 modèle · F4 vue · ^C arrêt · ^Q sortie"
        } else if area.width < 115 {
            "F2 thèmes · F3 modèles · F4 vues · F5 état · ^C arrêt · ^Q sortie"
        } else {
            "F2 thèmes · F3 modèles · F4 vues · F5 état · Pg↑↓ journal · Ctrl+N nouveau · Ctrl+C arrêt · Ctrl+Q sortie"
        };
        f.render_widget(
            Paragraph::new(clean(footer, self.unicode())).style(self.style("muted")),
            parts[5],
        );
        self.draw_modal(f, area);
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
        let connection = self.text("connection");
        let connection = if connection.is_empty() {
            "non observée".into()
        } else {
            connection
        };
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
        let primary = format!(
            "{} {}",
            clean(motif, self.unicode()),
            if title.is_empty() { "AURORA" } else { &title }
        );
        let first = paired(
            &primary,
            &format!("Connexion : {connection}"),
            area.width as usize,
            self.unicode(),
        );
        let target = paired(
            &format!("Cible {target}"),
            &format!("Diag. {latency}"),
            area.width as usize,
            self.unicode(),
        );
        let identity = paired(
            &format!("Session {session}"),
            &format!("Mission {mission}"),
            area.width as usize,
            self.unicode(),
        );
        let phase = paired(
            &self.text("phase"),
            &self.theme_id(),
            area.width as usize,
            self.unicode(),
        );
        let gradient = self.depth() == "truecolor"
            && self
                .state
                .pointer("/theme/banner/gradient")
                .and_then(Value::as_bool)
                .unwrap_or(false);
        let first_line = if gradient {
            let start = self.palette()["banner"].as_str().unwrap_or("");
            let end = self.palette()["banner_alt"].as_str().unwrap_or("");
            let length = first.chars().count().saturating_sub(1).max(1) as f64;
            Line::from(
                first
                    .chars()
                    .enumerate()
                    .map(|(i, ch)| {
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
                        Span::styled(
                            ch.to_string(),
                            Style::default().fg(color).add_modifier(Modifier::BOLD),
                        )
                    })
                    .collect::<Vec<_>>(),
            )
        } else {
            Line::styled(first, self.style("primary").add_modifier(Modifier::BOLD))
        };
        let lines = vec![
            first_line,
            Line::styled(target, self.style("secondary")),
            Line::styled(identity, self.style("text")),
            Line::styled(phase, self.style("muted")),
        ];
        f.render_widget(Paragraph::new(lines), area);
    }
    fn draw_welcome(&self, f: &mut Frame, area: Rect) {
        welcome::draw(self, f, area);
    }
    fn draw_tabs(&self, f: &mut Frame, area: Rect) {
        let page = self.text("page");
        let tabs = list(&self.state, "tabs");
        let mut spans = Vec::new();
        let compact = area.width < 85;
        for (i, t) in tabs.iter().enumerate() {
            let id = item_id(t);
            let mut label = string(t, "label");
            if label.is_empty() {
                label = id.clone();
            }
            if compact && id != page {
                continue;
            }
            let active = id == page;
            spans.push(Span::styled(
                clean(
                    &format!(
                        " {}{} {}{} ",
                        if active {
                            self.component("tab_open")
                        } else {
                            String::new()
                        },
                        i + 1,
                        label,
                        if active {
                            self.component("tab_close")
                        } else {
                            String::new()
                        }
                    ),
                    self.unicode(),
                ),
                if active {
                    self.style("primary").add_modifier(Modifier::BOLD)
                } else {
                    self.style("muted")
                },
            ));
        }
        if compact {
            spans.push(Span::styled(
                clean("  F4 : changer de vue", self.unicode()),
                self.style("muted"),
            ));
        }
        f.render_widget(Paragraph::new(Line::from(spans)), area);
    }
    fn draw_modal(&self, f: &mut Frame, area: Rect) {
        let Some(m) = &self.modal else { return };
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
