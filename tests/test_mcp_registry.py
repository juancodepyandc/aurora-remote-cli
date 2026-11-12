"""Local MCP registry: client imports, search, installation and real transports."""
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from aurora_cli import mcp_registry as reg
from aurora_cli.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
CATALOG = {"command": sys.executable, "args": [str(FIXTURES / "mcp_catalog_server.py")]}


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.chdir(tmp_path)
    return home


def write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def test_every_known_client_format_is_understood(home, tmp_path):
    apps = reg._app_config_root()
    write(apps / "Claude" / "claude_desktop_config.json", {"mcpServers": {"desk": CATALOG}})
    write(home / ".claude.json", {"mcpServers": {"code-user": CATALOG},
                                  "projects": {str(tmp_path.resolve()): {"mcpServers": {"code-local": CATALOG}}}})
    write(home / ".cursor" / "mcp.json", {"mcpServers": {"cursor-remote": {"url": "https://mcp.example/sse"}}})
    write(apps / "Code" / "User" / "mcp.json",
          '// commentaire\n{"servers": {"vs": {"type": "http", "url": "https://mcp.example/mcp",},},}')
    write(home / ".codeium" / "windsurf" / "mcp_config.json", {"mcpServers": {"wind": {"serverUrl": "https://w.example/mcp"}}})
    write(home / ".gemini" / "settings.json", {"mcpServers": {"gem": {"httpUrl": "https://g.example/mcp",
                                                                       "headers": {"X-Key": "${GEM_KEY}"}}}})
    write(home / ".codex" / "config.toml", '[mcp_servers.codex-tool]\ncommand = "uvx"\nargs = ["tool"]\n'
                                            '[mcp_servers.codex-tool.env]\nTOKEN = "x"\n')
    rows = {row["name"]: row for row in reg.scan_clients(tmp_path)}
    assert set(rows) == {"desk", "code-user", "code-local", "cursor-remote", "vs", "wind", "gem", "codex-tool"}
    assert rows["cursor-remote"]["config"]["transport"] == "sse"
    assert rows["vs"]["config"] == {"url": "https://mcp.example/mcp", "transport": "http", "headers": {}}
    assert rows["wind"]["config"]["transport"] == "http"
    assert rows["gem"]["missing"] == ["GEM_KEY"]
    assert rows["codex-tool"]["config"] == {"command": "uvx", "args": ["tool"], "env": {"TOKEN": "x"}}


def test_import_disable_and_project_priority(home, tmp_path):
    imported, skipped = reg.import_servers({"tools": CATALOG, "other": CATALOG})
    assert imported == ["tools", "other"] and skipped == []
    write(tmp_path / ".mcp.json", {"mcpServers": {"tools": {"command": "project-tool"}}})
    visible = reg.servers(tmp_path)
    assert visible["tools"]["command"] == "project-tool" and visible["tools"]["scope"] == "projet"
    assert reg.set_enabled("tools", False)
    assert "tools" not in reg.servers(tmp_path)
    assert reg.servers(tmp_path, include_disabled=True)["tools"]["disabled"] is True
    assert reg.set_enabled("tools", True) and "tools" in reg.servers(tmp_path)
    again, conflict = reg.import_servers({"other": {"command": "different"}})
    assert again == [] and conflict == ["other"]
    assert reg.remove("other") and "other" not in reg.load_registry()


def test_registry_file_is_private_and_placeholders_expand_only_at_call_time(home, monkeypatch):
    reg.add("secret", {"command": "tool", "env": {"TOKEN": "${MY_TOKEN:-fallback}"}})
    if sys.platform != "win32":
        assert reg.registry_path().stat().st_mode & 0o777 == 0o600
    assert reg.load_registry()["secret"]["env"]["TOKEN"] == "${MY_TOKEN:-fallback}"
    assert reg.expand(reg.load_registry()["secret"])["env"]["TOKEN"] == "fallback"
    monkeypatch.setenv("MY_TOKEN", "real")
    assert reg.expand(reg.load_registry()["secret"])["env"]["TOKEN"] == "real"


def test_live_refresh_then_fuzzy_search_finds_tools_prompts_and_resources(home, tmp_path):
    reg.add("catalogue", CATALOG)
    cache = reg.refresh(timeout=20)
    assert [t["name"] for t in cache["catalogue"]["tools"]] == ["first", "second"]
    assert reg.search("zzz-inconnu") == []
    kinds = {(h["kind"], h["name"]) for h in reg.search("REVIEW")}  # case-insensitive
    assert ("prompt", "review") in kinds
    assert reg.search("document")[0]["name"] == "document"


def test_cli_local_call_and_unknown_command_message(home):
    reg.add("catalogue", CATALOG)
    reg.add("missing", {"command": "definitely-not-installed-xyz"})
    runner = CliRunner()
    result = runner.invoke(main, ["--color", "never", "mcp", "call", "catalogue", "first"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["content"][0]["type"] == "text"
    failed = runner.invoke(main, ["--color", "never", "mcp", "call", "missing", "x"])
    assert failed.exit_code != 0 and "Commande introuvable" in failed.output
    listed = runner.invoke(main, ["--color", "never", "mcp", "list", "--json", "--local"])
    names = {row["name"] for row in json.loads(listed.output)["local"]}
    assert names == {"catalogue", "missing"}


def test_cli_add_import_file_and_url_validation(home, tmp_path):
    runner = CliRunner()
    added = runner.invoke(main, ["--color", "never", "mcp", "add", "remote-tool", "--url", "https://mcp.example/mcp",
                                 "--header", "Authorization=Bearer ${TOKEN}"])
    assert added.exit_code == 0, added.output
    assert "TOKEN" in added.output
    write(tmp_path / "exported.json", {"mcpServers": {"exported": CATALOG}})
    imported = runner.invoke(main, ["--color", "never", "mcp", "import", str(tmp_path / "exported.json")])
    assert imported.exit_code == 0 and "exported" in imported.output
    bad = runner.invoke(main, ["--color", "never", "mcp", "add", "bad", "--url", "ftp://x"])
    assert bad.exit_code != 0


REGISTRY_ENTRY = {
    "name": "io.github.example/files", "description": "Files", "version": "1.2.3",
    "packages": [{"registryType": "pypi", "identifier": "mcp-files", "version": "1.2.3",
                  "transport": {"type": "stdio"},
                  "environmentVariables": [{"name": "ROOT_DIR", "isRequired": True}],
                  "packageArguments": [{"type": "named", "name": "--mode", "value": "ro"}]},
                 {"registryType": "npm", "identifier": "@example/files", "version": "1.2.3",
                  "transport": {"type": "stdio"}}],
    "remotes": [{"type": "streamable-http", "url": "https://files.example/mcp",
                 "headers": [{"name": "Authorization", "value": "Bearer {api_key}"}]}],
}


def test_registry_entries_become_runnable_configurations():
    npm = reg.install_config(REGISTRY_ENTRY)
    assert npm["command"] == "npx" and npm["args"] == ["-y", "@example/files@1.2.3"]
    pypi = reg.install_config({**REGISTRY_ENTRY, "packages": REGISTRY_ENTRY["packages"][:1]}, env={"ROOT_DIR": "/data"})
    assert pypi["command"] == "uvx" and pypi["args"] == ["mcp-files==1.2.3", "--mode", "ro"]
    assert pypi["env"] == {"ROOT_DIR": "/data"}
    remote = reg.install_config(REGISTRY_ENTRY, prefer="remote")
    assert remote["url"] == "https://files.example/mcp"
    assert remote["headers"] == {"Authorization": "Bearer ${API_KEY}"}
    assert reg.default_alias("io.github.example/files") == "files"


def test_registry_search_parses_official_api_and_skips_deleted(monkeypatch):
    import httpx

    def handler(request):
        assert request.url.params["search"] == "files" and request.url.params["version"] == "latest"
        return httpx.Response(200, json={"servers": [
            {"server": REGISTRY_ENTRY, "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}}},
            {"server": {**REGISTRY_ENTRY, "name": "gone/x"},
             "_meta": {"io.modelcontextprotocol.registry/official": {"status": "deleted"}}}]})
    rows = reg.registry_search("files", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert [r["name"] for r in rows] == ["io.github.example/files"]


@pytest.mark.parametrize("mode", ["json", "sse", "legacy"])
def test_http_transports_complete_a_real_session(mode, home):
    sys.path.insert(0, str(FIXTURES))
    from mcp_http_server import Fixture
    fixture = Fixture(mode=mode)
    try:
        path = "/sse" if mode == "legacy" else "/mcp"
        cfg = reg.normalize({"url": fixture.url + path})
        assert cfg["transport"] == ("sse" if mode == "legacy" else "http")
        tools = reg.all_items(cfg, "tools", timeout=10)
        assert [t["name"] for t in tools] == ["echo"]
        response, error = reg.call(cfg, "tools/call", {"name": "echo", "arguments": {"text": "salut"}}, timeout=10)
        assert error is None and response["result"]["content"][0]["text"] == "echo:salut"
        if mode != "legacy":
            sent = [message for _, message, _ in fixture.posts]
            assert [m.get("method") for m in sent][:2] == ["initialize", "notifications/initialized"]
            assert all(headers.get("Mcp-Session-Id") == "session-fixture"
                       for _, message, headers in fixture.posts[1:])
    finally:
        reg.close(reg.normalize({"url": fixture.url + ("/sse" if mode == "legacy" else "/mcp")}))
        fixture.close()


def test_streamable_url_falls_back_to_legacy_sse_on_405(home):
    sys.path.insert(0, str(FIXTURES))
    from mcp_http_server import Fixture
    fixture = Fixture(mode="legacy")
    try:
        cfg = {"url": fixture.url + "/sse", "transport": "http"}
        response, error = reg.call(cfg, "tools/list", {}, timeout=10)
        assert error is None and response["result"]["tools"][0]["name"] == "echo"
    finally:
        reg.close({"url": fixture.url + "/sse", "transport": "http"})
        fixture.close()
