"""Provision what a job needs, remember who installed it, and clean up after.

This is the CLI half of a rule that already exists in the Linux project: an
agent may install something to finish a job, and must be able to remove it
afterwards so the host does not accumulate tens of gigabytes. Two things there
are not optional, and they are the reason this is a module rather than a shell
alias:

**Provenance.** Every path JOBIA installs into is written to a ledger with a
timestamp and the job that caused it. Cleanup then only ever considers paths in
that ledger, so a model the user already had — a TRELLIS, a Hunyuan, anything
found by the scan — is structurally unreachable from the deletion path. A
cleanup that guesses is how 14 GB of someone's weights disappear.

**Consent.** Downloads are gigabytes. Installing is asked for, in the user's
own language, with the real size and the real consequence, and the answer is
recorded. A machine under pressure is offered smaller options or a wait, never
the largest one by default.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import locations
from . import security

LEDGER_NAME = "provisioned.json"
LEDGER_VERSION = 1


@dataclass
class ProvisionRecord:
    """One thing JOBIA installed, and why.

    ``owned`` is the safety property: it is True only for a path this process
    created. Imported and pre-existing models are recorded with ``owned=False``
    and are never candidates for removal.
    """

    path: str
    kind: str = "model"
    agent: str = ""
    job: str = ""
    bytes: int = 0
    installed_at: float = 0.0
    owned: bool = True
    runtime: str = ""
    note: str = ""

    @property
    def installed_at_label(self) -> str:
        if not self.installed_at:
            return ""
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.installed_at))


def ledger_path() -> Path:
    return locations.data_dir() / LEDGER_NAME


def load_ledger() -> list[ProvisionRecord]:
    """Read the ledger, tolerating a missing or corrupt file.

    A damaged ledger must never become permission to delete: it returns an
    empty list, which means "JOBIA installed nothing it knows of", so cleanup
    does nothing rather than guessing.
    """
    path = ledger_path()
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    records: list[ProvisionRecord] = []
    known = {f for f in ProvisionRecord.__dataclass_fields__}
    for entry in payload.get("records", []):
        if not isinstance(entry, dict) or "path" not in entry:
            continue
        fields = {k: v for k, v in entry.items() if k in known}
        try:
            records.append(ProvisionRecord(**fields))
        except TypeError:
            continue
    return records


def save_ledger(records: list[ProvisionRecord]) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": LEDGER_VERSION,
        "updated_at": time.time(),
        "records": [asdict(r) for r in records],
    }
    # Written to a temporary file and moved into place, so an interrupted
    # write cannot leave a half-written ledger. A truncated ledger is not a
    # cosmetic problem here: it is the list of things JOBIA believes it may
    # delete, and losing it must never turn into deleting something else.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    security.restrict_file(path)


def record_installed(path: str | Path, *, kind: str = "model", agent: str = "",
                     job: str = "", size_bytes: int = 0, runtime: str = "",
                     owned: bool = True, note: str = "") -> ProvisionRecord:
    """Add a path to the ledger. Idempotent per path."""
    resolved = str(Path(path).expanduser())
    record = ProvisionRecord(
        path=resolved, kind=kind, agent=agent, job=job, bytes=size_bytes,
        installed_at=time.time(), owned=owned, runtime=runtime, note=note,
    )
    records = [r for r in load_ledger() if r.path != resolved]
    records.append(record)
    save_ledger(records)
    return record


def record_existing(paths: list[str], *, agent: str = "") -> None:
    """Note models that were already present, as explicitly *not* owned.

    This is the second half of the safety property. Writing them down with
    ``owned=False`` documents the decision and makes the invariant auditable:
    if a TRELLIS is ever at risk, the reason is visible in the ledger.
    """
    records = load_ledger()
    known = {r.path for r in records}
    for raw in paths:
        resolved = str(Path(raw).expanduser())
        if resolved in known:
            continue
        records.append(ProvisionRecord(
            path=resolved, kind="model", agent=agent, bytes=0,
            installed_at=0.0, owned=False,
            note="Détecté avant toute installation par JOBIA",
        ))
    save_ledger(records)


def removable(records: list[ProvisionRecord] | None = None) -> list[ProvisionRecord]:
    """Records that cleanup is allowed to consider.

    Filters: JOBIA must own the record, the record must be of a kind it knows
    how to remove, and a filesystem record must still exist. Anything the scan
    found is excluded by ``owned=False``, which is what protects a model the
    user installed themselves.

    An ``ollama-model`` record has no path on disk — it is a tag in the
    runtime's store — so it is never filtered on existence.
    """
    records = records if records is not None else load_ledger()
    out: list[ProvisionRecord] = []
    for record in records:
        if not record.owned:
            continue
        if record.kind == "ollama-model":
            out.append(record)
            continue
        if record.kind not in {"model", "runtime", "package", "venv"}:
            continue
        if not Path(record.path).exists():
            continue
        out.append(record)
    return out


def record_size(record: ProvisionRecord) -> int:
    """Bytes attributable to one record, for a reclaim estimate.

    A filesystem record is measured on disk; an Ollama tag is not, because
    the store is shared and summing blobs would over-report what removing one
    tag frees. ``bytes`` holds the size the provider declared at fetch time.
    """
    if record.kind == "ollama-model":
        return record.bytes
    return dir_size(Path(record.path))


def dir_size(path: Path, *, budget: int = 200_000) -> int:
    total = 0
    seen = 0
    for dirpath, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            seen += 1
            if seen > budget:
                return total
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    return total


def human(size_bytes: int) -> str:
    """A byte count as something a human reads, at the right scale.

    The threshold matters: a 4 GB model formatted as "4096 Mo" reads as noise,
    and dividing twice (once here, once at the call site) turned a 4.4 GB
    download into a fictional 4.4 TB.
    """
    value = float(size_bytes)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if value < 1024 or unit == "To":
            rendered = f"{value:.1f}".removesuffix(".0")
            return f"{rendered} {unit}"
        value /= 1024
    return f"{value:.1f} To"


def summary(records: list[ProvisionRecord] | None = None) -> dict:
    records = records if records is not None else load_ledger()
    owned = [r for r in records if r.owned]
    foreign = [r for r in records if not r.owned]
    return {
        "installed_by_jobia": len(owned),
        "preexisting": len(foreign),
        "reclaimable_bytes": sum(record_size(r) for r in removable(owned)),
        "installed": owned,
        "preexisting_records": foreign,
    }
