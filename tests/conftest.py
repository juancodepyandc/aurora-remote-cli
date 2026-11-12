"""Shared fixtures.

Every test runs against a temporary config directory so a developer's real
``config.json`` is never read or written, and so tests never depend on the
machine's actual configuration.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point config, history and data paths at a throwaway directory."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("JOBIA_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("JOBIA_DATA_DIR", str(tmp_path / "data"))
    # Skills and MCP imports read and write under the home directory
    # (~/.aurora/skills, ~/.claude.json, ~/.cursor…): never the real one.
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # Keep the developer's real terminal from changing results.
    monkeypatch.setenv("JOBIA_COLOR", "truecolor")
    monkeypatch.setenv("JOBIA_ANIM", "none")
    from aurora_cli.core import locations
    from aurora_cli import brand
    for key in ('api_key','server_url','mode','provider','model'):
        for name in (brand.env(key),brand.env_legacy(key)):
            monkeypatch.delenv(name,raising=False)
    # An empty temporary config must not migrate this machine's real legacy
    # credentials and then dispatch a supposedly isolated test to its server.
    monkeypatch.setattr(locations,'legacy_config_files',lambda: [])

    # Tunnel discovery reads a public repository and then probes the real PC.
    # Tests must never do either: a test that needs a publication patches
    # ``connect.discover_tunnel`` or passes its own client to ``tunnel``.
    from aurora_cli import tunnel
    import httpx

    def offline_publication(*args, client_factory=None, **kwargs):
        if client_factory is not None:
            return real_read_publication(*args, client_factory=client_factory, **kwargs)
        raise httpx.ConnectError('Discovery disabled in tests')

    real_read_publication = tunnel.read_publication
    monkeypatch.setattr(tunnel, 'read_publication', offline_publication)
    tunnel.forget()
    # locations reads the env vars at call time, so no cache to clear.
    assert locations.config_dir() == config_dir
    assert locations.data_dir() == tmp_path / "data"
    return config_dir


@pytest.fixture
def caps_truecolor():
    """A Capabilities object pinned to a full colour, animating terminal."""
    from aurora_cli.core import capabilities

    return capabilities.detect()
