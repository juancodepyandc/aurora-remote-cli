"""Branding, environment variables and secret handling."""
from __future__ import annotations

import json

import pytest

from aurora_cli import brand, config


def test_product_identity():
    assert brand.APP_NAME == "JOBIA"
    assert brand.APP_FULL_NAME == "Juan Of Bike IA"
    assert "jobia" in brand.APP_COMMANDS


def test_env_namespacing():
    assert brand.env("theme") == "JOBIA_THEME"
    # Pre-rename variables must keep resolving, or old setups silently break.
    assert brand.env_legacy("server_url") == "AURORA_SERVER_URL"
    assert brand.env_legacy("api_key") == "AURORA_API_KEY"


def test_legacy_env_is_actually_read(monkeypatch):
    monkeypatch.setenv("AURORA_API_KEY", "legacy-secret")
    monkeypatch.delenv("JOBIA_API_KEY", raising=False)
    assert config.get("api_key") == "legacy-secret"


def test_new_env_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("AURORA_API_KEY", "old")
    monkeypatch.setenv("JOBIA_API_KEY", "new")
    assert config.get("api_key") == "new"


def test_secret_keys_are_flagged():
    assert config.is_secret("api_key")
    assert not config.is_secret("server_url")


def test_save_is_atomic_and_private(isolated_config):
    config.set_key("mode", "local")
    path = isolated_config / "config.json"
    assert json.loads(path.read_text())["mode"] == "local"
    # A config holding a key must not be world readable.
    assert path.stat().st_mode & 0o077 == 0


def test_config_roundtrip_preserves_unknown_keys(isolated_config):
    path = isolated_config / "config.json"
    path.write_text(json.dumps({"custom_key": "keep me", "mode": "remote"}))
    assert config.get("custom_key") == "keep me"
    config.set_key("theme", "otter")
    stored = json.loads(path.read_text())
    assert stored["custom_key"] == "keep me"
    assert stored["theme"] == "otter"


@pytest.mark.parametrize("bad", [{"mode": "sideways"}, {"theme": 12}])
def test_bad_config_never_breaks_startup(isolated_config, bad):
    """A hand-edited config must not raise; the CLI has to start anyway."""
    (isolated_config / "config.json").write_text(json.dumps(bad))
    assert isinstance(config.get("mode", "auto"), str)
