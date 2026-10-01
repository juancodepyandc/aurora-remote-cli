"""Install isolated Python engines and start an existing Ollama daemon."""
import os
import re
import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import time
import venv

import httpx

from . import locations
from .runtime_provisioning import install_requirements, run_logged, ProvisioningError, Diagnosis


def _compatible_python() -> str:
    """Locate an interpreter satisfying the portable adapter's tested contract."""
    candidates = [os.environ.get("JOBIA_PYTHON", ""), "python3.11", "python3.12",
                  "python3.10", "python3", "python", sys.executable]
    launcher = shutil.which("py")
    if launcher:
        for version in ("3.11", "3.12", "3.10"):
            try:
                candidates.append(subprocess.check_output(
                    [launcher, "-" + version, "-c", "import sys; print(sys.executable)"],
                    text=True, stderr=subprocess.DEVNULL, timeout=15).strip())
            except (OSError, subprocess.SubprocessError):
                continue
    for candidate in candidates:
        if not candidate or not shutil.which(candidate):
            continue
        try:
            raw = subprocess.check_output(
                [candidate, "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
                text=True, timeout=15).strip()
            major, minor = map(int, raw.split(".", 1))
            # This is the adapter's declared/tested interpreter contract, not
            # a list of exceptions deduced from individual installer failures.
            if major == 3 and 10 <= minor <= 12:
                return shutil.which(candidate) or candidate
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
    uv = shutil.which("uv")
    if uv:
        log = locations.data_dir() / "provisioning" / "python-install.log"
        result = run_logged([uv, "python", "install", "3.11"], log, timeout=600)
        if result.returncode == 0:
            return subprocess.check_output([uv, "python", "find", "3.11"], text=True, timeout=30).strip()
    raise RuntimeError("Aucun interpréteur disponible ne satisfait le contrat Python 3.10–3.12 du moteur 3D.")


def _install_requirements_adaptively(python: Path, requirements: Path) -> list[dict]:
    return install_requirements(python, requirements)


def runtime_python(root: Path, *, windows: bool | None = None) -> Path:
    return root / ("Scripts/python.exe" if (os.name == "nt" if windows is None else windows) else "bin/python")


def _3d_roots():
    """Discover installed adapters through configured model search roots."""
    seen = set()
    roots = [locations.data_dir() / "models"]
    roots.extend(path for _runtime, path in locations.model_search_roots())
    explicit = os.environ.get("JOBIA_3D_ROOT", "").strip()
    if explicit:
        roots.insert(0, Path(explicit).expanduser())
    for directory in roots:
        if not directory.is_dir():
            continue
        try:
            candidates = [directory, *sorted(directory.iterdir())]
        except OSError:
            continue
        for root in candidates:
            if root in seen:
                continue
            seen.add(root)
            if (root / "backend.py").is_file() and (root / "compat_patches.py").is_file():
                yield root


def _probe_3d(root: Path, *, texture: bool) -> bool:
    python = runtime_python(root / "venv")
    if not python.is_file():
        return False
    repo = root / "Hunyuan3D-2.1"
    script = "\n".join([
        "import sys, os",
        'os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "1.0")',
        f"sys.path[:0] = {list(map(str, (root, repo, repo / 'hy3dshape', repo / 'hy3dpaint')))!r}",
        "import torch, backend, compat_patches",
        "selected_backend = backend.detect(); compat_patches.apply(selected_backend)",
        "from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline",
        "from hy3dshape.rembg import BackgroundRemover",
        "import trimesh, xatlas, pygltflib",
        *(["import paint", "paint.apply_patches(selected_backend)",
           "from textureGenPipeline import Hunyuan3DPaintPipeline",
           "from DifferentiableRenderer import mesh_inpaint_processor"] if texture else []),
    ])
    try:
        result = run_logged([str(python), "-c", script], root / "provisioning" / "runtime-smoke.log",
                            timeout=180, cwd=repo)
        return result.returncode == 0
    except ProvisioningError:
        return False


def ensure_3d_engine(*, texture: bool = True):
    """Reuse a verified adapter before preparing a portable isolated runtime."""
    for root in _3d_roots():
        if _probe_3d(root, texture=texture):
            return root
    root = locations.data_dir() / "models" / "hunyuan3d-portable"
    root.parent.mkdir(parents=True, exist_ok=True)
    logs = locations.data_dir() / "provisioning" / "hunyuan3d-portable"

    def checked(command, name, timeout=1800, cwd=None):
        result = run_logged(command, logs / name, timeout=timeout, cwd=cwd)
        if result.returncode:
            raise ProvisioningError("La préparation du moteur portable a échoué; les installations existantes sont conservées.",
                                    log_dir=logs, diagnosis=Diagnosis("runtime"))
    if not root.exists():
        checked(["git", "clone", "--depth", "1",
                 "https://github.com/VladimirTalyzin/hunyuan3d-2.1-mac-rocm.git", str(root)],
                "clone.log", 900)
    repo = root / "Hunyuan3D-2.1"
    if not repo.exists():
        checked(["git", "clone", "--depth", "1", "https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1.git",
                 str(repo)], "clone-upstream.log", 900)
    python = runtime_python(root / "venv")
    if not python.is_file():
        checked([_compatible_python(), "-m", "venv", str(root / "venv")], "venv.log", 120)
    checked([str(python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
            "packaging.log", 600)
    # The portable adapter's pure-PyTorch dependency contract has no MPS-only
    # packages despite this historical filename. Never strip CUDA packages
    # out of Tencent's original recipe to pretend it supports this backend.
    requirements = root / "requirements_mac.txt"
    if not requirements.is_file():
        raise RuntimeError(f"Le moteur ne fournit pas son contrat de dépendances : {requirements}")
    prepared = root / "requirements.jobia.txt"
    prepared.write_text("torch\ntorchvision\n" + requirements.read_text(encoding="utf-8"), encoding="utf-8")
    _install_requirements_adaptively(python, prepared)
    checked([str(python), str(root / "scripts" / "build_extensions.py"), "--raster", "auto"],
            "extensions.log", cwd=root)
    if not _probe_3d(root, texture=texture):
        raise ProvisioningError("Le moteur n'a pas validé ses imports de génération et de texturation.",
                                log_dir=root / "provisioning", diagnosis=Diagnosis("smoke"))
    (root / ".jobia-ready").write_text(json.dumps({"python": str(python), "texture": texture,
        "requirements_sha256": hashlib.sha256(prepared.read_bytes()).hexdigest()}), encoding="utf-8")
    return root


def python_engine(name, packages):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("Nom de moteur invalide")
    root = locations.data_dir() / "engines" / name
    python = runtime_python(root)
    if not python.exists():
        if any(re.split(r'[<>=!~;\[]', p, maxsplit=1)[0] == 'torch' for p in packages):
            result = run_logged([_compatible_python(), '-m', 'venv', str(root)],
                locations.data_dir() / 'provisioning' / (name + '-venv.log'), timeout=180)
            if result.returncode:
                raise RuntimeError('Création du runtime Python compatible échouée.')
        else:
            venv.EnvBuilder(with_pip=True).create(root)
    modules = {"Pillow": "PIL"}
    names = [re.split(r"[<>=!~;\[]", package, maxsplit=1)[0].strip() for package in packages]
    probe = "import importlib, importlib.metadata\nfrom pip._vendor.packaging.requirements import Requirement\n"
    probe += "\n".join(
        f"req = Requirement({package!r}); assert (req.marker is not None and not req.marker.evaluate()) or req.specifier.contains(importlib.metadata.version(req.name), prereleases=True)"
        for package in packages)
    probe += "\n" + "\n".join(
        f"importlib.import_module({modules.get(name, name.replace('-', '_'))!r})" for name in names)
    if subprocess.run([str(python), "-c", probe], capture_output=True, timeout=180).returncode == 0:
        return python
    requirements = root / "requirements.jobia.txt"
    requirements.write_text("\n".join(packages) + "\n", encoding="utf-8")
    _install_requirements_adaptively(python, requirements)
    result = run_logged([str(python), "-c", probe], root / "provisioning" / "runtime-smoke.log", timeout=180)
    if result.returncode:
        raise ProvisioningError("Les imports du moteur restent invalides après installation.",
                                log_dir=root / "provisioning", diagnosis=Diagnosis("smoke"))
    return python


def ensure_hf():
    from .fetcher import _hf_cli
    found = _hf_cli()
    if found:
        return found
    python = python_engine('huggingface', ['huggingface_hub'])
    return str(python.parent / ('hf.exe' if os.name == 'nt' else 'hf'))


def start_ollama():
    binary = shutil.which('ollama')
    if not binary:
        import click
        command = None
        if sys.platform == 'darwin' and shutil.which('brew'):
            command = [shutil.which('brew'), 'install', 'ollama']
        elif os.name == 'nt' and shutil.which('winget'):
            command = [shutil.which('winget'), 'install', '--id', 'Ollama.Ollama', '--exact']
        if command:
            subprocess.run(command, check=True)
            binary = shutil.which('ollama')
        if not binary:
            raise RuntimeError('Ollama absent : installe le moteur depuis https://ollama.com/download puis réessaie.')
    url = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434').rstrip('/')
    if '://' not in url:
        url = 'http://' + url
    def ready():
        try:
            return httpx.get(url + '/api/tags', timeout=1).is_success
        except httpx.HTTPError:
            return False
    if ready():
        return
    log = locations.data_dir() / 'ollama-start.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('ab') as stream:
        process = subprocess.Popen([binary, 'serve'], stdout=stream, stderr=stream,
                                   start_new_session=True)
    for _ in range(40):
        if ready():
            return
        if process.poll() is not None:
            break
        time.sleep(.25)
    if process.poll() is None:
        process.terminate()
    raise RuntimeError(f'Ollama ne démarre pas ; diagnostic : {log}')
