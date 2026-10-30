use aurora_native::{ModalKind, Ui, markdown};
use ratatui::{Terminal, backend::TestBackend, style::Color};
use serde_json::{Value, json};
use std::{collections::BTreeSet, fs, path::PathBuf};

fn theme(id: &str) -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../aurora_cli/themes/data")
        .join(format!("{id}.json"));
    serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
}
fn fixture(id: &str) -> Value {
    json!({"title":"Aurora","connection":"en ligne","target":"demo.local","latency":"42 ms","session":"s-demo","mission":"m-demo","status":"idle","phase":"Prêt","welcome":true,"page":"conversation","body":"# Un horizon ouvert.\n\nDécrivez le résultat attendu.\n\n**/help** commandes et raccourcis.","activity":["Aucun outil exécuté"],"sidebar":["Mission observée","Aucune preuve reçue"],"theme":theme(id),"unicode":true,"color_depth":"truecolor","animate":false,"tabs":[{"id":"conversation","label":"Conversation"},{"id":"journal","label":"Journal"}]})
}
fn render(ui: &mut Ui, width: u16) -> (String, ratatui::buffer::Buffer) {
    let mut terminal = Terminal::new(TestBackend::new(width, 32)).unwrap();
    terminal.draw(|f| ui.draw(f)).unwrap();
    let buffer = terminal.backend().buffer().clone();
    let text = (0..32)
        .map(|y| {
            (0..width)
                .map(|x| buffer[(x, y)].symbol())
                .collect::<String>()
        })
        .collect::<Vec<_>>()
        .join("\n");
    (text, buffer)
}
#[test]
fn eight_themes_four_widths_keep_remote_context_visible() {
    let mut distinct = BTreeSet::new();
    for id in [
        "otter",
        "jobia",
        "abyss",
        "glacier",
        "synthwave",
        "brutal",
        "phosphor",
        "plain",
    ] {
        for width in [60, 80, 120, 200] {
            let mut ui = Ui::default();
            ui.update(fixture(id));
            let (text, _) = render(&mut ui, width);
            for required in [
                "Aurora",
                "en ligne",
                "demo.local",
                "42 ms",
                "s-demo",
                "m-demo",
            ] {
                assert!(text.contains(required), "{id}/{width}: missing {required}");
            }
            if width == 80 {
                distinct.insert(text);
            }
        }
    }
    assert_eq!(
        distinct.len(),
        8,
        "Themes must differ structurally, not only by color"
    );
}
#[test]
fn ascii_no_color_and_low_color_render_without_escape_leaks() {
    for depth in ["1", "4", "8", "24"] {
        let mut ui = Ui::default();
        let mut state = fixture("otter");
        state["color_depth"] = json!(depth);
        state["unicode"] = json!(false);
        ui.update(state);
        let (text, buffer) = render(&mut ui, 60);
        assert!(text.is_ascii());
        assert!(!text.contains('\x1b'));
        for cell in buffer.content {
            match depth {
                "1" => assert_eq!(cell.fg, Color::Reset),
                "4" => assert!(matches!(cell.fg, Color::Reset | Color::Indexed(0..=15))),
                "8" => assert!(matches!(cell.fg, Color::Reset | Color::Indexed(_))),
                _ => {}
            }
        }
    }
}

#[test]
fn retained_mission_and_pending_request_keep_welcome_actions_at_small_height() {
    for id in [
        "otter",
        "jobia",
        "abyss",
        "glacier",
        "synthwave",
        "brutal",
        "phosphor",
        "plain",
    ] {
        let mut state = fixture(id);
        state["body"] = json!(
            "# Un horizon ouvert.\n\nDécrivez le résultat attendu.\n\n**/modules** Explorer  \n**/missions** Reprendre  \n**/theme** Changer d’univers  \n**/help** Commandes"
        );
        let notices = json!([
            "Mission conservée · /missions",
            "Reprise incomplète · /retry"
        ]);
        state["recovery_notices"] = notices.clone();
        state["activity"] = notices;
        let mut ui = Ui::default();
        ui.update(state);
        let mut terminal = Terminal::new(TestBackend::new(60, 24)).unwrap();
        terminal.draw(|f| ui.draw(f)).unwrap();
        let text = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|c| c.symbol())
            .collect::<String>();
        for required in [
            "/modules",
            "/missions",
            "/theme",
            "/help",
            "Mission conservée",
            "Reprise incomplète",
            "/retry",
        ] {
            assert!(text.contains(required), "{id}: {required} absent");
        }
    }
}
#[test]
fn permissions_disconnect_and_errors_have_textual_states() {
    for status in ["permission_required", "reconnecting", "failed", "completed"] {
        let mut ui = Ui::default();
        let mut state = fixture("jobia");
        state["welcome"] = json!(false);
        state["status"] = json!(status);
        state["connection"] = json!("hors ligne");
        state["body"] = json!(
            "# Autorisation requise\n\nEffet : écrire un fichier.\n\nConfirmer ou refuser explicitement."
        );
        ui.update(state);
        let (text, _) = render(&mut ui, 60);
        assert!(text.contains(status));
        assert!(text.contains("hors ligne"));
        assert!(text.contains("Autorisation requise"));
    }
}
#[test]
fn markdown_preserves_table_values_and_diff_markers_at_small_widths() {
    let mut ui = Ui::default();
    ui.update(fixture("glacier"));
    let text = "# Rapport\n**Important** et `code`\n| Champ | Valeur |\n| --- | --- |\n| preuve | abcdefghijklmnopqrstuvwxyz |\n```diff\n+ ajouté\n- supprimé\n```";
    for width in [18, 54, 114] {
        let lines = markdown(text, &ui, width);
        let shown = lines
            .iter()
            .map(|l| {
                l.spans
                    .iter()
                    .map(|s| s.content.as_ref())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n");
        assert!(shown.contains("Important") && !shown.contains("**Important**"));
        assert!(shown.contains("+ ajouté"));
        assert!(shown.contains("- supprimé"));
        for c in 'a'..='z' {
            assert!(shown.contains(c));
        }
    }
}
#[test]
fn model_cancel_busy_draft_and_theme_preview_are_reversible() {
    let mut ui = Ui::default();
    let mut state = fixture("otter");
    state["busy"] = json!(true);
    state["models"] = json!(["demo-model"]);
    ui.update(state);
    for c in "brouillon".chars() {
        ui.input.insert(c);
    }
    assert!(ui.submit().is_none());
    assert_eq!(ui.input.value(), "brouillon");
    ui.open_modal(ModalKind::Model);
    assert!(ui.close_modal(false).unwrap().contains("picker_cancel"));
}

#[test]
fn multiline_input_tracks_the_visible_cursor_and_preserves_unicode_payload() {
    let mut input = aurora_native::Input::default();
    for c in "abcdef猫\nété".chars() {
        input.insert(c);
    }
    let (rows, row, col) = input.layout(5, true);
    assert_eq!((row, col), (2, 3));
    assert_eq!(rows[0].to_string(), "abcde");
    assert_eq!(rows[1].to_string(), "f猫");
    let (ascii, _, _) = input.layout(5, false);
    assert!(ascii.iter().all(|row| row.to_string().is_ascii()));
    assert_eq!(input.value(), "abcdef猫\nété");
}

#[test]
fn long_wrapped_output_follows_the_last_line_after_resize() {
    let mut ui = Ui::default();
    let mut state = fixture("plain");
    state["welcome"] = json!(false);
    state["body"] = json!(format!(
        "{}\nLAST VISIBLE LINE",
        "long words and wrapping ".repeat(500)
    ));
    ui.update(state);
    for width in [60, 200, 80] {
        let (text, _) = render(&mut ui, width);
        assert!(text.contains("LAST VISIBLE LINE"), "width {width}");
    }
}

#[test]
fn active_mission_accepts_only_declared_presentation_commands() {
    let mut ui = Ui::default();
    let mut state = fixture("otter");
    state["busy"] = json!(true);
    state["interactive_commands"] = json!(["/theme", "/help", "/view"]);
    ui.update(state);
    ui.input.text = "/theme glacier".chars().collect();
    assert!(ui.submit().unwrap().contains("/theme glacier"));
    ui.input.text = "Une autre mission".chars().collect();
    assert!(ui.submit().is_none());
    assert_eq!(ui.input.value(), "Une autre mission");
}

#[test]
fn model_picker_can_restore_server_automatic_selection() {
    let mut ui = Ui::default();
    let mut state = fixture("otter");
    state["models"] = json!(["observed"]);
    state["model"] = json!("observed");
    ui.update(state);
    ui.open_modal(ModalKind::Model);
    ui.move_modal(-1);
    let sent: Value = serde_json::from_str(&ui.close_modal(true).unwrap()).unwrap();
    assert_eq!(sent["command"], "model");
    assert_eq!(sent["value"], "");
}

#[test]
fn offline_welcome_explains_the_failure_in_unicode_and_ascii() {
    for unicode in [true, false] {
        let mut ui = Ui::default();
        let mut state = fixture("otter");
        state["unicode"] = json!(unicode);
        state["connection"] = json!("hors ligne");
        state["health"] = json!("Accès refusé : appareil à reconnecter");
        ui.update(state);
        let (text, _) = render(&mut ui, 60);
        assert!(text.contains("refus"));
        assert!(text.contains("F5"));
        if !unicode {
            assert!(text.is_ascii());
        }
    }
}

#[test]
fn recalled_history_and_backend_recovery_preserve_drafts() {
    let mut ui = Ui::default();
    let state = fixture("otter");
    ui.update(state.clone());
    ui.input.text = "Ancienne demande".chars().collect();
    ui.submit();
    ui.input.text = "Brouillon en cours".chars().collect();
    ui.recall(true);
    ui.recall(false);
    assert_eq!(ui.input.value(), "Brouillon en cours");
    let mut recovered = state;
    recovered["draft_restore"] = json!({"revision":1,"text":"Vérifie le reçu 猫"});
    ui.update(recovered.clone());
    assert_eq!(
        ui.input.value(),
        "Brouillon en cours",
        "Never overwrite ongoing typing"
    );
    ui.input.clear();
    recovered["draft_restore"]["revision"] = json!(2);
    ui.update(recovered.clone());
    assert_eq!(ui.input.value(), "Vérifie le reçu 猫");
    ui.input.insert('!');
    ui.update(recovered);
    assert_eq!(ui.input.value(), "Vérifie le reçu 猫!");
}

#[test]
fn every_welcome_keeps_actions_at_compact_and_full_heights() {
    for id in [
        "otter",
        "jobia",
        "abyss",
        "plain",
        "phosphor",
        "brutal",
        "glacier",
        "synthwave",
    ] {
        for (width, height) in [(60, 24), (60, 32), (80, 32), (120, 32), (200, 32)] {
            for unicode in [true, false] {
                let mut state = fixture(id);
                state["unicode"] = json!(unicode);
                if !unicode {
                    state["color_depth"] = json!("none");
                }
                state["body"] = json!(
                    "# Un horizon ouvert.\n\nDécrivez le résultat attendu.\n\n**/modules** Explorer  \n**/missions** Reprendre  \n**/theme** Changer d’univers  \n**/help** Commandes"
                );
                let mut ui = Ui::default();
                ui.update(state);
                let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
                terminal.draw(|f| ui.draw(f)).unwrap();
                let buffer = terminal.backend().buffer();
                let text = (0..height)
                    .map(|y| {
                        (0..width)
                            .map(|x| buffer[(x, y)].symbol())
                            .collect::<String>()
                    })
                    .collect::<Vec<_>>()
                    .join("\n");
                for action in ["/modules", "/missions", "/theme", "/help"] {
                    assert!(
                        text.contains(action),
                        "{id}/{width}/{height}: {action}\n{text}"
                    );
                }
                if !unicode {
                    assert!(text.is_ascii(), "{id}: {text}");
                }
            }
        }
    }
}
