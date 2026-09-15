"""Aurora CLI configuration — stored at ~/.aurora/config.json"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any


CONFIG_DIR = Path.home() / ".aurora"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history"
SESSIONS_DIR = CONFIG_DIR / "sessions"

DEFAULT_CONFIG = {
    "server_url": "",
    "api_key": "",
    "default_permissions": "AUTONOMOUS",
    "default_workspace": "",
    "theme": "default",
    "language": "auto",
}


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def load() -> dict[str, Any]:
    ensure_dirs()
    if CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text("utf-8"))}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save(cfg: dict[str, Any]) -> None:
    ensure_dirs()
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def set_key(key: str, value: Any) -> None:
    cfg = load()
    cfg[key] = value
    save(cfg)


def is_configured() -> bool:
    cfg = load()
    return bool(cfg.get("server_url")) and bool(cfg.get("api_key"))
