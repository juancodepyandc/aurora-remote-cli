"""CLI surface: commands must start, respect overrides, and hide secrets."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from aurora_cli import config
from aurora_cli.cli import main

LOCAL_ONLY = [
    "theme", "env", "doctor", "models", "providers", "scan", "config",
]


@pytest.fixture
def run(isolated_config, monkeypatch):
    """Invoke the CLI with output captured, and with a fast local scan."""
    monkeypatch.setattr(
        "aurora_cli.core.discovery.scan",
        lambda **_kwargs: __import__(
            "aurora_cli.core.discovery", fromlist=["ScanResult"]
        ).ScanResult(providers=[], loose_models=[]),
    )
    runner = CliRunner()

    def invoke(*args, **kwargs):
        return runner.invoke(main, list(args), **kwargs)

    return invoke


@pytest.mark.parametrize("command", LOCAL_ONLY)
def test_local_commands_run_without_a_bridge(run, command):
    """These must all work with nothing configured and no network."""
    result = run(command)
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("theme", ["jobia", "otter", "abyss", "plain"])
def test_preview_renders_every_theme(run, theme):
    result = run("preview", theme)
    assert result.exit_code == 0, result.output
    assert result.output.strip()


def test_help_lists_every_command(run):
    output = run("--help").output
    for name in ("chat", "models", "providers", "theme", "connect", "status"):
        assert name in output


def test_global_theme_override(run, isolated_config):
    result = run("--theme", "plain", "theme")
    assert result.exit_code == 0
    assert "plain" in result.output


def test_unknown_theme_is_rejected(run):
    result = run("theme", "not-a-theme")
    assert result.exit_code != 0
    assert "inconnu" in result.output.lower()


def test_config_show_masks_the_key(run, isolated_config):
    config.set_key("api_key", "super-secret-value")
    output = run("config").output
    assert "super-secret-value" not in output
    assert "********" in output or "****" in output


def test_config_set_and_read_back(run, isolated_config):
    assert run("config", "mode", "local").exit_code == 0
    stored = json.loads((isolated_config / "config.json").read_text())
    assert stored["mode"] == "local"


def test_no_escape_sequences_when_colour_is_off(run, monkeypatch):
    monkeypatch.setenv("JOBIA_COLOR", "none")
    result = run("doctor")
    assert "\x1b" not in result.output


def test_chat_exits_nonzero_without_a_local_model(run, isolated_config):
    """A script must be able to branch on 'no local model available'."""
    result = run("chat")
    assert result.exit_code != 0
