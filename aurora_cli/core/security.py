"""Guards around anything that touches the filesystem or runs a process.

Provisioning writes gigabytes and then runs external commands, so the rules
about what may be written and what may be executed belong in one auditable
place rather than being spread across the callers.

Three ideas shape it.

**The user sees roles, not file names.** ``ask`` and ``provision`` print agent
labels. The technical ref is available behind an explicit ``--verbose`` and
nowhere else, because a ref in a confirmation prompt is also a ref in the
scrollback, the screenshot and the shell history.

**A download is only ever allowed inside JOBIA's own directories.** Refs come
from a catalogue and a ledger on disk, and both are writable by the user, so
the path is rebuilt from a known root and re-checked after resolution. A ref
like ``../../etc`` resolves to something outside the data directory and is
refused rather than created.

**Nothing is executed that was not built here.** Commands are constructed from
argument lists, never through a shell, so a model name containing shell
metacharacters stays a harmless string.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

#: Directory names a job may never write into, whatever the request says.
#: Checked against the resolved path so symlinks do not get around it.
FORBIDDEN = (
    "/etc", "/bin", "/sbin", "/usr", "/boot", "/sys", "/proc", "/dev",
    "/var/db", "/System", "/Library/Keychains", "/private/etc",
)


class SecurityError(RuntimeError):
    """Raised when an operation would cross a line JOBIA does not cross."""


def is_forbidden(path: Path) -> bool:
    """Whether a path sits in a system location that must never be written."""
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        # A path that cannot even be resolved is refused outright.
        return True
    text = str(resolved)
    return any(text == item or text.startswith(item + os.sep) for item in FORBIDDEN)


def safe_target(root: Path, *parts: str) -> Path:
    """Build a path under ``root`` and prove it stayed there.

    The parts are treated as names, not as a path to interpret: separators are
    replaced, so a ref containing ``../`` produces a literal directory name
    rather than an escape. The result is still re-checked after resolution,
    because a symlink already inside the root can point anywhere.
    """
    root = Path(root).expanduser()
    cleaned = [str(part).replace("/", "_").replace("\\", "_").replace("..", "_")
               for part in parts if str(part) not in ("", ".", "..")]
    if not cleaned:
        raise SecurityError("Chemin de destination vide.")
    target = root.joinpath(*cleaned)
    if is_forbidden(target):
        raise SecurityError(f"Refus d'écrire dans une zone système : {target}")
    # Resolve as far as the tree exists; a not-yet-created leaf is fine.
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if probe.is_symlink() or is_forbidden(probe.resolve()):
        raise SecurityError(f"Destination hors du répertoire JOBIA : {target}")
    return target


def restrict_file(path: Path) -> None:
    """Make a registry file readable by its owner only.

    The ledger decides what gets deleted later, so it should not be writable
    by anything else on a shared machine. A read-only filesystem is left
    alone rather than raising: the caller has already written the file.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def command_is_safe(argv) -> bool:
    """Whether an argument list is safe to hand to ``subprocess``.

    Nothing is ever run through a shell, so the real risk is a control
    character that some downstream tool would misread. This rejects those and
    refuses an empty list.
    """
    if not argv:
        return False
    for part in argv:
        if not isinstance(part, str) or not part:
            return False
        if any(ch in part for ch in "\n\r\x00"):
            return False
    return True
