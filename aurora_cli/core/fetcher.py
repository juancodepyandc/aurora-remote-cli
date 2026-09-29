"""Fetch a model, safely, resumably, and only after being asked.

The Linux project learned the hard way that a naive download is a trap:

* ``hf download`` takes one ``--include`` pattern per call. Passing several
  makes the later ones fall through to positional FILENAMES, and the download
  reports success while silently omitting weights. The DiT went missing that
  way — one loop iteration per pattern is the fix, and it is reproduced here.
* A download that dies at 90% must not restart. ``hf`` and the Hugging Face
  cache are content-addressed, so a re-run resumes at the files that are
  missing instead of re-fetching the ones already on disk.
* Nothing is written to the ledger until the artefact has been *verified*.
  A half-downloaded directory recorded as installed is a cleanup bug waiting
  to happen, and worse, it is recorded as if it were complete.

Everything here is cancellable and reports what it is doing, because a
multi-gigabyte fetch with no output reads as a hang.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import locations
from . import security
from .catalog import Artifact
from .provision import dir_size, record_installed


class ProvisionError(RuntimeError):
    """A provisioning step failed in a way the user needs to see."""


@dataclass
class Plan:
    """What a download will do, resolved before anything is fetched."""

    artifact: Artifact
    target: Path
    needs_bytes: int
    commands: list[list[str]]
    owned: bool = False


def _hf_cli() -> str:
    """The Hugging Face CLI, preferring the current name over the deprecated one.

    ``hf`` is the modern entry point; ``huggingface-cli`` is kept as a
    fallback because an older venv may only have it, and failing with "no
    such command" after resolving a 23 GB plan is a poor way to learn that.
    """
    for name in ("hf", "huggingface-cli"):
        found = shutil.which(name)
        if found:
            return found
    managed = locations.data_dir() / "engines" / "huggingface" / ("Scripts/hf.exe" if os.name == "nt" else "bin/hf")
    return str(managed) if managed.is_file() else ""


def check_runtime(artifact: Artifact) -> tuple[bool, str]:
    """Whether the runtime that can serve this artefact is present."""
    if artifact.runtime == "ollama":
        if not shutil.which("ollama"):
            return False, "Ollama absent. Installe-le depuis https://ollama.com/download"
        return True, "Ollama détecté."
    if artifact.runtime == "huggingface":
        if _hf_cli():
            return True, f"CLI Hugging Face détecté ({_hf_cli()})."
        return False, ("Ni 'hf' ni 'huggingface-cli' trouvés. "
                       "Installe avec : pip install -U 'huggingface_hub[cli]'")
    return False, f"Runtime inconnu : {artifact.runtime}"


def hf_target_dir(artifact: Artifact) -> Path:
    """Where a Hugging Face artefact is stored, and kept across runs.

    Placed inside JOBIA's own data directory so cleanup has one bounded
    location to reason about, and so a model JOBIA downloaded cannot be
    confused with one the user put there themselves.
    """
    slug = artifact.ref.replace("/", "--")
    root = (locations.data_dir() / "models").resolve()
    target = root / slug
    if not target.resolve().is_relative_to(root) or target.resolve() == root:
        raise ProvisionError("Destination hors du répertoire des modèles.")
    return target


def preflight(artifact: Artifact, machine) -> Plan:
    """Resolve where and how, and refuse a download the host cannot survive.

    Checked before the network is touched, because the failure this prevents
    is a machine that runs out of memory or disk minutes later, with a partial
    download already on it.
    """
    if artifact.runtime == "ollama":
        # Ollama owns its own store (~/.ollama/models); `pull` is the whole
        # transaction. There is no JOBIA-controlled directory to verify, so
        # the ledger records the tag and the runtime's own listing is the
        # source of truth. Inventing a path here would record an install that
        # does not exist and then "clean up" a directory that was never used.
        commands = [["ollama", "pull", artifact.ref]]
        target = locations.home() / ".ollama" / "models"
        needs = artifact.bytes or 0
    else:
        cli = _hf_cli()
        if not cli:
            raise ProvisionError(
                "Ni 'hf' ni 'huggingface-cli' disponibles : "
                "pip install -U 'huggingface_hub[cli]'")
        target = hf_target_dir(artifact)
        # One --include per invocation: several patterns in one call make the
        # CLI treat the extras as positional filenames and silently skip them.
        # With no pattern at all, drop the flag: a bare "." is not the same as
        # "the whole repo" and confuses the resolver.
        if artifact.include:
            commands = [
                [cli, "download", artifact.ref, "--local-dir", str(target),
                 "--include", pattern]
                for pattern in artifact.include
            ]
        else:
            commands = [[cli, "download", artifact.ref, "--local-dir", str(target)]]
        needs = artifact.bytes or 0

    if machine.free_disk_gb * (1024 ** 3) < needs * 1.15:
        raise ProvisionError(
            f"Espace insuffisant : {needs / _GO:.1f} Go nécessaires plus une "
            f"marge, {machine.free_disk_gb:.0f} Go libres. "
            f"Libère de la place ou choisis une version plus légère.")

    if artifact.ram_gb and machine.free_ram_gb < artifact.ram_gb * 0.5:
        raise ProvisionError(
            f"Mémoire insuffisante : {artifact.label} a besoin d'environ "
            f"{artifact.ram_gb:.0f} Go, {machine.free_ram_gb:.0f} Go sont "
            f"libres. Ferme des applications, ou utilise une version light.")

    # Every command is checked before it is ever built into a Plan, and the
    # destination is refused if it resolves outside JOBIA's data directory.
    # Both come from disk-written sources (catalogue, ledger), so neither is
    # trusted implicitly.
    for command in commands:
        if not security.command_is_safe(command):
            raise ProvisionError(f"Commande refusée : {command!r}")
    if security.is_forbidden(target):
        raise ProvisionError(f"Destination refusée, zone système : {target}")

    return Plan(artifact=artifact, target=target, needs_bytes=needs,
                commands=commands, owned=(not target.exists() if artifact.runtime != "ollama"
                                          else _ollama_state(artifact.ref) is False))


_GO = 1024 ** 3


def _already_complete(plan: Plan) -> bool:
    """Whether the target already holds the artefact.

    For Hugging Face this is the presence of every requested include pattern,
    which is exactly the thing that silently failed before: checking only for
    the directory would report a download with a missing DiT as complete.
    """
    # Always let the downloader reconcile its resumable cache. File presence
    # cannot establish completeness (partial weights, empty component folders).
    return False


def download(plan: Plan, *, job: str = "", progress=None) -> None:
    """Run the plan, one command at a time, reporting each step.

    ``progress`` is called with a human sentence before each command and once
    after, so a caller can surface it. Re-running is safe: the Hugging Face
    cache is content-addressed, so completed files are skipped rather than
    re-fetched.
    """
    if _already_complete(plan):
        if progress:
            progress(f"Déjà présent, téléchargement sauté : {plan.target}")
    else:
        for index, command in enumerate(plan.commands, start=1):
            label = " ".join(command[:4])
            if progress:
                progress(f"({index}/{len(plan.commands)}) {label}")
            try:
                result = subprocess.run(
                    command, text=True, timeout=None)
            except FileNotFoundError as exc:
                raise ProvisionError(f"Commande introuvable : {command[0]}") from exc
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                raise ProvisionError(
                    f"Échec de « {label} » (code {result.returncode}) : "
                    f"{detail[:400] or 'aucune sortie'}")


def verify(plan: Plan) -> None:
    """Refuse to record an artefact that did not land.

    Verified before the ledger is written, so cleanup never inherits a
    directory that is mostly empty.
    """
    if plan.artifact.runtime == "ollama":
        # Ollama owns its own store; ask it rather than inspecting a path
        # JOBIA does not control. A pull the runtime does not list did not
        # install anything, and recording it would be a lie the ledger keeps.
        if not _ollama_has(plan.artifact.ref):
            raise ProvisionError(
                f"Ollama a terminé mais {plan.artifact.ref} n'apparaît pas dans "
                f"`ollama list` : l'installation n'a pas eu lieu.")
        return
    if not plan.target.is_dir() or not any(
            p.is_file() and p.stat().st_size > 0 and ".cache" not in p.relative_to(plan.target).parts
            for p in plan.target.rglob("*")):
        raise ProvisionError(
            f"Téléchargement terminé sans contenu dans {plan.target} : "
            f"rien à enregistrer.")
    for pattern in plan.artifact.include:
        base = pattern.rstrip("/*")
        if not any(p.is_file() and p.stat().st_size > 0
                   for p in plan.target.glob(pattern)):
            raise ProvisionError(
                f"Pièce manquante après téléchargement : {base}. "
                f"Refais la commande pour reprendre (les fichiers déjà "
                f"présents ne seront pas retéléchargés).")


def _ollama_has(ref: str) -> bool:
    return _ollama_state(ref) is True


def _ollama_state(ref: str) -> bool | None:
    """Whether ``ollama list`` reports this model.

    Unknown rather than absent when the runtime cannot be asked, so a
    read-only or unusual install is not reported as a failed download.
    """
    try:
        result = subprocess.run(["ollama", "list"], capture_output=True,
                                text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    name = ref if ":" in ref else ref + ":latest"
    for line in (result.stdout or "").splitlines()[1:]:
        fields = line.split()
        if fields and name == fields[0]:
            return True
    return False


def record(plan: Plan, *, agent: str, job: str = "") -> int:
    """Write the artefact to the ledger, owned, and return its size.

    Two shapes of ownership, because deletion differs by runtime:

    * A Hugging Face tree is a directory JOBIA created, so the path itself is
      the record and removal is ``rmtree`` on that path.
    * An Ollama model lives in the runtime's own store, shared with every
      other model. Recording that directory would let cleanup delete the whole
      store, so the tag is recorded instead and removal goes through
      ``ollama rm <tag>``.

    Getting this wrong is the difference between reclaiming 4 GB and deleting
    every model the user ever installed.
    """
    if plan.artifact.runtime == "ollama":
        record_installed(f"ollama:{plan.artifact.ref}", kind="ollama-model",
                         agent=agent, job=job, size_bytes=plan.artifact.bytes or 0,
                         runtime="ollama", owned=plan.owned,
                         note=f"{plan.artifact.ref} ({plan.artifact.tier})")
        return plan.artifact.bytes or 0

    size = dir_size(plan.target)
    record_installed(plan.target, kind="model", agent=agent, job=job,
                     size_bytes=size, runtime=plan.artifact.runtime,
                     owned=plan.owned,
                     note=f"{plan.artifact.ref} ({plan.artifact.tier})")
    return size


def install(artifact: Artifact, machine, *, agent: str = "", job: str = "",
            progress=None) -> tuple[Plan, int]:
    """Preflight, download, verify, record. The whole path, in order."""
    plan = preflight(artifact, machine)
    if progress:
        progress(f"Vérification de l'espace et de la mémoire…")
    download(plan, job=job, progress=progress)
    verify(plan)
    size = record(plan, agent=agent or artifact.agent_id, job=job)
    return plan, size


def manifest(plan: Plan) -> dict:
    """A written record of what was installed, for audit and for docs."""
    return {
        "ref": plan.artifact.ref,
        "runtime": plan.artifact.runtime,
        "tier": plan.artifact.tier,
        "target": str(plan.target),
        "include": list(plan.artifact.include),
        "bytes": dir_size(plan.target),
        "at": time.time(),
    }
