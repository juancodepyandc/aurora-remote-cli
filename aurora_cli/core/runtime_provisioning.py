"""Evidence based dependency repairs for JOBIA's isolated runtimes.

The resolver may replace an unavailable exact version with a version selected
by pip. It never removes a required package, and a successful installation is
not evidence that a runtime is ready: callers must run their import probe.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid


@dataclass(frozen=True)
class Diagnosis:
    kind: str
    packages: tuple[str, ...] = ()


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def diagnose_pip(output: str) -> Diagnosis:
    """Read package evidence, never prose like 'some pyproject.toml projects'."""
    missing = re.findall(
        r"(?:No matching distribution found for|Could not find a version that "
        r"satisfies the requirement)\s+([A-Za-z0-9][A-Za-z0-9_.-]*)", output,
    )
    if missing:
        return Diagnosis("unavailable", tuple(dict.fromkeys(map(canonical_name, missing))))
    builds = re.findall(r"(?:Failed building wheel for|ERROR: Failed building wheel for)\s+"
                        r"([A-Za-z0-9][A-Za-z0-9_.-]*)", output)
    builds.extend(re.findall(r"Could not build wheels for\s+([A-Za-z0-9][A-Za-z0-9_.-]*)", output))
    # pip 25 prints a heading followed by a Unicode arrow and a package list.
    builds.extend(re.findall(r"^\s*╰─>\s*([A-Za-z0-9][A-Za-z0-9_.-]*(?:,\s*"
                             r"[A-Za-z0-9][A-Za-z0-9_.-]*)*)\s*$", output, re.M))
    flat = [canonical_name(item.strip()) for entry in builds for item in entry.split(",")]
    if flat:
        return Diagnosis("build", tuple(dict.fromkeys(flat)))
    if "ResolutionImpossible" in output or "conflicting dependencies" in output:
        return Diagnosis("conflict")
    if "BackendUnavailable" in output:
        return Diagnosis("build_backend")
    return Diagnosis("unknown")


def relax_unavailable_pin(lines: list[str], package: str) -> tuple[list[str], dict | None]:
    """Keep the dependency and its marker; relax only a literal exact pin.

    Lower bounds preserve the upstream baseline. API compatibility is checked
    separately by the runtime smoke test. Ranges, URLs and transitive
    dependencies cannot be rewritten safely from an error message.
    """
    pattern = re.compile(r"^(\s*)([A-Za-z0-9][A-Za-z0-9_.-]*)(\[[^]]+\])?"
                         r"\s*==\s*([A-Za-z0-9][A-Za-z0-9.!+_-]*)(\s*(?:;.*|#.*)?)$")
    for index, line in enumerate(lines):
        match = pattern.fullmatch(line)
        if match and canonical_name(match[2]) == canonical_name(package):
            replacement = f"{match[1]}{match[2]}{match[3] or ''}>={match[4]}{match[5]}"
            result = list(lines)
            result[index] = replacement
            return result, {"package": canonical_name(package), "before": line, "after": replacement}
    return lines, None


class ProvisioningError(RuntimeError):
    def __init__(self, message: str, *, log_dir: Path, diagnosis: Diagnosis):
        self.log_dir = log_dir
        self.diagnosis = diagnosis
        super().__init__(f"{message} Diagnostic conservé : {log_dir}")


def run_logged(command: list[str], log: Path, *, timeout: int = 3600,
               cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Capture noisy installers while retaining their complete diagnostic."""
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.write_text(str(exc), encoding="utf-8")
        raise ProvisioningError("Le processus de préparation n'a pas terminé.",
                                log_dir=log.parent, diagnosis=Diagnosis("process")) from exc
    log.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    return result


def install_requirements(python: Path, requirements: Path, *, max_attempts: int = 12) -> list[dict]:
    """Resolve, record, install. Retry only when evidence changes the plan."""
    log_dir = requirements.parent / "provisioning" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8])
    log_dir.mkdir(parents=True)
    lines = requirements.read_text(encoding="utf-8").splitlines()
    repairs: list[dict] = []
    history: list[dict] = []
    seen: set[str] = set()
    binary_only: set[str] = set()
    for attempt in range(1, max_attempts + 1):
        source = "\n".join(lines) + "\n"
        fingerprint = hashlib.sha256((source + repr(sorted(binary_only))).encode()).hexdigest()
        if fingerprint in seen:
            break
        seen.add(fingerprint)
        # Keep relative -r/-c references relative to the original input.
        candidate = requirements.with_name(f".jobia-{log_dir.name}-{attempt}.txt")
        candidate.write_text(source, encoding="utf-8")
        (log_dir / f"requirements-{attempt}.txt").write_text(source, encoding="utf-8")
        command = [str(python), "-m", "pip", "install", "--disable-pip-version-check",
                   "--prefer-binary", "-r", str(candidate)]
        if binary_only:
            command += ["--only-binary", ",".join(sorted(binary_only))]
        try:
            result = run_logged(command + ["--dry-run", "--report", str(log_dir / f"resolve-{attempt}.json")],
                                log_dir / f"resolve-{attempt}.log")
            phase = "resolve"
            if result.returncode == 0:
                result = run_logged(command + ["--report", str(log_dir / f"installed-{attempt}.json")],
                                    log_dir / f"install-{attempt}.log")
                phase = "install"
        finally:
            candidate.unlink(missing_ok=True)
        diagnosis = diagnose_pip(result.stdout + "\n" + result.stderr)
        history.append({"attempt": attempt, "phase": phase, "requirements_sha256": fingerprint,
                        "binary_only": sorted(binary_only),
                        "returncode": result.returncode, "diagnosis": asdict(diagnosis)})
        (log_dir / "history.json").write_text(json.dumps({"attempts": history, "repairs": repairs}, indent=2), encoding="utf-8")
        if result.returncode == 0:
            return repairs
        # A failed source build permits one different strategy: require an
        # actual wheel for the failing package. Pip then establishes whether
        # the pinned wheel is available before any version can be relaxed.
        if diagnosis.kind == "build" and set(diagnosis.packages) - binary_only:
            binary_only.update(diagnosis.packages)
            repairs.append({"strategy": "compatible_wheels", "packages": list(diagnosis.packages)})
            continue
        if diagnosis.kind == "unavailable":
            for package in diagnosis.packages:
                changed, repair = relax_unavailable_pin(lines, package)
                if repair:
                    lines = changed
                    repairs.append(repair)
                    break
            else:
                raise ProvisioningError("Aucune distribution compatible ne satisfait les dépendances requises "
                                        "(" + ", ".join(diagnosis.packages) + "). Un autre environnement ou moteur est nécessaire.",
                                        log_dir=log_dir, diagnosis=diagnosis)
            continue
        label = ", ".join(diagnosis.packages) or diagnosis.kind
        raise ProvisioningError(f"La préparation reste incompatible ({label}); les dépendances requises ont été conservées.",
                                log_dir=log_dir, diagnosis=diagnosis)
    raise ProvisioningError("Les variantes de dépendances disponibles ont été épuisées.",
                            log_dir=log_dir, diagnosis=Diagnosis("exhausted"))
