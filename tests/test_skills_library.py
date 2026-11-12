"""Local skills library: format, discovery, imports, search and activation."""
import json
import shutil
import zipfile
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from aurora_cli import skills
from aurora_cli.cli import main


def skill_dir(root: Path, name: str, front: str, body: str = "Corps.") -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\n{front}\n---\n{body}\n", encoding="utf-8")
    return folder


def test_frontmatter_supports_folded_blocks_lists_and_quotes():
    meta, body = skills.parse_frontmatter(
        '---\nname: "pdf-outils"\ndescription: >\n  Lire et remplir\n  des PDF.\n'
        'triggers:\n  - pdf\n  - "formulaire é"\ntags: [a, b]\nlicense: \'MIT\'\nmetadata:\n  version: 2\n---\n# Titre\n')
    assert meta["name"] == "pdf-outils"
    assert meta["description"] == "Lire et remplir des PDF."
    assert meta["triggers"] == ["pdf", "formulaire é"] and meta["tags"] == ["a", "b"]
    assert meta["license"] == "MIT" and meta["metadata"] == {"version": "2"}
    assert body == "# Titre"


def test_discovery_order_project_first_and_claude_folders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    skill_dir(tmp_path / ".aurora" / "skills", "dup", "name: dup\ndescription: projet")
    skill_dir(Path.home() / ".aurora" / "skills", "dup", "name: dup\ndescription: utilisateur")
    skill_dir(Path.home() / ".claude" / "skills", "claude-only", "name: claude-only\ndescription: depuis Claude")
    found = {s.name: s for s in skills.discover(tmp_path)}
    assert found["dup"].description == "projet" and found["dup"].level == "projet"
    assert found["claude-only"].level == "claude"
    assert skills.set_enabled("claude-only", False)
    assert "claude-only" not in {s.name for s in skills.discover(tmp_path)}
    assert skills.set_enabled("claude-only", True)


def test_import_folder_zip_and_refuse_zip_slip(tmp_path):
    source = tmp_path / "catalogue"
    skill_dir(source / "skills", "alpha", "name: alpha\ndescription: Premier")
    skill_dir(source / "skills", "beta", "name: beta\ndescription: Second")
    (source / "skills" / "alpha" / "scripts").mkdir()
    (source / "skills" / "alpha" / "scripts" / "run.py").write_text("print(1)")
    report = skills.import_skills(str(source), names=["alpha"])
    assert report.imported == ["alpha"] and set(report.available) == {"alpha", "beta"}
    imported = skills.user_library() / "alpha"
    assert (imported / "scripts" / "run.py").is_file()
    assert json.loads((imported / ".jobia-skill.json").read_text())["source"] == str(source.resolve())
    assert skills.import_skills(str(source), names=["alpha"]).skipped == ["alpha"]

    archive = tmp_path / "gamma.skill"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("gamma/SKILL.md", "---\nname: gamma\ndescription: Archive\n---\nOK\n")
    assert skills.import_skills(str(archive)).imported == ["gamma"]

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../escape/SKILL.md", "---\nname: escape\ndescription: x\n---\n")
    with pytest.raises(ValueError, match="hors du dossier"):
        skills.import_skills(str(evil))


def test_import_from_github_reference_and_raw_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "upstream"
    skill_dir(repo / "skills", "pptx", "name: pptx\ndescription: Slides")
    calls = []

    def fake_fetch(owner, name, branch, destination):
        calls.append((owner, name, branch))
        shutil.copytree(repo, destination)
        return "abc123"

    monkeypatch.setattr(skills, "fetch_repository", fake_fetch)
    assert skills.import_skills("anthropics/skills", names=["pptx"]).imported == ["pptx"]
    assert skills.import_skills("https://github.com/anthropics/skills/tree/main/skills/pptx",
                                project=True).imported == ["pptx"]
    assert calls == [("anthropics", "skills", ""), ("anthropics", "skills", "main")]
    assert (tmp_path / ".aurora" / "skills" / "pptx" / "SKILL.md").is_file()

    def handler(request):
        return httpx.Response(200, text="---\nname: distant\ndescription: Depuis une URL\n---\nCorps\n")
    real_get = httpx.get
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Client(transport=httpx.MockTransport(handler)).get(url))
    try:
        assert skills.import_skills("https://example.org/distant/SKILL.md").imported == ["distant"]
    finally:
        monkeypatch.setattr(httpx, "get", real_get)


def test_search_ranks_name_triggers_and_body_and_suggests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    skills.create("xlsx", "Spreadsheet expertise", "Formulas and pivot tables.", triggers=("excel",))
    skills.create("pdf", "PDF forms", "Fill and merge PDF files.")
    ranked = [s.name for _, s in skills.search("tableur formules")]
    assert ranked[0] == "xlsx"
    assert skills.suggest("Mets ces chiffres dans un fichier excel").name == "xlsx"
    assert skills.suggest("Bonjour, comment vas-tu ?") is None


def test_context_block_is_bounded_and_points_to_the_full_file(tmp_path):
    skills.create("long", "Long skill", "x" * 50000)
    block = skills.context_block(["long", "inconnu"])
    assert block.startswith("[SKILL ACTIVÉ · long]") and len(block) < 13000
    assert "suite dans" in block


def test_remove_keeps_a_backup_and_cli_round_trip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    created = runner.invoke(main, ["--color", "never", "skills", "create", "notes", "--description",
                                   "Prise de notes", "--instructions", "# Notes", "--trigger", "notes"])
    assert created.exit_code == 0, created.output
    listed = runner.invoke(main, ["--color", "never", "skills", "list", "--local", "--json"])
    assert [row["name"] for row in json.loads(listed.output)["local"]] == ["notes"]
    found = runner.invoke(main, ["--color", "never", "skills", "search", "note", "--json"])
    assert json.loads(found.output)["local"][0]["name"] == "notes"
    read = runner.invoke(main, ["--color", "never", "skills", "read", "notes"])
    assert "# Notes" in read.output
    removed = runner.invoke(main, ["--color", "never", "skills", "remove", "notes"])
    assert removed.exit_code == 0 and "Sauvegarde" in removed.output
    assert skills.get("notes") is None


@pytest.mark.parametrize("source,expected", [
    ("anthropics/skills", ("anthropics", "skills", "", "")),
    ("gh:owner/repo", ("owner", "repo", "", "")),
    ("https://github.com/o/r.git", ("o", "r", "", "")),
    ("https://github.com/o/r/tree/dev/a/b", ("o", "r", "dev", "a/b")),
    ("https://example.org/x/y", None),
    ("./local/dir", None),
])
def test_github_references(source, expected):
    assert skills._github(source) == expected
