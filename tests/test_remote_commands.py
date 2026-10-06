"""Offline contracts for remote readiness and the distinct mission command."""
from types import SimpleNamespace
from unittest.mock import Mock
import json
import pytest

from click.testing import CliRunner
from aurora_cli import cli, config
from aurora_cli.core import capabilities


def test_remote_mission_and_local_run_are_both_reachable():
    runner = CliRunner()
    mission = runner.invoke(cli.main, ["mission", "--help"])
    local = runner.invoke(cli.main, ["run", "--help"])
    assert mission.exit_code == local.exit_code == 0
    assert "--server-workspace" in mission.output
    assert "--capability" in local.output


def test_failed_remote_mission_exits_nonzero_and_closes_client(monkeypatch):
    from aurora_cli import bridge, mission
    client = Mock()
    monkeypatch.setattr(bridge, "Bridge", lambda: client)
    monkeypatch.setattr(mission, "run_mission", lambda *a, **k: False)
    result = CliRunner().invoke(cli.main, ["mission", "read this project"])
    assert result.exit_code != 0
    client.close.assert_called_once()


@pytest.mark.parametrize("permissions,expected", [(None, "SAFE"), ("autonomous", "AUTONOMOUS")])
def test_mission_permissions_are_explicit_without_changing_saved_default(monkeypatch, permissions, expected):
    from aurora_cli import bridge, mission
    client = Mock()
    run = Mock(return_value=True)
    monkeypatch.setattr(bridge, "Bridge", lambda: client)
    monkeypatch.setattr(mission, "run_mission", run)
    monkeypatch.setattr(config, "get", lambda key, default=None: "SAFE" if key == "default_permissions" else default)
    save = Mock()
    monkeypatch.setattr(config, "save", save)
    args = ["mission", "deliver a file"]
    if permissions:
        args += ["--permissions", permissions]
    result = CliRunner().invoke(cli.main, args)
    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["permissions"] == expected
    save.assert_not_called()
    client.close.assert_called_once()


@pytest.mark.parametrize("status,expected_exit", [(200, 0), (401, 1), (503, 1)])
def test_connect_command_reaches_registration_and_reports_failures(monkeypatch, status, expected_exit):
    import httpx
    from aurora_cli import connect
    response = httpx.Response(status, json={"ok": status == 200},
                              request=httpx.Request("POST", "https://bridge.test/api/cli/register"))
    register = Mock(return_value=response)
    monkeypatch.setattr(connect, "_register", register)
    save = Mock()
    monkeypatch.setattr(config, "set_key", save)
    result = CliRunner().invoke(cli.main, ["connect", "--server", "https://bridge.test",
                                           "--api-key", "fixture-key"])
    assert result.exit_code == expected_exit, result.output
    register.assert_called_once_with("https://bridge.test", "fixture-key")
    assert "Traceback" not in result.output
    if expected_exit:
        save.assert_not_called()
    else:
        assert save.call_args_list[0].args == ("server_url", "https://bridge.test")


@pytest.mark.parametrize("command", [["agents", "list"], ["agents", "disable", "reviewer"], ["mcp", "list"], ["skills", "list"]])
def test_remote_management_uses_real_bridge_methods_and_closes(monkeypatch, command):
    from aurora_cli.bridge import Bridge
    client = Mock(spec=Bridge)
    for method in ("agents_official", "agents_dynamic", "agent_disable", "mcp_list", "mcp_tools", "skills_list"):
        getattr(client, method).return_value = {"ok": True, "agents": [], "servers": [], "tools": [], "skills": []}
    monkeypatch.setattr(cli, "_client", lambda: client)
    result = CliRunner().invoke(cli.main, command)
    assert result.exit_code == 0, result.output
    client.close.assert_called_once()


def test_disable_is_not_announced_as_success_when_bridge_refuses(monkeypatch):
    from aurora_cli.bridge import Bridge
    client = Mock(spec=Bridge)
    client.agent_disable.return_value = {"ok": False, "error": "Not authorised"}
    monkeypatch.setattr(cli, "_client", lambda: client)
    result = CliRunner().invoke(cli.main, ["agents", "disable", "reviewer"])
    assert result.exit_code != 0
    assert "Agent désactivé" not in result.output


def test_remote_doctor_does_not_confuse_open_bridge_with_ready_missions(monkeypatch):
    client = Mock()
    client.doctor.return_value = {"ok": True, "ready": False,
                                  "checks": [{"name": "Daemon", "ok": False}]}
    monkeypatch.setattr(cli, "_client", lambda: client)
    result = CliRunner().invoke(cli.main, ["doctor", "--remote", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False
    client.close.assert_called_once()


def test_unreachable_doctor_is_machine_readable(monkeypatch):
    client = Mock()
    client.doctor.return_value = {"ok": False, "error": "Connection failed"}
    monkeypatch.setattr(cli, "_client", lambda: client)
    result = CliRunner().invoke(cli.main, ["doctor", "--remote", "--json"])
    assert result.exit_code == 1
    assert any(c.get("detail") == "Connection failed" for c in json.loads(result.output)["checks"])


def test_color_options_match_public_cli_values(monkeypatch):
    monkeypatch.setenv("JOBIA_COLOR", "never")
    assert capabilities.detect().color_depth == "none"
    monkeypatch.setenv("JOBIA_COLOR", "always")
    assert capabilities.detect().color_depth == "truecolor"


def test_no_color_presence_is_respected(monkeypatch):
    monkeypatch.delenv("JOBIA_COLOR", raising=False)
    monkeypatch.setenv("NO_COLOR", "")
    assert capabilities.detect().color_depth == "none"


def test_color_option_controls_the_actual_renderer():
    runner = CliRunner()
    never = runner.invoke(cli.main, ["--color", "never", "theme"])
    always = runner.invoke(cli.main, ["--color", "always", "theme"])
    assert never.exit_code == always.exit_code == 0
    assert "\x1b[" not in never.output
    assert "\x1b[" in always.output


def test_remote_stream_delivers_a_verified_file_with_the_bound_renderer(tmp_path, monkeypatch):
    import base64
    import hashlib
    import io
    from rich.console import Console
    from aurora_cli import display, mission
    payload = b"verified remote artifact"
    client = Mock()
    client.server_url = "https://example.test"
    client.mission_start.return_value = {"mission_id": "mis_test"}
    client.mission_stream.return_value = iter([
        {"type": "step_start", "step": "Réflexion"},
        {"type": "token", "content": "Checking output"},
        {"type": "file_transfer", "filename": "delivery/result.txt", "data": base64.b64encode(payload).decode(),
         "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()},
        {"type": "mission_complete", "result": "File checked"},
    ])
    renderer = display.Display(Console(file=io.StringIO(), width=60), caps=capabilities.detect(force_animation="none"))
    monkeypatch.setattr(display, "view", renderer)
    assert mission.run_mission(client, "deliver result", workspace=str(tmp_path))
    assert (tmp_path / "delivery/result.txt").read_bytes() == payload
