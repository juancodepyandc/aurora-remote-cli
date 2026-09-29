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
    # Keep the developer's real terminal from changing results.
    monkeypatch.setenv("JOBIA_COLOR", "truecolor")
    monkeypatch.setenv("JOBIA_ANIM", "none")
    from aurora_cli.core import locations

    # locations reads the env vars at call time, so no cache to clear.
    assert locations.config_dir() == config_dir
    assert locations.data_dir() == tmp_path / "data"
    return config_dir


@pytest.fixture
def caps_truecolor():
    """A Capabilities object pinned to a full colour, animating terminal."""
    from aurora_cli.core import capabilities

    return capabilities.detect()
