"""One bounded selection policy for all roles, with auditable failure memory.

Catalogue preference is not a quality measurement. Only an evaluated engine
has a proven score. Replacement never deletes another installation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
import uuid
from pathlib import Path

from aurora_cli.adapters import AdapterRegistry
from . import catalog, locations
from .agents import get
from .machine import profile


def environment_key(machine):
    from aurora_cli.adapters import manifest_path
    keys = ('os_name', 'os_version', 'arch', 'accelerator', 'total_ram_gb', 'vram_gb')
    return hashlib.sha256(json.dumps({k: getattr(machine, k, None) for k in keys},
                                    sort_keys=True).encode() + manifest_path().read_bytes()).hexdigest()


class SelectionMemory:
    """Remember infrastructure failures for this hardware, not semantic guesses."""
    def __init__(self, path=None, *, clock=time.time):
        self.path = Path(path) if path else locations.data_dir() / 'selection' / 'failures.json'
        self.clock = clock

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def blocked(self, machine, capability, spec, *, context=''):
        record = self.read().get(environment_key(machine), {}).get(capability, {}).get(spec)
        if (record and record.get('until', 0) > self.clock()
                and (not record.get('context') or record['context'] == context)):
            return record.get('reason', 'Échec récent de cette recette.')
        return ''

    def failure(self, machine, capability, spec, reason, *, context='', ttl_s=3600):
        data = self.read()
        bucket = data.setdefault(environment_key(machine), {}).setdefault(capability, {})
        bucket[spec] = dict(reason=reason[-2000:], context=context, until=self.clock() + ttl_s)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.touch(mode=0o600, exist_ok=False)
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)


@dataclass(frozen=True)
class ModelChoice:
    artifact: catalog.Artifact
    runner_id: str
    installed: bool
    proven_score: float | None = None

    def to_dict(self):
        return dict(model=self.artifact.ref, runtime=self.artifact.runtime, runner=self.runner_id,
                    installed=self.installed, tier=self.artifact.tier, ram_gb=self.artifact.ram_gb,
                    proven_score=self.proven_score,
                    quality_status='evaluated_metric_not_universal_quality' if self.proven_score is not None else 'catalogue_preference')


def installed(artifact):
    if artifact.runtime == 'huggingface':
        from .pipeline import find_model
        return find_model(artifact.ref, capability='image' if artifact.agent_id == 'image' else '') is not None
    if artifact.runtime == 'ollama':
        # Do not pull a model to answer a read-only selection question.
        from .pipeline import find_ollama_model
        return bool(find_ollama_model(artifact.ref))
    return False


def choices(role, *, machine=None, excluded=(), require_textures=False, context='',
            registry=None, memory=None, installed_probe=None, proven=None):
    """Return executable/provisionable candidates and every rejection reason."""
    machine = machine or profile()
    agent = get(role)
    if agent is None:
        raise ValueError(f'Rôle inconnu : {role}')
    registry, memory = registry or AdapterRegistry(), memory or SelectionMemory()
    installed_probe = installed_probe or installed
    rejected, accepted = {}, []
    capability = agent.capability
    capability = 'llm' if capability == 'code' else capability
    for artifact in catalog.for_agent(agent):
        reason = ''
        runner, missing = registry.resolve(capability, artifact.ref)
        if artifact.ref in excluded:
            reason = 'Déjà essayé pour cette demande.'
        elif runner is None:
            reason = missing
        elif runner.incompatibility(machine, require_textures=require_textures):
            reason = runner.incompatibility(machine, require_textures=require_textures)
        elif artifact.ram_gb > catalog.usable_ram_gb(machine):
            reason = f'{artifact.ram_gb:g} Go requis, budget RAM disponible insuffisant.'
        else:
            reason = memory.blocked(machine, capability, artifact.ref, context=context)
        present = bool(installed_probe(artifact)) if not reason else False
        if not reason and not present and artifact.bytes is not None:
            if artifact.bytes / 1024**3 > max(0, machine.free_disk_gb - 2):
                reason = 'Espace disque insuffisant pour installer cette recette.'
        if reason:
            rejected[artifact.ref] = reason
            continue
        score = proven.score if proven and proven.spec == artifact.ref else None
        accepted.append(ModelChoice(artifact, runner.id, present, score))
    accepted.sort(key=lambda c: (c.proven_score is not None, c.proven_score or 0,
        registry.runners[c.runner_id].priority, catalog.TIERS.index(c.artifact.tier), c.installed), reverse=True)
    return accepted, rejected


def choose(role, **kwargs):
    options, rejected = choices(role, **kwargs)
    if not options:
        raise RuntimeError(f'Aucune recette compatible pour {role}. ' + json.dumps(rejected, ensure_ascii=False))
    return options[0]


def remember_failure(role, spec, reason, *, context='', infrastructure=False):
    agent = get(role)
    capability = 'llm' if agent.capability == 'code' else agent.capability
    SelectionMemory().failure(profile(), capability, spec, reason,
        context='' if infrastructure else context, ttl_s=3600 if infrastructure else 900)


def rank_available(models, *, role='resume', machine=None):
    """Rank served models for chat/code/academic/cyber without inventing quality."""
    machine = machine or profile()
    agent = get(role) or get('resume')
    known = {a.ref: a for a in catalog.for_agent(agent)}
    memory = SelectionMemory()
    usable = []
    for model in models:
        if model.capability in {'embedding', 'image', '3d', 'audio', 'video', 'tts'}:
            continue
        artifact = known.get(model.name)
        need = artifact.ram_gb if artifact else (model.size_bytes / 1024**3 * 1.2 if model.size_bytes else 0)
        if need > catalog.usable_ram_gb(machine) or memory.blocked(machine, agent.capability, model.name):
            continue
        usable.append(model)
    return sorted(usable, key=lambda m: (
        0 if m.capability in {'llm', 'code'} else 1 if not m.capability else 2,
        -catalog.TIERS.index(known[m.name].tier) if m.name in known else 1,
        0 if m.size_bytes else 1, m.name))
