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
