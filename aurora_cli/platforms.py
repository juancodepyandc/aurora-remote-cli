"""Which build of a dependency this machine can actually run.

The previous behaviour was implicit and fragile: code asked for `torch`, got
whatever the platform happened to serve, and failures surfaced as a crash deep
inside a model load. Choosing a version is a compatibility question, so it is
answered here from a declared matrix rather than from whatever resolves first.

Two things this deliberately separates, because they are commonly confused:

* *Detecting* the host — facts, read from the running interpreter.
* *Resolving* against the matrix — a decision, driven by data the user can edit,
  with the reason returned alongside the answer.

Every resolution returns why. "torch 2.14 is unavailable on windows/arm64" is
actionable; a bare ImportError three layers down is not. An unresolvable
combination is reported as such instead of silently falling back to whatever is
installed, because a silent fallback is how a machine ends up running a build
nobody chose.
"""
from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from packaging.specifiers import InvalidSpecifier, SpecifierSet


def manifest_path() -> Path:
    override = os.environ.get("JOBIA_PLATFORMS")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "policies" / "platforms.toml"


def load_matrix() -> dict:
    path = manifest_path()
    if not path.is_file():
        raise FileNotFoundError(f"platform manifest not found at {path}")
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib

        return tomllib.loads(text)
    except ModuleNotFoundError:
        import tomli

        return tomli.loads(text)


@dataclass(frozen=True)
class Host:
    """Facts about the machine, all read rather than assumed."""

    system: str          # windows | darwin | linux
    arch: str            # arm64 | x86_64 | ...
    machine: str         # raw uname machine, kept for diagnostics
    python: tuple        # (major, minor)
    implementation: str  # CPython, PyPy, ...
    libc: str            # gnu | musl | "" — unknown is not the same as gnu
    gpu: str             # mps | cuda | rocm | cpu

    def key(self) -> str:
        return f"{self.system}-{self.arch}-{self.libc or 'unknown'}"


def detect(system: str | None = None, arch: str | None = None,
           machine: str | None = None, libc: str | None = None,
           gpu: str | None = None) -> Host:
    """Describe the host. Each field can be overridden so tests need no hardware."""
    # `is None` rather than `or`: an explicit empty override is a fact the caller
    # is asserting, not a request to guess.
    detected_system = system if system is not None else platform.system().lower()
    detected_machine = machine if machine is not None else platform.machine().lower()
    detected_arch = arch if arch is not None else _normalise_arch(detected_machine)
    detected_libc = libc if libc is not None else _detect_libc(detected_system)
    return Host(
        system=detected_system,
        arch=detected_arch,
        machine=detected_machine,
        python=sys.version_info[:2],
        implementation=platform.python_implementation(),
        libc=detected_libc,
        gpu=gpu if gpu is not None else _detect_gpu(),
    )


def _normalise_arch(machine: str) -> str:
    machine = machine.lower()
    if machine in ("arm64", "aarch64", "armv8", "armv8l"):
        return "arm64"
    if machine in ("x86_64", "amd64", "x64"):
        return "x86_64"
    if machine.startswith("armv7"):
        return "armv7"
    return machine or "unknown"


def _detect_libc(system: str) -> str:
    """gnu vs musl, which decides whether a manylinux wheel is usable at all."""
    if system != "linux":
        return ""
    try:
        libc, _ = platform.libc_ver()
    except Exception:
        libc = ""
    if "musl" in (libc or "").lower():
        return "musl"
    if "glibc" in (libc or "").lower():
        return "gnu"
    # An unidentified C library must not select the glibc wheel rules.
    for root in ("/lib", "/usr/lib"):
        if next(Path(root).glob("ld-musl-*.so.1"), None) is not None:
            return "musl"
    return ""


def _detect_gpu() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "rocm" if getattr(getattr(torch, "version", None), "hip", None) else "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


@dataclass(frozen=True)
class Resolution:
    """The chosen constraint plus the reason, always both."""

    package: str
    constraint: str
    reason: str
    ok: bool

    def to_dict(self) -> dict:
        return {"package": self.package, "constraint": self.constraint,
                "reason": self.reason, "ok": self.ok}


class Resolver:
    """Answers 'can this host run this package' from the declared matrix."""

    def __init__(self, matrix: dict | None = None):
        self.matrix = matrix if matrix is not None else load_matrix()

    def target(self, host: Host) -> dict | None:
        """The most specific matching target, so a narrow rule beats a broad one.

        Specificity matters: `linux-arm64-musl` must be able to override
        `linux-arm64`, otherwise a musl fix can never be expressed.
        """
        best = None
        best_score = -1
        for name, body in (self.matrix.get("target") or {}).items():
            if not _matches_target(body, host):
                continue
            score = sum(1 for field in ("system", "arch", "libc", "gpu") if body.get(field))
            if score > best_score:
                best, best_score = body, score
        return best

    def resolve(self, package: str, host: Host | None = None) -> Resolution:
        host = host or detect()
        target = self.target(host)
        if target is None:
            return Resolution(package, "", f"no target declared for {host.key()}", False)
        declared = (target.get("package") or {}).get(package)
        if declared is None:
            return Resolution(package, "", (
                f"{package} is not declared for {host.key()}; "
                f"declared here: {sorted((target.get('package') or {}).keys())}"
            ), False)
        # A constraint may be written as a bare string, or as a table once it
        # needs more than a version (index URL, extras). Both are accepted so the
        # manifest can grow without a format break.
        if isinstance(declared, str):
            constraint = declared
        else:
            constraint = declared.get("constraint", "")
        # The package constraint describes which build to take; it is NOT a
        # Python requirement. Those are different questions and are declared in
        # different keys — comparing a package version against the interpreter
        # version is a category error that silently never fires.
        python_requirement = target.get("python", "")
        try:
            SpecifierSet(constraint)
            supported_python = SpecifierSet(python_requirement)
        except InvalidSpecifier as exc:
            return Resolution(package, constraint, f"invalid platform constraint: {exc}", False)
        python_version = ".".join(map(str, host.python))
        if python_version not in supported_python:
            return Resolution(package, constraint, (
                f"{host.key()} runs Python {python_version}, "
                f"but this target needs Python {python_requirement}"
            ), False)
        return Resolution(package, constraint, (
            f"{host.key()} matches target '{target.get('name', '?')}', "
            f"{package}{constraint}"
        ), True)

    def profile(self, host: Host | None = None) -> dict:
        host = host or detect()
        target = self.target(host)
        packages = sorted((target or {}).get("package") or {})
        # Every package any target declares, not just this one: a package missing
        # from this target is precisely the fact worth reporting, and restricting
        # to the local declarations would hide it.
        known = set()
        for body in (self.matrix.get("target") or {}).values():
            known |= set(body.get("package") or {})
        return {
            "host": {
                "key": host.key(), "system": host.system, "arch": host.arch,
                "machine": host.machine, "libc": host.libc, "gpu": host.gpu,
                "python": f"{host.python[0]}.{host.python[1]}",
                "implementation": host.implementation,
            },
            "target": (target or {}).get("name"),
            "packages": {name: self.resolve(name, host).to_dict() for name in packages},
            "unusable": sorted(name for name in known if not self.resolve(name, host).ok),
        }


def _matches_target(body: dict, host: Host) -> bool:
    if body.get("system") and body["system"] != host.system:
        return False
    if body.get("arch") and body["arch"] != host.arch:
        return False
    if body.get("libc") and body["libc"] != host.libc:
        return False
    if body.get("gpu") and body["gpu"] != host.gpu:
        return False
    return True
