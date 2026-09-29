"""The guards that stand between a request and the filesystem.

These tests are the argument that provisioning cannot touch a system
directory, cannot be talked into a shell, and cannot corrupt the ledger that
decides what gets deleted.
"""

import json
import os
import stat
from pathlib import Path

import pytest

from aurora_cli.core import security
from aurora_cli.core.security import SecurityError, safe_target, command_is_safe


# --- system paths ------------------------------------------------------------

@pytest.mark.parametrize("bad", ["/etc/passwd", "/usr/bin/python", "/bin/sh",
                                 "/System/Library/Foo", "/boot/config",
                                 "/proc/self/mem"])
def test_system_paths_are_refused(bad):
    assert security.is_forbidden(Path(bad))


def test_ordinary_paths_are_allowed(tmp_path):
    assert not security.is_forbidden(tmp_path / "models" / "thing")
    assert not security.is_forbidden(Path.home() / "Documents" / "photo.png")


# --- target building ---------------------------------------------------------

def test_a_ref_cannot_escape_its_root(tmp_path):
    """A catalogue ref is disk data, not a trusted path."""
    target = safe_target(tmp_path, "..", "..", "etc")
    assert str(target).startswith(str(tmp_path))
    assert ".." not in target.name


def test_separators_in_a_ref_become_ordinary_characters(tmp_path):
    target = safe_target(tmp_path, "org/model")
    assert target.parent == tmp_path
    assert "/" not in target.name


def test_an_empty_target_is_refused(tmp_path):
    with pytest.raises(SecurityError):
        safe_target(tmp_path, "", ".")


def test_traversal_segments_cannot_reach_a_system_root(tmp_path):
    """Repeated `..` is neutralised rather than resolved upwards.

    The point is not that the call is refused: it is that it lands on an
    ordinary, harmless directory inside JOBIA's own root. An earlier test
    asserted a raise here, which passed for the wrong reason and would have
    kept passing if the sanitiser stopped working at all.
    """
    target = safe_target(tmp_path, "..", "..", "..", "etc")
    assert target.parent == tmp_path
    assert not security.is_forbidden(target)


def test_a_symlink_inside_the_root_pointing_outside_is_refused(tmp_path):
    """A symlink already in the data directory must not become an exit."""
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    with pytest.raises(SecurityError):
        safe_target(root, "link", "model")


# --- command safety ----------------------------------------------------------

def test_a_normal_command_is_allowed():
    assert command_is_safe(["ollama", "pull", "qwen3:14b"])


def test_an_empty_command_is_refused():
    assert not command_is_safe([])
    assert not command_is_safe(None)


def test_a_command_with_a_newline_is_refused():
    """A newline in a ref must not become a second command."""
    assert not command_is_safe(["hf", "download", "org/model\nrm -rf /"])


def test_a_command_with_a_null_byte_is_refused():
    assert not command_is_safe(["hf", "download", "org/model\x00"])


def test_shell_metacharacters_are_harmless_without_a_shell():
    """These stay literal because nothing is ever passed through a shell."""
    argv = ["hf", "download", "org/model; rm -rf ~"]
    assert command_is_safe(argv)
    assert argv[2] == "org/model; rm -rf ~"


# --- the ledger that decides what gets deleted -------------------------------

def _ledger_path(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("JOBIA_DATA_HOME", str(tmp_path / "data"))
    from aurora_cli.core import provision
    import importlib
    importlib.reload(provision)
    return provision


def test_the_ledger_is_owner_only(tmp_path, monkeypatch):
    provision = _ledger_path(monkeypatch, tmp_path)
    provision.save_ledger([])
    mode = stat.S_IMODE(provision.ledger_path().stat().st_mode)
    assert mode == 0o600, "le registre contient ce qui peut être supprimé"


def test_a_corrupt_ledger_does_not_become_an_empty_one(tmp_path, monkeypatch):
    """Losing the record of what is ours must never authorise a deletion."""
    provision = _ledger_path(monkeypatch, tmp_path)
    provision.record_installed(tmp_path / "model-a", agent="image")
    path = provision.ledger_path()
    path.write_text("{ this is not json", encoding="utf-8")
    assert provision.load_ledger() == []


def test_an_interrupted_write_leaves_the_previous_ledger_intact(tmp_path, monkeypatch):
    provision = _ledger_path(monkeypatch, tmp_path)
    provision.record_installed(tmp_path / "model-a", agent="image")
    before = json.loads(provision.ledger_path().read_text(encoding="utf-8"))
    # Simulate a crash during the write by leaving the temporary file behind.
    stale = provision.ledger_path().with_suffix(".json.tmp")
    stale.write_text("truncated", encoding="utf-8")
    after = json.loads(provision.ledger_path().read_text(encoding="utf-8"))
    assert before == after
    assert not [p for p in provision.ledger_path().parent.iterdir()
                if p.suffix == ".tmp" and p.stat().st_size == 0]
