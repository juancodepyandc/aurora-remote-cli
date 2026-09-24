"""Aurora CLI configuration — stored at $XDG_CONFIG_HOME/aurora/config.json"""
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

PERMISSION_LEVELS = ("SAFE", "STANDARD", "AUTONOMOUS", "FULL")

_cached: tuple[str, int, int, dict[str, Any]] | None = None


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def load() -> dict[str, Any]:
    ensure_dirs()
    if not CONFIG_FILE.exists():
        return dict(DEFAULT_CONFIG)
    try:
        stamp = os.stat(CONFIG_FILE)
        key = (str(CONFIG_FILE), stamp.st_mtime_ns, stamp.st_size)
        global _cached
        if _cached is not None and _cached[:3] == key:
            return dict(_cached[3])
    except OSError:
        return dict(DEFAULT_CONFIG)
    try:
        raw = json.loads(CONFIG_FILE.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)
    merged = {**DEFAULT_CONFIG, **raw}
    _cached = (*key, merged)
    return dict(merged)


def save(cfg: dict[str, Any]) -> None:
    ensure_dirs()
    tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")
    os.replace(tmp, CONFIG_FILE)
    global _cached
    _cached = None


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
