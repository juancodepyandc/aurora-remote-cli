"""Install isolated Python engines and start an existing Ollama daemon."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import venv

import httpx

from . import locations


def ensure_3d_engine():
    """Prepare the Hunyuan runtime under JOBIA data, adapted to this host."""
    root = locations.data_dir() / "models" / "hunyuan3d-2.1"
    repo = root / "Hunyuan3D-2.1"
    python = root / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
    root.mkdir(parents=True, exist_ok=True)
    if not repo.exists():
        subprocess.run(["git", "clone", "--depth", "1",
                        "https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1.git",
                        str(repo)], check=True, timeout=900)
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(root / "venv")
    stamp = root / ".jobia-ready"
    requirements = repo / "requirements.txt"
    if not stamp.exists() and requirements.is_file():
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)],
                       check=True, timeout=1800)
    if not stamp.exists() and ((repo / "pyproject.toml").is_file() or (repo / "setup.py").is_file()):
        subprocess.run([str(python), "-m", "pip", "install", "-e", str(repo)],
                       check=True, timeout=1800)
    # A previous interrupted install may have left the readiness marker while
    # the core runtime is absent. Repair that state before returning.
    torch_check = subprocess.run([str(python), "-c", "import torch"],
                                 capture_output=True)
    if torch_check.returncode != 0:
        subprocess.run([str(python), "-m", "pip", "install", "torch"],
                       check=True, timeout=1800)
    stamp.touch()
    return root


def python_engine(name, packages):
    root = locations.data_dir() / 'engines' / name
    python = root / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(root)
    subprocess.run([str(python), '-m', 'pip', 'install', *packages], check=True)
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
