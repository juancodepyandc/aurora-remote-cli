"""Configuration: one JSON file, one cache, one atomic writer.

Two invariants this module is responsible for:

* ``config.json`` holds an API key, so it is written ``0600`` via a temporary
  file and an atomic replace. On Windows the chmod is a no-op, which is
  acceptable because the file lives under the user's roaming profile.
* Reads are cheap. The file is stat-cached on (path, mtime, size), so the
  REPL can call :func:`get` in a loop without re-parsing JSON each time.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from aurora_cli import brand
from aurora_cli.core import locations


def _config_file() -> Path:
    """Resolve the config path on every call.

    Reading it once at import would freeze the location, which breaks
    ``JOBIA_CONFIG_DIR`` and makes tests order dependent.
    """
    return locations.config_file()


PERMISSION_LEVELS = ("SAFE", "STANDARD", "AUTONOMOUS", "FULL")

DEFAULT_CONFIG: dict[str, Any] = {
    "server_url": "",
    "api_key": "",
    "default_permissions": "AUTONOMOUS",
    "default_workspace": "",
    "theme": "",
    "language": "auto",
    "mode": "auto",
    "provider": "",
    "local_endpoints": [],
    "default_model": "",
    "animation": "auto",
}

_cached: tuple[str, int, int, dict[str, Any]] | None = None


def ensure_dirs() -> None:
    locations.ensure_dirs()


def _migrate_legacy() -> None:
    """Copy a pre-rename config into place, once, without overwriting."""
    if _config_file().exists():
        return
    for legacy in locations.legacy_config_files():
        try:
            locations.config_dir().mkdir(parents=True, exist_ok=True)
            shutil.copy(str(legacy), str(_config_file()))
            _restrict(_config_file())
            return
        except OSError:
            continue


def _restrict(path: Path) -> None:
    """Owner-only permissions; a no-op on Windows."""
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


def load() -> dict[str, Any]:
    """Return the merged configuration, migrating legacy data on first use."""
    ensure_dirs()
    _migrate_legacy()
    if not _config_file().exists():
        return dict(DEFAULT_CONFIG)
    try:
        stamp = os.stat(_config_file())
        key = (str(_config_file()), stamp.st_mtime_ns, stamp.st_size)
    except OSError:
        return dict(DEFAULT_CONFIG)
    global _cached
    if _cached is not None and _cached[:3] == key:
        return dict(_cached[3])
    try:
        raw = json.loads(_config_file().read_text("utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return dict(DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        return dict(DEFAULT_CONFIG)
    merged = {**DEFAULT_CONFIG, **raw}
    _cached = (*key, merged)
    return dict(merged)


def save(cfg: dict[str, Any]) -> None:
    """Atomically write the configuration with owner-only permissions."""
    ensure_dirs()
    target = _config_file()
    fd, name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=target.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(cfg, stream, indent=2, ensure_ascii=False)
        _restrict(tmp)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    _restrict(target)
    global _cached
    _cached = None


def get(key: str, default: Any = None) -> Any:
    """Read a key, letting environment variables win over the file."""
    env_value = _env_override(key)
    if env_value is not None:
        return env_value
    return load().get(key, default)


def _env_override(key: str) -> Any:
    mapping = {
        "server_url": brand.env("server_url"),
        "api_key": brand.env("api_key"),
        "theme": brand.env("theme"),
        "mode": brand.env("mode"),
        "provider": brand.env("provider"),
        "default_model": brand.env("model"),
    }
    names = [mapping[key]] if key in mapping else []
    # Fall back to the pre-rename variable so existing setups keep working.
    if key in ("server_url", "api_key"):
        names.append(brand.env_legacy(key))
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def set_key(key: str, value: Any) -> None:
    cfg = load()
    cfg[key] = value
    save(cfg)


#: Keys whose value must never be printed by ``config`` or ``status``.
SECRET_KEYS = ("api_key",)


def is_secret(key: str) -> bool:
    """True when a config key holds a credential that must stay hidden."""
    return key in SECRET_KEYS


def set(key: str, value: Any) -> None:
    """Alias of :func:`set_key` for call sites that read better this way."""
    set_key(key, value)


def resolve_server_url(explicit: str = "", cfg: dict[str, Any] | None = None) -> str:
    """Resolve the remote bridge URL, explicit argument first."""
    if cfg is None:
        cfg = load()
    url = explicit or os.environ.get(brand.env("server_url"), "") \
        or os.environ.get(brand.env_legacy("server_url"), "") \
        or str(cfg.get("server_url", ""))
    return url.strip().rstrip("/")


def is_configured() -> bool:
    """True when a remote bridge is both addressed and authorised."""
    cfg = load()
    return bool(resolve_server_url(cfg=cfg)) and bool(get("api_key"))


def config_path() -> Path:
    return _config_file()


def describe() -> str:
    """Human readable location and provenance, for ``config show``."""
    cfg = load()
    lines = [f"fichier   : {_config_file()}",
             f"dossier   : {locations.config_dir()}"]
    for key, value in sorted(cfg.items()):
        shown = "***" if is_secret(key) and value else value
        if isinstance(shown, (dict, list)):
            shown = json.dumps(shown, ensure_ascii=False)
        lines.append(f"  {key:<22} {shown}")
    env_keys = sorted(k for k in os.environ if k.startswith(brand.ENV_PREFIX))
    if env_keys:
        lines.append("variables d'environnement actives :")
        lines.extend(f"  {k}" for k in env_keys)
    return "\n".join(lines)
