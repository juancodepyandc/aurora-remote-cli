"""Provision reviewed native model recipes in isolated, revision-specific venvs.

No upstream setup script, sudo, global /tmp tree or unreviewed Hub code is
executed. Build logs and source revisions survive any failed installation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import hashlib

from . import locations
from .runtime_provisioning import run_logged


def _checked(command, root, name, *, cwd=None, timeout=3600):
    log = root / 'provisioning' / (name + '.log')
    result = run_logged(list(map(str, command)), log, cwd=cwd, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'Préparation native échouée ({name}). Diagnostic : {log}')
    return result.stdout.strip()


def clone_reviewed(repository, revision, destination, root, name):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('La recette native exige une révision Git immuable vérifiée.')
    if not destination.exists():
        _checked(['git', 'clone', '--no-checkout', repository, destination], root, name + '-clone')
        _checked(['git', '-C', destination, 'checkout', '--detach', revision], root, name + '-checkout')
    actual = _checked(['git', '-C', destination, 'rev-parse', 'HEAD'], root, name + '-revision')
    origin = _checked(['git', '-C', destination, 'remote', 'get-url', 'origin'], root, name + '-origin')
    if actual != revision or origin != repository:
        raise RuntimeError('Sources existantes différentes de la recette ; aucun fichier remplacé.')
    dirty = _checked(['git', '-C', destination, 'status', '--porcelain', '--untracked-files=no'], root, name + '-status')
    if dirty:
        raise RuntimeError('Sources natives modifiées ; elles sont conservées sans installation par-dessus.')
    _checked(['git', '-C', destination, 'submodule', 'update', '--init', '--recursive'], root, name + '-submodules')


def ensure_native(runner, model_name, *, machine=None):
    from .machine import profile
    from .bootstrap import _compatible_python, ensure_hf
    from .pipeline import find_model
    from . import fetcher, catalog
    machine = machine or profile()
    reason = runner.incompatibility(machine)
    if reason:
        raise RuntimeError(reason)
    if not shutil.which('nvcc'):
        raise RuntimeError('Compilation CUDA nécessaire : nvcc indisponible. Aucune installation système forcée.')
    root = locations.data_dir() / 'engines' / (runner.id + '-' + runner.revision[:12])
    ownership = root / '.jobia-native.json'
    if root.exists() and not ownership.is_file():
        raise RuntimeError(f'Répertoire existant sans preuve de gestion : {root}. Aucun écrasement.')
    root.mkdir(parents=True, exist_ok=True)
    if not ownership.exists():
        ownership.write_text(json.dumps({'runner': runner.id, 'revision': runner.revision}), encoding='utf-8')
    python = root / ('venv/Scripts/python.exe' if os.name == 'nt' else 'venv/bin/python')
    repo = root / 'repo'
    signature = dict(model=model_name, repository=runner.repository, revision=runner.revision,
                     packages=list(runner.packages), torch_packages=list(runner.torch_packages),
                     extensions=list(runner.extensions), imports=list(runner.probe_imports),
                     dependencies=list(runner.dependencies))
    receipt = root / 'runtime.json'
    probe = ('import sys, importlib; sys.path.insert(0, sys.argv[1]); import torch; '
             'assert torch.cuda.is_available(), "CUDA indisponible dans le runtime"; ' +
             f'assert torch.cuda.get_device_properties(0).total_memory >= {runner.min_vram_gb} * 1024**3, "VRAM du GPU effectif insuffisante"; ' +
             '; '.join(f'importlib.import_module({name!r})' for name in runner.probe_imports))
    if receipt.is_file() and python.is_file():
        saved = json.loads(receipt.read_text())
        if saved.get('signature') == signature:
            clone_reviewed(runner.repository, runner.revision, repo, root, 'source')
            _checked([python, '-c', probe, repo], root, 'runtime-probe', timeout=180)
            verify_dependencies(runner, saved.get('dependencies', {}))
            if Path(saved['weights']).is_dir() and all(Path(p).is_dir() for p in saved.get('dependencies', {}).values()):
                return root, python
    lock = root / '.install.lock'
    try:
        handle = lock.open('x')
    except FileExistsError:
        raise RuntimeError(f'Installation native déjà active ou interrompue : {lock}') from None
    try:
        with handle:
            handle.write(str(os.getpid()))
        clone_reviewed(runner.repository, runner.revision, repo, root, 'source')
        if not python.is_file():
            _checked([_compatible_python(), '-m', 'venv', root / 'venv'], root, 'venv')
        _checked([python, '-m', 'pip', 'install', 'setuptools', 'wheel', 'ninja', 'packaging', 'psutil'], root, 'build-tools')
        _checked([python, '-m', 'pip', 'install', *runner.torch_packages,
                  '--index-url', runner.torch_index], root, 'torch')
        # CUDA extensions require the torch headers in this same venv.
        _checked([python, '-m', 'pip', 'install', '--no-build-isolation', *runner.packages], root, 'packages')
        for index, extension in enumerate(runner.extensions):
            path = repo / extension['subdirectory'] if extension.get('subdirectory') else root / 'sources' / str(index)
            if not extension.get('subdirectory'):
                path.parent.mkdir(exist_ok=True)
                clone_reviewed(extension['repository'], extension['revision'], path, root, f'extension-{index}')
            if extension.get('package_subdirectory'):
                path = path / extension['package_subdirectory']
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError('Extension hors du runtime géré.')
            _checked([python, '-m', 'pip', 'install', '--no-build-isolation', path], root, f'extension-{index}-build')
        _checked([python, '-c', probe, repo], root, 'runtime-probe', timeout=180)
        found = find_model(model_name, capability='3d')
        if found:
            weights = found[0]
        else:
            artifact = next(a for a in catalog.ARTIFACTS if a.ref == model_name)
            ensure_hf()
            plan, _ = fetcher.install(artifact, machine, job='native-runtime')
            weights = plan.target
        dependencies = {}
        for dependency in runner.dependencies:
            spec = dependency['spec']
            component = catalog.Artifact('3d', 'component', 'huggingface', spec, spec,
                                         include=tuple(dependency.get('include', ())), revision=dependency.get('revision', ''))
            ensure_hf()
            # Fetcher checks the actual required files; existing directories
            # are not enough. Authentication/licence failures are preserved.
            plan, _ = fetcher.install(component, machine, job='native-dependency')
            dependencies[dependency.get('for_spec', spec)] = str(plan.target.resolve())
        verify_dependencies(runner, dependencies)
        temporary = receipt.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(signature=signature, weights=str(weights.resolve()), dependencies=dependencies),
                                         indent=2), encoding='utf-8')
        temporary.replace(receipt)
        return root, python
    finally:
        lock.unlink(missing_ok=True)


def verify_dependencies(runner, dependencies):
    """Only the explicit, pinned component code may be loaded by the recipe."""
    for dependency in runner.dependencies:
        name = dependency.get('for_spec', dependency['spec'])
        if name not in dependencies:
            raise RuntimeError(f'Composant requis absent : {name}')
        root = Path(dependencies[name]).resolve()
        for filename, expected in dependency.get('hashes', {}).items():
            path = root / filename
            if not path.resolve().is_relative_to(root) or not path.is_file():
                raise RuntimeError(f'Source de composant absente : {filename}')
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeError(f'Source de composant non conforme à la recette : {filename}')
