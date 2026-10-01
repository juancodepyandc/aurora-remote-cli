"""Pluggable runners: how a capability is actually executed.

The engine registry knows *which* models exist, research finds *candidates*, and
the probe knows how to *judge* — but nothing owned the middle: how a given model
for a given capability is invoked. That lived as a hardcoded branch in the 3D
path, which is why exactly one capability has one working engine.

This module owns it, as data. A runner declares:

  * the capability and the specs it can serve
  * the argv template, with typed placeholders the caller supplies
  * the environment a backend needs, so the MPS watermark and memory limits are
    declared once instead of being rediscovered at runtime
  * the checks it is expected to satisfy

Nothing is interpreted from a string beyond placeholder substitution, and no
runner is invented at runtime: a capability with no matching runner is reported
by name, which is what makes "unsupported here" a fact rather than a guess.

The manifest is `policies/adapters.toml`. Adding a model or a platform is an edit
there; this file does not change.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def placeholders(template: str) -> set:
    """Placeholder names in an argv template."""
    return set(PLACEHOLDER.findall(template))


def manifest_path() -> Path:
    override = os.environ.get("JOBIA_ADAPTERS")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "policies" / "adapters.toml"


def load_manifest() -> dict:
    """Read the runner manifest. A missing manifest is an error, not an empty set.

    Defaulting to "no runners" would make an unreadable manifest look identical to
    a machine that simply has nothing installed, and those need different fixes.
    """
    path = manifest_path()
    if not path.is_file():
        raise FileNotFoundError(f"adapter manifest not found at {path}")
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib

        return tomllib.loads(text)
    except ModuleNotFoundError:
        import tomli

        return tomli.loads(text)


@dataclass(frozen=True)
class Runner:
    """One declared way to execute a capability for a set of model specs."""

    id: str
    capability: str
    #: Specs (repo ids, model names) this runner serves. Empty means "any", which
    #: is only appropriate when the runner really is spec-agnostic.
    specs: tuple = ()
    argv: tuple = ()
    #: Placeholders the caller must supply, discovered from the argv template.
    required: tuple = ()
    env: tuple = ()
    #: Named checks this runner can satisfy, for honest reporting.
    provides: tuple = ()
    texturing: bool = False
    interpreter: str = ""
    parameters: tuple = ()
    systems: tuple = ()
    accelerators: tuple = ()
    min_vram_gb: float = 0.0
    priority: int = 0
    worker: str = ''
    provisioner: str = ''
    repository: str = ''
    revision: str = ''
    packages: tuple = ()
    setup_args: tuple = ()
    probe_imports: tuple = ()
    extensions: tuple = ()
    torch_packages: tuple = ()
    torch_index: str = ''
    dependencies: tuple = ()
    geometry_contract: str = 'volumetric'

    def incompatibility(self, machine, *, require_textures=False) -> str:
        if require_textures and not self.texturing:
            return 'Cet adaptateur ne produit pas les textures demandées.'
        if self.systems and getattr(machine, 'os_name', '').casefold() not in {s.casefold() for s in self.systems}:
            return f'Systèmes pris en charge : {", ".join(self.systems)} ; hôte : {getattr(machine, "os_name", "inconnu")}.'
        label = getattr(machine, 'accelerator', '').casefold()
        device = ('cuda' if 'nvidia' in label or 'cuda' in label else
                  'rocm' if 'rocm' in label or 'amd' in label else
                  'mps' if 'metal' in label else 'cpu')
        if self.accelerators and device not in self.accelerators:
            return f'Accélérateurs pris en charge : {", ".join(self.accelerators)} ; hôte : {device}.'
        if self.min_vram_gb and (getattr(machine, 'unified_memory', False)
                                or getattr(machine, 'vram_gb', 0) < self.min_vram_gb):
            return f'{self.min_vram_gb:g} Go de VRAM dédiée requis, RAM unifiée non équivalente.'
        return ''

    def matches(self, capability: str, spec: str) -> bool:
        if capability != self.capability:
            return False
        if not self.specs:
            return True
        lowered = spec.lower()
        return any(candidate.lower() == lowered for candidate in self.specs)

    def environment(self) -> dict:
        return dict(pair.split("=", 1) for pair in self.env if "=" in pair)

    def to_dict(self) -> dict:
        return {"id": self.id, "capability": self.capability, "specs": list(self.specs),
                "provides": list(self.provides), "texturing": self.texturing,
                "interpreter": self.interpreter or "inherited",
                "parameters": dict(self.parameters), 'systems': list(self.systems),
                'accelerators': list(self.accelerators), 'min_vram_gb': self.min_vram_gb,
                'worker': self.worker, 'provisioner': self.provisioner,
                'geometry_contract': self.geometry_contract}


class AdapterRegistry:
    """Resolves a (capability, spec) pair to a runner, or says why it cannot."""

    def __init__(self, manifest: dict | None = None):
        self.manifest = manifest if manifest is not None else load_manifest()
        self.runners: dict[str, Runner] = {}
        for name, body in (self.manifest.get("runner") or {}).items():
            if body.get('geometry_contract', 'volumetric') not in {'volumetric', 'surface'}:
                raise ValueError(f'Unknown geometry contract for {name}')
            declared = body["argv"]
            argv = tuple(shlex.split(declared) if isinstance(declared, str) else declared)
            self.runners[name] = Runner(
                id=name,
                capability=body.get("capability", "3d"),
                specs=tuple(body.get("specs", ())),
                argv=argv,
                required=tuple(sorted(set(PLACEHOLDER.findall(" ".join(argv))))),
                env=tuple(body.get("env", ())),
                provides=tuple(body.get("provides", ())),
                texturing=bool(body.get("texturing", False)),
                interpreter=body.get("interpreter", ""),
                parameters=tuple(body.get("parameters", {}).items()),
                systems=tuple(body.get('systems', ())),
                accelerators=tuple(body.get('accelerators', ())),
                min_vram_gb=float(body.get('min_vram_gb', 0)),
                priority=int(body.get('priority', 0)),
                worker=body.get('worker', ''), provisioner=body.get('provisioner', ''),
                repository=body.get('repository', ''), revision=body.get('revision', ''),
                packages=tuple(body.get('packages', ())), setup_args=tuple(body.get('setup_args', ())),
                probe_imports=tuple(body.get('probe_imports', ())),
                extensions=tuple(body.get('extensions', ())),
                torch_packages=tuple(body.get('torch_packages', ())),
                torch_index=body.get('torch_index', ''),
                dependencies=tuple(body.get('dependencies', ())),
                geometry_contract=body.get('geometry_contract', 'volumetric'),
            )

    def resolve(self, capability: str, spec: str) -> tuple[Runner | None, str]:
        """The runner for this pair, or a reason naming what is missing."""
        # An explicit model adapter takes precedence over a generic runner.
        for runner in sorted(self.runners.values(), key=lambda r: not bool(r.specs)):
            if runner.matches(capability, spec):
                return runner, "declared"
        known = sorted({r.capability for r in self.runners.values()})
        return None, (
            f"no runner declares {capability!r} for {spec!r}; "
            f"capabilities with runners: {known or 'none'}. "
            f"Add one to {manifest_path()}."
        )

    def build(self, runner: Runner, **values) -> list[str]:
        """Substitute placeholders into the argv template.

        Placeholders become single argv entries, so a path with spaces cannot
        smuggle in extra arguments.
        """
        values = dict(runner.parameters) | values
        missing = [name for name in runner.required if name not in values]
        if missing:
            raise KeyError(f"runner {runner.id} needs {missing}")
        command: list[str] = []
        for token in runner.argv:
            def substitute(match):
                return str(values[match.group(1)])

            rendered = PLACEHOLDER.sub(substitute, token)
            # A manifest uses an empty placeholder for an optional flag, so an
            # empty substitution must leave no argument behind at all.
            if rendered:
                command.append(rendered)
        return command

    def describe(self) -> list[dict]:
        return [runner.to_dict() for runner in self.runners.values()]

    def gaps(self, capability: str, specs) -> dict:
        """Which candidates have no runner. Reported, never guessed at."""
        missing = {}
        for spec in specs:
            runner, reason = self.resolve(capability, spec)
            if runner is None:
                missing[spec] = reason
        return missing
