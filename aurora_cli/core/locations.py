"""Filesystem locations that matter, resolved per operating system.

The previous build assumed a POSIX home directory and a Mac. Everything
here is written so that a Windows path, a Linux path and a macOS path are
all first-class, because "works on any OS" is a requirement, not a nicety.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from aurora_cli import brand

# --- Application directories ---------------------------------------------


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var, "").strip()
    return Path(value).expanduser() if value else Path.home() / fallback


def data_dir() -> Path:
    """Where sessions, logs and caches live."""
    override = os.environ.get(brand.env("data_dir"), "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / brand.APP_SLUG
    return _xdg("XDG_DATA_HOME", ".local/share") / brand.APP_SLUG


def config_dir() -> Path:
    """Where ``config.json`` lives."""
    override = os.environ.get(brand.env("config_dir"), "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / brand.APP_SLUG
    return _xdg("XDG_CONFIG_HOME", ".config") / brand.APP_SLUG


def config_file() -> Path:
    """Full path of the user's configuration file."""
    return config_dir() / "config.json"


def brain_dir() -> Path:
    return data_dir() / "brain"


def logs_dir() -> Path:
    return data_dir() / "logs"


def sessions_dir() -> Path:
    return data_dir() / "sessions"


def history_file() -> Path:
    return data_dir() / "history"


def ensure_dirs() -> None:
    for directory in (config_dir(), data_dir(), brain_dir(), logs_dir(), sessions_dir()):
        directory.mkdir(parents=True, exist_ok=True)


# --- Legacy migration -----------------------------------------------------

_LEGACY_CONFIG_DIRS = (".aurora", ".aurora-cli", ".nexus")


def legacy_config_files() -> list[Path]:
    """Config files written by earlier builds, newest location last."""
    candidates = [Path.home() / name / "config.json" for name in _LEGACY_CONFIG_DIRS]
    legacy_appdata = _xdg("XDG_CONFIG_HOME", ".config") / "aurora" / "config.json"
    candidates.append(legacy_appdata)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        candidates.append(Path(base) / "aurora" / "config.json")
    return [p for p in candidates if p.is_file()]


# --- Model caches ---------------------------------------------------------


def home() -> Path:
    return Path.home()


def _mac_library(kind: str) -> Path:
    return home() / "Library" / kind


def model_search_roots() -> list[Path]:
    """Every directory that may hold model weights, in priority order.

    Grouped by runtime so the caller can attribute a file to the tool that
    owns it, which is what makes ``models`` output useful rather than a
    flat list of unexplained paths.
    """
    roots: list[tuple[str, Path]] = []
    add = roots.append

    # --- Ollama ---
    ollama_home = os.environ.get("OLLAMA_MODELS", "").strip()
    if ollama_home:
        add(("ollama", Path(ollama_home).expanduser()))
    add(("ollama", home() / ".ollama" / "models"))
    if sys.platform == "darwin":
        add(("ollama", _mac_library("Application Support") / "Ollama" / "models"))

    # --- Hugging Face ---
    hf_home = os.environ.get("HF_HOME", "").strip()
    if hf_home:
        add(("huggingface", Path(hf_home).expanduser() / "hub"))
    add(("huggingface", home() / ".cache" / "huggingface" / "hub"))
    if sys.platform == "darwin":
        add(("huggingface", _mac_library("Caches") / "huggingface" / "hub"))
    elif sys.platform == "win32":
        add(("huggingface", _xdg("LOCALAPPDATA", "AppData/Local") / "huggingface" / "hub"))

    # --- LM Studio ---
    add(("lmstudio", home() / ".lmstudio" / "models"))
    add(("lmstudio", home() / ".cache" / "lm-studio" / "models"))
    if sys.platform == "darwin":
        add(("lmstudio", _mac_library("Application Support") / "LM Studio" / "models"))
    if sys.platform == "win32":
        add(("lmstudio", _xdg("LOCALAPPDATA", "AppData/Local") / "LM-Studio" / "models"))

    # --- llama.cpp ---
    add(("llamacpp", home() / ".cache" / "llama.cpp"))
    if sys.platform == "win32":
        add(("llamacpp", _xdg("LOCALAPPDATA", "AppData/Local") / "llama.cpp"))

    # --- LocalAI ---
    add(("localai", _xdg("XDG_DATA_HOME", ".local/share") / "local-ai" / "models"))
    if sys.platform == "darwin":
        add(("localai", _mac_library("Application Support") / "LocalAI" / "models"))

    # --- Jan ---
    add(("jan", home() / ".jan" / "models"))
    if sys.platform == "darwin":
        add(("jan", _mac_library("Application Support") / "jan" / "models"))

    # --- GPT4All ---
    add(("gpt4all", home() / ".local" / "share" / "gpt4all" / "models"))
    if sys.platform == "darwin":
        add(("gpt4all", _mac_library("Application Support") / "gpt4all" / "models"))

    # --- KoboldCpp ---
    add(("koboldcpp", home() / "koboldcpp" / "models"))
    add(("koboldcpp", home() / ".cache" / "koboldcpp"))

    # --- Project-local, so a checkout with its own models is found ---
    add(("projet", Path.cwd() / "models"))
    add(("projet", Path.cwd() / "weights"))
    add(("projet", Path.cwd() / ".jobia" / "models"))

    # --- Explicit overrides: JOBIA_MODEL_ROOTS is how a user points JOBIA at a
    # model tree it cannot guess, e.g. a 3D pipeline living outside the cwd.
    for extra in os.environ.get("JOBIA_MODEL_ROOTS", "").split(os.pathsep):
        extra = extra.strip()
        if extra:
            add(("jobia", Path(extra).expanduser()))

    seen: set[str] = set()
    unique: list[tuple[str, Path]] = []
    for runtime, path in roots:
        key = f"{runtime}:{path}"
        if key not in seen:
            seen.add(key)
            unique.append((runtime, path))
    return unique


def executable_dirs() -> list[Path]:
    """Directories to search for local runtime binaries."""
    dirs: list[Path] = []
    raw = os.environ.get("PATH", "")
    for entry in raw.split(os.pathsep):
        entry = entry.strip().strip('"')
        if entry:
            dirs.append(Path(entry))
    # Common install locations that are not always on PATH.
    for extra in ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin",
                  str(home() / ".local/bin"), str(home() / "bin"),
                  str(home() / ".cargo/bin")):
        dirs.append(Path(extra))
    if sys.platform == "win32":
        for extra in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                      os.environ.get("LOCALAPPDATA", "")):
            if extra:
                dirs.append(Path(extra))
    seen: set[str] = set()
    unique = []
    for path in dirs:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def is_windows() -> bool:
    return sys.platform == "win32"


def binary_names(*names: str) -> tuple[str, ...]:
    """Return the executable names to look for on this platform.

    ``binary_names("llama-server")`` gives ``("llama-server",)`` on POSIX and
    ``("llama-server.exe", "llama-server")`` on Windows, so callers can pass
    their raw names and let this decide.
    """
    flat: list[str] = []
    for name in names:
        flat.append(name)
    if not is_windows():
        return tuple(flat)
    out: list[str] = []
    for name in flat:
        if name.endswith(".exe"):
            out.append(name)
        else:
            out.extend((f"{name}.exe", name))
    return tuple(dict.fromkeys(out))
