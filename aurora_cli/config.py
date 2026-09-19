"""Aurora CLI configuration — stored at ~/.aurora/config.json"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any
from aurora_cli.core.paths import APP_CONFIG_DIR, APP_DATA_DIR
import shutil

CONFIG_DIR = APP_CONFIG_DIR
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = APP_DATA_DIR / "history"
SESSIONS_DIR = APP_DATA_DIR / "sessions"

# Migration si l'ancienne conf ~/.aurora/config.json existe toujours
_old_config_dir = Path.home() / ".aurora"
_old_config_file = _old_config_dir / "config.json"
if _old_config_file.exists() and not CONFIG_FILE.exists():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(str(_old_config_file), str(CONFIG_FILE))

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


def resolve_server_url(explicit: str = "", cfg: dict[str, Any] | None = None) -> str:
    if cfg is None:
        cfg = load()
    return (explicit or os.environ.get("AURORA_SERVER_URL") or cfg.get("server_url", "")).rstrip("/")


def is_configured() -> bool:
    cfg = load()
    return bool(resolve_server_url(cfg=cfg)) and bool(cfg.get("api_key"))
