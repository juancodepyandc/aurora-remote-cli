"""Autonomous Engine Manager - handles install → use → user-confirmed cleanup.

The AI decides what engines/models it needs, installs them, uses them,
and ONLY cleans up after explicit user confirmation.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import aurora_root, data_dir, models_dir, output_dir, hf_cache_dir, ollama_models_dir

log = logging.getLogger("engine_manager")


def _target(name: str) -> Path:
    """Names are identifiers, never paths supplied to a recursive deletion."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) or name in {".", ".."}:
        raise ValueError("Nom d'installation invalide")
    return models_dir() / name


def _mark_managed(path: Path, name: str) -> dict:
    receipt = {"managed_by": "jobia", "name": name, "install_path": str(path.resolve())}
    (path / ".jobia-managed.json").write_text(json.dumps(receipt), encoding="utf-8")
    return {"managed_by": "jobia"}


def _owns_files(record) -> bool:
    path = record.install_path
    allowed = {models_dir().resolve(), (data_dir() / "engines").resolve()}
    if path.is_symlink() or path.parent.resolve() not in allowed:
        return False
    if record.metadata.get("managed_by") != "jobia":
        return False
    try:
        receipt = json.loads((path / ".jobia-managed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return receipt == {"managed_by": "jobia", "name": record.name, "install_path": str(path.resolve())}


@dataclass
class EngineRecord:
    """Record of an installed engine/model."""
    name: str
    capability: str  # "image", "3d", "text", "vision", "audio", "code"
    install_path: Path
    source: str  # "huggingface", "ollama", "local", "builtin"
    installed_at: float
    size_bytes: int = 0
    metadata: dict = field(default_factory=dict)
    in_use: bool = False  # Protected from cleanup while True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "capability": self.capability,
            "install_path": str(self.install_path),
            "source": self.source,
            "installed_at": self.installed_at,
            "size_bytes": self.size_bytes,
            "metadata": self.metadata,
            "in_use": self.in_use,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EngineRecord":
        return cls(
            name=data["name"],
            capability=data["capability"],
            install_path=Path(data["install_path"]),
            source=data["source"],
            installed_at=data["installed_at"],
            size_bytes=data.get("size_bytes", 0),
            metadata=data.get("metadata", {}),
            in_use=data.get("in_use", False),
        )


class EngineRegistry:
    """Persistent registry of all installed engines/models."""

    def __init__(self, registry_path: Path | None = None):
        self.registry_path = registry_path or (data_dir() / "engine_registry.json")
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, EngineRecord] = {}
        self._load()

    def _load(self) -> None:
        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text())
                for rec_data in data.get("records", []):
                    rec = EngineRecord.from_dict(rec_data)
                    self._records[rec.name] = rec
            except Exception as e:
                log.warning(f"Failed to load engine registry: {e}")

    def _save(self) -> None:
        data = {
            "version": 1,
            "updated_at": time.time(),
            "records": [rec.to_dict() for rec in self._records.values()],
        }
        tmp = self.registry_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(self.registry_path)

    def add(self, record: EngineRecord) -> None:
        self._records[record.name] = record
        self._save()

    def get(self, name: str) -> EngineRecord | None:
        return self._records.get(name)

    def list_all(self) -> list[EngineRecord]:
        return list(self._records.values())

    def list_by_capability(self, capability: str) -> list[EngineRecord]:
        return [r for r in self._records.values() if r.capability == capability]

    def mark_in_use(self, name: str, in_use: bool = True) -> bool:
        if name in self._records:
            self._records[name].in_use = in_use
            self._save()
            return True
        return False

    def remove(self, name: str, force: bool = False) -> bool:
        rec = self._records.get(name)
        if not rec:
            return False
        if rec.in_use:
            raise RuntimeError(f"Le moteur {name} est actif et reste protégé.")
        if rec.source == "ollama":
            if rec.metadata.get("managed_by") != "jobia":
                raise PermissionError("Modèle Ollama découvert, non installé par JOBIA.")
            import httpx
            endpoint = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
            if "://" not in endpoint:
                endpoint = "http://" + endpoint
            response = httpx.request("DELETE", endpoint + "/api/delete", json={"model": rec.metadata["model_name"]}, timeout=60)
            response.raise_for_status()
            del self._records[name]
            self._save()
            return True
        if not _owns_files(rec):
            raise PermissionError("Nettoyage refusé : seuls les fichiers installés et enregistrés par JOBIA dans ses répertoires gérés peuvent être supprimés.")
        # Actually delete files
        if rec.install_path.exists():
            if rec.install_path.is_dir():
                shutil.rmtree(rec.install_path)
            else:
                rec.install_path.unlink()
        del self._records[name]
        self._save()
        return True

    def get_total_size(self) -> int:
        return sum(r.size_bytes for r in self._records.values())


class EngineInstaller:
    """Installs engines/models from various sources."""

    def __init__(self, registry: EngineRegistry):
        self.registry = registry

    def install_hf_model(self, repo_id: str, capability: str, local_name: str | None = None) -> EngineRecord:
        """Install a model from Hugging Face Hub."""
        name = local_name or repo_id.replace("/", "--")
        target_dir = _target(name)
        created = not target_dir.exists()
        if target_dir.is_symlink():
            raise ValueError("Une installation gérée ne peut pas cibler un lien symbolique")
        target_dir.mkdir(parents=True, exist_ok=True)

        log.info(f"Installing HF model {repo_id} to {target_dir}")
        try:
            from .core.bootstrap import python_engine
            from .core.runtime_provisioning import run_logged
            python = python_engine("huggingface", ["huggingface_hub"])
            result = run_logged([str(python), "-c", "import sys; from huggingface_hub import snapshot_download; snapshot_download(repo_id=sys.argv[1], local_dir=sys.argv[2])",
                                 repo_id, str(target_dir)], data_dir() / "provisioning" / "hf-download.log", timeout=7200)
            if result.returncode:
                raise RuntimeError("Téléchargement incomplet; diagnostic dans provisioning/hf-download.log")
        except Exception as e:
            log.error(f"Failed to download {repo_id}: {e}")
            raise

        size = sum(f.stat().st_size for f in target_dir.rglob("*") if f.is_file())

        record = EngineRecord(
            name=name,
            capability=capability,
            install_path=target_dir,
            source="huggingface",
            installed_at=time.time(),
            size_bytes=size,
            metadata={"repo_id": repo_id, **(_mark_managed(target_dir, name) if created else {})},
        )
        self.registry.add(record)
        return record

    def install_ollama_model(self, model_name: str, *, capability: str = 'text') -> EngineRecord:
        """Install a model into Ollama."""
        log.info(f"Installing Ollama model {model_name}")
        try:
            import httpx
            from .core.bootstrap import start_ollama
            start_ollama()
            endpoint = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
            if "://" not in endpoint:
                endpoint = "http://" + endpoint
            before = httpx.get(endpoint + "/api/tags", timeout=30)
            before.raise_for_status()
            existing_names = {item.get("name") for item in before.json().get("models", [])}
            already_present = model_name in existing_names or model_name + ":latest" in existing_names
            resp = httpx.post(
                endpoint + "/api/pull",
                json={"model": model_name, "stream": False},
                timeout=600.0,
            )
            resp.raise_for_status()
            if resp.json().get("error") or resp.json().get("status") != "success":
                raise RuntimeError(resp.json().get("error", "Téléchargement Ollama non terminé"))
        except Exception as e:
            log.error(f"Failed to install Ollama model {model_name}: {e}")
            raise

        # Find the actual model path
        ollama_dir = ollama_models_dir()
        record = EngineRecord(
            name=model_name,
            capability=capability,
            install_path=ollama_dir / model_name,
            source="ollama",
            installed_at=time.time(),
            size_bytes=0,  # Size managed by Ollama
            metadata={"model_name": model_name, **({"managed_by": "jobia"} if not already_present else {})},
        )
        self.registry.add(record)
        return record

    def ensure_python_env(self, env_name: str, packages: list[str]) -> Path:
        """Ensure a Python virtual environment exists with required packages."""
        from .core.bootstrap import python_engine
        return python_engine(env_name, packages)

    def install_hunyuan3d(self, repo_id: str = "tencent/Hunyuan3D-2.1") -> EngineRecord:
        """Install Hunyuan3D with full environment (venv + dependencies)."""
        name = repo_id.replace("/", "--")
        target_dir = models_dir() / name
        target_dir.mkdir(parents=True, exist_ok=True)

        log.info(f"Installing Hunyuan3D {repo_id} to {target_dir}")

        # 1. Download model weights from HF
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(
                repo_id=repo_id,
                local_dir=target_dir,
                local_dir_use_symlinks=False,
                resume_download=True,
            )
        except Exception as e:
            log.error(f"Failed to download {repo_id}: {e}")
            raise

        # 2. Create venv with all dependencies
        venv_path = target_dir / "venv"
        python_bin = venv_path / "bin" / "python"

        if not python_bin.exists():
            log.info(f"Creating Hunyuan3D venv at {venv_path}")
            venv_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)

        # Install dependencies from requirements.txt if available
        req_file = target_dir / "Hunyuan3D-2.1" / "requirements.txt"
        if req_file.exists():
            log.info("Installing Hunyuan3D dependencies from requirements.txt")
            subprocess.run([str(sys.executable), "-m", "pip", "install", "-q", "-r", str(req_file)], check=True)
        else:
            # Fallback: install core dependencies
            core_packages = [
                "torch", "transformers", "diffusers", "accelerate", "huggingface-hub",
                "safetensors", "numpy", "scipy", "einops", "opencv-python",
                "imageio", "scikit-image", "rembg", "realesrgan", "trimesh",
                "pymeshlab", "pygltflib", "xatlas", "open3d", "omegaconf",
                "pyyaml", "tqdm", "psutil", "pydantic", "timm"
            ]
            subprocess.run([str(python_bin), "-m", "pip", "install", "-q", *core_packages], check=True)

        size = sum(f.stat().st_size for f in target_dir.rglob("*") if f.is_file())

        record = EngineRecord(
            name="tencent--Hunyuan3D-2.1",
            capability="3d",
            install_path=target_dir,
            source="huggingface",
            installed_at=time.time(),
            size_bytes=size,
            metadata={"repo_id": "tencent/Hunyuan3D-2.1", "has_venv": True},
        )
        self.registry.add(record)
        return record


class AutonomousEngineManager:
    """
    High-level manager that the AI uses to get what it needs.

    Usage:
        mgr = AutonomousEngineManager()
        engine = mgr.ensure("image", "stabilityai/sdxl-turbo")
        # Use engine...
        mgr.release("stabilityai--sdxl-turbo")
        # Later, if user confirms:
        mgr.cleanup("stabilityai--sdxl-turbo", confirmed_by_user=True)
    """

    def __init__(self):
        self.registry = EngineRegistry()
        self.installer = EngineInstaller(self.registry)
        self._active_engines: set[str] = set()
        # Scan for local installations on startup
        self.scan_local_installations()

    def ensure(self, capability: str, model_spec: str, **kwargs) -> EngineRecord:
        """
        Ensure an engine/model is available for the given capability.

        Args:
            capability: "image", "3d", "text", "vision", "audio", "code"
            model_spec: Model identifier (HF repo_id, Ollama name, etc.)
            **kwargs: Additional options (source, env_name, packages, etc.)

        Returns:
            EngineRecord with install_path and metadata
        """
        # Check if already installed
        existing = self._find_existing(capability, model_spec)
        if existing and capability not in {'3d', 'image', 'audio', 'tts'}:
            log.info(f"Using existing {capability} engine: {existing.name}")
            return existing

        # Install new
        log.info(f"Installing {capability} engine: {model_spec}")
        record = self._install(capability, model_spec, **kwargs)
        self._active_engines.add(record.name)
        return record

    def _find_existing(self, capability: str, model_spec: str) -> EngineRecord | None:
        """Check if model already installed with valid installation."""
        raw_matches = []
        # Normalize model_spec for matching
        normalized_spec = model_spec.lower().replace("/", "--").replace("_", "-")
        spec_parts = normalized_spec.split("--")
        alt_normalized = spec_parts[-1] if len(spec_parts) > 1 else normalized_spec

        for rec in self.registry.list_by_capability(capability):
            rec_name = rec.name.lower()
            rec_norm = rec.name.lower().replace("/", "--").replace("_", "-")

            # Match by repo_id in metadata (exact match) - highest priority
            if rec.metadata.get("repo_id", "").lower() == model_spec.lower():
                raw_matches.append((rec, 1000))
                continue
            # Match by exact normalized name (full model_spec) - highest priority
            if normalized_spec == rec_norm:
                raw_matches.append((rec, 1000 + len(rec_norm)))
                continue
            # Match by exact alt_normalized (only if it's the full record name, not a prefix)
            if alt_normalized == rec_norm:
                raw_matches.append((rec, 900 + len(rec_norm)))
                continue
            # Match by alt_normalized as exact word boundary (not a prefix)
            if f"-{alt_normalized}-" in f"-{rec_norm}-" or rec_norm.endswith(f"-{alt_normalized}"):
                raw_matches.append((rec, 800 + len(rec_norm)))
                continue
            # Match by alt_normalized as prefix (lower priority)
            if rec_norm.startswith(f"{alt_normalized}-"):
                raw_matches.append((rec, 700 + len(rec_norm)))
                continue

        if not raw_matches:
            return None

        # Post-process: if there's an exact match on alt_normalized that is a prefix of another record,
        # deprioritize the shorter one in favor of the longer one
        final_matches = []
        for rec, priority in raw_matches:
            rec_norm = rec.name.lower().replace("/", "--").replace("_", "-")
            # Check if this is an exact alt_normalized match that is a prefix of another record
            if priority >= 900 and priority < 1000:  # exact alt_normalized match
                is_prefix_of_longer = any(
                    other_rec != rec and other_rec.name.lower().replace("/", "--").replace("_", "-").startswith(f"{alt_normalized}-")
                    for other_rec, _ in raw_matches
                )
                if is_prefix_of_longer:
                    # Deprioritize this match
                    priority = 600 + len(rec.name)
            final_matches.append((rec, priority))

        if not final_matches:
            return None

        # Sort by priority (higher first), then by venv existence, then by size (larger = more complete)
        def sort_key(x):
            rec, priority = x
            venv_exists = (x[0].install_path / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")).exists()
            size_mb = x[0].size_bytes / 1e6
            return (priority * 10000 + (1000 if venv_exists else 0) + size_mb * 10)

        final_matches.sort(key=sort_key, reverse=True)

        return final_matches[0][0]

    def scan_local_installations(self) -> int:
        """Scan for local model installations and register them."""
        from aurora_cli.core.locations import model_search_roots
        registered = 0

        for runtime, root in model_search_roots():
            if not root.is_dir():
                continue
            # Skip HF cache
            if "huggingface" in root.name.lower() or ".cache" in str(root).lower():
                continue
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                venv = child / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
                if not venv.exists():
                    continue
                # Determine capability from directory name
                name = child.name
                capability = "unknown"
                name_lower = name.lower()
                if any(k in name_lower for k in ["hunyuan", "trellis", "meshy", "tripo", "rodin"]):
                    capability = "3d"
                elif any(k in name_lower for k in ["sdxl", "flux", "stable-diffusion", "midjourney"]):
                    capability = "image"

                # Check if already registered
                existing = self._find_existing(capability, name)
                if existing:
                    continue

                size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
                record = EngineRecord(
                    name=name,
                    capability=capability,
                    install_path=child,
                    source="local",
                    installed_at=time.time(),
                    size_bytes=size,
                    metadata={"local_scan": True},
                )
                self.registry.add(record)
                registered += 1
                log.info(f"Auto-registered local installation: {name} ({capability})")

        return registered

    def _install(self, capability: str, model_spec: str, **kwargs) -> EngineRecord:
        source = kwargs.get("source", "auto")

        if capability in {'3d', 'image', 'audio', 'tts'}:
            from .core.pipeline import ensure_model
            prepared = ensure_model(model_spec, capability)
            if not prepared:
                raise RuntimeError(f'Runtime absent pour {capability} : {model_spec}')
            path, python = prepared
            record = EngineRecord(name=model_spec.replace('/', '--'), capability=capability,
                install_path=path, source=source, installed_at=time.time(),
                metadata={'runtime_prepared': True, 'python': str(python), 'spec': model_spec})
            self.registry.add(record)
            return record

        if source == "huggingface" or (source == "auto" and "/" in model_spec and capability in ("image", "3d")):
            return self.installer.install_hf_model(model_spec, capability)
        elif source == "ollama" or (source == "auto" and capability in {'text', 'llm', 'vision'}):
            return self.installer.install_ollama_model(model_spec, capability=capability)
        elif source == "builtin" or capability == "code":
            # Built-in capabilities (code, etc.) use Ollama or local
            return self.installer.install_ollama_model(model_spec, capability=capability)
        else:
            raise ValueError(f"Unknown source {source} for capability {capability}")

    def use(self, name: str) -> EngineRecord:
        """Mark engine as in-use (protected from cleanup)."""
        rec = self.registry.get(name)
        if not rec:
            raise KeyError(f"Engine {name} not found")
        self.registry.mark_in_use(name, True)
        self._active_engines.add(name)
        return rec

    def release(self, name: str) -> None:
        """Mark engine as no longer in-use (available for cleanup)."""
        self.registry.mark_in_use(name, False)
        self._active_engines.discard(name)

    def cleanup(self, name: str, confirmed_by_user: bool = False, force: bool = False) -> bool:
        """
        Clean up an engine/model.

        Args:
            name: Engine name
            confirmed_by_user: MUST be True for actual deletion (safety)
            force: Ignore in_use flag (dangerous)

        Returns:
            True if cleaned up, False if skipped
        """
        if not confirmed_by_user:
            log.info(f"Cleanup skipped for {name} - user confirmation required")
            return False

        rec = self.registry.get(name)
        if not rec:
            log.warning(f"Engine {name} not found in registry")
            return False

        if rec.in_use and not force:
            log.warning(f"Engine {name} is in use, skipping cleanup (use force=True to override)")
            return False

        log.info(f"Cleaning up engine: {name}")
        self.registry.remove(name, force=force)
        self._active_engines.discard(name)
        return True

    def cleanup_all_unused(self, confirmed_by_user: bool = False) -> int:
        """Clean up all engines not marked in_use."""
        if not confirmed_by_user:
            log.info("Bulk cleanup skipped - user confirmation required")
            return 0

        cleaned = 0
        for rec in self.registry.list_all():
            if not rec.in_use:
                try:
                    self.registry.remove(rec.name)
                    cleaned += 1
                except Exception as e:
                    log.warning(f"Failed to clean {rec.name}: {e}")
        return cleaned

    def list_engines(self) -> list[dict]:
        """List all engines with status."""
        return [
            {
                "name": r.name,
                "capability": r.capability,
                "source": r.source,
                "size_mb": round(r.size_bytes / 1e6, 1),
                "in_use": r.in_use,
                "path": str(r.install_path),
            }
            for r in self.registry.list_all()
        ]

    def status(self) -> dict:
        """Get overall status."""
        total = len(self.registry._records)
        active = len([r for r in self.registry._records.values() if r.in_use])
        total_size = self.registry.get_total_size()
        return {
            "total_engines": total,
            "active": active,
            "idle": total - active,
            "total_size_mb": round(total_size / 1e6, 1),
            "engines": self.list_engines(),
        }
