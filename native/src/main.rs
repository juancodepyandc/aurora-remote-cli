use aurora_native::{MAX_MESSAGE_BYTES, ModalKind, Ui, command, item_id, parse_state};
use crossterm::event::{
    self, DisableBracketedPaste, EnableBracketedPaste, Event, KeyCode, KeyEventKind, KeyModifiers,
};
use ratatui::{
    Terminal,
    backend::{CrosstermBackend, TestBackend},
};
use serde_json::{Value, json};
use std::{
    fs,
    io::{self, BufRead, BufReader, IsTerminal, Read, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::mpsc::{self, Receiver, SyncSender},
    thread,
    time::{Duration, Instant},
};

enum Message {
    State(Value),
    Notice(String),
    Input(Event),
    Closed,
    Exit,
}

struct TerminalGuard;
impl Drop for TerminalGuard {
    fn drop(&mut self) {
        let _ = crossterm::execute!(io::stdout(), DisableBracketedPaste);
        ratatui::restore();
    }
}

struct Engine {
    child: Child,
    input: ChildStdin,
}
impl Engine {
    fn send(&mut self, message: &str) -> io::Result<()> {
        self.input.write_all(message.as_bytes())?;
        self.input.write_all(b"\n")?;
        self.input.flush()
    }
    fn spawn(python: &str, tx: SyncSender<Message>) -> io::Result<Self> {
        let mut cmd = Command::new(python);
        cmd.args(["-m", "aurora_cli.render_server"])
            .env("JOBIA_RENDER_TTY", "1");
        if std::env::var_os("JOBIA_COLOR").is_none() {
            let term = std::env::var("TERM").unwrap_or_default();
            let ct = std::env::var("COLORTERM").unwrap_or_default();
            let color = if std::env::var_os("NO_COLOR").is_some() || term == "dumb" {
                "none"
            } else if ct.contains("truecolor") || ct.contains("24bit") {
                "truecolor"
            } else if term.contains("256color") {
                "color256"
            } else {
                "ansi"
            };
            cmd.env("JOBIA_COLOR", color);
        }
        let mut child = cmd
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()?;
        let input = child
            .stdin
            .take()
            .ok_or_else(|| io::Error::other("Engine stdin unavailable"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| io::Error::other("Engine stdout unavailable"))?;
        thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                let mut line = Vec::new();
                match reader
                    .by_ref()
                    .take((MAX_MESSAGE_BYTES + 1) as u64)
                    .read_until(b'\n', &mut line)
                {
                    Ok(0) => break,
                    Ok(_) => {
                        if line.len() > MAX_MESSAGE_BYTES {
                            let _ = tx.send(Message::Notice(
                                "État du moteur trop volumineux ; sortie interrompue".into(),
                            ));
                            break;
                        }
                        let decoded = std::str::from_utf8(&line)
                            .ok()
                            .and_then(|s| serde_json::from_str::<Value>(s).ok());
                        let message = match decoded {
                            Some(ref value)
                                if value.get("version").and_then(Value::as_u64) == Some(1)
                                    && value["type"] == "exit" =>
                            {
                                Message::Exit
                            }
                            Some(ref value)
                                if value.get("version").and_then(Value::as_u64) == Some(1)
                                    && value["type"] == "error" =>
                            {
                                Message::Notice(aurora_native::clean(
                                    value["message"].as_str().unwrap_or("Erreur du moteur"),
                                    true,
                                ))
                            }
                            _ => match std::str::from_utf8(&line)
                                .ok()
                                .and_then(|s| parse_state(s).ok())
                            {
                                Some(state) => Message::State(state),
                                None => Message::Notice(
                                    "Message incompatible reçu du moteur Python".into(),
                                ),
                            },
                        };
                        if tx.send(message).is_err() {
                            return;
                        }
                    }
                    Err(_) => break,
                }
            }
            let _ = tx.send(Message::Closed);
        });
        Ok(Self { child, input })
    }
    fn stop(&mut self) {
        let _ = self.send(&command("quit", ""));
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            if self.child.try_wait().ok().flatten().is_some() {
                return;
            }
            thread::sleep(Duration::from_millis(20));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
impl Drop for Engine {
    fn drop(&mut self) {
        self.stop();
    }
}

fn key(ui: &mut Ui, event: Event) -> (Option<String>, bool) {
    match event {
        Event::Resize(_, _) => (None, true),
        Event::Paste(text) => {
            for ch in text
                .chars()
                .filter(|c| !c.is_control() || *c == '\n' || *c == '\t')
                .take(100_000)
            {
                ui.input.insert(ch);
            }
            (None, true)
        }
        Event::Key(k) if k.kind != KeyEventKind::Release => {
            let ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
            let alt = k.modifiers.contains(KeyModifiers::ALT);
            if ctrl && k.code == KeyCode::Char('q') {
                return (Some(command("quit", "")), true);
            }
            if ctrl && k.code == KeyCode::Char('c') {
                return (Some(command("stop", "")), true);
            }
            if ui.modal.is_some() {
                return match k.code {
                    KeyCode::Up | KeyCode::Left => (ui.move_modal(-1), true),
                    KeyCode::Down | KeyCode::Right | KeyCode::Tab => (ui.move_modal(1), true),
                    KeyCode::Enter => (ui.close_modal(true), true),
                    KeyCode::Esc => (ui.close_modal(false), true),
                    _ => (None, false),
                };
            }
            if ctrl {
                return match k.code {
                    KeyCode::Char('n') => (Some(command("new", "")), true),
                    KeyCode::Char('u') => {
                        ui.input.clear();
                        (None, true)
                    }
                    KeyCode::Char('w') => {
                        ui.input.delete_word();
                        (None, true)
                    }
                    KeyCode::Char('a') => {
                        ui.input.cursor = 0;
                        (None, true)
                    }
                    KeyCode::Char('e') => {
                        ui.input.cursor = ui.input.text.len();
                        (None, true)
                    }
                    KeyCode::Home => {
                        ui.follow = false;
                        ui.scroll = 0;
                        (None, true)
                    }
                    KeyCode::End => {
                        ui.follow = true;
                        (None, true)
                    }
                    _ => (None, false),
                };
            }
            if alt {
                match k.code {
                    KeyCode::Enter => {
                        ui.input.insert('\n');
                        return (None, true);
                    }
                    KeyCode::Char(c) if ('1'..='9').contains(&c) => {
                        let i = (c as u8 - b'1') as usize;
                        if let Some(tab) = ui
                            .state
                            .get("tabs")
                            .and_then(Value::as_array)
                            .and_then(|t| t.get(i))
                        {
                            return (Some(command("page", &item_id(tab))), true);
                        }
                    }
                    _ => {}
                }
            }
            match k.code {
                KeyCode::Tab => {
                    ui.complete();
                    (None, true)
                }
                KeyCode::F(2) => {
                    ui.open_modal(ModalKind::Theme);
                    (None, true)
                }
                KeyCode::F(3) => {
                    ui.open_modal(ModalKind::Model);
                    (None, true)
                }
                KeyCode::F(4) => (Some(command("view_next", "")), true),
                KeyCode::F(5) => (Some(command("doctor", "")), true),
                KeyCode::PageUp => {
                    ui.follow = false;
                    ui.scroll = ui.scroll.saturating_sub(10);
                    (None, true)
                }
                KeyCode::PageDown => {
                    ui.follow = false;
                    ui.scroll = ui.scroll.saturating_add(10);
                    (None, true)
                }
                KeyCode::Up => {
                    ui.recall(true);
                    (None, true)
                }
                KeyCode::Down => {
                    ui.recall(false);
                    (None, true)
                }
                KeyCode::Left => {
                    ui.input.left();
                    (None, true)
                }
                KeyCode::Right => {
                    ui.input.right();
                    (None, true)
                }
                KeyCode::Home => {
                    ui.input.cursor = 0;
                    (None, true)
                }
                KeyCode::End => {
                    ui.input.cursor = ui.input.text.len();
                    (None, true)
                }
                KeyCode::Backspace => {
                    ui.input.backspace();
                    (None, true)
                }
                KeyCode::Delete => {
                    ui.input.delete();
                    (None, true)
                }
                KeyCode::Enter => (ui.submit(), true),
                KeyCode::Char(ch) => {
                    if ui.input.text.len() < 100_000 {
                        ui.input.insert(ch);
                    }
                    (None, true)
                }
                KeyCode::Esc => {
                    ui.follow = true;
                    ui.notice.clear();
                    (None, true)
                }
                _ => (None, false),
            }
        }
        _ => (None, false),
    }
}

fn process(ui: &mut Ui, msg: Message, engine: &mut Engine) -> bool {
    match msg {
        Message::State(state) => {
            let picker = state["picker"].as_str().unwrap_or("").to_owned();
            let old_picker = ui.state["picker"].as_str().unwrap_or("").to_owned();
            ui.update(state);
            if ui.modal.is_none() && picker != old_picker {
                if picker == "theme" {
                    ui.open_modal(ModalKind::Theme);
                } else if picker == "model" {
                    ui.open_modal(ModalKind::Model);
                }
            }
        }
        Message::Exit => return false,
        Message::Notice(notice) => ui.notice = notice,
        Message::Closed => ui.notice = "Moteur Python déconnecté · Ctrl+Q pour quitter".into(),
        Message::Input(event) => {
            let (message, _) = key(ui, event);
            if let Some(message) = message {
                if message.contains("\"command\":\"quit\"")
                    && engine.child.try_wait().ok().flatten().is_some()
                {
                    return false;
                }
                if engine.send(&message).is_err() {
                    ui.notice = "Envoi impossible : moteur déconnecté".into();
                }
            }
        }
    }
    true
}

fn run(python: &str) -> io::Result<()> {
    if !io::stdin().is_terminal() || !io::stdout().is_terminal() {
        return Err(io::Error::other(
            "Le rendu natif nécessite un terminal. Utilisez jobia ui --text pour un pipe.",
        ));
    }
    let (tx, rx): (SyncSender<Message>, Receiver<Message>) = mpsc::sync_channel(64);
    let mut engine = Engine::spawn(python, tx.clone())?;
    crossterm::terminal::enable_raw_mode()?;
    let _guard = TerminalGuard;
    crossterm::execute!(
        io::stdout(),
        crossterm::terminal::EnterAlternateScreen,
        EnableBracketedPaste
    )?;
    let low_color = matches!(
        std::env::var("JOBIA_COLOR").unwrap_or_default().as_str(),
        "ansi" | "color8" | "16"
    );
    let writer = aurora_native::ansi16::Ansi16::new(io::BufWriter::new(io::stdout()), low_color);
    let mut terminal = Terminal::new(CrosstermBackend::new(writer))?;
    thread::spawn(move || {
        while let Ok(event) = event::read() {
            if tx.send(Message::Input(event)).is_err() {
                break;
            }
        }
    });
    let mut ui = Ui::default();
    let mut active = true;
    while active {
        terminal.draw(|frame| ui.draw(frame))?;
        let msg = if let Some(interval) = ui.motion_interval() {
            match rx.recv_timeout(interval) {
                Ok(msg) => Some(msg),
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    ui.frame = ui.frame.wrapping_add(1);
                    None
                }
                Err(_) => break,
            }
        } else {
            match rx.recv() {
                Ok(msg) => Some(msg),
                Err(_) => break,
            }
        };
        if let Some(msg) = msg {
            active = process(&mut ui, msg, &mut engine);
        }
        // Coalesce queued engine states in one frame without losing keyboard events.
        while active {
            match rx.try_recv() {
                Ok(msg) => active = process(&mut ui, msg, &mut engine),
                Err(_) => break,
            }
        }
    }
    engine.stop();
    Ok(())
}

fn color_string(color: ratatui::style::Color) -> String {
    match color {
        ratatui::style::Color::Rgb(r, g, b) => format!("#{r:02x}{g:02x}{b:02x}"),
        ratatui::style::Color::Indexed(i) => format!("indexed:{i}"),
        ratatui::style::Color::Reset => "default".into(),
        other => format!("{other:?}").to_lowercase(),
    }
}
fn snapshot(args: &[String]) -> io::Result<()> {
    let file = args
        .first()
        .ok_or_else(|| io::Error::other("--snapshot FILE WIDTH HEIGHT [--cells FILE]"))?;
    let width = args
        .get(1)
        .and_then(|s| s.parse::<u16>().ok())
        .filter(|n| (20..=500).contains(n))
        .ok_or_else(|| io::Error::other("width must be 20..500"))?;
    let height = args
        .get(2)
        .and_then(|s| s.parse::<u16>().ok())
        .filter(|n| (8..=200).contains(n))
        .ok_or_else(|| io::Error::other("height must be 8..200"))?;
    let content = fs::read_to_string(file)?;
    let state = parse_state(&content)
        .or_else(|_| serde_json::from_str::<Value>(&content).map_err(|_| "Invalid state"))
        .map_err(io::Error::other)?;
    let mut ui = Ui::default();
    ui.update(state);
    // Snapshot fixtures can include a draft; the live input remains locally owned.
    ui.input.text = ui.state["prompt"].as_str().unwrap_or("").chars().collect();
    ui.input.cursor = ui.input.text.len();
    let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
    terminal.draw(|f| ui.draw(f)).unwrap();
    let buffer = terminal.backend().buffer();
    for row in 0..height {
        let text = (0..width)
            .map(|col| buffer[(col, row)].symbol())
            .collect::<String>();
        println!("{text}");
    }
    if let Some(index) = args.iter().position(|s| s == "--cells") {
        let path = args
            .get(index + 1)
            .ok_or_else(|| io::Error::other("Missing cells output"))?;
        let rows=(0..height).map(|y|(0..width).map(|x|{let cell=&buffer[(x,y)];json!({"symbol":cell.symbol(),"fg":color_string(cell.fg),"bg":color_string(cell.bg),"modifier":format!("{:?}",cell.modifier)})}).collect::<Vec<_>>()).collect::<Vec<_>>();
        fs::write(
            path,
            serde_json::to_vec(&json!({"width":width,"height":height,"rows":rows}))?,
        )?;
    }
    Ok(())
}
fn main() {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let result = if args.first().map(String::as_str) == Some("--snapshot") {
        snapshot(&args[1..])
    } else if args.first().map(String::as_str) == Some("--help") {
        println!(
            "aurora-native --backend PYTHON\naurora-native --snapshot FILE WIDTH HEIGHT [--cells FILE]\nNative presentation client, JSONL protocol v1. Launch through jobia ui."
        );
        Ok(())
    } else if args.first().map(String::as_str) == Some("--version") {
        println!("aurora-native {} protocol 1", env!("CARGO_PKG_VERSION"));
        Ok(())
    } else if let Some(i) = args.iter().position(|s| s == "--backend") {
        match args.get(i + 1) {
            Some(python) => run(python),
            None => Err(io::Error::other("Missing Python executable")),
        }
    } else {
        Err(io::Error::other("Use --backend PYTHON or --help"))
    };
    if let Err(err) = result {
        eprintln!("Aurora : {err}");
        std::process::exit(2);
    }
}

#[cfg(test)]
mod interaction_tests {
    use super::*;
    use crossterm::event::KeyEvent;

    #[test]
    fn stop_remains_available_inside_each_picker() {
        for kind in [ModalKind::Theme, ModalKind::Model] {
            let mut ui = Ui::default();
            ui.update(json!({"busy":true,"themes":[{"id":"otter"}],"models":["observed"]}));
            ui.open_modal(kind);
            assert!(ui.modal.is_some());
            let (sent, _) = key(
                &mut ui,
                Event::Key(KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL)),
            );
            let sent: Value =
                serde_json::from_str(&sent.expect("Ctrl+C must reach the engine")).unwrap();
            assert_eq!(sent["command"], "stop");
        }
    }
}
