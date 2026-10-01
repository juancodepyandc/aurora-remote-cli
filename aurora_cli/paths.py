"""Portable paths for Aurora engines - no hardcoded paths."""
from __future__ import annotations
import os
import shutil
from pathlib import Path
from .core import locations


def _env_path(name: str, default: str | None = None) -> Path | None:
    val = os.environ.get(name, "").strip()
    if val:
        return Path(val).expanduser()
    return Path(default).expanduser() if default else None


def aurora_root() -> Path:
    """Root of the Aurora project - configurable via AURORA_ROOT env var."""
    # This identifies source code only. Runtime data uses JOBIA locations.
    env = _env_path("AURORA_ROOT")
    if env and env.exists():
        return env
    return Path(__file__).resolve().parents[1]


def engines_dir() -> Path:
    return locations.data_dir() / "engines"


def output_dir() -> Path:
    return locations.data_dir() / "outputs"


def models_dir() -> Path:
    return locations.data_dir() / "models"


def comfy_root() -> Path | None:
    env = _env_path("COMFY_ROOT")
    if env and env.exists():
        return env
    # Auto-detect common locations
    for candidate in [
        Path.home() / "modele" / "comfyui" / "comfyui",
        Path.home() / "ComfyUI",
        Path.home() / "comfyui",
        aurora_root() / "modele" / "comfyui" / "comfyui",
    ]:
        if candidate.exists():
            return candidate
    return None


def blender_executable() -> Path | None:
    """Find Blender executable - configurable via BLENDER_PATH."""
    env = _env_path("BLENDER_PATH")
    if env and env.exists():
        return env
    installed = shutil.which("blender")
    if installed:
        return Path(installed)
    # Auto-detect common locations
    import sys
    if sys.platform == "darwin":
        for candidate in sorted(Path("/Applications").glob("Blender*.app/Contents/MacOS/Blender"), reverse=True):
            if candidate.exists():
                return candidate
    elif sys.platform == "win32":
        for candidate in sorted((Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Blender Foundation").glob("Blender*/blender.exe"), reverse=True):
            if candidate.exists():
                return candidate
    else:
        for candidate in [
            Path("/usr/bin/blender"),
            Path("/usr/local/bin/blender"),
            Path("/snap/bin/blender"),
        ]:
            if candidate.exists():
                return candidate
    return None


def hf_cache_dir() -> Path:
    env = _env_path("HF_HOME")
    if env:
        return env / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def ollama_models_dir() -> Path:
    env = _env_path("OLLAMA_MODELS")
    if env:
        return env
    import sys
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Ollama" / "models"
    return Path.home() / ".ollama" / "models"


def data_dir() -> Path:
    return locations.data_dir()


def config_dir() -> Path:
    return locations.config_dir()


# Backwards compatibility - deprecated but kept for existing code
AURORA = aurora_root()
BLENDER = blender_executable() or Path("")
OUTPUT_ROOT = output_dir() / "3d"
